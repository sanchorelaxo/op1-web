"""GET /api/firmware/member: serve one member's bytes from an image archive.

The browser-side USB update (vendored hundekraft op1push.js) reads the
selected .op1 file member by member over this route, so it needs to accept
real factory member names — including spaced ones like "content/awa beef.raw"
that used to trip the path validator — and answer 404 for anything absent.
"""
from __future__ import annotations

import http.client
import io
import tarfile
import threading
from http.server import ThreadingHTTPServer

import pytest

from op1_studio import firmware_patcher, server


@pytest.fixture()
def live(config, tmp_path):
    """A real Handler on a real socket, with a probe image in a temp library."""
    config.firmware_dir = tmp_path  # never touch the user's real firmware library
    tar = io.BytesIO()
    with tarfile.open(fileobj=tar, mode="w", format=tarfile.GNU_FORMAT) as tf:
        data = b"awa beef raw"
        info = tarfile.TarInfo("content/awa beef.raw")
        info.size = len(data)
        info.mtime = 0
        tf.addfile(info, io.BytesIO(data))
    (config.firmware_dir / "probe.op1").write_bytes(
        firmware_patcher.pack_op1(tar.getvalue())
    )

    previous = getattr(server.Handler, "app", None)
    server.Handler.app = server.StudioApp(config)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        httpd.shutdown()
        httpd.server_close()
        if previous is None:
            del server.Handler.app
        else:
            server.Handler.app = previous


def get(port: int, path: str):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path)
    res = conn.getresponse()
    body = res.read()
    status = res.status
    conn.close()
    return status, body


def test_member_bytes_roundtrip_with_spaces(live):
    status, body = get(
        live, "/api/firmware/member?name=probe.op1&path=content%2Fawa%20beef.raw"
    )
    assert status == 200
    assert body == b"awa beef raw"


def test_missing_member_is_404(live):
    status, _ = get(live, "/api/firmware/member?name=probe.op1&path=nope.bin")
    assert status == 404


def test_empty_member_path_is_404(live):
    status, _ = get(live, "/api/firmware/member?name=probe.op1&path=")
    assert status == 404
