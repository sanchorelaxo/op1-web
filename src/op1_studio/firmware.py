"""Custom firmware service: installer library, op1r builds, te-boot flashing.

The installer library is a plain directory of ``.op1`` update files. The
browser UI lists those names, can save a dragged file into it, can build a new
op1r image into it, and can flash any file from it onto the OP-1 te-boot
update volume.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import threading
import time
from pathlib import Path

from . import firmware_patcher
from .config import StudioConfig

FIRMWARE_DATA_DIR = Path(__file__).resolve().parent / "firmware_data"
PLUGINS_DIR = FIRMWARE_DATA_DIR / "plugins"
SYMBOLS_DIR = FIRMWARE_DATA_DIR / "symbols"
CHANGELOG_PATH = FIRMWARE_DATA_DIR / "CHANGELOG.md"

MAX_INSTALLER_BYTES = 25 * 1024 * 1024
TEBOOT_MAX_TOTAL_BYTES = 100 * 1024 * 1024
OP1_LAYOUT_DIRS = ("synth", "drum", "tape", "album")

TEBOOT_GUIDANCE = (
    "the .op1 is on the te-boot volume and it is unmounted; on the OP-1 press 1 "
    "(update) and then the COM key to start flashing; never press 7 or 8, they erase"
)


class FirmwareError(RuntimeError):
    """Raised when a firmware operation cannot be performed safely."""


_manifest_cache: tuple[float, dict] | None = None
_tar_cache_lock = threading.Lock()
_tar_cache: dict[str, list[tuple[str, bytes]]] = {}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def catalog() -> dict:
    """The vendored op1r manifest: original image, reference build, plugins."""
    global _manifest_cache
    manifest_path = FIRMWARE_DATA_DIR / "manifest.json"
    stamp = manifest_path.stat().st_mtime_ns
    if _manifest_cache is None or _manifest_cache[0] != stamp:
        _manifest_cache = (stamp, json.loads(manifest_path.read_text("utf-8")))
    return _manifest_cache[1]


def safe_installer_name(raw_name: str) -> str:
    name = str(raw_name)
    if "/" in name or "\\" in name:
        raise FirmwareError(f"installer name must be a bare file name: {raw_name!r}")
    name = Path(name).name.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ ()-]*\.op1", name, re.IGNORECASE):
        raise FirmwareError(f"not a firmware installer file name: {raw_name!r}")
    return name


def list_installers(config: StudioConfig) -> list[dict]:
    directory = config.firmware_dir
    directory.mkdir(parents=True, exist_ok=True)
    records = []
    for path in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
        if not path.is_file() or path.suffix.lower() != ".op1":
            continue
        stat = path.stat()
        records.append(
            {
                "name": path.name,
                "bytes": stat.st_size,
                "modified_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
                "sha256": _cached_installer_sha(path),
            }
        )
    return records


_installer_sha_cache: dict[str, tuple[int, int, str]] = {}


def _cached_installer_sha(path: Path) -> str:
    stat = path.stat()
    key = str(path)
    cached = _installer_sha_cache.get(key)
    if cached and cached[0] == stat.st_size and cached[1] == stat.st_mtime_ns:
        return cached[2]
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    value = digest.hexdigest()
    _installer_sha_cache[key] = (stat.st_size, stat.st_mtime_ns, value)
    return value


def installer_path(config: StudioConfig, name: str) -> Path:
    path = config.firmware_dir / safe_installer_name(name)
    if not path.is_file():
        raise FirmwareError(f"no installer named {path.name}")
    return path


def save_installer(config: StudioConfig, name: str, data: bytes) -> dict:
    if not data:
        raise FirmwareError("the dropped firmware file is empty")
    if len(data) > MAX_INSTALLER_BYTES:
        raise FirmwareError(
            f"{len(data)} bytes is too large for an OP-1 update file (max {MAX_INSTALLER_BYTES})"
        )
    directory = config.firmware_dir
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / safe_installer_name(name)
    partial = destination.with_name(f".{destination.name}.{os.getpid()}.part")
    partial.write_bytes(data)
    partial.replace(destination)
    return {"saved": str(destination), "name": destination.name, "bytes": len(data)}


def delete_installer(config: StudioConfig, name: str) -> dict:
    path = installer_path(config, name)
    if path.stat().st_size == 0:
        raise FirmwareError("refusing to remove a zero-byte file; delete it by hand")
    path.unlink()
    _installer_sha_cache.pop(str(path), None)
    return {"deleted": path.name}


def build_output_name(ident: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "", ident.lower()) or "custom"
    return f"{stem}.op1"


def build_custom_image(
    config: StudioConfig,
    source_name: str,
    plugin_names: list[str],
    overwrite: bool = False,
) -> dict:
    source_path = installer_path(config, source_name)
    source_bytes = source_path.read_bytes()
    manifest = catalog()
    log_lines: list[str] = []

    def append_log(line: str) -> None:
        log_lines.append(str(line))

    started = time.monotonic()
    patched_bytes, tar_bytes = firmware_patcher.patch(
        source_bytes, manifest, log=append_log, plugins=list(plugin_names)
    )
    finished = time.monotonic()

    reference_sha = manifest["result"]["sha256"]
    produced_sha = sha256_bytes(patched_bytes)
    every_plugin = {p["name"] for p in manifest.get("plugins", [])}
    selected_plugins = set(plugin_names)
    exact_reference = produced_sha == reference_sha
    recompressed_reference = (
        not exact_reference and every_plugin <= selected_plugins and selected_plugins <= every_plugin
    )

    output_name = build_output_name(manifest["result"]["ident"])
    output_path = config.firmware_dir / output_name
    if output_path.exists() and not overwrite:
        output_path = config.firmware_dir / build_output_name(
            f"{manifest['result']['ident']} {int(finished)}"
        )
    partial = output_path.with_name(f".{output_path.name}.{os.getpid()}.part")
    partial.write_bytes(patched_bytes)
    partial.replace(output_path)

    return {
        "name": output_path.name,
        "bytes": len(patched_bytes),
        "sha256": produced_sha,
        "reference_sha256": reference_sha,
        "exact_reference_build": exact_reference,
        "recompressed_reference_build": recompressed_reference,
        "plugins": sorted(selected_plugins),
        "seconds": round(finished - started, 1),
        "log": log_lines,
    }


def image_tar_members(op1_bytes: bytes) -> list[tuple[str, bytes]]:
    _, members = firmware_patcher.unpack(op1_bytes)
    return [
        (info.name, data)
        for info, data in members
        if info.isfile() and data is not None
    ]


def cached_image_members(config: StudioConfig, name: str) -> list[tuple[str, bytes]]:
    path = installer_path(config, name)
    stat = path.stat()
    cache_key = f"{path}|{stat.st_size}|{stat.st_mtime_ns}"
    with _tar_cache_lock:
        cached = _tar_cache.get(cache_key)
        if cached is None:
            cached = image_tar_members(path.read_bytes())
            _tar_cache.clear()
            _tar_cache[cache_key] = cached
    return cached


def image_member_listing(config: StudioConfig, name: str) -> dict:
    members = cached_image_members(config, name)
    return {
        "name": Path(safe_installer_name(name)).name,
        "members": [
            {
                "path": member_path,
                "bytes": len(member_bytes),
                "sha256": sha256_bytes(member_bytes),
            }
            for member_path, member_bytes in members
        ],
    }


def image_member_bytes(config: StudioConfig, name: str, member_path: str) -> bytes:
    # exact-match lookup against the archive's own member list: nothing here
    # touches the filesystem, so names with spaces (factory content like
    # "content/awa beef.raw") are valid. Empty is the only malformed path.
    if not str(member_path).strip():
        raise FirmwareError(f"not a readable member path: {member_path!r}")
    for candidate_path, candidate_bytes in cached_image_members(config, name):
        if candidate_path == member_path:
            return candidate_bytes
    raise FirmwareError(f"{name} has no member {member_path}")


def plugin_bytes(plugin_file: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.op1p", str(plugin_file), re.IGNORECASE):
        raise FirmwareError(f"not a plugin file name: {plugin_file!r}")
    source = PLUGINS_DIR / plugin_file
    if not source.is_file():
        raise FirmwareError(f"no bundled plugin file {plugin_file}")
    data = source.read_bytes()
    for entry in catalog().get("plugins", []):
        if entry["file"] == source.name and sha256_bytes(data) != entry["sha256"]:
            raise FirmwareError(f"bundled plugin {source.name} is damaged")
    return data


def symbol_map_versions() -> list[str]:
    if not SYMBOLS_DIR.is_dir():
        return []
    return sorted(
        path.stem.removeprefix("op1r-") if path.stem.startswith("op1r-") else path.stem
        for path in SYMBOLS_DIR.glob("*.json")
    )


def symbol_map(version: str) -> dict:
    cleaned = str(version).strip().lower()
    if not re.fullmatch(r"(op1r-)?[0-9]+|r246", cleaned):
        raise FirmwareError(f"not a known symbol map version: {version!r}")
    file_name = cleaned if cleaned.startswith(("op1r-", "r246")) else f"op1r-{cleaned}"
    source = SYMBOLS_DIR / f"{file_name}.json"
    if not source.is_file():
        raise FirmwareError(f"no symbol map for {file_name}")
    return json.loads(source.read_text("utf-8"))


def changelog_text() -> str:
    return CHANGELOG_PATH.read_text("utf-8")


def _volume_total_bytes(path: Path) -> int:
    return shutil.disk_usage(path).total


def find_teboot_mount() -> Path | None:
    from .device import SEARCH_ROOTS

    candidates: list[Path] = []
    for root in SEARCH_ROOTS:
        if not root.is_dir():
            continue
        try:
            children = [child for child in root.iterdir() if child.is_dir()]
        except OSError:
            continue
        for child in children:
            candidates.append(child)
            try:
                candidates.extend(inner for inner in child.iterdir() if inner.is_dir())
            except OSError:
                pass
    for candidate in candidates:
        if is_teboot_volume(candidate):
            return candidate
    return None


def is_teboot_volume(path: Path) -> bool:
    try:
        total_bytes = _volume_total_bytes(path)
    except OSError:
        return False
    if not 0 < total_bytes < TEBOOT_MAX_TOTAL_BYTES:
        return False
    if not os.access(path, os.W_OK):
        return False
    if any((path / directory).is_dir() for directory in OP1_LAYOUT_DIRS):
        return False
    try:
        entries = list(path.iterdir())
    except OSError:
        return False
    return all(
        entry.suffix.lower() in {"", ".op1"} and not entry.name.startswith(".")
        for entry in entries
    )


def teboot_state() -> dict:
    mount = find_teboot_mount()
    devices = teboot_block_devices()
    auto_mounted = False
    if mount is None and any(not d["mounted"] for d in devices):
        # detected but unmounted: the user should never reach for udisksctl
        mount = ensure_teboot_mount()
        auto_mounted = mount is not None
        if mount is not None:
            devices = teboot_block_devices()

    if mount is None:
        visible = next((d for d in devices if not d["mounted"]), None)
        if visible:
            help_text = (
                f"te-boot device {visible['device']} detected but not mounted; "
                f"if the volume does not appear, run: udisksctl mount -b {visible['device']}"
            )
        else:
            help_text = (
                "no te-boot update volume: power the OP-1 off, hold COM, power it on "
                "and keep holding until the te-boot menu appears"
            )
        return {
            "present": bool(devices),
            "mounted": False,
            "path": None,
            "free_bytes": 0,
            "device": visible["device"] if visible else None,
            "auto_mounted": False,
            "help": help_text,
        }

    usage = shutil.disk_usage(mount)
    device_name = next(
        (d["device"] for d in devices if d["mountpoint"] == str(mount)),
        devices[0]["device"] if len(devices) == 1 else None,
    )
    return {
        "present": True,
        "mounted": True,
        "path": str(mount),
        "free_bytes": usage.free,
        "device": device_name,
        "auto_mounted": auto_mounted,
        "help": TEBOOT_GUIDANCE,
    }


# --------------------------------------------------------------------------
# te-boot block device + auto-mount
# --------------------------------------------------------------------------


def _lsblk_json() -> dict:
    """Raw ``lsblk`` JSON; the seam every te-boot device test fakes."""
    result = subprocess.run(
        ["lsblk", "-J", "-b", "-o", "NAME,SIZE,FSTYPE,MOUNTPOINT,MODEL"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return json.loads(result.stdout or "{}")


def teboot_block_devices() -> list[dict]:
    """Unmounted-or-mounted te-boot disks seen by the kernel.

    Fingerprint: the OP-1 reports model ``OP-1 Disk``; only its RAM-disk
    sizes qualify, so a real 4 GB disk-mode volume never matches and neither
    does any other USB stick. A broken lsblk is simply 'no devices'.
    """
    try:
        data = _lsblk_json()
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    found: list[dict] = []
    for dev in data.get("blockdevices", []):
        model = (dev.get("model") or "").casefold()
        size = dev.get("size") or 0
        if (
            dev.get("fstype") == "vfat"
            and "op-1" in model
            and 0 < size < TEBOOT_MAX_TOTAL_BYTES
        ):
            found.append(
                {
                    "device": f"/dev/{dev['name']}",
                    "size": size,
                    "model": dev.get("model"),
                    "mountpoint": dev.get("mountpoint"),
                    "mounted": bool(dev.get("mountpoint")),
                }
            )
    return found


_mounter_lock = threading.Lock()
_last_mount_attempt: dict[str, float] = {}
MOUNT_RETRY_SECONDS = 10.0


def _mount_block_device(device_name: str) -> None:
    """Ask the desktop to mount one device; polkit may refuse."""
    try:
        subprocess.run(
            ["udisksctl", "mount", "-b", device_name],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        detail = (getattr(exc, "stderr", "") or str(exc)).strip()
        raise FirmwareError(f"could not mount {device_name}: {detail}") from exc


def ensure_teboot_mount() -> Path | None:
    """The te-boot volume, mounting it first when the device is only visible.

    Polling this from the UI is safe: attempts per device are throttled,
    failures never raise, and an already-mounted volume costs one scan.
    """
    mount = find_teboot_mount()
    if mount is not None:
        return mount
    now = time.monotonic()
    for dev in teboot_block_devices():
        if dev["mounted"]:
            continue
        with _mounter_lock:
            last = _last_mount_attempt.get(dev["device"], 0.0)
            if now - last < MOUNT_RETRY_SECONDS:
                continue
            _last_mount_attempt[dev["device"]] = now
        try:
            _mount_block_device(dev["device"])
        except FirmwareError:
            continue
        mount = find_teboot_mount()
        if mount is not None:
            return mount
    return None


def _resolve_block_device(mount: Path) -> str:
    result = subprocess.run(
        ["findmnt", "-n", "-o", "SOURCE", "--target", str(mount)],
        capture_output=True,
        text=True,
        timeout=5,
    )
    device_name = result.stdout.strip()
    if not device_name:
        raise FirmwareError("could not resolve the te-boot block device")
    return device_name


def _unmount_volume(device_name: str) -> None:
    try:
        subprocess.run(
            ["udisksctl", "unmount", "-b", device_name],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise FirmwareError(f"unmount failed: {exc}") from exc


def flash_installer(config: StudioConfig, name: str, mount: Path | None = None) -> dict:
    """Copy one installer onto the te-boot volume, then unmount the device.

    Without an explicit ``mount`` the auto-mounter runs first, so a te-boot
    device that is detected but not yet mounted still flashes.
    """
    source_path = installer_path(config, name)
    target_mount = Path(mount) if mount is not None else ensure_teboot_mount()
    if target_mount is None:
        raise FirmwareError(
            "no te-boot update volume is mounted: hold COM while powering the OP-1 on"
        )
    if not target_mount.is_dir():
        raise FirmwareError(f"{target_mount} is not a directory")
    source_bytes = source_path.read_bytes()
    usage = shutil.disk_usage(target_mount)
    if len(source_bytes) > usage.free:
        raise FirmwareError(
            f"{target_mount} has {usage.free} bytes free, the update file needs {len(source_bytes)}"
        )
    if any((target_mount / directory).is_dir() for directory in OP1_LAYOUT_DIRS):
        raise FirmwareError(
            f"{target_mount} is the OP-1 disk-mode volume, not the ~15 MB te-boot update volume"
        )
    destination = target_mount / source_path.name
    destination.write_bytes(source_bytes)
    synced = os.open(destination, os.O_RDONLY)
    try:
        os.fsync(synced)
    finally:
        os.close(synced)
    read_back = destination.read_bytes()
    if sha256_bytes(read_back) != sha256_bytes(source_bytes):
        raise FirmwareError(f"{destination.name} did not read back identically after the copy")

    unmounted = False
    unmount_note = ""
    try:
        _unmount_volume(_resolve_block_device(target_mount))
        unmounted = True
    except (FirmwareError, OSError) as exc:
        unmount_note = str(exc)

    return {
        "flashed": True,
        "installer": source_path.name,
        "bytes": len(source_bytes),
        "sha256": sha256_bytes(source_bytes),
        "mount": str(target_mount),
        "unmounted": unmounted,
        "unmount_note": unmount_note,
        "guidance": TEBOOT_GUIDANCE if unmounted else (
            "the .op1 is copied to the te-boot volume; unmount it (eject on the desktop) "
            "before the OP-1 can use it, then press 1 and COM on the OP-1"
        ),
    }


def firmware_state(config: StudioConfig) -> dict:
    manifest = catalog()
    return {
        "installer_dir": str(config.firmware_dir),
        "installers": list_installers(config),
        "catalog": {
            "title": manifest.get("title"),
            "ident": manifest["result"]["ident"],
            "original": manifest["original"],
            "reference_sha256": manifest["result"]["sha256"],
            "reference_bytes": manifest["result"]["size"],
            "plugins": manifest.get("plugins", []),
        },
        "teboot": teboot_state(),
        "symbol_map_versions": symbol_map_versions(),
    }
