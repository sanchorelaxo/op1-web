"""Turn a WAV into a drum kit the OP-1 will actually list.

The online guide puts it in one sentence: one sound file, twelve seconds
maximum, saved as .aif, transferred into the user folder inside the drum
folder. The device then reads more than that sentence says:

* the container is AIFC with little-endian samples (``sowt``) inside a
  ``FORM``/``AIFF`` header, with an ``FVER`` chunk first,
* a kit is 24 pads, and each pad's ``start``/``end`` is a 32-bit fraction of a
  24 second timeline (``2**32`` per 1,058,400 frames), not a frame count,
* twelve seconds is exactly half that timeline, and exactly the limit.

One sound dropped in therefore becomes one slice that starts at zero and runs
to the end of the file, copied onto all 24 pads so any key plays it.
"""
from __future__ import annotations

import json
import shutil
import struct
import subprocess
from array import array
from pathlib import Path

TARGET_RATE = 44100
MAX_SECONDS = 12.0
MAX_FRAMES = int(TARGET_RATE * MAX_SECONDS)
# The slice map is normalised over 24 seconds of 44.1 kHz frames, which is why
# a 12 second file ends at 2**31 and not at 0xFFFFFFFF.
TIMELINE_FRAMES = 24 * TARGET_RATE
PADS = 24
NAME_LIMIT = 10

RATE_44100 = b"\x40\x0e\xac\x44\x00\x00\x00\x00\x00\x00"
FVER = b"\xa2\x80\x51\x40"
PCM_DESCRIPTION = b"Signed integer (little-endian) linear PCM"


class KitError(ValueError):
    """The upload cannot become a kit.

    A ValueError on purpose: the HTTP layer already turns those into a 400 with
    the message intact, so a 30 second file reads as a refusal, not a crash.
    """


# -- reading the upload -------------------------------------------------


def _parse_wav(data: bytes, filename: str) -> tuple[array, int, int]:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise KitError(f"{filename}: not a RIFF/WAVE file")

    fmt = None
    pcm = None
    off = 12
    while off + 8 <= len(data):
        cid = data[off : off + 4]
        size = struct.unpack("<I", data[off + 4 : off + 8])[0]
        payload = data[off + 8 : off + 8 + size]
        if cid == b"fmt ":
            if len(payload) < 16:
                raise KitError(f"{filename}: truncated fmt chunk")
            fmt = struct.unpack("<HHIIHH", payload[:16])
        elif cid == b"data":
            pcm = payload
            break
        off += 8 + size + (size & 1)

    if fmt is None:
        raise KitError(f"{filename}: no fmt chunk")
    if pcm is None:
        raise KitError(f"{filename}: no data chunk")

    codec, channels, rate, _byte_rate, _align, bits = fmt
    if codec != 1 or bits != 16:
        raise KitError(
            f"{filename}: only 16-bit PCM WAV is supported, "
            f"got format {codec} with {bits}-bit samples"
        )
    if not 1 <= channels <= 2:
        raise KitError(f"{filename}: {channels} channels, expected mono or stereo")
    if rate < 8000:
        raise KitError(f"{filename}: {rate} Hz is too low to be a sample")

    usable = len(pcm) - (len(pcm) % (channels * 2))
    samples = array("h")
    samples.frombytes(pcm[:usable])
    return samples, channels, rate


def _downmix(samples: array, channels: int) -> array:
    if channels == 1:
        return samples
    out = array("h", bytes(len(samples)))  # one zero sample per stereo frame
    for i in range(len(out)):
        left = samples[2 * i]
        right = samples[2 * i + 1]
        out[i] = int(round((left + right) / 2))
    return out


def _resample(samples: array, rate: int) -> array:
    if rate == TARGET_RATE:
        return samples
    count = len(samples)
    if count == 0:
        raise KitError("the upload contains no audio")
    out_count = max(1, round(count * TARGET_RATE / rate))
    step = rate / TARGET_RATE
    out = array("h", bytes(2 * out_count))
    last = count - 1
    for i in range(out_count):
        pos = i * step
        index = int(pos)
        frac = pos - index
        a = samples[min(index, last)]
        b = samples[min(index + 1, last)]
        out[i] = int(round(a + (b - a) * frac))
    return out


