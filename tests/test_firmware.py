"""Firmware service: installer library, te-boot presence, auto-mount, flash.

The OP-1 answers in three modes -- disk, firmware (te-boot), MIDI -- and this
file guards the middle one. Auto-mount is a requirement, not a nicety: the
te-boot volume shows up as a ~15 MB vfat block device and the user should
never have to touch ``udisksctl`` by hand to flash from the web UI.
"""
from __future__ import annotations

import base64
import http.client
import json
import socket
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from op1_studio import device, firmware
from op1_studio.config import StudioConfig


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


@pytest.fixture()
def fw_config(tmp_path: Path) -> StudioConfig:
    """A StudioConfig whose installer library lives in tmp, never the real dir."""
    cfg = StudioConfig(
        data_dir=tmp_path / "data",
        firmware_dir=tmp_path / "firmware",
        mount=None,
        port=0,
    )
    cfg.ensure_dirs()
    return cfg


@pytest.fixture()
def hermetic_teboot(monkeypatch):
    """No te-boot hardware in unit tests: fake the scan seams."""
    monkeypatch.setattr(firmware, "teboot_block_devices", lambda: [])
    monkeypatch.setattr(firmware, "find_teboot_mount", lambda: None)
    monkeypatch.setattr(firmware, "_last_mount_attempt", {})


def write_installer(cfg: StudioConfig, name: str, data: bytes = b"op1-image-bytes") -> Path:
    cfg.firmware_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.firmware_dir / name
    path.write_bytes(data)
    return path


def fake_lsblk(devices: list[dict]) -> dict:
    return {"blockdevices": devices}


# --------------------------------------------------------------------------
# installer library
# --------------------------------------------------------------------------


class TestSafeInstallerName:
    def test_accepts_plain_firmware_names(self):
        assert firmware.safe_installer_name("op1_246.op1") == "op1_246.op1"
        assert firmware.safe_installer_name("OP1 2.4.6 (stock).OP1") == "OP1 2.4.6 (stock).OP1"

    def test_rejects_names_with_path_separators(self):
        # the browser sends file.name; anything with separators is crafted
        for bad in ["/etc/passwd.op1", "..\\a\\b.op1", "a b/c.op1"]:
            with pytest.raises(firmware.FirmwareError):
                firmware.safe_installer_name(bad)

    @pytest.mark.parametrize(
        "bad",
        ["", "   ", ".op1", "firmware", "firmware.op1p", "firmware.exe", "../x.op1/"],
    )
    def test_rejects_anything_not_ending_in_op1(self, bad):
        with pytest.raises(firmware.FirmwareError):
            firmware.safe_installer_name(bad)


class TestInstallerLibrary:
    def test_save_then_list_roundtrip(self, fw_config):
        saved = firmware.save_installer(fw_config, "op1_test.op1", b"abc123")
        assert saved["name"] == "op1_test.op1"
        rows = firmware.list_installers(fw_config)
        assert [r["name"] for r in rows] == ["op1_test.op1"]
        assert rows[0]["bytes"] == 6
        assert len(rows[0]["sha256"]) == 64

    def test_save_rejects_empty_and_huge(self, fw_config, monkeypatch):
        with pytest.raises(firmware.FirmwareError, match="empty"):
            firmware.save_installer(fw_config, "x.op1", b"")
        monkeypatch.setattr(firmware, "MAX_INSTALLER_BYTES", 10)
        with pytest.raises(firmware.FirmwareError, match="too large"):
            firmware.save_installer(fw_config, "x.op1", b"x" * 11)

    def test_save_overwrites_atomically(self, fw_config):
        firmware.save_installer(fw_config, "x.op1", b"one")
        firmware.save_installer(fw_config, "x.op1", b"two!")
        assert firmware.installer_path(fw_config, "x.op1").read_bytes() == b"two!"
        leftovers = [p.name for p in fw_config.firmware_dir.iterdir() if ".part" in p.name]
        assert leftovers == []

    def test_delete_removes_and_missing_raises(self, fw_config):
        write_installer(fw_config, "gone.op1")
        assert firmware.delete_installer(fw_config, "gone.op1") == {"deleted": "gone.op1"}
        with pytest.raises(firmware.FirmwareError, match="no installer"):
            firmware.delete_installer(fw_config, "gone.op1")

    def test_delete_refuses_zero_byte_files(self, fw_config):
        write_installer(fw_config, "empty.op1", b"")
        with pytest.raises(firmware.FirmwareError, match="zero-byte"):
            firmware.delete_installer(fw_config, "empty.op1")

    def test_list_skips_non_op1_files(self, fw_config):
        fw_config.firmware_dir.mkdir(parents=True, exist_ok=True)
        (fw_config.firmware_dir / "notes.txt").write_text("x")
        write_installer(fw_config, "keep.op1")
        assert [r["name"] for r in firmware.list_installers(fw_config)] == ["keep.op1"]


