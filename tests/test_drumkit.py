"""Any WAV in, one OP-1 drum kit out.

The online guide says one sound file, twelve seconds maximum, saved as .aif and
dropped into the user folder inside the drum folder. The device is pickier than
that sentence suggests: it reads AIFC with little-endian PCM (``sowt``), and a
kit is 24 slices whose start/end are 32-bit fractions of a 24 second timeline.
Getting any of that wrong produces a file the OP-1 lists as empty or refuses to
load, so every field below is asserted against a real kit's shape.
"""
from __future__ import annotations

import json
import shutil
import struct
import subprocess

import pytest

from op1_studio import drumkit

RATE_44100 = b"\x40\x0e\xac\x44\x00\x00\x00\x00\x00\x00"

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg is not installed"
)


def encoded(source: bytes, *args: str) -> bytes:
    """Let ffmpeg turn raw PCM into whatever container the test needs."""
    done = subprocess.run(
        [
            shutil.which("ffmpeg"), "-nostdin", "-v", "error",
            "-f", "s16le", "-ar", "44100", "-ac", "1", "-i", "pipe:0",
            *args, "pipe:1",
        ],
        input=source,
        capture_output=True,
        check=True,
    )
    return done.stdout


def wav_bytes(
    pcm: bytes,
    *,
    rate: int = 44100,
    channels: int = 2,
    bits: int = 16,
    fmt: int = 1,
) -> bytes:
    """A minimal RIFF/WAVE around raw interleaved samples."""
    block = channels * bits // 8
    fmt_chunk = struct.pack("<HHIIHH", fmt, channels, rate, rate * block, block, bits)
    body = (
        b"WAVE"
        + b"fmt " + struct.pack("<I", len(fmt_chunk)) + fmt_chunk
        + b"data" + struct.pack("<I", len(pcm)) + pcm
        + (b"\x00" if len(pcm) % 2 else b"")
    )
    return b"RIFF" + struct.pack("<I", len(body)) + body


def frames_of(mono_pcm: bytes) -> int:
    return len(mono_pcm) // 2


def chunks(raw: bytes) -> dict[bytes, bytes]:
    assert raw[:4] == b"FORM"
    assert struct.unpack(">I", raw[4:8])[0] == len(raw) - 8
    out = {}
    off = 12
    while off < len(raw) - 8:
        cid = raw[off : off + 4]
        size = struct.unpack(">I", raw[off + 4 : off + 8])[0]
        out[cid] = raw[off + 8 : off + 8 + size]
        off += 8 + size + (size & 1)
    return out


# -- reading the upload -------------------------------------------------


def test_stereo_is_averaged_down_to_one_channel():
    stereo = struct.pack("<hhhh", 1000, 3000, -4000, 0)

    prep = drumkit.prepare(wav_bytes(stereo), "hit.wav")

    assert prep["channels"] == 1
    assert prep["frames"] == 2
    assert struct.unpack("<hh", prep["pcm"]) == (2000, -2000)


def test_mono_passes_through_unchanged():
    mono = struct.pack("<hhh", 32767, -32768, 0)

    prep = drumkit.prepare(wav_bytes(mono, channels=1), "hit.wav")

    assert prep["pcm"] == mono


def test_a_file_longer_than_twelve_seconds_is_refused():
    too_long = b"\x00\x00" * 2 * (44100 * 12 + 1)

    with pytest.raises(drumkit.KitError) as err:
        drumkit.prepare(wav_bytes(too_long), "long.wav")

    assert "12 seconds" in str(err.value)


def test_exactly_twelve_seconds_is_accepted():
    twelve = b"\x00\x00" * 2 * (44100 * 12)

    prep = drumkit.prepare(wav_bytes(twelve), "twelve.wav")

    assert prep["frames"] == 44100 * 12
    assert prep["seconds"] == pytest.approx(12.0)


def test_22050_is_doubled_to_44100():
    mono = struct.pack("<hhhh", 0, 1000, 2000, 3000)

    prep = drumkit.prepare(wav_bytes(mono, channels=1, rate=22050), "half.wav")

    assert prep["rate"] == 44100
    assert 7 <= prep["frames"] <= 9


def test_48000_is_resampled_to_44100():
    # 480 frames at 48 kHz is 10 ms; at 44.1 kHz that is 441 frames.
    mono = struct.pack("<h", 0) * 480

    prep = drumkit.prepare(wav_bytes(mono, channels=1, rate=48000), "pa.wav")

    assert prep["rate"] == 44100
    assert abs(prep["frames"] - 441) <= 2


def test_a_format_wav_cannot_hold_is_explained(monkeypatch):
    monkeypatch.setattr(drumkit.shutil, "which", lambda _name: None)

    with pytest.raises(drumkit.KitError) as err:
        drumkit.prepare(wav_bytes(b"\x00" * 32, fmt=3, bits=32), "float.wav")

    assert "16-bit PCM WAV" in str(err.value)
    assert "ffmpeg" in str(err.value)