def display_name(filename: str) -> str:
    """The name the OP-1 shows: no extension, no separators, ten characters."""
    stem = Path(Path(filename or "upload").name).stem
    cleaned = "".join(
        c for c in stem if c.isprintable() and c not in '/\\:*?"<>|'
    ).strip(" .")
    return cleaned[:NAME_LIMIT] or "upload"


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _decode_with_ffmpeg(data: bytes, filename: str, reason: str | None) -> array:
    """Decode anything ffmpeg understands into mono 44.1 kHz 16-bit PCM.

    Slower than reading a 16-bit WAV by hand, but it is the same decoder
    that plays the file, so MP3, FLAC, Ogg, 24-bit and float WAVs all arrive as
    the samples the user actually hears.
    """
    binary = _ffmpeg()
    if binary is None:
        raise KitError(
            f"{reason or f'{filename}: not a 16-bit PCM WAV'}; ffmpeg is not "
            "installed, so other formats cannot be decoded"
        )
    try:
        done = subprocess.run(
            [
                binary, "-nostdin", "-v", "error",
                "-t", f"{MAX_SECONDS + 1:.3f}",
                "-i", "pipe:0",
                "-map", "0:a:0",
                "-ac", "1", "-ar", str(TARGET_RATE),
                "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1",
            ],
            input=data,
            capture_output=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise KitError(f"{filename}: decoding took longer than a minute") from exc
    if done.returncode != 0 or not done.stdout:
        detail = (done.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        tail = detail[-1] if detail else "no audio stream"
        raise KitError(f"{filename}: could not be decoded ({tail})")
    samples = array("h")
    samples.frombytes(done.stdout[: len(done.stdout) - (len(done.stdout) % 2)])
    return samples


def prepare(data: bytes, filename: str = "upload.wav") -> dict:
    """Validate an upload and return mono 44.1 kHz 16-bit PCM plus its facts."""
    samples = None
    reason = f"{filename}: not a 16-bit PCM WAV"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        try:
            raw, channels, rate = _parse_wav(data, filename)
        except KitError as err:
            # A 24-bit or float WAV is still perfectly good audio; let ffmpeg
            # have it and keep the strict reader as the fast, dependency-free
            # path for the common case.
            reason = str(err)
        else:
            samples = _resample(_downmix(raw, channels), rate)
    if samples is None:
        samples = _decode_with_ffmpeg(data, filename, reason)

    frames = len(samples)
    if frames == 0:
        raise KitError(f"{filename}: the upload contains no audio")
    if frames > MAX_FRAMES:
        raise KitError(
            f"{filename}: longer than the OP-1's {MAX_SECONDS:.0f} seconds"
        )
    return {
        "name": display_name(filename),
        "rate": TARGET_RATE,
        "channels": 1,
        "frames": frames,
        "seconds": round(frames / TARGET_RATE, 3),
        "pcm": samples.tobytes(),
    }


# -- writing the kit ----------------------------------------------------


def _chunk(cid: bytes, payload: bytes) -> bytes:
    return cid + struct.pack(">I", len(payload)) + payload + (b"\x00" if len(payload) % 2 else b"")


def _metadata(name: str, frames: int) -> dict:
    end = (frames << 32) // TIMELINE_FRAMES
    return {
        "drum_version": 2,
        "dyna_env": [0, 8192, 0, 8192, 0, 0, 0, 0],
        "end": [end] * PADS,
        "fx_active": False,
        "fx_params": [8000] * 8,
        "fx_type": "delay",
        "lfo_active": False,
        "lfo_params": [16000, 16000, 16000, 16000, 0, 0, 0, 0],
        "lfo_type": "tremolo",
        "name": name,
        "octave": 0,
        "pitch": [0] * PADS,
        "playmode": [8192] * PADS,
        "reverse": [8192] * PADS,
        "start": [0] * PADS,
        "type": "drum",
        "volume": [8192] * PADS,
    }


def build_kit(name: str, pcm: bytes, frames: int) -> bytes:
    """Wrap mono 44.1 kHz 16-bit samples in an AIFC the OP-1 accepts."""
    if len(pcm) != frames * 2:
        raise KitError(f"expected {frames * 2} bytes for {frames} frames, got {len(pcm)}")
    if frames > MAX_FRAMES:
        raise KitError(
            f"{frames / TARGET_RATE:.1f} s is longer than the OP-1's "
            f"{MAX_SECONDS:.0f} seconds"
        )

    comm = (
        struct.pack(">hIh", 1, frames, 16)
        + RATE_44100
        + b"sowt"
        + bytes([len(PCM_DESCRIPTION)])
        + PCM_DESCRIPTION
    )
    appl = b"op-1" + json.dumps(
        _metadata(name[:NAME_LIMIT] or "upload", frames), separators=(",", ":")
    ).encode("utf-8")
    body = (
        _chunk(b"FVER", FVER)
        + _chunk(b"COMM", comm)
        + _chunk(b"APPL", appl)
        + _chunk(b"SSND", b"\x00" * 8 + pcm)
    )
    return b"FORM" + struct.pack(">I", len(body) + 4) + b"AIFF" + body