# --------------------------------------------------------------------------
# patcher plumbing
# --------------------------------------------------------------------------


class TestBuildOutputName:
    def test_ident_is_slugified(self):
        assert firmware.build_output_name("op1r 63") == "op1r63.op1"
        assert firmware.build_output_name("op1r 44-rc 1") == "op1r44rc1.op1"
        assert firmware.build_output_name("!!!") == "custom.op1"


class TestPluginBytes:
    def test_unknown_plugin_names_are_rejected(self):
        for bad in ["nope.op1p", "../secret.op1p", "a/b.op1p", "notaplugin.bin"]:
            with pytest.raises(firmware.FirmwareError):
                firmware.plugin_bytes(bad)

    def test_bundled_plugin_matches_the_manifest_sha(self):
        catalog = firmware.catalog()
        assert catalog["plugins"], "vendored manifest must list plugins"
        entry = catalog["plugins"][0]
        data = firmware.plugin_bytes(entry["file"])
        assert firmware.sha256_bytes(data) == entry["sha256"]


class TestImageMemberBytes:
    def test_member_path_validation(self, monkeypatch):
        monkeypatch.setattr(
            firmware,
            "cached_image_members",
            lambda cfg, name: [("a/b.bin", b"x"), ("content/awa beef.raw", b"y")],
        )
        assert firmware.image_member_bytes(None, "x.op1", "a/b.bin") == b"x"
        assert firmware.image_member_bytes(None, "x.op1", "content/awa beef.raw") == b"y"
        with pytest.raises(firmware.FirmwareError, match="member path"):
            firmware.image_member_bytes(None, "x.op1", "")
        with pytest.raises(firmware.FirmwareError, match="no member"):
            firmware.image_member_bytes(None, "x.op1", "missing.bin")


# --------------------------------------------------------------------------
# te-boot volume fingerprint
# --------------------------------------------------------------------------


class TestTebootVolume:
    @pytest.fixture()
    def small_volume(self, tmp_path, monkeypatch):
        monkeypatch.setattr(firmware, "_volume_total_bytes", lambda p: 15 * 1024 * 1024)
        return tmp_path

    def test_accepts_a_fat_volume_with_only_op1_files(self, small_volume, monkeypatch):
        (small_volume / "op1_246.op1").write_bytes(b"x")
        assert firmware.is_teboot_volume(small_volume) is True

    def test_rejects_large_volumes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(firmware, "_volume_total_bytes", lambda p: 4 * 1024 ** 3)
        assert firmware.is_teboot_volume(tmp_path) is False

    def test_rejects_the_disk_mode_volume(self, mount, monkeypatch):
        monkeypatch.setattr(firmware, "_volume_total_bytes", lambda p: 15 * 1024 * 1024)
        (mount / "op1_246.op1").write_bytes(b"x")
        assert firmware.is_teboot_volume(mount) is False

    def test_rejects_foreign_files_on_the_volume(self, small_volume):
        (small_volume / "holiday_pics.txt").write_text("no")
        assert firmware.is_teboot_volume(small_volume) is False

    def test_find_scans_the_search_roots(self, tmp_path, monkeypatch):
        volume = tmp_path / "user" / "0021-0000"
        volume.mkdir(parents=True)
        (volume / "op1_246.op1").write_bytes(b"x")
        monkeypatch.setattr(device, "SEARCH_ROOTS", (tmp_path / "user",))
        monkeypatch.setattr(firmware, "_volume_total_bytes", lambda p: 15 * 1024 * 1024)
        assert firmware.find_teboot_mount() == volume


