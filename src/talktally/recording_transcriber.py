"""Utilities for transcribing recorded files stored on disk."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from .common.transcription import LocalTranscriber


SUPPORTED_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".aiff", ".aif"}


def list_recordings(directory: Path) -> list[Path]:
    """Return supported audio files in the directory, newest first."""
    if not directory.exists():
        return []
    recordings = [
        p
        for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    return sorted(recordings, key=lambda p: p.stat().st_mtime, reverse=True)


@dataclass(slots=True)
class RecordingTranscriptionResult:
    source: Path
    transcript: str
    output_path: Path | None
    model: str | None = None


def transcribe_recording(
    audio_path: Path,
    *,
    cmd: str,
    model: str | None = None,
    extra_args: str = "",
    debug: Callable[[str], None] | None = None,
    write_text: bool = True,
    overwrite: bool = True,
) -> RecordingTranscriptionResult:
    """Transcribe `audio_path` and optionally persist a sibling .txt file."""
    if debug is None:
        def _noop(_msg: str) -> None:
            return None

        debug = _noop
    transcriber = LocalTranscriber(
        cmd=cmd,
        extra_args=extra_args,
        model=model,
        debug=debug,
    )
    text = transcriber.transcribe(audio_path)
    output: Path | None = None
    if write_text:
        output = audio_path.with_suffix(".txt")
        if not overwrite and output.exists():
            raise FileExistsError(str(output))
        output.write_text(text, encoding="utf-8")
        try:
            metadata = {
                "model": model,
                "source": audio_path.name,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            meta_path = output.with_suffix(output.suffix + ".meta.json")
            meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        except Exception:
            pass
    return RecordingTranscriptionResult(
        source=audio_path,
        transcript=text,
        output_path=output,
        model=model,
    )
