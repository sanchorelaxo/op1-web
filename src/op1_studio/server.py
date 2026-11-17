"""HTTP proxy for the OP-1 studio client.

The server owns three things the browser cannot: the mounted OP-1 volume, the
local file library, and outbound HTTP to op1.fun. The client is a static
three.js app that only ever talks to this process, which keeps CORS, filesystem
paths and device writes in one auditable place.

Standard library only, same as the quneo editor twin.
"""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import posixpath
import sys
import threading
import time
import traceback
import urllib.parse
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import aiff, device, drumkit, firmware, hundekraft, library, op1fun
from .config import StudioConfig

CLIENT_DIR = Path(__file__).parent / "client"

VERSION = "0.1"
STARTED_AT = time.monotonic()

# -- service restart ---------------------------------------------------------
# One mechanism, always the same: the process replaces itself with execve, so
# the listening port and the PID survive and the code now on disk takes over.
# That works whether a supervisor started it or the user ran serve.py by hand,
# and it needs no unit file. The delay lets the HTTP response flush first.
# (Detecting systemd from INVOCATION_ID is not an option: every child of a
# systemd-managed session inherits it, so a bare shell looks supervised too.)
RESTART_DELAY = 0.4        # seconds between the response and the reload

# Streaming block for audio responses: big enough to keep syscalls cheap,
# small enough that a long album side does not balloon memory.
AUDIO_BLOCK = 256 * 1024
# A twelve second mono WAV is ~1 MB; anything far larger is not a drum sample.
MAX_DRUM_UPLOAD = 32 * 1024 * 1024


def parse_range(header: str, size: int) -> tuple[int, int] | None:
    """Parse one ``bytes=`` range, or None when it cannot be satisfied.

    Only the first range is honoured: a browser asks for one range at a time,
    and multipart responses would be more machinery than this needs.
    """
    if size <= 0 or not header.startswith("bytes="):
        return None
    spec = header[6:].split(",")[0].strip()
    if "-" not in spec:
        return None
    first, last = spec.split("-", 1)
    try:
        if first == "":
            want = int(last)
            if want <= 0:
                return None
            start, end = max(0, size - want), size - 1
        else:
            start = int(first)
            end = int(last) if last else size - 1
    except ValueError:
        return None
    if start < 0 or start >= size or end < start:
        return None
    return start, min(end, size - 1)


def restart_argv() -> list[str]:
    """Re-run the package, not the file: ``python -m`` keeps relative imports."""
    return [sys.executable, "-m", "op1_studio.server"]


def restart_env(config: StudioConfig) -> dict:
    """The environment the fresh image needs to come back on the same socket."""
    env = dict(os.environ)
    src = str(Path(__file__).resolve().parent.parent)
    parts = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
    if src not in parts:
        parts.insert(0, src)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env["OP1_STUDIO_HOST"] = config.host
    env["OP1_STUDIO_PORT"] = str(config.port)
    env["OP1_STUDIO_DATA"] = str(config.data_dir)
    if config.mount:
        env["OP1_MOUNT"] = str(config.mount)
    else:
        env.pop("OP1_MOUNT", None)   # a stale mount would beat the real one
    return env


