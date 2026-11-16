"""Device layer: mount detection and every write gate."""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import make_aiff
from op1_studio import device


def test_find_mount_accepts_explicit_op1_layout(mount):
    assert device.find_mount(mount) == mount


def test_find_mount_rejects_a_directory_without_the_four_banks(tmp_path):
    (tmp_path / "synth").mkdir()
    (tmp_path / "drum").mkdir()
    assert device.find_mount(tmp_path) is None


def test_status_lists_user_bank_files_and_tape_tracks(mount):
    s = device.status(mount)
    d = s.to_dict()
    assert d["mounted"] is True
    assert d["label"] == "54FF-1FEE"
    assert "user/1.aif" in d["synth"]
    assert {t["name"] for t in d["tape_tracks"]} == {
        "track_1.aif",
        "track_2.aif",
        "track_3.aif",
        "track_4.aif",
    }
    assert d["guidance"]


def test_status_without_a_device_explains_disk_mode(tmp_path):
    d = device.status(tmp_path / "nope").to_dict()
    assert d["mounted"] is False
    assert "disk" in d["guidance"].lower()


def test_install_synth_patch_lands_in_synth_user(mount, tmp_path):
    src = make_aiff(tmp_path / "src" / "Guitar.aif", {"type": "synth"})
    result = device.install_patch(src, "synth", mount)
    assert (mount / "synth" / "user" / "Guitar.aif").is_file()
    assert result["installed"] == "Guitar.aif"
    assert result["bytes"] == src.stat().st_size


def test_install_drum_patch_lands_in_drum_user(mount, tmp_path):
    src = make_aiff(tmp_path / "src" / "Kit.aiff", {"type": "drum"})
    device.install_patch(src, "drum", mount)
    assert (mount / "drum" / "user" / "Kit.aiff").is_file()


def test_sampler_patches_go_to_the_synth_bank(mount, tmp_path):
    src = make_aiff(tmp_path / "src" / "S.aif", {"type": "sampler"})
    device.install_patch(src, "sampler", mount)
    assert (mount / "synth" / "user" / "S.aif").is_file()


def test_install_refuses_to_overwrite_without_force(mount, tmp_path):
    src = make_aiff(tmp_path / "src" / "Guitar.aif")
    device.install_patch(src, "synth", mount)
    with pytest.raises(device.DeviceError, match="already exists"):
        device.install_patch(src, "synth", mount)
    device.install_patch(src, "synth", mount, force=True)


def test_install_rejects_a_non_aiff_file(mount, tmp_path):
    src = tmp_path / "snare.wav"
    src.write_bytes(b"RIFF....WAVE")
    with pytest.raises(device.DeviceError, match="not an AIFF"):
        device.install_patch(src, "synth", mount)


def test_install_rejects_unknown_category(mount, tmp_path):
    src = make_aiff(tmp_path / "src" / "x.aif")
    with pytest.raises(device.DeviceError, match="unknown patch category"):
        device.install_patch(src, "album", mount)


def test_install_sanitizes_names_and_keeps_them_inside_the_bank(mount, tmp_path):
    src = make_aiff(tmp_path / "src" / "evil.aif")
    result = device.install_patch(src, "synth", mount, name="../../etc/passwd")
    assert result["installed"] == "passwd.aif"
    assert (mount / "synth" / "user" / "passwd.aif").is_file()
    assert not (mount / "etc").exists()


def test_install_requires_a_mount(tmp_path):
    src = make_aiff(tmp_path / "src" / "x.aif")
    with pytest.raises(device.DeviceError, match="not mounted"):
        device.install_patch(src, "synth", tmp_path / "nope")


def test_install_tape_backs_up_the_existing_tape(mount, tmp_path):
    tracks = [make_aiff(tmp_path / "src" / f"t{i}.aif") for i in range(1, 5)]
    backups = tmp_path / "backups"
    result = device.install_tape(tracks, mount, backup_root=backups)
    assert result["installed"] == list(device.TAPE_TRACKS)
    made = [p for p in backups.iterdir() if p.is_dir()]
    assert len(made) == 1
    assert {p.name for p in made[0].iterdir()} == set(device.TAPE_TRACKS)
    assert (mount / "tape" / "track_1.aif").stat().st_size == tracks[0].stat().st_size


def test_install_tape_needs_exactly_four_tracks(mount, tmp_path):
    tracks = [make_aiff(tmp_path / "src" / f"t{i}.aif") for i in range(3)]
    with pytest.raises(device.DeviceError, match="exactly 4"):
        device.install_tape(tracks, mount, backup=False)


def test_eject_reports_when_nothing_is_mounted(tmp_path):
    result = device.eject(tmp_path / "nope")
    assert result["ejected"] is False
