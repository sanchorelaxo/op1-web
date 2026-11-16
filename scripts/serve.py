"""Launch the OP-1 studio proxy."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from op1_studio.config import StudioConfig  # noqa: E402
from op1_studio.server import serve  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="OP-1 studio proxy")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--mount", default=None, help="force an OP-1 mount point")
    parser.add_argument("--data-dir", default=None)
    args = parser.parse_args()

    cfg = StudioConfig.from_env()
    if args.host:
        cfg.host = args.host
    if args.port:
        cfg.port = args.port
    if args.mount:
        cfg.mount = Path(args.mount).expanduser()
    if args.data_dir:
        cfg.data_dir = Path(args.data_dir).expanduser()
    serve(cfg)


if __name__ == "__main__":
    main()
