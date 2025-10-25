from __future__ import annotations

import json
import os
import time
from pathlib import Path

from talktally.recording_transcriber import (
    list_recordings,
    transcribe_recording,
    RecordingTranscriptionResult,
)


def test_list_recordings_filters_and_sorts(tmp_path: Path) -> None:
    newer = tmp_path / "b.wav"
    older = tmp_path / "a.mp3"
    skipped = tmp_path / "notes.txt"

    older.write_bytes(b"1")
    # Ensure mod time ordering
    time.sleep(0.01)
    newer.write_bytes(b"2")
    skipped.write_text("ignore me")

    # Force known mtimes
    os.utime(older, (older.stat().st_atime, older.stat().st_mtime - 5))

    recordings = list_recordings(tmp_path)
    assert [p.name for p in recordings] == ["b.wav", "a.mp3"]


def test_transcribe_recording_writes_text(monkeypatch, tmp_path: Path) -> None:
    audio = tmp_path / "example.wav"
    audio.write_bytes(b"binary")

    captured = {}

    class DummyTranscriber:
        def __init__(self, *args, **kwargs):
            captured["init"] = {"args": args, "kwargs": kwargs}

        def transcribe(self, audio_path: Path) -> str:
            captured["path"] = Path(audio_path)
            return "hello world"

    monkeypatch.setattr(
        "talktally.recording_transcriber.LocalTranscriber",
        lambda *args, **kwargs: DummyTranscriber(*args, **kwargs),
    )

    result = transcribe_recording(
        audio,
        cmd="dummy",
        model="tiny",
        extra_args="--fast",
        debug=lambda msg: None,
    )

    assert isinstance(result, RecordingTranscriptionResult)
    assert result.source == audio
    assert result.transcript == "hello world"
    assert result.output_path is not None
    assert result.output_path.read_text() == "hello world"
    assert captured["path"] == audio
    assert captured["init"]["kwargs"]["model"] == "tiny"
    assert result.model == "tiny"
    meta_path = result.output_path.with_suffix(result.output_path.suffix + ".meta.json")
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert meta["model"] == "tiny"
    assert meta["source"] == "example.wav"