# --------------------------------------------------------------------------
# te-boot block device + auto-mount (the new behaviour)
# --------------------------------------------------------------------------


SDA_TEBOOT = {
    "name": "sda",
    "size": 15728640,
    "fstype": "vfat",
    "mountpoint": None,
    "model": "OP-1 Disk",
}
SDA_MOUNTED = dict(SDA_TEBOOT, mountpoint="/media/rjodouin/0021-0000")
# the dict shape teboot_block_devices() returns (used by fakes of that seam)
SDA_DEVICE = {
    "device": "/dev/sda",
    "size": SDA_TEBOOT["size"],
    "model": SDA_TEBOOT["model"],
    "mountpoint": None,
    "mounted": False,
}
DISK_MODE = {
    "name": "sdb",
    "size": 4 * 1024 ** 3,
    "fstype": "vfat",
    "mountpoint": None,
    "model": "OP-1 Disk",
}
NOISE = [
    {"name": "nvme0n1", "size": 2 * 1024 ** 4, "fstype": None, "mountpoint": None,
     "model": "Samsung SSD 980 PRO 2TB"},
    {"name": "loop0", "size": 21217280, "fstype": "squashfs", "mountpoint": "/snap/x",
     "model": None},
]


class TestBlockDevices:
    def test_only_the_small_vfat_op1_disk_qualifies(self, monkeypatch):
        monkeypatch.setattr(
            firmware, "_lsblk_json", lambda: fake_lsblk([SDA_TEBOOT, DISK_MODE] + NOISE)
        )
        devices = firmware.teboot_block_devices()
        assert [d["device"] for d in devices] == ["/dev/sda"]
        assert devices[0]["mounted"] is False

    def test_a_mounted_teboot_device_is_reported_as_mounted(self, monkeypatch):
        monkeypatch.setattr(firmware, "_lsblk_json", lambda: fake_lsblk([SDA_MOUNTED]))
        devices = firmware.teboot_block_devices()
        assert devices[0]["mounted"] is True

    def test_a_broken_lsblk_is_not_a_crash(self, monkeypatch):
        def boom():
            raise OSError("lsblk missing")

        monkeypatch.setattr(firmware, "_lsblk_json", boom)
        assert firmware.teboot_block_devices() == []


