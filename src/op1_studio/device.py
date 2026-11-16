"""Find and talk to an OP-1 mounted in disk mode.

The original OP-1 exposes a FAT volume with exactly four folders:
``synth/``, ``drum/``, ``tape/`` and ``album/``. That fingerprint is what we
look for; nothing here needs to know a USB vendor id.

Read-only operations are always safe. Writing is the caller's decision, and
every write in this module is preceded by a free-space check and a path
containment check.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

# Folders that must all exist for a volume to count as an OP-1 disk.
REQUIRED_DIRS = ("synth", "drum", "tape", "album")
# Where Linux desktops mount removable media.
SEARCH_ROOTS = (
    Path("/media"),
    Path("/run/media"),
    Path("/mnt"),
    Path("/Volumes"),  # macOS, for people who keep the same library
)
# One AIFF page is small; a patch is typically < 1 MB, a tape track ~32 MB.
MIN_FREE_BYTES = 512 * 1024

TAPE_TRACKS = ("track_1.aif", "track_2.aif", "track_3.aif", "track_4.aif")
ALBUM_SIDES = ("side_a.aif", "side_b.aif")


class DeviceError(RuntimeError):
    """Raised when an install cannot be performed safely."""


@dataclass
class DeviceStatus:
    mounted: bool
    path: str | None
    label: str | None
    free_bytes: int
    total_bytes: int
    synth: list[dict]
    drum: list[dict]
    tape_tracks: list[dict]
    album: list[dict]
    guidance: str

    def to_dict(self) -> dict:
        return asdict(self)


def _has_op1_layout(path: Path) -> bool:
    try:
        return all((path / d).is_dir() for d in REQUIRED_DIRS)
    except OSError:
        return False


def find_mount(explicit: Path | None = None) -> Path | None:
    """Return the OP-1 volume, or None.

    ``explicit`` wins when it points at a real OP-1 layout, which is how a
    user with an unusual mount point bypasses auto-detection.
    """
    if explicit is not None:
        p = Path(explicit)
        return p if _has_op1_layout(p) else None

    candidates: list[Path] = []
    for root in SEARCH_ROOTS:
        if not root.is_dir():
            continue
        try:
            children = [c for c in root.iterdir() if c.is_dir()]
        except OSError:
            continue
        for child in children:
            candidates.append(child)
            # /media/<user>/<label> on most desktops
            try:
                candidates.extend(c for c in child.iterdir() if c.is_dir())
            except OSError:
                pass

    for cand in candidates:
        if _has_op1_layout(cand):
            return cand
    return None


def _aif_entries(folder: Path, prefix: str) -> list[dict]:
    """Playable clips in one bank, each with the path the audio route needs."""
    if not folder.is_dir():
        return []
    return [
        {
            "name": str(p.relative_to(folder)),
            "rel": f"{prefix}/{p.relative_to(folder).as_posix()}",
            "bytes": p.stat().st_size,
        }
        for p in sorted(folder.rglob("*"))
        if p.is_file() and p.suffix.lower() in {".aif", ".aiff"}
    ]


def _track_info(path: Path, rel: str) -> dict:
    try:
        st = path.stat()
        return {"name": path.name, "rel": rel, "bytes": st.st_size, "mtime": int(st.st_mtime)}
    except OSError:
        return {"name": path.name, "rel": rel, "bytes": 0, "mtime": 0}


GUIDANCE_MOUNTED = (
    "OP-1 is mounted in DISK mode. Installs write straight to the volume. "
    "Press SHIFT+COM and pick DISK again only if the volume disappears."
)
GUIDANCE_NOT_MOUNTED = (
    "No OP-1 volume found. On the OP-1: press SHIFT+COM, choose DISK (T4), "
    "then reconnect USB if the volume still does not appear. USB MIDI-only "
    "mode cannot receive files."
)


def status(explicit: Path | None = None) -> DeviceStatus:
    mount = find_mount(explicit)
    if mount is None:
        return DeviceStatus(
            mounted=False,
            path=None,
            label=None,
            free_bytes=0,
            total_bytes=0,
            synth=[],
            drum=[],
            tape_tracks=[],
            album=[],
            guidance=GUIDANCE_NOT_MOUNTED,
        )

    usage = shutil.disk_usage(mount)
    tape_dir = mount / "tape"
    album_dir = mount / "album"
    return DeviceStatus(
        mounted=True,
        path=str(mount),
        label=mount.name,
        free_bytes=usage.free,
        total_bytes=usage.total,
        synth=_aif_entries(mount / "synth", "synth"),
        drum=_aif_entries(mount / "drum", "drum"),
        tape_tracks=[
            _track_info(tape_dir / t, f"tape/{t}") for t in TAPE_TRACKS if (tape_dir / t).exists()
        ],
        album=[
            _track_info(album_dir / s, f"album/{s}") for s in ALBUM_SIDES if (album_dir / s).exists()
        ],
        guidance=GUIDANCE_MOUNTED,
    )


def _contained(mount: Path, target: Path) -> None:
    """Reject anything that would land outside the OP-1 volume."""
    resolved = target.resolve()
    if mount.resolve() not in resolved.parents:
        raise DeviceError(f"refusing to write outside the OP-1 volume: {target}")


def _safe_name(name: str) -> str:
    """Keep names FAT-friendly and free of separators and control chars."""
    base = Path(name).name.strip()
    cleaned = "".join(c for c in base if c.isprintable() and c not in '/\\:*?"<>|')
    cleaned = cleaned.replace("\x00", "").strip(" .")
    if not cleaned:
        raise DeviceError(f"invalid file name: {name!r}")
    if not cleaned.lower().endswith((".aif", ".aiff")):
        cleaned += ".aif"
    return cleaned[:120]


def install_patch(
    source: Path,
    category: str,
    mount: Path | None = None,
    *,
    name: str | None = None,
    force: bool = False,
) -> dict:
    """Copy one .aif patch into ``synth/user/`` or ``drum/user/`` on the OP-1.

    ``category`` is the AIFF APPL type: drum goes to drum/user/, every
    synth/sampler patch goes to synth/user/. The OP-1 only lists files inside
    those ``user`` folders, so writing to the bank root would be invisible.
    Never overwrites unless ``force`` is true.
    """
    target = find_mount(mount)
    if target is None:
        raise DeviceError("OP-1 is not mounted in disk mode")

    if category == "drum":
        folder = target / "drum" / "user"
    elif category in {"synth", "sampler"}:
        folder = target / "synth" / "user"
    else:
        raise DeviceError(f"unknown patch category: {category}")

    src = Path(source)
    if not src.is_file():
        raise DeviceError(f"source file missing: {src}")
    if src.suffix.lower() not in {".aif", ".aiff"}:
        raise DeviceError(f"not an AIFF patch: {src.name}")

    size = src.stat().st_size
    usage = shutil.disk_usage(target)
    if usage.free < size + MIN_FREE_BYTES:
        raise DeviceError(
            f"not enough space on the OP-1: need {size} bytes, {usage.free} free"
        )

    dest = folder / _safe_name(name or src.name)
    _contained(target, dest)
    if dest.exists() and not force:
        raise DeviceError(f"{dest.name} already exists on the OP-1 (use force to replace)")

    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return {"installed": dest.name, "category": category, "bytes": size, "path": str(dest)}


def install_tape(
    tracks: list[Path],
    mount: Path | None = None,
    *,
    backup: bool = True,
    backup_root: Path | None = None,
) -> dict:
    """Replace the four tape tracks, backing up the current tape first.

    OP-1 tape is destructive by nature: there is one tape, four tracks. We
    copy the existing tracks into the library backup folder before writing,
    and we require exactly four audio files.
    """
    target = find_mount(mount)
    if target is None:
        raise DeviceError("OP-1 is not mounted in disk mode")
    if len(tracks) != 4:
        raise DeviceError(f"a tape needs exactly 4 tracks, got {len(tracks)}")

    tape_dir = target / "tape"
    total = sum(Path(t).stat().st_size for t in tracks)
    usage = shutil.disk_usage(target)
    if usage.free < total + MIN_FREE_BYTES:
        raise DeviceError(
            f"not enough space for the tape: need {total} bytes, {usage.free} free"
        )

    backup_path = None
    if backup:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        root = Path(backup_root) if backup_root else target.parent
        root.mkdir(parents=True, exist_ok=True)
        backup_path = root / f"op1-tape-backup-{stamp}"
        backup_path.mkdir(parents=True, exist_ok=False)
        for name in TAPE_TRACKS:
            src = tape_dir / name
            if src.exists():
                shutil.copy2(src, backup_path / name)

    for src, name in zip(tracks, TAPE_TRACKS):
        dest = tape_dir / name
        _contained(target, dest)
        shutil.copyfile(src, dest)

    return {
        "installed": list(TAPE_TRACKS),
        "bytes": total,
        "backup": str(backup_path) if backup_path else None,
    }


def eject(mount: Path | None = None) -> dict:
    """Unmount the volume so the OP-1 can leave disk mode cleanly."""
    target = find_mount(mount)
    if target is None:
        return {"ejected": False, "reason": "OP-1 is not mounted"}
    try:
        dev = subprocess.run(
            ["findmnt", "-n", "-o", "SOURCE", "--target", str(target)],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        if not dev:
            raise DeviceError("could not resolve the OP-1 block device")
        subprocess.run(["udisksctl", "unmount", "-b", dev], check=True, timeout=15)
    except (subprocess.SubprocessError, OSError) as exc:
        raise DeviceError(f"unmount failed: {exc}") from exc
    return {"ejected": True, "device": dev}
