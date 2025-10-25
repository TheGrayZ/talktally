from pathlib import Path


from talktally.common.fs import unique_path


def test_unique_path_generates_incremented_names(tmp_path: Path) -> None:
    base = tmp_path / "mic.wav"
    # First call should be the base if missing
    assert unique_path(base) == base

    # Create base file, then expect (1)
    base.write_bytes(b"")
    p1 = unique_path(base)
    assert p1.name == "mic (1).wav"

    # Create (1), expect (2)
    (tmp_path / "mic (1).wav").write_bytes(b"")
    p2 = unique_path(base)
    assert p2.name == "mic (2).wav"


def test_unique_path_no_suffix(tmp_path: Path) -> None:
    base = tmp_path / "mixed"
    base.write_bytes(b"")
    p1 = unique_path(base)
    assert p1.name == "mixed (1)"
