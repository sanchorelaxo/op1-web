"""Shared fixtures: a fake OP-1 volume and a valid OP-1 AIFF writer."""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import pytest

# The package lives in src/, so tests can import it without an editable install.
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def make_aiff(path: Path, meta: dict | None = None, samples: bytes = b"\x00" * 64) -> Path:
    """Write a minimal but structurally valid OP-1 AIFF.

    FORM/AIFF followed by an ``APPL`` chunk with the ``op-1`` signature and a
    NUL-padded JSON payload, then a tiny SSND chunk. Enough for the metadata
    parser and for file-size checks.
    """
    payload = b"op-1" + json.dumps(meta or {"type": "synth"}).encode() + b"\x00" * 3
    appl = b"APPL" + struct.pack(">I", len(payload)) + payload
    if len(payload) % 2:
        appl += b"\x00"
    ssnd = b"SSND" + struct.pack(">I", 8 + len(samples)) + b"\x00" * 8 + samples
    if len(samples) % 2:
        ssnd += b"\x00"
    comm = (
        b"COMM"
        + struct.pack(">I", 18)
        + struct.pack(">hIh", 1, 1, 16)
        + b"\x40\x0e\xac\x44"
        + b"\x00" * 6
    )
    body = b"AIFF" + comm + appl + ssnd
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"FORM" + struct.pack(">I", len(body)) + body)
    return path


@pytest.fixture()
def mount(tmp_path: Path) -> Path:
    """A directory that looks exactly like an OP-1 in disk mode."""
    root = tmp_path / "54FF-1FEE"
    for name in ("synth", "drum", "tape", "album"):
        (root / name).mkdir(parents=True)
    (root / "synth" / "user").mkdir()
    (root / "drum" / "user").mkdir()
    for i in range(1, 9):
        make_aiff(root / "synth" / "user" / f"{i}.aif", {"type": "synth"})
        make_aiff(root / "drum" / "user" / f"{i}.aif", {"type": "drum"})
    for name in ("track_1.aif", "track_2.aif", "track_3.aif", "track_4.aif"):
        (root / "tape" / name).write_bytes(b"FORM" + b"\x00" * 12)
    (root / "album" / "side_a.aif").write_bytes(b"FORM" + b"\x00" * 12)
    (root / "album" / "side_b.aif").write_bytes(b"FORM" + b"\x00" * 12)
    return root


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    """A private data dir with the library layout the app expects."""
    root = tmp_path / "op1-studio"
    for name in ("patches", "packs", "tapes", "samples"):
        (root / "library" / name).mkdir(parents=True)
        (root / "cache" / name).mkdir(parents=True)
    (root / "backups").mkdir(parents=True)
    return root


@pytest.fixture()
def config(mount: Path, data_dir: Path):
    from op1_studio.config import StudioConfig

    cfg = StudioConfig(data_dir=data_dir, mount=mount, port=0)
    cfg.ensure_dirs()
    return cfg
