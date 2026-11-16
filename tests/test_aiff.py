"""AIFF -> WAV conversion for browser playback.

Chromium cannot decode AIFF at all, so the proxy converts on the way out.
These tests pin the parts that are easy to get subtly wrong: the 80-bit
sample rate, big-endian sample order, 24-bit downconversion, and the signed
8-bit case where AIFF and WAV disagree about the midpoint.
"""
from __future__ import annotations

import io
import struct
from pathlib import Path

import pytest

from op1_studio import aiff

RATE_44100 = b"\x40\x0e\xac\x44" + b"\x00" * 6


def aiff_file(
    tmp_path: Path,
    pcm: bytes,
    *,
    channels: int = 1,
    bits: int = 16,
    frames: int | None = None,
    rate: bytes = RATE_44100,
    compression: str | None = None,
    name: str = "clip.aif",
) -> Path:
    """Write a structurally valid AIFF (or AIFC) around the given PCM."""
    frames = frames if frames is not None else len(pcm) // (channels * (bits // 8))
    if compression:
        comm_payload = (
            struct.pack(">hIh", channels, frames, bits)
            + rate
            + compression.encode()
            + b"\x00\x00"          # empty Pascal string, padded
        )
        form_type = b"AIFC"
    else:
        comm_payload = struct.pack(">hIh", channels, frames, bits) + rate
        form_type = b"AIFF"
    comm = b"COMM" + struct.pack(">I", len(comm_payload)) + comm_payload
    ssnd = b"SSND" + struct.pack(">I", 8 + len(pcm)) + b"\x00" * 8 + pcm
    body = form_type + comm + ssnd
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"FORM" + struct.pack(">I", len(body)) + body)
    return path


def parse_wav(raw: bytes) -> dict:
    assert raw[:4] == b"RIFF"
    assert raw[8:12] == b"WAVE"
    assert raw[12:16] == b"fmt "
    fmt_size = struct.unpack("<I", raw[16:20])[0]
    audio_format, channels, rate, _byte_rate, block_align, bits = struct.unpack(
        "<HHIIHH", raw[20:36]
    )
    data_at = 20 + fmt_size
    assert raw[data_at : data_at + 4] == b"data"
    data_size = struct.unpack("<I", raw[data_at + 4 : data_at + 8])[0]
    return {
        "format": audio_format,
        "channels": channels,
        "rate": rate,
        "block_align": block_align,
        "bits": bits,
        "data": raw[data_at + 8 : data_at + 8 + data_size],
        "data_size": data_size,
    }


# -- probing ----------------------------------------------------------------


def test_probe_reads_the_comm_chunk(tmp_path):
    pcm = b"\x00\x01" * 10
    path = aiff_file(tmp_path, pcm, channels=1, bits=16)

    info = aiff.probe(path)

    assert info.channels == 1
    assert info.bits == 16
    assert info.sample_rate == 44100
    assert info.frames == 10
    assert info.data_bytes == len(pcm)
    assert info.compression == "NONE"


def test_probe_handles_stereo_48k(tmp_path):
    rate = b"\x40\x0e\xbb\x80" + b"\x00" * 6  # 48000.0
    path = aiff_file(tmp_path, b"\x00\x00" * 6, channels=2, bits=16, rate=rate)

    info = aiff.probe(path)

    assert info.channels == 2
    assert info.sample_rate == 48000
    assert info.frames == 3


def test_probe_accepts_the_clip_fixture_from_conftest(mount):
    """The shared fixture writes an OP-1 shaped file; probe must cope."""
    info = aiff.probe(mount / "synth" / "user" / "1.aif")
    assert info.channels == 1
    assert info.bits == 16


# -- converting -------------------------------------------------------------


def test_wav_header_is_16_bit_pcm_matching_the_source(tmp_path):
    path = aiff_file(tmp_path, b"\x12\x34" * 16, channels=2, bits=16)

    wav = parse_wav(aiff.to_wav_bytes(path))

    assert wav["format"] == 1          # PCM
    assert wav["channels"] == 2
    assert wav["rate"] == 44100
    assert wav["bits"] == 16
    assert wav["block_align"] == 4
    assert wav["data_size"] == 32