class TestAutoMount:
    def test_mounts_a_detected_unmounted_teboot_device(self, tmp_path, monkeypatch):
        volume = tmp_path / "0021-0000"
        volume.mkdir()
        attempts = []
        monkeypatch.setattr(firmware, "_last_mount_attempt", {})
        monkeypatch.setattr(firmware, "teboot_block_devices", lambda: [dict(SDA_DEVICE)])
        monkeypatch.setattr(
            firmware, "find_teboot_mount", lambda: None if not attempts else volume
        )
        monkeypatch.setattr(firmware, "_mount_block_device", attempts.append)

        assert firmware.ensure_teboot_mount() == volume
        assert attempts == ["/dev/sda"]

    def test_mount_attempt_is_throttled_per_device(self, monkeypatch):
        attempts = []
        monkeypatch.setattr(firmware, "_last_mount_attempt", {})
        monkeypatch.setattr(firmware, "find_teboot_mount", lambda: None)
        monkeypatch.setattr(firmware, "teboot_block_devices", lambda: [dict(SDA_DEVICE)])
        monkeypatch.setattr(firmware, "_mount_block_device", attempts.append)

        assert firmware.ensure_teboot_mount() is None
        assert firmware.ensure_teboot_mount() is None  # inside the throttle window
        assert attempts == ["/dev/sda"]

    def test_failing_mount_is_reported_not_raised(self, monkeypatch):
        def boom(device_name):
            raise firmware.FirmwareError("auth required")

        monkeypatch.setattr(firmware, "_last_mount_attempt", {})
        monkeypatch.setattr(firmware, "find_teboot_mount", lambda: None)
        monkeypatch.setattr(firmware, "teboot_block_devices", lambda: [dict(SDA_DEVICE)])
        monkeypatch.setattr(firmware, "_mount_block_device", boom)

        assert firmware.ensure_teboot_mount() is None

    def test_state_shows_firmware_mode_when_only_the_device_is_visible(self, monkeypatch):
        """present=True, mounted=False: the device is in firmware mode, the
        mount just failed -- the UI must light up and explain."""
        monkeypatch.setattr(firmware, "find_teboot_mount", lambda: None)
        monkeypatch.setattr(firmware, "_last_mount_attempt", {})
        monkeypatch.setattr(firmware, "teboot_block_devices", lambda: [dict(SDA_DEVICE)])
        monkeypatch.setattr(firmware, "_mount_block_device", lambda d: (_ for _ in ()).throw(
            firmware.FirmwareError("no polkit")
        ))

        state = firmware.teboot_state()
        assert state["present"] is True
        assert state["mounted"] is False
        assert state["device"] == "/dev/sda"
        assert "udisksctl" in state["help"]

    def test_state_auto_mounts_and_reports_it(self, tmp_path, monkeypatch):
        volume = tmp_path / "0021-0000"
        volume.mkdir()
        mounted = {"now": False}

        def fake_find():
            return volume if mounted["now"] else None

        def fake_mount(name):
            mounted["now"] = True

        monkeypatch.setattr(firmware, "find_teboot_mount", fake_find)
        monkeypatch.setattr(firmware, "_last_mount_attempt", {})
        monkeypatch.setattr(firmware, "teboot_block_devices", lambda: [dict(SDA_DEVICE)])
        monkeypatch.setattr(firmware, "_mount_block_device", fake_mount)

        state = firmware.teboot_state()
        assert state == {
            "present": True,
            "mounted": True,
            "path": str(volume),
            "free_bytes": state["free_bytes"],
            "device": "/dev/sda",
            "auto_mounted": True,
            "help": firmware.TEBOOT_GUIDANCE,
        }

    def test_state_without_anything_is_red_with_power_cycle_help(self, hermetic_teboot):
        state = firmware.teboot_state()
        assert state["present"] is False
        assert state["mounted"] is False
        assert "COM" in state["help"]


# --------------------------------------------------------------------------
# flash
# --------------------------------------------------------------------------


