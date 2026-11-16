"""POST /api/drum/upload: a WAV dropped on the page becomes a playable kit.

The upload is the one route that writes to the OP-1, so the tests care about
what lands where: the file must appear in ``drum/user/`` under a FAT-safe name,
it must be listed by /api/status the moment the client refreshes, and it must
come back out of /api/audio as audio. The refusal cases matter just as much,
because a 30 second file drops silently on the device and looks like a bug.
"""
from __future__ import annotations

import http.client
import json
import struct
import threading
from http.server import ThreadingHTTPServer

import pytest

from op1_studio import drumkit, server
from test_drumkit import wav_bytes


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


def post(port: int, path: str, body: bytes):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(
        "POST", path, body=body, headers={"Content-Type": "application/octet-stream"}
    )
    res = conn.getresponse()
    payload = res.read()
    conn.close()
    return res, json.loads(payload or b"{}")


def get(port: int, path: str):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path)
    res = conn.getresponse()
    payload = res.read()
    conn.close()
    return res, payload


# 1.4 s of stereo marimba-ish noise, the shape of the real example file.
def example_wav() -> bytes:
    frames = 44100 * 14 // 10
    pcm = struct.pack("<h", 1200) * 2 * frames
    return wav_bytes(pcm)


def test_upload_lands_in_drum_user_and_is_listed_and_playable(live):
    config, port = live

    res, body = post(port, "/api/drum/upload?name=Alesis-S4-Plus-SterMarimb-C4.wav", example_wav())

    assert res.status == 200, body
    assert body["rel"].startswith("drum/user/")
    assert body["saved"] == "Alesis-S4-Plus-SterMarimb-C4.aif"
    assert body["seconds"] == pytest.approx(1.4, abs=0.01)
    assert body["category"] == "drum"

    written = config.mount / body["rel"]
    assert written.is_file()
    assert struct.unpack(">I", written.read_bytes()[4:8])[0] == written.stat().st_size - 8

    _res, raw = get(port, "/api/status")
    drum = json.loads(raw)["drum"]
    assert body["rel"] in [c["rel"] for c in drum]

    res, audio = get(port, f"/api/audio?rel={body['rel']}")
    assert res.status == 200
    assert res.getheader("Content-Type") == "audio/wav"
    assert audio[:4] == b"RIFF"
    assert audio[24:28] == struct.pack("<I", 44100)


def test_long_audio_is_refused_and_nothing_is_written(live):
    config, port = live
    before = sorted(p.name for p in (config.mount / "drum" / "user").iterdir())
    too_long = wav_bytes(struct.pack("<h", 10) * 2 * (44100 * 13))

    res, body = post(port, "/api/drum/upload?name=long.wav", too_long)

    assert res.status == 400
    assert "12 seconds" in body["error"]
    assert sorted(p.name for p in (config.mount / "drum" / "user").iterdir()) == before


def test_second_upload_of_the_same_name_does_not_clobber_the_first(live):
    config, port = live
    first = example_wav()

    _res, a = post(port, "/api/drum/upload?name=hit.wav", first)
    _res, b = post(port, "/api/drum/upload?name=hit.wav", first)

    assert a["saved"] != b["saved"]
    folder = config.mount / "drum" / "user"
    assert (folder / a["saved"]).read_bytes()[:4] == b"FORM"
    assert len(list(folder.glob("hit*.aif"))) == 2


def test_a_name_with_a_path_in_it_stays_in_drum_user(live):
    config, port = live

    _res, body = post(port, "/api/drum/upload?name=..%2F..%2Fevil.wav", example_wav())

    assert "/" not in body["saved"]
    assert (config.mount / "drum" / "user" / body["saved"]).is_file()
    assert not (config.mount.parent / "evil.aif").exists()


def test_a_missing_name_is_a_bad_request(live):
    _config, port = live

    res, body = post(port, "/api/drum/upload", example_wav())

    assert res.status == 400
    assert "name" in body["error"]


def test_uploading_without_a_mounted_device_explains_itself(live):
    config, port = live
    config.mount = config.data_dir / "not-a-device"

    res, body = post(port, "/api/drum/upload?name=hit.wav", example_wav())

    assert res.status == 400
    assert "not mounted" in body["error"]
