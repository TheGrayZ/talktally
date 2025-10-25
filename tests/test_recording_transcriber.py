from __future__ import annotations
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
        model="base",
        extra_args="--fast",
        debug=lambda msg: None,
    )

    assert isinstance(result, RecordingTranscriptionResult)
    assert result.source == audio
    assert result.transcript == "hello world"
    assert result.output_path is not None
    assert result.output_path.name == "example__base.txt"
    assert result.output_path.read_text() == "hello world"
    assert captured["path"] == audio
    assert captured["init"]["kwargs"]["model"] == "base"
    assert result.model == "base"


def test_transcribe_recording_retains_previous_models(monkeypatch, tmp_path: Path) -> None:
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"data")

    returns = {
        "tiny": "hello tiny",
        "base": "hello base",
        "small.en": "hello small",
    }

    class DummyTranscriber:
        def __init__(self, *, model=None, **_kwargs):
            self.model = model

        def transcribe(self, audio_path: Path) -> str:
            return returns[self.model]

    monkeypatch.setattr(
        "talktally.recording_transcriber.LocalTranscriber",
        lambda *args, **kwargs: DummyTranscriber(*args, **kwargs),
    )

    first = transcribe_recording(audio, cmd="cmd", model="tiny")
    second = transcribe_recording(audio, cmd="cmd", model="base")
    third = transcribe_recording(audio, cmd="cmd", model="small.en")
    fourth = transcribe_recording(audio, cmd="cmd", model="base")

    assert first.output_path and second.output_path and third.output_path and fourth.output_path
    assert len(
        {first.output_path, second.output_path, third.output_path, fourth.output_path}
    ) == 4
    assert first.output_path.name == "sample__tiny.txt"
    assert second.output_path.name == "sample__base.txt"
    assert third.output_path.name == "sample__small.en.txt"
    assert fourth.output_path.name == "sample__base (2).txt"
    assert first.output_path.read_text() == "hello tiny"
    assert second.output_path.read_text() == "hello base"
    assert third.output_path.read_text() == "hello small"
    assert fourth.output_path.read_text() == "hello base"
