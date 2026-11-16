"""GET /api/audio: hand the browser a WAV it can play from an AIFF on the device.

The route is deliberately boring: resolve inside the mount, convert once into
the cache, then serve bytes with Range support so a six-minute tape track can
seek without re-converting. The tests below care about the three things that
break: path traversal, repeated conversion, and a Range header the player needs.
"""
from __future__ import annotations

import http.client
import threading
from http.server import ThreadingHTTPServer

import pytest

from op1_studio import aiff, server


@pytest.fixture()
def live(config):
    """A real Handler on a real socket, mounted on the fake OP-1 volume."""
    previous = getattr(server.Handler, "app", None)
    app = server.StudioApp(config)
    server.Handler.app = app
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


def get(port: int, path: str, headers: dict | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path, headers=headers or {})
    res = conn.getresponse()
    body = res.read()
    conn.close()
    return res, body


# -- status carries the path the client needs -------------------------------


def test_status_marks_every_playable_clip_with_the_device_relative_path(live):
    _config, port = live
    import json

    res, raw = get(port, "/api/status")
    body = json.loads(raw)

    assert res.status == 200
    synth = body["synth"][0]
    assert synth["name"].startswith("user/")
    assert synth["rel"] == f"synth/{synth['name']}"
    assert body["drum"][0]["rel"].startswith("drum/")
    assert body["tape_tracks"][0]["rel"] == "tape/track_1.aif"
    assert body["album"][0]["rel"] == "album/side_a.aif"


# -- the audio route --------------------------------------------------------


def test_a_synth_clip_comes_back_as_wav(live):
    _config, port = live

    res, body = get(port, "/api/audio?rel=synth/user/1.aif")

    assert res.status == 200
    assert res.headers["Content-Type"] == "audio/wav"
    assert res.headers["Accept-Ranges"] == "bytes"
    assert body[:4] == b"RIFF"
    assert body[8:12] == b"WAVE"
    assert int(res.headers["Content-Length"]) == len(body)


def test_a_tape_track_is_accepted_once_it_is_real_aiff(live):
    config, port = live
    from conftest import make_aiff

    # conftest writes a FORM stub for tape; replace it with a real clip.
    make_aiff(config.mount / "tape" / "track_1.aif", samples=b"\x00\x01" * 8)

    res, body = get(port, "/api/audio?rel=tape/track_1.aif")

    assert res.status == 200
    assert body[:4] == b"RIFF"


def test_the_second_request_is_served_from_the_cache(live, monkeypatch):
    config, port = live
    calls = []
    real = aiff.write_wav

    def counting(source, dest):
        calls.append(source)
        return real(source, dest)

    monkeypatch.setattr(aiff, "write_wav", counting)

    first = get(port, "/api/audio?rel=synth/user/1.aif")[1]
    second = get(port, "/api/audio?rel=synth/user/1.aif")[1]

    assert first == second
    assert len(calls) == 1
    assert len(list((config.cache_dir / "audio").glob("*.wav"))) == 1


def test_a_range_request_gets_206_with_just_that_slice(live):
    _config, port = live

    res, body = get(port, "/api/audio?rel=synth/user/1.aif", {"Range": "bytes=0-3"})

    assert res.status == 206
    assert body == b"RIFF"
    total = res.headers["Content-Range"].split("/")[1]
    assert total.isdigit()
    assert int(total) > 4


def test_a_range_past_the_end_is_refused(live):
    _config, port = live

    res, _body = get(port, "/api/audio?rel=synth/user/1.aif", {"Range": "bytes=999999-1000000"})

    assert res.status == 416
    assert res.headers["Content-Range"].startswith("bytes */")


def test_a_missing_clip_is_a_404(live):
    _config, port = live

    res, _body = get(port, "/api/audio?rel=synth/user/nope.aif")

    assert res.status == 404


def test_a_path_traversal_is_refused(live):
    _config, port = live

    res, body = get(port, "/api/audio?rel=../../etc/passwd")

    assert res.status == 400
    assert b"outside" in body


def test_a_file_that_is_not_aiff_is_refused_with_a_reason(live):
    _config, port = live

    # conftest's tape stub is a FORM header with no chunks.
    res, body = get(port, "/api/audio?rel=tape/track_1.aif")

    assert res.status == 400
    assert b"AIFF" in body


def test_a_query_without_rel_is_a_400(live):
    _config, port = live

    res, _body = get(port, "/api/audio")

    assert res.status == 400