class TestFlash:
    def test_flash_writes_syncs_and_unmounts(self, fw_config, tmp_path, monkeypatch, hermetic_teboot):
        source = write_installer(fw_config, "op1_246_op1r.op1", b"FIRMWARE")
        volume = tmp_path / "0021-0000"
        volume.mkdir()
        unmounted = []
        monkeypatch.setattr(firmware, "_resolve_block_device", lambda m: "/dev/sda")
        monkeypatch.setattr(firmware, "_unmount_volume", unmounted.append)

        result = firmware.flash_installer(fw_config, "op1_246_op1r.op1", mount=volume)

        assert (volume / "op1_246_op1r.op1").read_bytes() == b"FIRMWARE"
        assert result["flashed"] is True
        assert result["sha256"] == firmware.sha256_bytes(b"FIRMWARE")
        assert unmounted == ["/dev/sda"]
        assert result["unmounted"] is True
        assert result["guidance"] == firmware.TEBOOT_GUIDANCE
        source.unlink()

    def test_flash_rejects_the_disk_mode_volume(self, fw_config, mount, hermetic_teboot):
        write_installer(fw_config, "x.op1")
        with pytest.raises(firmware.FirmwareError, match="disk-mode"):
            firmware.flash_installer(fw_config, "x.op1", mount=mount)

    def test_flash_uses_the_auto_mounter_when_unmounted(self, fw_config, tmp_path, monkeypatch):
        volume = tmp_path / "vol"
        volume.mkdir()
        write_installer(fw_config, "x.op1", b"data")
        seen = []
        monkeypatch.setattr(firmware, "ensure_teboot_mount", lambda: volume)
        monkeypatch.setattr(firmware, "_resolve_block_device", lambda m: seen.append(m) or "/dev/sdb")
        monkeypatch.setattr(firmware, "_unmount_volume", lambda d: None)

        result = firmware.flash_installer(fw_config, "x.op1")
        assert result["mount"] == str(volume)

    def test_flash_without_any_device_explains_the_menu(self, fw_config, monkeypatch):
        write_installer(fw_config, "x.op1")
        monkeypatch.setattr(firmware, "ensure_teboot_mount", lambda: None)
        with pytest.raises(firmware.FirmwareError, match="COM"):
            firmware.flash_installer(fw_config, "x.op1")

    def test_flash_checks_free_space_first(self, fw_config, tmp_path, monkeypatch, hermetic_teboot):
        volume = tmp_path / "vol"
        volume.mkdir()
        write_installer(fw_config, "big.op1", b"x" * 5000)

        class Usage:
            free = 100

        monkeypatch.setattr(firmware.shutil, "disk_usage", lambda p: Usage())
        with pytest.raises(firmware.FirmwareError, match="free"):
            firmware.flash_installer(fw_config, "big.op1", mount=volume)


# --------------------------------------------------------------------------
# state blob
# --------------------------------------------------------------------------


class TestFirmwareState:
    def test_state_gathers_catalog_installers_teboot(self, fw_config, hermetic_teboot):
        write_installer(fw_config, "op1_246.op1")
        state = firmware.firmware_state(fw_config)
        assert state["installer_dir"] == str(fw_config.firmware_dir)
        assert [r["name"] for r in state["installers"]] == ["op1_246.op1"]
        assert state["catalog"]["ident"] == firmware.catalog()["result"]["ident"]
        assert state["catalog"]["plugins"]
        assert state["teboot"]["present"] is False
        assert state["symbol_map_versions"]

    def test_symbol_maps_are_vendored_for_known_versions(self):
        versions = firmware.symbol_map_versions()
        assert versions
        assert firmware.symbol_map(versions[0])


# --------------------------------------------------------------------------
# wire routes
# --------------------------------------------------------------------------


