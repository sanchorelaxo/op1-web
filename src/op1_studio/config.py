"""Runtime configuration for the OP-1 studio proxy.

Everything the server touches lives under one data directory so the device
side stays read-only until the user explicitly installs something.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_data_dir() -> Path:
    env = os.environ.get("OP1_STUDIO_DATA")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".local" / "share" / "op1-studio"


def _default_installer_dir() -> Path:
    env = os.environ.get("OP1_FIRMWARE_DIR")
    if env:
        return Path(env).expanduser()
    companion = Path.home() / "Documents" / "git" / "op1-firmware"
    if companion.is_dir():
        return companion
    return _default_data_dir() / "firmware"


@dataclass
class StudioConfig:
    """Paths and knobs for the proxy.

    data_dir     scratch space: downloaded patches, backups, caches
    mount        forced OP-1 mount point; None = auto-detect
    host         bind address (default: loopback only)
    port         HTTP port
    """

    data_dir: Path = field(default_factory=_default_data_dir)
    mount: Path | None = None
    host: str = "127.0.0.1"
    port: int = 8770
    firmware_dir: Path = field(default_factory=_default_installer_dir)

    @property
    def library_dir(self) -> Path:
        return self.data_dir / "library"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    def ensure_dirs(self) -> None:
        for p in (self.library_dir, self.backup_dir, self.cache_dir, self.firmware_dir):
            p.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> "StudioConfig":
        mount = os.environ.get("OP1_MOUNT")
        return cls(
            data_dir=_default_data_dir(),
            mount=Path(mount) if mount else None,
            host=os.environ.get("OP1_STUDIO_HOST", "127.0.0.1"),
            port=int(os.environ.get("OP1_STUDIO_PORT", "8770")),
        )
