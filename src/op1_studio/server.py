"""HTTP proxy for the OP-1 studio client.

The server owns three things the browser cannot: the mounted OP-1 volume, the
local file library, and outbound HTTP to op1.fun. The client is a static
three.js app that only ever talks to this process, which keeps CORS, filesystem
paths and device writes in one auditable place.

Standard library only, same as the quneo editor twin.
"""
from __future__ import annotations

import json
import mimetypes
import posixpath
import traceback
import urllib.parse
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import device, library, op1fun
from .config import StudioConfig

CLIENT_DIR = Path(__file__).parent / "client"


class StudioApp:
    """Request-independent operations, so tests can call them directly."""

    def __init__(self, config: StudioConfig):
        self.config = config
        self.config.ensure_dirs()

    # --- state ---------------------------------------------------------
    def status(self) -> dict:
        st = device.status(self.config.mount).to_dict()
        st["library_dir"] = str(self.config.library_dir)
        st["mount_override"] = str(self.config.mount) if self.config.mount else None
        return st

    def library(self) -> dict:
        return {
            "patches": library.scan_patches(self.config.library_dir),
            "tapes": library.scan_tapes(self.config.library_dir),
            "samples": library.scan_samples(self.config.library_dir),
        }

    # --- op1.fun -------------------------------------------------------
    def patches(self, page: int, query: str, kind: str = "") -> dict:
        return op1fun.list_patches(page=page, query=query, kind=kind)

    def pack(self, url: str) -> dict:
        return op1fun.list_pack(url)

    def install_remote(self, url: str, force: bool = False, folder: str = "") -> dict:
        path, meta = op1fun.download_patch(url, self.config.cache_dir)
        kind = str(meta.get("type") or "synth").lower()
        # The OP-1 lists a flat folder per bank, so a pack becomes a name
        # prefix rather than a subdirectory the device would never show.
        dest_name = f"{folder} - {path.name}" if folder else path.name
        result = device.install_patch(
            path, kind, self.config.mount, name=dest_name, force=force
        )
        result["meta"] = meta
        return result

    def install_pack(self, url: str, force: bool = False) -> dict:
        pack = op1fun.list_pack(url)
        safe_pack = "".join(
            c for c in pack["title"] if c.isalnum() or c in " -_"
        ).strip() or "pack"
        installed, failed = [], []
        for member in pack["patches"]:
            try:
                installed.append(
                    self.install_remote(member["url"], force=force, folder=safe_pack)
                )
            except Exception as exc:
                failed.append({"url": member["url"], "error": str(exc)})
        return {
            "pack": pack["title"],
            "folder": safe_pack,
            "installed": installed,
            "failed": failed,
        }

    # --- local library -------------------------------------------------
    def _resolve_library(self, rel: str) -> Path:
        root = self.config.library_dir.resolve()
        target = (root / rel).resolve()
        if root not in target.parents and target != root:
            raise ValueError(f"path escapes the library: {rel}")
        return target

    def install_local(self, rel: str, force: bool = False) -> dict:
        path = self._resolve_library(rel)
        meta = op1fun.read_aiff_metadata(path)
        kind = str(meta.get("type") or "synth").lower()
        result = device.install_patch(path, kind, self.config.mount, force=force)
        result["meta"] = meta
        return result

    def install_tape(self, rel: str, backup: bool = True) -> dict:
        folder = self._resolve_library(rel)
        tracks = sorted(p for p in folder.glob("*.aif") if p.is_file())
        tracks += sorted(p for p in folder.glob("*.aiff") if p.is_file())
        if len(tracks) < 4:
            raise ValueError(f"{folder.name}: expected 4 AIFF tracks, found {len(tracks)}")
        return device.install_tape(
            tracks[:4],
            self.config.mount,
            backup=backup,
            backup_root=self.config.backup_dir,
        )

    def upload(self, folder: str, name: str, data: bytes) -> dict:
        if folder not in {"patches", "samples"}:
            raise ValueError("uploads go to patches/ or samples/")
        dest = library.save_upload(self.config.library_dir, folder, name, data)
        return {"saved": str(dest), "bytes": len(data)}

    def eject(self) -> dict:
        return device.eject(self.config.mount)


class Handler(SimpleHTTPRequestHandler):
    app: StudioApp
    server_version = "op1-studio/0.1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(CLIENT_DIR), **kwargs)

    # --- plumbing ------------------------------------------------------
    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message: str, status: int = 500) -> None:
        self._json({"error": message}, status)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        print(f"[op1-studio] {format % args}")

    def end_headers(self) -> None:  # noqa: D401
        """Never let the browser heuristically cache the client bundle.

        SimpleHTTPRequestHandler sends only Last-Modified, which lets Chromium
        keep a stale ES module after an edit. Static assets are tiny and local,
        so no-store costs nothing and keeps reloads honest.
        """
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _json_body(self) -> dict:
        raw = self._body()
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON body: {exc}") from exc

    # --- routes --------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        path = parsed.path

        try:
            if path == "/api/health":
                return self._json({"ok": True, "version": "0.1"})
            if path == "/api/status":
                return self._json(self.app.status())
            if path == "/api/library":
                return self._json(self.app.library())
            if path == "/api/op1fun/patches":
                page = int(qs.get("page", ["1"])[0])
                query = qs.get("q", [""])[0]
                kind = qs.get("kind", [""])[0]
                return self._json(self.app.patches(page, query, kind))
            if path == "/api/op1fun/pack":
                url = qs.get("url", [""])[0]
                if not url:
                    return self._error("url is required", HTTPStatus.BAD_REQUEST)
                return self._json(self.app.pack(url))
        except Exception as exc:
            return self._error(str(exc), HTTPStatus.BAD_GATEWAY)

        if path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/upload":
                query = urllib.parse.parse_qs(parsed.query)
                folder = query.get("folder", ["patches"])[0]
                name = query.get("name", ["upload.aif"])[0]
                return self._json(self.app.upload(folder, name, self._body()))
            body = self._json_body()
            if path == "/api/op1fun/install":
                url = body.get("url", "")
                if not url:
                    return self._error("url is required", HTTPStatus.BAD_REQUEST)
                return self._json(
                    self.app.install_remote(
                        url, bool(body.get("force")), body.get("folder", "")
                    )
                )
            if path == "/api/pack/install":
                url = body.get("url", "")
                if not url:
                    return self._error("url is required", HTTPStatus.BAD_REQUEST)
                return self._json(self.app.install_pack(url, bool(body.get("force"))))
            if path == "/api/install/local":
                return self._json(
                    self.app.install_local(body.get("rel", ""), bool(body.get("force")))
                )
            if path == "/api/tape/install":
                return self._json(
                    self.app.install_tape(
                        body.get("rel", ""), bool(body.get("backup", True))
                    )
                )
            if path == "/api/eject":
                return self._json(self.app.eject())
        except (device.DeviceError, op1fun.Op1FunError, ValueError) as exc:
            return self._error(str(exc), HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            traceback.print_exc()
            return self._error(str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)
        return self._error("unknown endpoint", HTTPStatus.NOT_FOUND)


def serve(config: StudioConfig | None = None) -> None:
    config = config or StudioConfig.from_env()
    Handler.app = StudioApp(config)
    httpd = ThreadingHTTPServer((config.host, config.port), Handler)
    print(f"op1-studio: http://{config.host}:{config.port}  (client: {CLIENT_DIR})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    serve()
