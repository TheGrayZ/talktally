from pathlib import Path
import re
import time

from talktally.common.fs import prefixed_with_end_timestamp


def test_prefixed_with_end_timestamp_formats_and_preserves_name(tmp_path: Path) -> None:
    base = tmp_path / "mic.wav"
    base.write_bytes(b"")

    # Simulate an end timestamp
    end_ts = 1730000000.0  # fixed timestamp for deterministic format
    target = prefixed_with_end_timestamp(base, end_ts)

    # Should be in same directory and preserve original name after underscore
    assert target.parent == base.parent
    assert target.name.endswith("_mic.wav")

    # Check format YYYY-MM-DD-HH-MM-SS
    prefix = target.name.split("_", 1)[0]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}", prefix)


def test_prefixed_with_end_timestamp_uniqueness(tmp_path: Path) -> None:
    base = tmp_path / "system.wav"
    base.write_bytes(b"")

    end_ts = time.time()
    first = prefixed_with_end_timestamp(base, end_ts)

    # Create the first target to force a collision
    first.write_bytes(b"")

    second = prefixed_with_end_timestamp(base, end_ts)
    assert second != first
    assert second.name.startswith(first.name.rsplit(".", 1)[0].split(" (", 1)[0])