def test_a_non_riff_file_is_refused_when_ffmpeg_is_missing(monkeypatch):
    monkeypatch.setattr(drumkit.shutil, "which", lambda _name: None)

    with pytest.raises(drumkit.KitError) as err:
        drumkit.prepare(b"not a wav at all", "fake.wav")

    assert "ffmpeg" in str(err.value)


def test_a_file_ffmpeg_cannot_read_says_so():
    with pytest.raises(drumkit.KitError) as err:
        drumkit.prepare(b"\x00" * 4096, "junk.wav")

    assert "could not be decoded" in str(err.value)


# -- everything else ffmpeg can read -----------------------------------


@needs_ffmpeg
def test_an_mp3_is_decoded_to_mono_44100():
    source = struct.pack("<h", 8000) * 44100  # one second of a flat tone
    mp3 = encoded(source, "-b:a", "128k", "-f", "mp3")

    prep = drumkit.prepare(mp3, "Kick Drum 01.mp3")

    assert prep["channels"] == 1
    assert prep["rate"] == 44100
    assert prep["seconds"] == pytest.approx(1.0, abs=0.05)
    assert prep["name"] == "Kick Drum "
    assert len(prep["pcm"]) == prep["frames"] * 2


@needs_ffmpeg
def test_a_stereo_mp3_is_downmixed_and_a_long_one_is_still_refused():
    source = struct.pack("<hh", 100, 200) * 44100 * 13
    mp3 = encoded(source, "-b:a", "128k", "-f", "mp3")

    with pytest.raises(drumkit.KitError) as err:
        drumkit.prepare(mp3, "beds.mp3")

    assert "12 seconds" in str(err.value)


@needs_ffmpeg
def test_flac_and_ogg_and_float_wav_all_arrive_as_the_same_samples():
    source = struct.pack("<h", 5000) * 22050
    mono = drumkit.prepare(wav_bytes(source, channels=1), "plain.wav")

    for name, args in (
        ("hit.flac", ("-f", "flac")),
        ("hit.ogg", ("-c:a", "libvorbis", "-f", "ogg")),
        ("hitf.wav", ("-c:a", "pcm_f32le", "-f", "wav")),
    ):
        prep = drumkit.prepare(encoded(source, *args), name)
        # Vorbis carries a short codec delay; the others must match exactly.
        assert prep["frames"] == pytest.approx(mono["frames"], abs=200), name


@needs_ffmpeg
def test_a_24_bit_wav_takes_the_ffmpeg_path_and_keeps_its_length():
    source = struct.pack("<h", 4000) * 4410
    wav24 = encoded(source, "-c:a", "pcm_s24le", "-f", "wav")

    prep = drumkit.prepare(wav24, "hat24.wav")

    assert prep["rate"] == 44100
    assert prep["frames"] == pytest.approx(4410, abs=2)


# -- writing the kit ----------------------------------------------------


def test_kit_is_aifc_sowt_44100_mono_and_the_device_round_trips_it():
    pcm = struct.pack("<hhhh", 0, 100, 200, 300)

    raw = drumkit.build_kit("marimb", pcm, frames_of(pcm))
    parts = chunks(raw)

    assert set(parts) == {b"FVER", b"COMM", b"APPL", b"SSND"}
    assert parts[b"FVER"] == b"\xa2\x80\x51\x40"
    comm = parts[b"COMM"]
    assert len(comm) == 64
    assert struct.unpack(">hIh", comm[:8]) == (1, 4, 16)
    assert comm[8:18] == RATE_44100
    assert comm[18:22] == b"sowt"
    assert parts[b"SSND"][8:] == pcm


def test_kit_slices_cover_the_whole_file_on_all_24_pads():
    pcm = b"\x00\x00" * 441

    meta = json.loads(chunks(drumkit.build_kit("hit", pcm, 441))[b"APPL"][4:])

    assert meta["type"] == "drum"
    assert meta["drum_version"] == 2
    assert len(meta["start"]) == len(meta["end"]) == 24
    assert set(meta["start"]) == {0}
    assert set(meta["end"]) == {(441 << 32) // drumkit.TIMELINE_FRAMES}


def test_twelve_seconds_lands_on_half_the_slice_timeline():
    end = (44100 * 12 << 32) // drumkit.TIMELINE_FRAMES

    assert end == 2**31


def test_kit_name_is_shortened_to_what_the_device_shows():
    pcm = b"\x00\x00" * 8

    meta = json.loads(
        chunks(drumkit.build_kit("Alesis-S4-Plus-SterMarimb-C4", pcm, 8))[b"APPL"][4:]
    )

    assert meta["name"] == "Alesis-S4-"
    assert len(meta["name"]) <= 10


def test_build_refuses_audio_the_device_would_not_load():
    frames = drumkit.MAX_FRAMES + 1

    with pytest.raises(drumkit.KitError) as err:
        drumkit.build_kit("long", b"\x00\x00" * frames, frames)

    assert "12 seconds" in str(err.value)