def test_16_bit_samples_are_byte_swapped_to_little_endian(tmp_path):
    samples = b"\x12\x34" + b"\xff\xfe" + b"\x80\x00"
    path = aiff_file(tmp_path, samples)

    wav = parse_wav(aiff.to_wav_bytes(path))

    assert wav["data"] == b"\x34\x12" + b"\xfe\xff" + b"\x00\x80"


def test_24_bit_samples_are_narrowed_to_16_bit(tmp_path):
    samples = b"\x12\x34\x56" + b"\xab\xcd\xef"
    path = aiff_file(tmp_path, samples, bits=24)

    wav = parse_wav(aiff.to_wav_bytes(path))

    assert wav["bits"] == 16
    assert wav["data"] == b"\x34\x12" + b"\xcd\xab"


def test_8_bit_samples_shift_from_signed_to_unsigned(tmp_path):
    samples = b"\x00" + b"\x7f" + b"\x80"
    path = aiff_file(tmp_path, samples, bits=8)

    wav = parse_wav(aiff.to_wav_bytes(path))

    assert wav["bits"] == 16
    # signed 8-bit expanded to 16-bit: 0 -> silence, 127 -> high, -128 -> low
    assert wav["data"] == b"\x00\x00" + b"\x00\x7f" + b"\x00\x80"


def test_aifc_none_is_accepted_like_plain_aiff(tmp_path):
    path = aiff_file(tmp_path, b"\x00\x01" * 4, compression="NONE")

    assert aiff.probe(path).compression == "NONE"
    assert parse_wav(aiff.to_wav_bytes(path))["data"] == b"\x01\x00" * 4


def test_aifc_sowt_is_already_little_endian_and_is_not_swapped(tmp_path):
    path = aiff_file(tmp_path, b"\x01\x00" * 4, compression="sowt")

    assert parse_wav(aiff.to_wav_bytes(path))["data"] == b"\x01\x00" * 4


def test_write_wav_streams_to_a_file_and_matches_the_in_memory_result(tmp_path):
    path = aiff_file(tmp_path, bytes(range(64)) * 400)
    dest = tmp_path / "out.wav"

    aiff.write_wav(path, dest)

    assert dest.read_bytes() == aiff.to_wav_bytes(path)
    assert parse_wav(dest.read_bytes())["data_size"] == 64 * 400


def test_write_wav_accepts_a_buffer(tmp_path):
    path = aiff_file(tmp_path, b"\x00\x01" * 2)
    buf = io.BytesIO()

    aiff.write_wav(path, buf)

    assert parse_wav(buf.getvalue())["data"] == b"\x01\x00" * 2


# -- refusing what it cannot convert ---------------------------------------


def test_a_riff_file_is_rejected_as_not_aiff(tmp_path):
    path = tmp_path / "song.wav"
    path.write_bytes(b"RIFF" + b"\x00" * 40)

    with pytest.raises(aiff.AudioError, match="AIFF"):
        aiff.probe(path)


def test_a_truncated_form_is_rejected(tmp_path):
    path = tmp_path / "broken.aif"
    path.write_bytes(b"FORM\x00\x00\x00\x10AIF")

    with pytest.raises(aiff.AudioError, match="truncated"):
        aiff.probe(path)


def test_a_compressed_aifc_is_refused_with_a_useful_message(tmp_path):
    path = aiff_file(tmp_path, b"\x00" * 64, compression="ima4")

    with pytest.raises(aiff.AudioError, match="ima4"):
        aiff.to_wav_bytes(path)


def test_more_frames_than_bytes_is_clamped_not_crashed(tmp_path):
    """A short SSND chunk must not make us read past the file."""
    path = aiff_file(tmp_path, b"\x00\x01" * 2, frames=999)

    wav = parse_wav(aiff.to_wav_bytes(path))

    assert wav["data_size"] == 4
