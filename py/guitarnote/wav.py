"""Reading and writing WAV files with the standard library.

The ``wave`` module only reads integer PCM, but a browser recording is most
naturally 32-bit float and many DAWs export float too, so this parses the RIFF
chunks directly. Supported: 8/16/24/32-bit integer PCM and 32/64-bit float,
including the WAVE_FORMAT_EXTENSIBLE header that multichannel and 24-bit files
often use.
"""
from __future__ import annotations

import struct
import sys
from array import array
from dataclasses import dataclass

PCM = 1
FLOAT = 3
EXTENSIBLE = 0xFFFE


class WavError(ValueError):
    """The bytes are not a WAV file this module can read."""


@dataclass
class Audio:
    samples: list[float]      #: the first channel, scaled to -1..1
    sample_rate: int
    channels: int             #: channels in the file; only the first is used
    encoding: str             #: e.g. "24-bit PCM", "32-bit float"

    @property
    def duration(self) -> float:
        return len(self.samples) / self.sample_rate


def _little_endian(values: array) -> array:
    if sys.byteorder == "big":
        values.byteswap()
    return values


def _decode(raw: bytes, tag: int, bits: int) -> tuple[list[float], str]:
    if tag == FLOAT and bits == 32:
        return list(_little_endian(array("f", raw))), "32-bit float"
    if tag == FLOAT and bits == 64:
        return list(_little_endian(array("d", raw))), "64-bit float"
    if tag != PCM:
        raise WavError(f"unsupported WAV format tag {tag}")
    if bits == 8:
        return [(b - 128) / 128.0 for b in raw], "8-bit PCM"
    if bits == 16:
        return [v / 32768.0 for v in _little_endian(array("h", raw))], "16-bit PCM"
    if bits == 24:
        # Widen each 3-byte sample to 4 bytes with a zero low byte, read as
        # 32-bit signed integers: far faster than int.from_bytes per sample.
        n = len(raw) // 3
        wide = bytearray(4 * n)
        wide[1::4], wide[2::4], wide[3::4] = raw[0:3 * n:3], raw[1:3 * n:3], raw[2:3 * n:3]
        return [v / 2147483648.0 for v in _little_endian(array("i", bytes(wide)))], "24-bit PCM"
    if bits == 32:
        return [v / 2147483648.0 for v in _little_endian(array("i", raw))], "32-bit PCM"
    raise WavError(f"unsupported PCM bit depth {bits}")


def read_wav(data: bytes) -> Audio:
    """Decode a WAV file held in memory; return its first channel."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise WavError("not a WAV file (no RIFF/WAVE header)")
    fmt = None
    raw = None
    pos = 12
    while pos + 8 <= len(data):
        chunk, size = struct.unpack("<4sI", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + size]
        if chunk == b"fmt ":
            tag, channels, rate, _, align, bits = struct.unpack("<HHIIHH", body[:16])
            if tag == EXTENSIBLE and len(body) >= 26:
                tag = struct.unpack("<H", body[24:26])[0]   # first bytes of the sub-format GUID
            fmt = (tag, channels, rate, align, bits)
        elif chunk == b"data":
            raw = body
        pos += 8 + size + (size & 1)                        # chunks are word-aligned
    if fmt is None or raw is None:
        raise WavError("WAV file has no fmt or data chunk")
    tag, channels, rate, align, bits = fmt
    if channels < 1 or rate < 1 or align < 1:
        raise WavError("WAV header is malformed")
    raw = raw[:len(raw) - len(raw) % align]                 # drop a trailing partial frame
    values, encoding = _decode(raw, tag, bits)
    return Audio(samples=values[0::channels], sample_rate=rate,
                 channels=channels, encoding=encoding)


def write_wav(samples: list[float], sample_rate: int, encoding: str = "float32") -> bytes:
    """Encode mono samples as a WAV file: ``"float32"`` or ``"pcm16"``."""
    if encoding == "float32":
        body = _little_endian(array("f", samples)).tobytes()
        tag, bits = FLOAT, 32
    elif encoding == "pcm16":
        clipped = (max(-32768, min(32767, round(s * 32768))) for s in samples)
        body = _little_endian(array("h", clipped)).tobytes()
        tag, bits = PCM, 16
    else:
        raise ValueError(f"unknown encoding {encoding!r}")
    align = bits // 8
    fmt = struct.pack("<HHIIHH", tag, 1, sample_rate, sample_rate * align, align, bits)
    return (b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(body)) + b"WAVE"
            + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"data" + struct.pack("<I", len(body)) + body)
