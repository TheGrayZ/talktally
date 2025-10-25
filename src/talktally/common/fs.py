"""Filesystem helpers for TalkTally.

Utilities for generating non-clobbering file paths, e.g. mic.wav -> mic (1).wav
"""

from __future__ import annotations

from pathlib import Path
import time


def unique_path(path: str | Path) -> Path:
    """Return a unique path by appending " (n)" before the suffix if needed.

    Examples:
    - "/tmp/mic.wav" -> if exists, returns "/tmp/mic (1).wav", then (2), etc.
    - "/tmp/mixed" (no suffix) -> "/tmp/mixed (1)"
    """
    p = Path(path)
    if not p.exists():
        return p

    stem = p.stem
    suffix = p.suffix  # includes the leading dot, or empty if none
    parent = p.parent

    n = 1
    while True:
        candidate = parent / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def prefixed_with_end_timestamp(path: str | Path, end_ts: float) -> Path:
    """Return a new unique Path with an end timestamp prefix.

    Format: YYYY-MM-DD-HH-MM-SS_originalname.ext
    The resulting path will be made unique if it already exists.
    """
    p = Path(path)
    ts_str = time.strftime("%Y-%m-%d-%H-%M-%S", time.localtime(end_ts))
    target = p.with_name(f"{ts_str}_{p.name}")
    return unique_path(target)