def schedule_restart(config: StudioConfig, exec_fn=None, delay: float = RESTART_DELAY) -> None:
    """Reload the process with the code now on disk, after ``delay`` seconds."""
    if exec_fn is None:
        def reload() -> None:
            os.execve(sys.executable, restart_argv(), restart_env(config))
        exec_fn = reload
    timer = threading.Timer(delay, exec_fn)
    timer.daemon = True
    timer.start()


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
        teboot = firmware.teboot_state()
        st["firmware"] = teboot
        st["modes"] = {
            "disk": bool(st["mounted"]),
            "firmware": bool(teboot["present"]),
            "midi": None,
        }
        return st

    def firmware_state(self) -> dict:
        return firmware.firmware_state(self.config)

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

    def upload_drum(self, name: str, data: bytes) -> dict:
        """Turn a dropped WAV into a kit in ``drum/user/`` on the device."""
        if not data:
            raise ValueError("the upload is empty")
        if len(data) > MAX_DRUM_UPLOAD:
            raise ValueError(
                f"the upload is {len(data)} bytes; a twelve second WAV is about 2 MB"
            )
        prepared = drumkit.prepare(data, Path(Path(name).name or "upload.wav").name)
        payload = drumkit.build_kit(
            prepared["name"], prepared["pcm"], prepared["frames"]
        )
        stem = Path(Path(name).name).stem or "kit"
        result = device.write_drum_kit(self.config.mount, stem, payload)
        result["seconds"] = prepared["seconds"]
        result["frames"] = prepared["frames"]
        return result

    def delete_entry(self, rel: str) -> dict:
        """Remove one file from the device; the pane's x calls this."""
        return device.delete_file(self.config.mount, rel)

    def eject(self) -> dict:
        return device.eject(self.config.mount)

    # --- audio ----------------------------------------------------------
    def audio_path(self, rel: str) -> Path:
        """Return a cached WAV for one clip on the device.

        The conversion happens once per (path, size, mtime) and lands in the
        data dir. Nothing is written to the OP-1, and nothing outside the mount
        can be read: ``resolve`` plus a containment check is what stops
        ``rel=../../etc/passwd`` from turning into a file read.
        """
        if not rel:
            raise ValueError("rel is required")
        mount = device.find_mount(self.config.mount)
        if mount is None:
            raise device.DeviceError("OP-1 is not mounted in disk mode")

        root = mount.resolve()
        target = (root / rel).resolve()
        if target == root or root not in target.parents:
            raise device.DeviceError(f"refusing to read outside the OP-1 volume: {rel}")
        if target.suffix.lower() not in {".aif", ".aiff"}:
            raise device.DeviceError(f"not an AIFF clip: {target.name}")
        if not target.is_file():
            raise FileNotFoundError(f"no such clip on the device: {rel}")

        st = target.stat()
        key = hashlib.sha1(
            f"{rel}|{st.st_size}|{st.st_mtime_ns}".encode("utf-8")
        ).hexdigest()[:16]
        out = self.config.cache_dir / "audio" / f"{target.stem}-{key}.wav"
        if not out.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
            partial = out.with_name(f"{out.name}.{os.getpid()}.part")
            aiff.write_wav(target, partial)
            partial.replace(out)
        return out


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

    def _text(self, text: str, status: int = 200) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _download(self, data: bytes, name: str) -> None:
        """Send bytes as a file download; ``name`` is a safe_installer_name."""
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_audio(self, path: Path) -> None:
        """Serve a WAV with Range support so the player can seek.

        The cache file is keyed by the source's size and mtime, so a re-recorded
        tape track gets a new file; tell the browser not to hold onto bytes.
        """
        size = path.stat().st_size
        header = self.headers.get("Range")
        partial = False
        start, end = 0, size - 1
        if header:
            parsed = parse_range(header, size)
            if parsed is None:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            start, end = parsed
            partial = True

        length = end - start + 1
        self.send_response(HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()

        with path.open("rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(AUDIO_BLOCK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                self.wfile.write(chunk)

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
                return self._json({
                    "ok": True,
                    "version": VERSION,
                    "pid": os.getpid(),
                    "uptime_s": round(time.monotonic() - STARTED_AT, 1),
                })
            if path == "/api/status":
                return self._json(self.app.status())
            if path == "/api/firmware":
                return self._json(self.app.firmware_state())
            if path == "/api/firmware/teboot":
                return self._json(firmware.teboot_state())
            if path == "/api/firmware/members":
                return self._json(
                    firmware.image_member_listing(
                        self.app.config, qs.get("name", [""])[0]
                    )
                )
            if path == "/api/firmware/changelog":
                return self._text(firmware.changelog_text())
            if path == "/api/firmware/hundekraft":
                return self._json(hundekraft.summary(self.app.config))
            if path == "/api/firmware/hundekraft/presets":
                try:
                    pack = hundekraft.presets_file(self.app.config, qs.get("config", [""])[0])
                except FileNotFoundError:
                    return self._error("no such preset pack", HTTPStatus.NOT_FOUND)
                return self._download(pack.read_bytes(), pack.name)
            if path.startswith("/api/firmware/symbols/"):
                version = urllib.parse.unquote(path.rsplit("/", 1)[1])
                return self._json(firmware.symbol_map(version))
            if path == "/api/firmware/file":
                name = firmware.safe_installer_name(qs.get("name", [""])[0])
                source = self.app.config.firmware_dir / name
                if not source.is_file():
                    return self._error(f"no installer {name}", HTTPStatus.NOT_FOUND)
                return self._download(source.read_bytes(), name)
            if path == "/api/library":
                return self._json(self.app.library())
            if path == "/api/audio":
                rel = qs.get("rel", [""])[0]
                try:
                    return self._send_audio(self.app.audio_path(rel))
                except FileNotFoundError as exc:
                    return self._error(str(exc), HTTPStatus.NOT_FOUND)
                except (aiff.AudioError, device.DeviceError, ValueError) as exc:
                    return self._error(str(exc), HTTPStatus.BAD_REQUEST)
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
        except firmware.FirmwareError as exc:
            return self._error(str(exc), HTTPStatus.BAD_REQUEST)
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
            if path == "/api/drum/upload":
                query = urllib.parse.parse_qs(parsed.query)
                name = query.get("name", [""])[0]
                if not name:
                    return self._error(
                        "name is required: the dropped file's name", HTTPStatus.BAD_REQUEST
                    )
                return self._json(self.app.upload_drum(name, self._body()))
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
            if path == "/api/device/delete":
                return self._json(self.app.delete_entry(body.get("rel", "")))
            if path == "/api/eject":
                return self._json(self.app.eject())
            if path == "/api/restart":
                return self._post_restart()
            if path == "/api/firmware/installer":
                raw = body.get("data_b64", "")
                try:
                    data = base64.b64decode(raw, validate=True)
                except (ValueError, TypeError) as exc:
                    raise ValueError(f"data_b64 is not valid base64: {exc}") from exc
                return self._json(
                    firmware.save_installer(self.app.config, body.get("name", ""), data)
                )
            if path == "/api/firmware/installer/delete":
                return self._json(
                    firmware.delete_installer(self.app.config, body.get("name", ""))
                )
            if path == "/api/firmware/build":
                plugins = body.get("plugins", [])
                if not isinstance(plugins, list):
                    raise ValueError("plugins must be a list of .op1p names")
                return self._json(
                    firmware.build_custom_image(
                        self.app.config,
                        body.get("installer", ""),
                        [str(p) for p in plugins],
                        bool(body.get("overwrite")),
                    )
                )
            if path == "/api/firmware/flash":
                return self._json(
                    firmware.flash_installer(self.app.config, body.get("installer", ""))
                )
            if path == "/api/firmware/hundekraft/build":
                selection = body.get("config")
                if not isinstance(selection, dict):
                    raise ValueError("config must be a hundekraft selection object")
                source = body.get("source")
                return self._json(
                    hundekraft.build(
                        self.app.config,
                        selection,
                        source=str(source) if source else None,
                        overwrite=bool(body.get("overwrite")),
                    )
                )
        except (
            device.DeviceError,
            firmware.FirmwareError,
            op1fun.Op1FunError,
            ValueError,
        ) as exc:
            return self._error(str(exc), HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            traceback.print_exc()
            return self._error(str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)
        return self._error("unknown endpoint", HTTPStatus.NOT_FOUND)

    def _post_restart(self) -> None:
        """Answer, then reload the code on disk.

        The reply goes out before the process is replaced, so the client sees
        ``restarting`` rather than a dropped connection. Port 0 means the
        socket was ephemeral and cannot be re-bound, and that is the only case
        where the proxy cannot bring itself back.
        """
        config = self.app.config
        if config.port == 0:
            return self._json(
                {"ok": False,
                 "error": "the proxy is on an ephemeral port \u2014 restart it manually"},
                HTTPStatus.CONFLICT,
            )
        self._json({
            "ok": True,
            "restarting": True,
            "version": VERSION,
        })
        schedule_restart(config)


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
