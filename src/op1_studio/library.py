"""Local library: everything downloaded or dropped into the data dir.

Layout (all optional):

    library/patches/<name>.aif
    library/packs/<pack>/<name>.aif
    library/tapes/<name>/{track_1..4.aif}
    library/samples/<name>.wav|aif

The library is intentionally just files on disk. There is no database to
migrate, so a user can point a file manager at it and understand it.
"""
from __future__ import annotations

from pathlib import Path

from .op1fun import read_aiff_metadata

AUDIO_EXT = {".aif", ".aiff", ".wav"}


def _entry(path: Path, root: Path, library_dir: Path) -> dict:
    kind = "patch" if path.parent == root else "pack"
    info = {
        "name": path.name,
        "rel": str(path.relative_to(library_dir)),
        "bytes": path.stat().st_size,
        "kind": kind,
        "category": "unknown",
        "title": path.stem,
    }
    if path.suffix.lower() in {".aif", ".aiff"}:
        try:
            meta = read_aiff_metadata(path)
            info["category"] = str(meta.get("type", "unknown")).lower()
            info["meta"] = {
                k: meta.get(k)
                for k in ("type", "octave", "lfo", "fx", "start", "end")
                if k in meta
            }
        except Exception:
            pass
    return info


def scan_patches(library_dir: Path) -> list[dict]:
    root = library_dir / "patches"
    if not root.is_dir():
        return []
    return [_entry(p, root, library_dir) for p in sorted(root.rglob("*")) if p.is_file()]


def scan_tapes(library_dir: Path) -> list[dict]:
    root = library_dir / "tapes"
    if not root.is_dir():
        return []
    tapes = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        tracks = sorted(folder.glob("*.aif")) + sorted(folder.glob("*.aiff"))
        tapes.append(
            {
                "name": folder.name,
                "rel": f"tapes/{folder.name}",
                "tracks": [t.name for t in tracks],
                "bytes": sum(t.stat().st_size for t in tracks),
            }
        )
    return tapes


def scan_samples(library_dir: Path) -> list[dict]:
    root = library_dir / "samples"
    if not root.is_dir():
        return []
    return [
        {"name": p.name, "bytes": p.stat().st_size}
        for p in sorted(root.iterdir())
        if p.is_file() and p.suffix.lower() in AUDIO_EXT
    ]


def save_upload(library_dir: Path, folder: str, name: str, data: bytes) -> Path:
    target_dir = library_dir / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in Path(name).name if c.isprintable() and c not in '/\\:*?"<>|')
    if not safe:
        raise ValueError("empty file name")
    dest = target_dir / safe
    dest.write_bytes(data)
    return dest
