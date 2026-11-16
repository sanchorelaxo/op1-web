"""Convert OP-1 AIFF clips into WAV the browser can actually play.

Chromium has no AIFF decoder, so the proxy converts on the way out instead of
pretending an ``<audio>`` tag can read the file. The two containers differ only
in framing and byte order: AIFF is big-endian PCM inside ``FORM``/``AIFF``, WAV
is little-endian PCM inside ``RIFF``/``WAVE``. For 16-bit audio the whole job is
a byte swap plus a 44-byte header, which is why this needs no ffmpeg.

OP-1 files are 16-bit, but the same code accepts 8, 24 and 32-bit integer
samples and narrows them to 16-bit, because an imported sample or a tape pulled
from another tool can be any of those. Anything genuinely compressed (ima4 and
friends) is refused with the compression name in the message rather than
producing noise.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

# One block is small enough to stay out of the way of playback and large enough
# that a 32 MB tape track is a few hundred writes rather than millions.
BLOCK = 256 * 1024

SUPPORTED = {"NONE", "sowt", "fl32", "FL32"}
# AIFC compression types whose payload is little-endian.
LITTLE_ENDIAN = {"sowt", "fl32", "FL32"}


class AudioError(ValueError):
    """Raised when a file is not AIFF, or uses a compression we cannot unwrap."""


@dataclass(frozen=True)
class AiffInfo:
    """Everything needed to locate and describe the sample data."""

    channels: int
    sample_rate: int
    bits: int
    frames: int
    data_offset: int      # absolute byte offset of the first sample
    data_bytes: int       # bytes actually present in the SSND chunk
    compression: str      # "NONE" for plain AIFF

    @property
    def bytes_per_sample(self) -> int:
        return self.bits // 8

    @property
    def block_align(self) -> int:
        return self.channels * 2      # output is always 16-bit

    @property
    def duration_s(self) -> float:
        return self.frames / self.sample_rate if self.sample_rate else 0.0


def _extended_to_float(raw: bytes) -> float:
    """Decode the 80-bit IEEE extended float AIFF uses for the sample rate."""
    if len(raw) < 10:
        raise AudioError("truncated sample rate field")
    exponent = struct.unpack(">H", raw[0:2])[0]
    mantissa = struct.unpack(">Q", raw[2:10])[0]
    sign = -1.0 if exponent & 0x8000 else 1.0
    exponent &= 0x7FFF
    if exponent == 0 and mantissa == 0:
        return 0.0
    return sign * mantissa * 2.0 ** (exponent - 16383 - 63)


def _walk_chunks(fh: BinaryIO, end: int):
    """Yield (chunk_id, size, payload_offset) across the FORM body.

    The caller may read any part of the payload; the next iteration seeks from
    the recorded offset, never from wherever the caller left the file pointer.
    """
    while fh.tell() + 8 <= end:
        chunk_id = fh.read(4)
        size = struct.unpack(">I", fh.read(4))[0]
        if len(chunk_id) < 4:
            return
        payload = fh.tell()
        yield chunk_id, size, payload
        fh.seek(payload + size + (size & 1))   # chunks are even-aligned


def probe(path: Path | str) -> AiffInfo:
    """Read only the headers: container type, COMM chunk, and SSND location."""
    target = Path(path)
    try:
        total = target.stat().st_size
        fh = target.open("rb")
    except OSError as exc:
        raise AudioError(f"cannot read {target}: {exc}") from exc

    with fh:
        head = fh.read(12)
        if len(head) < 12:
            raise AudioError(f"{target.name} is truncated before the FORM header ended")
        if head[:4] != b"FORM":
            raise AudioError(f"{target.name} is not an AIFF file (no FORM header)")
        if head[8:12] not in (b"AIFF", b"AIFC"):
            raise AudioError(f"{target.name} is not an AIFF file (type {head[8:12]!r})")

        body_end = min(8 + struct.unpack(">I", head[4:8])[0], total)
        comm: tuple | None = None
        ssnd: tuple[int, int] | None = None
        compression = "NONE"

        for chunk_id, size, payload in _walk_chunks(fh, body_end):
            if chunk_id == b"COMM":
                raw = fh.read(min(size, 40))
                if len(raw) < 18:
                    raise AudioError("truncated COMM chunk")
                channels, frames, bits = struct.unpack(">hIh", raw[0:8])
                rate = _extended_to_float(raw[8:18])
                if len(raw) >= 22:
                    compression = raw[18:22].decode("latin-1")
                comm = (channels, frames, bits, rate)
            elif chunk_id == b"SSND":
                head_raw = fh.read(min(size, 8))
                if len(head_raw) < 8:
                    raise AudioError("truncated SSND chunk")
                offset = struct.unpack(">I", head_raw[0:4])[0]
                ssnd = (payload + 8 + offset, size - 8 - offset)
            elif chunk_id in (b"APPL", b"NAME", b"AUTH", b"ANNO"):
                continue
            if comm and ssnd:
                break

    if comm is None:
        raise AudioError(f"{target.name} has no COMM chunk")
    if ssnd is None:
        raise AudioError(f"{target.name} has no SSND chunk (truncated?)")
    if compression not in SUPPORTED:
        raise AudioError(
            f"{target.name} uses AIFC compression {compression!r}; "
            "only uncompressed AIFF can be converted"
        )

    channels, frames, bits, rate = comm
    if channels <= 0 or bits <= 0:
        raise AudioError(f"{target.name} declares {channels} channels at {bits} bits")
    if bits not in (8, 16, 24, 32):
        raise AudioError(f"{target.name} uses {bits}-bit samples, which is unsupported")

    data_offset, data_bytes = ssnd
    if data_offset < 0 or data_offset > total:
        raise AudioError(f"{target.name} points past the end of the file")
    # A short SSND must not turn into a read past EOF: trust the smaller of the
    # declared frame count and the bytes that are actually there.
    wanted = frames * channels * (bits // 8)
    available = max(0, min(data_bytes, total - data_offset))
    data_bytes = min(wanted, available)
    frames = data_bytes // (channels * (bits // 8))

    return AiffInfo(
        channels=channels,
        sample_rate=int(round(rate)),
        bits=bits,
        frames=frames,
        data_offset=data_offset,
        data_bytes=data_bytes,
        compression=compression,
    )


def wav_header(info: AiffInfo) -> bytes:
    """A 44-byte canonical PCM WAV header around ``info``'s frame count."""
    data_size = info.frames * info.block_align
    byte_rate = info.sample_rate * info.block_align
    return (
        b"RIFF"
        + struct.pack("<I", 36 + data_size)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<IHHIIHH", 16, 1, info.channels, info.sample_rate, byte_rate, info.block_align, 16)
        + b"data"
        + struct.pack("<I", data_size)
    )


