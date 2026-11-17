"""hundekraft 3.3 (kennel) builder: vendored assets, selection, routes, real build.

The workshop repo (github.com/Hundekraft/op1-workshop, GPL-3) ships the whole
builder as runtime.py plus base/variants patch assets; op1-web vendors them
under firmware_data/hundekraft/ and runs the identical pipeline server-side.
Builds start from the stock op1_246.op1 only, verified by sha256.
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import re
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from op1_studio import firmware, hundekraft
from op1_studio.config import StudioConfig


REAL_FW_DIR = Path.home() / "Documents" / "git" / "op1-firmware"
R246_SHA256 = "c5315218f825f143b415ca554516541898abee843d3a236df0b54c04e1fb13a9"


def full_selection() -> dict:
    catalog = hundekraft.catalog()
    return {
        "schema": 1,
        "base": catalog["base"],
        "synths": [f["id"] for f in catalog["synths"]],
        "effects": [f["id"] for f in catalog["effects"]],
        "utilities": [f["id"] for f in catalog["utilities"]],
        "preset_cap": catalog["preset_caps"][-1],
    }


@pytest.fixture()
def hk_config(tmp_path: Path) -> StudioConfig:
    cfg = StudioConfig(
        data_dir=tmp_path / "data",
        firmware_dir=tmp_path / "firmware",
        mount=None,
        port=0,
    )
    cfg.ensure_dirs()
    return cfg


@pytest.fixture()
def live_hk(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(firmware, "teboot_block_devices", lambda: [])
    monkeypatch.setattr(firmware, "find_teboot_mount", lambda: None)
    monkeypatch.setattr(firmware, "_last_mount_attempt", {})
    cfg = StudioConfig(
        data_dir=tmp_path / "data",
        mount=tmp_path / "nomount",
        port=0,
        firmware_dir=tmp_path / "firmware",
    )
    cfg.ensure_dirs()
    from op1_studio import server

    previous = getattr(server.Handler, "app", None)
    server.Handler.app = server.StudioApp(cfg)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield cfg, port
    finally:
        httpd.shutdown()
        httpd.server_close()
        if previous is None:
            del server.Handler.app
        else:
            server.Handler.app = previous


def json_request(port: int, path: str, payload: dict | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        if payload is None:
            conn.request("GET", path)
        else:
            conn.request("POST", path, json.dumps(payload))
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        conn.close()
    try:
        return response.status, json.loads(body)
    except json.JSONDecodeError:
        return response.status, body


class TestVendoredAssets:
    def test_release_is_hundekraft_33(self):
        release = hundekraft.release()
        product = release["release"]["product"]
        assert (product["name"], product["version"]) == ("hundekraft", "3.3")
        assert hundekraft.catalog()["base"] == "kennel30-plugins"

    def test_vendored_runtime_matches_pinned_hash(self):
        data = (hundekraft.DATA_DIR / "runtime.py").read_bytes()
        assert hashlib.sha256(data).hexdigest() == hundekraft.release()["runtime_sha256"]

    def test_vendored_base_and_variant_present(self):
        variant_id = next(iter(hundekraft.release()["variants"]))
        assert (hundekraft.DATA_DIR / "base" / "manifest.json").is_file()
        variant_dir = hundekraft.DATA_DIR / "variants" / variant_id
        assert (variant_dir / "manifest.json").is_file()
        assert (variant_dir / "presets.zip").is_file()

    def test_license_files_vendored(self):
        assert "GNU GENERAL PUBLIC LICENSE" in (hundekraft.DATA_DIR / "LICENSE").read_text()
        assert "kennel" in (hundekraft.DATA_DIR / "NOTICE").read_text()
        assert "op1-workshop" in (hundekraft.DATA_DIR / "UPSTREAM.md").read_text()


class TestSelection:
    def test_full_selection_maps_to_vendored_variant(self):
        selection_id, variant_id = hundekraft.config_ids(full_selection())
        assert re.fullmatch(r"[0-9a-f]+", selection_id)
        assert variant_id in hundekraft.release()["variants"]

    def test_unknown_synth_rejected(self):
        selection = full_selection()
        selection["synths"] = ["not_a_synth"]
        with pytest.raises(firmware.FirmwareError):
            hundekraft.config_ids(selection)

    def test_bad_preset_cap_rejected(self):
        selection = full_selection()
        selection["preset_cap"] = 42
        with pytest.raises(firmware.FirmwareError):
            hundekraft.config_ids(selection)

    def test_hidden_reserved_plugin_rejected(self):
        selection = full_selection()
        catalog = hundekraft.catalog()
        reserved = [f["id"] for f in catalog["op1r_plugins"] if f.get("reserved")]
        if not reserved:
            pytest.skip("catalog has no reserved plugins")
        selection["hidden"] = [reserved[0]]
        with pytest.raises(firmware.FirmwareError):
            hundekraft.config_ids(selection)


class TestBuildable:
    def test_random_bytes_not_buildable(self, hk_config):
        path = hk_config.firmware_dir / "op1_junk.op1"
        path.write_bytes(b"nope")
        assert hundekraft.buildable(hk_config, "op1_junk.op1") is False

    def test_only_r246_is_buildable(self, hk_config):
        assert hundekraft.original_sha256() == R246_SHA256


@pytest.mark.skipif(
    not (REAL_FW_DIR / "op1_246.op1").is_file(), reason="stock op1_246.op1 not available"
)
@pytest.mark.skipif(
    not os.environ.get("OP1_TEST_SLOW"), reason="set OP1_TEST_SLOW=1 to run the real build"
)
class TestRealBuild:
    def test_build_all_on_from_stock_r246(self, hk_config):
        source = REAL_FW_DIR / "op1_246.op1"
        assert hashlib.sha256(source.read_bytes()).hexdigest() == R246_SHA256
        (hk_config.firmware_dir / "op1_246.op1").write_bytes(source.read_bytes())
        result = hundekraft.build(hk_config, full_selection())
        assert result["bytes"] > 10_000_000
        assert result["report"]
        out = hk_config.firmware_dir / result["name"]
        assert out.is_file()
        members = {info.name for info, _ in hundekraft.runtime().unpack(out.read_bytes())}
        assert any(name.endswith("content/wavetable.knl") for name in members)
        assert any("te-boot" in name for name in members)
        presets = hk_config.firmware_dir / result["presets_name"]
        assert presets.is_file()
        second = hundekraft.build(hk_config, full_selection())
        assert second["reused"] is True
        assert second["presets_bytes"] == presets.stat().st_size


class TestRoutes:
    def test_state_lists_images_with_buildable_flag(self, live_hk):
        cfg, port = live_hk
        (cfg.firmware_dir / "op1_junk.op1").write_bytes(b"nope")
        status, payload = json_request(port, "/api/firmware/hundekraft")
        assert status == 200
        assert payload["product"]["label"] == "hundekraft 3.3"
        assert {row["name"] for row in payload["images"]} == {"op1_junk.op1"}
        assert payload["images"][0]["buildable"] is False
        assert payload["default_config"]["schema"] == 1
        assert len(payload["synths"]) >= 2

    def test_build_rejects_non_r246_source(self, live_hk):
        cfg, port = live_hk
        (cfg.firmware_dir / "op1_junk.op1").write_bytes(b"nope")
        status, payload = json_request(
            port,
            "/api/firmware/hundekraft/build",
            {"source": "op1_junk.op1", "config": full_selection()},
        )
        assert status == 400
        assert "R246" in payload["error"]

    def test_build_rejects_unknown_selection(self, live_hk):
        cfg, port = live_hk
        (cfg.firmware_dir / "op1_junk.op1").write_bytes(b"nope")
        status, payload = json_request(
            port,
            "/api/firmware/hundekraft/build",
            {"source": "op1_junk.op1", "config": {"synths": ["bogus"]}},
        )
        assert status == 400

    def test_presets_requires_hex_config_id(self, live_hk):
        _, port = live_hk
        status, _ = json_request(port, "/api/firmware/hundekraft/presets?config=../x")
        assert status == 400

    def test_presets_missing_build_is_404(self, live_hk):
        _, port = live_hk
        status, _ = json_request(port, "/api/firmware/hundekraft/presets?config=abcd1234")
        assert status == 404
