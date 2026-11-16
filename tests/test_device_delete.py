"""Deleting one entry from the device, and nothing else.

The x in the pane is destructive, so the route has to be narrow: a device-relative
path that resolves inside the mounted volume, a real file at the end of it, and
no way to reach the rest of the disk with ``..`` or an absolute path. Everything
else on the device must survive the delete untouched.
"""
from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from op1_studio import server


@pytest.fixture()
def live(config):
    previous = getattr(server.Handler, "app", None)
    server.Handler.app = server.StudioApp(config)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield config, port
    finally:
        httpd.shutdown()
        httpd.server_close()
        if previous is None:
            del server.Handler.app
        else:
            server.Handler.app = previous


def delete(port: int, payload: dict):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(
        "POST",
        "/api/device/delete",
        body=json.dumps(payload),
        headers={"Content-Type": "application/json"},
    )
    res = conn.getresponse()
    body = res.read()
    conn.close()
    return res.status, json.loads(body or b"{}")


def status(port: int) -> dict:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", "/api/status")
    res = conn.getresponse()
    body = res.read()
    conn.close()
    return json.loads(body)


def test_deleting_a_drum_entry_removes_only_that_file(live):
    config, port = live
    victim = config.mount / "drum" / "user" / "3.aif"
    size = victim.stat().st_size

    code, body = delete(port, {"rel": "drum/user/3.aif"})

    assert code == 200, body
    assert body["rel"] == "drum/user/3.aif"
    assert body["bytes"] == size
    assert not victim.exists()
    assert (config.mount / "drum" / "user" / "2.aif").is_file()
    assert (config.mount / "drum" / "user" / "4.aif").is_file()
    assert "drum/user/3.aif" not in [c["rel"] for c in status(port)["drum"]]


def test_deleting_a_tape_track_works(live):
    config, port = live

    code, body = delete(port, {"rel": "tape/track_2.aif"})

    assert code == 200, body
    assert not (config.mount / "tape" / "track_2.aif").exists()


def test_a_path_that_climbs_out_of_the_volume_is_refused(live):
    config, port = live
    outside = config.mount.parent / "precious.aif"
    outside.write_bytes(b"keep me")

    code, body = delete(port, {"rel": "../precious.aif"})

    assert code == 400
    assert "outside" in body["error"]
    assert outside.is_file()


def test_an_absolute_path_is_refused(live):
    config, port = live
    target = config.mount / "drum" / "user" / "1.aif"

    code, body = delete(port, {"rel": str(target)})

    assert code == 400
    assert target.is_file()


def test_a_directory_is_not_a_file_to_delete(live):
    config, port = live

    code, body = delete(port, {"rel": "drum/user"})

    assert code == 400
    assert (config.mount / "drum" / "user").is_dir()


def test_a_missing_file_says_not_found(live):
    _config, port = live

    code, body = delete(port, {"rel": "drum/user/nope.aif"})

    assert code == 400
    assert "not found" in body["error"]


def test_a_missing_rel_is_a_bad_request(live):
    _config, port = live

    code, body = delete(port, {})

    assert code == 400
    assert "rel" in body["error"]


def test_deleting_stops_when_the_device_is_not_mounted(live):
    config, port = live
    config.mount = config.data_dir / "no-device-here"

    code, body = delete(port, {"rel": "drum/user/1.aif"})

    assert code == 400
    assert "not mounted" in body["error"]