def _convert_block(block: bytes, info: AiffInfo) -> bytes:
    """Turn one run of source samples into 16-bit little-endian PCM."""
    if info.compression in LITTLE_ENDIAN and info.bits == 16:
        return block                       # sowt: already little-endian
    if info.bits == 16:
        swapped = bytearray(block)
        swapped[0::2], swapped[1::2] = block[1::2], block[0::2]
        return bytes(swapped)
    if info.bits == 8:
        # AIFF is signed 8-bit, WAV is unsigned: expand to 16-bit signed, which
        # keeps the level right instead of just flipping the midpoint.
        return b"".join(
            struct.pack("<h", ((b - 256) if b > 127 else b) << 8) for b in block
        )
    if info.bits == 24:
        out = bytearray(len(block) // 3 * 2)
        for i in range(0, len(block) - 2, 3):
            # Keep the high two bytes of the big-endian sample, then swap them.
            out[i // 3 * 2] = block[i + 1]
            out[i // 3 * 2 + 1] = block[i]
        return bytes(out)
    # 32-bit: float samples are LE in AIFC fl32, integers are BE.
    if info.compression in LITTLE_ENDIAN:
        values = struct.unpack("<%df" % (len(block) // 4), block)
        return b"".join(
            struct.pack("<h", max(-32768, min(32767, int(round(v * 32767))))) for v in values
        )
    out = bytearray(len(block) // 4 * 2)
    for i in range(0, len(block) - 3, 4):
        out[i // 4 * 2] = block[i + 1]
        out[i // 4 * 2 + 1] = block[i]
    return bytes(out)


def write_wav(source: Path | str, dest: Path | BinaryIO) -> AiffInfo:
    """Stream ``source`` into ``dest`` as 16-bit PCM WAV.

    Streaming keeps a 32 MB tape track from becoming 32 MB of Python bytes plus
    a second copy, and it means the proxy can start answering while it works.
    """
    info = probe(source)
    raw = (info.bits // 8) * info.channels
    block_samples = max(raw, (BLOCK // raw) * raw)

    def emit(sink: BinaryIO) -> None:
        sink.write(wav_header(info))
        remaining = info.data_bytes
        with Path(source).open("rb") as fh:
            fh.seek(info.data_offset)
            while remaining > 0:
                chunk = fh.read(min(block_samples, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                usable = len(chunk) - (len(chunk) % raw)
                if usable:
                    sink.write(_convert_block(chunk[:usable], info))

    if hasattr(dest, "write"):
        emit(dest)                                     # type: ignore[arg-type]
    else:
        out = Path(str(dest))
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("wb") as fh:
            emit(fh)
    return info


def to_wav_bytes(source: Path | str) -> bytes:
    """Whole-file convenience wrapper, used by tests and small clips."""
    import io

    buf = io.BytesIO()
    write_wav(source, buf)
    return buf.getvalue()
