"""Utilities for transcribing recorded files stored on disk."""

from __future__ import annotations

from dataclasses import dataclass
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


def model_filename_token(model: str) -> str:
    """Return a filesystem-friendly token for embedding `model` into filenames."""
    token_parts: list[str] = []
    previous_sep = False
    for ch in (model or "").strip():
        if ch.isalnum() or ch in {"-", "."}:
            token_parts.append(ch.lower())
            previous_sep = False
        else:
            if not previous_sep:
                token_parts.append("_")
                previous_sep = True
    token = "".join(token_parts).strip("_")
    return token or "model"


def _next_available_path(base: Path) -> Path:
    """Return `base` or a suffixed variant `base (n)` if the path already exists."""
    if not base.exists():
        return base
    stem = base.stem
    suffix = base.suffix
    counter = 2
    while True:
        candidate = base.with_name(f"{stem} ({counter}){suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


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
        if model:
            token = model_filename_token(model)
            output = audio_path.with_name(f"{audio_path.stem}__{token}.txt")
        else:
            output = audio_path.with_suffix(".txt")
        if output.exists():
            if not overwrite:
                raise FileExistsError(str(output))
            output = _next_available_path(output)
        output.write_text(text, encoding="utf-8")
    return RecordingTranscriptionResult(
        source=audio_path,
        transcript=text,
        output_path=output,
        model=model,
    )