def json_request(port: int, path: str, payload: dict | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    body = json.dumps(payload).encode() if payload is not None else None
    conn.request("POST" if body else "GET", path, body=body,
                 headers={"Content-Type": "application/json"} if body else {})
    res = conn.getresponse()
    raw = res.read()
    info = (res.status, res.getheader("Content-Type"), raw)
    conn.close()
    return info


@pytest.fixture()
def live_fw(tmp_path: Path, monkeypatch):
    """A real Handler whose firmware_dir is tmp and whose te-boot is silent."""
    monkeypatch.setattr(firmware, "teboot_block_devices", lambda: [])
    monkeypatch.setattr(firmware, "find_teboot_mount", lambda: None)
    monkeypatch.setattr(firmware, "_last_mount_attempt", {})
    data_dir = tmp_path / "data"
    cfg = StudioConfig(
        data_dir=data_dir, mount=tmp_path / "nomount", port=0,
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


class TestFirmwareRoutes:
    def test_get_state(self, live_fw):
        _cfg, port = live_fw
        status, _ctype, raw = json_request(port, "/api/firmware")
        assert status == 200
        state = json.loads(raw)
        assert state["catalog"]["plugins"]
        assert state["teboot"]["present"] is False

    def test_get_teboot_reports_presence_fields(self, live_fw):
        _cfg, port = live_fw
        status, _ctype, raw = json_request(port, "/api/firmware/teboot")
        body = json.loads(raw)
        assert status == 200
        assert set(body) >= {"present", "mounted", "path", "free_bytes", "help"}

    def test_post_installer_saves_and_deletes(self, live_fw):
        cfg, port = live_fw
        status, _ctype, raw = json_request(
            port, "/api/firmware/installer",
            {"name": "op1_drop.op1", "data_b64": base64.b64encode(b"payload").decode()},
        )
        assert status == 200, raw
        assert (cfg.firmware_dir / "op1_drop.op1").read_bytes() == b"payload"

        status, _ctype, raw = json_request(port, "/api/firmware/installer/delete", {"name": "op1_drop.op1"})
        assert status == 200
        assert not (cfg.firmware_dir / "op1_drop.op1").exists()

    def test_post_installer_validates_the_name(self, live_fw):
        _cfg, port = live_fw
        status, _ctype, raw = json_request(
            port, "/api/firmware/installer", {"name": "firmware.exe", "data_b64": "aGk="}
        )
        assert status == 400

    def test_get_file_downloads_an_installer(self, live_fw):
        cfg, port = live_fw
        write_installer(cfg, "x.op1", b"download-me")
        status, ctype, raw = json_request(port, "/api/firmware/file?name=x.op1")
        assert status == 200
        assert raw == b"download-me"
        assert "octet-stream" in ctype

    def test_get_file_missing_is_404(self, live_fw):
        _cfg, port = live_fw
        status, _ctype, _raw = json_request(port, "/api/firmware/file?name=nope.op1")
        assert status == 404

    def test_get_changelog_and_symbols(self, live_fw):
        _cfg, port = live_fw
        status, _ctype, raw = json_request(port, "/api/firmware/changelog")
        assert status == 200 and b"op1r" in raw
        version = firmware.symbol_map_versions()[0]
        status, _ctype, raw = json_request(port, f"/api/firmware/symbols/{version}")
        assert status == 200
        assert isinstance(json.loads(raw), dict) and json.loads(raw)

    def test_build_uses_the_patcher_and_lands_in_the_library(self, live_fw, monkeypatch):
        cfg, port = live_fw
        write_installer(cfg, "op1_246.op1", b"orig")
        monkeypatch.setattr(
            firmware.firmware_patcher, "patch",
            lambda source, manifest, log=None, plugins=None: (b"patched-bytes", b""),
        )
        status, _ctype, raw = json_request(
            port, "/api/firmware/build",
            {"installer": "op1_246.op1", "plugins": []},
        )
        assert status == 200, raw
        body = json.loads(raw)
        assert (cfg.firmware_dir / body["name"]).read_bytes() == b"patched-bytes"
        assert body["sha256"] == firmware.sha256_bytes(b"patched-bytes")

    def test_flash_route_calls_the_service(self, live_fw, tmp_path, monkeypatch):
        cfg, port = live_fw
        write_installer(cfg, "x.op1", b"firmware")
        volume = tmp_path / "vol"
        volume.mkdir()
        monkeypatch.setattr(firmware, "ensure_teboot_mount", lambda: volume)
        monkeypatch.setattr(firmware, "_resolve_block_device", lambda m: "/dev/sda")
        monkeypatch.setattr(firmware, "_unmount_volume", lambda d: None)

        status, _ctype, raw = json_request(port, "/api/firmware/flash", {"installer": "x.op1"})
        body = json.loads(raw)
        assert status == 200, raw
        assert body["flashed"] is True
        assert (volume / "x.op1").read_bytes() == b"firmware"

    def test_flash_route_without_device_is_400(self, live_fw, monkeypatch):
        cfg, port = live_fw
        write_installer(cfg, "x.op1")
        monkeypatch.setattr(firmware, "ensure_teboot_mount", lambda: None)
        status, _ctype, raw = json_request(port, "/api/firmware/flash", {"installer": "x.op1"})
        assert status == 400
        assert "COM" in json.loads(raw)["error"]
