from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


FileFormat = Literal["wav", "mp3", "flac"]


@dataclass(frozen=True)
class WavSettings:
    sample_rate: int = 48_000
    bit_depth: int = 16  # 16 or 24


@dataclass(frozen=True)
class Mp3Settings:
    bitrate_kbps: int = 192  # 96, 128, 160, 192, 256, 320


@dataclass(frozen=True)
class FlacSettings:
    sample_rate: int = 48_000
    bit_depth: int = 16  # 16 or 24
    compression_level: int = 5  # 0..8


def format_default_extension(fmt: FileFormat) -> str:
    return {"wav": ".wav", "mp3": ".mp3", "flac": ".flac"}[fmt]


def replace_extension(filename: str, new_ext: str) -> str:
    # Assumes new_ext includes dot
    import os

    root, _old = os.path.splitext(filename)
    return root + new_ext


def wav_bytes_per_minute(channels: int, sample_rate: int, bit_depth: int) -> int:
    bytes_per_sec = sample_rate * channels * (bit_depth // 8)
    return bytes_per_sec * 60


def mp3_bytes_per_minute(bitrate_kbps: int) -> int:
    # CBR kbps -> bytes/min; independent of channel count
    return int((bitrate_kbps * 1000 // 8) * 60)


def flac_bytes_per_minute(
    channels: int, sample_rate: int, bit_depth: int, level: int
) -> int:
    # Rough estimate using heuristic compression ratio by level
    level = max(0, min(8, level))
    ratios = {
        0: 0.70,
        1: 0.68,
        2: 0.66,
        3: 0.64,
        4: 0.62,
        5: 0.60,
        6: 0.58,
        7: 0.57,
        8: 0.56,
    }
    uncompressed = wav_bytes_per_minute(channels, sample_rate, bit_depth)
    return int(uncompressed * ratios.get(level, 0.60))


def human_readable_bytes(n: int) -> str:
    MiB = 1024 * 1024
    KiB = 1024
    if n >= MiB:
        return f"{n / MiB:.1f} MiB"
    if n >= KiB:
        return f"{n / KiB:.0f} KiB"
    return f"{n} B"
