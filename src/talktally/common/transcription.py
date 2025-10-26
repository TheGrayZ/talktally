"""Shared transcription helpers for TalkTally."""

from __future__ import annotations

import json
import shlex
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence


def _default_debug(msg: str) -> None:
    pass


def _as_command_parts(cmd: str | Sequence[str]) -> list[str]:
    if isinstance(cmd, str):
        parts = shlex.split(cmd)
    else:
        parts = list(cmd)
    return [p for p in parts if p]


def _contains_model_flag(args: Sequence[str]) -> bool:
    for item in args:
        token = item.strip()
        if not token:
            continue
        if token in {"--model", "-m"}:
            return True
        if token.startswith("--model="):
            return True
    return False


@dataclass(slots=True)
class LocalTranscriber:
    """Best-effort interface to local whisper/wispr style CLIs."""

    cmd: str | Sequence[str] = "whisper"
    extra_args: str | Sequence[str] = ""
    model: str | None = None
    debug: Callable[[str], None] = _default_debug
    _cmd: list[str] = field(init=False, repr=False)
    _extra: list[str] = field(init=False, repr=False)
    _model: str | None = field(init=False, repr=False)

    def __post_init__(self) -> None:
        import shutil

        parts = _as_command_parts(self.cmd)
        if not parts:
            parts = ["whisper"]
        if shutil.which(parts[0]) is None:
            fallback = shutil.which("whisper")
            if fallback is not None:
                parts = [fallback]
        self._cmd = parts
        if isinstance(self.extra_args, str):
            self._extra = shlex.split(self.extra_args)
        else:
            self._extra = list(self.extra_args)
        model = (self.model or "").strip()
        self._model = model or None
        message = "transcriber command = " + " ".join(self._cmd)
        if self._model:
            message += f" model={self._model}"
        if self._extra:
            message += f" extra={' '.join(self._extra)}"
        self.debug(message)

    def transcribe(self, audio_path: str | Path, *, cancel_flag: Callable[[], bool] | None = None) -> str:
        """Return normalized single-line transcript text.
        
        Args:
            audio_path: Path to the audio file to transcribe
            cancel_flag: Optional callable that returns True if cancellation is requested
        """
        src = Path(audio_path)
        if not src.exists():
            raise FileNotFoundError(f"Audio file '{src}' not found.")

        cmd_name = Path(self._cmd[0]).name.lower()
        if cmd_name == "whisper":
            return self._transcribe_whisper(src, cancel_flag=cancel_flag)
        return self._transcribe_stdout_tool(src, cancel_flag=cancel_flag)

    # ---- whisper CLI ----
    def _transcribe_whisper(self, audio_path: Path, *, cancel_flag: Callable[[], bool] | None = None) -> str:
        tmpdir = Path(tempfile.mkdtemp(prefix="talktally_whisper_"))
        txt_path = tmpdir / (audio_path.stem + ".txt")
        json_path = tmpdir / (audio_path.stem + ".json")
        self.debug(f"whisper tmpdir={tmpdir} expect_txt={txt_path.name}")
        try:
            cmd = list(self._cmd) + [str(audio_path)]
            if (
                self._model
                and not _contains_model_flag(cmd)
                and not _contains_model_flag(self._extra)
            ):
                cmd.extend(["--model", self._model])
            if self._extra:
                cmd.extend(self._extra)
            cmd.extend(
                [
                    "--output_dir",
                    str(tmpdir),
                    "--output_format",
                    "txt",
                    "--verbose",
                    "False",
                ]
            )
            # Use Popen for cancellable subprocess
            import time
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            
            # Poll for completion or cancellation
            while proc.poll() is None:
                if cancel_flag and cancel_flag():
                    self.debug("whisper cancellation requested, terminating process")
                    proc.terminate()
                    try:
                        proc.wait(timeout=2.0)  # Give it 2 seconds to terminate gracefully
                    except subprocess.TimeoutExpired:
                        proc.kill()  # Force kill if it doesn't terminate
                        proc.wait()
                    raise InterruptedError("Transcription cancelled by user")
                time.sleep(0.1)  # Check every 100ms
            
            # Get the final result
            stdout, stderr = proc.communicate()
        except FileNotFoundError as e:  # noqa: BLE001
            raise RuntimeError(
                f"Transcriber command '{' '.join(self._cmd)}' not found. "
                "Set Settings.dictation_wispr_cmd."
            ) from e
        finally:
            # whisper lazily writes the JSON only when requested; ensure we create copy before cleaning.
            pass

        self.debug(f"whisper rc={proc.returncode}")
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="ignore").strip()
            out = stdout.decode("utf-8", errors="ignore").strip()
            self.debug(f"whisper stderr: {err}")
            raise RuntimeError(f"whisper failed ({proc.returncode}): {err or out}")

        try:
            files = [p.name for p in tmpdir.glob("*")]
            self.debug(f"whisper outputs: {files}")
            text = ""
            if txt_path.exists():
                text = txt_path.read_text(encoding="utf-8", errors="ignore").strip()
            if not text and json_path.exists():
                try:
                    payload = json.loads(
                        json_path.read_text(encoding="utf-8", errors="ignore")
                    )
                    segments: Iterable[dict[str, str]] = payload.get("segments", [])
                    text = " ".join(
                        seg.get("text", "") for seg in segments if seg
                    ).strip()
                    self.debug(
                        "whisper json fallback used"
                        if text
                        else "whisper json fallback empty"
                    )
                except Exception as exc:  # noqa: BLE001
                    self.debug(f"whisper json parse failed: {exc}")
            self.debug(
                f"whisper read {len(text)} chars from "
                f"{txt_path.name if txt_path.exists() else 'N/A'}"
            )
        finally:
            for p in tmpdir.glob("*"):
                try:
                    p.unlink()
                except Exception:
                    pass
            try:
                tmpdir.rmdir()
            except Exception:
                pass
        return text.replace("\r", " ").replace("\n", " ").strip()

    # ---- stdout tools ----
    def _transcribe_stdout_tool(self, audio_path: Path, *, cancel_flag: Callable[[], bool] | None = None) -> str:
        try:
            cmd = list(self._cmd) + [str(audio_path)]
            if (
                self._model
                and not _contains_model_flag(cmd)
                and not _contains_model_flag(self._extra)
            ):
                cmd.extend(["--model", self._model])
            if self._extra:
                cmd.extend(self._extra)
            # Use Popen for cancellable subprocess
            import time
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            
            # Poll for completion or cancellation
            while proc.poll() is None:
                if cancel_flag and cancel_flag():
                    self.debug("stdout-tool cancellation requested, terminating process")
                    proc.terminate()
                    try:
                        proc.wait(timeout=2.0)  # Give it 2 seconds to terminate gracefully
                    except subprocess.TimeoutExpired:
                        proc.kill()  # Force kill if it doesn't terminate
                        proc.wait()
                    raise InterruptedError("Transcription cancelled by user")
                time.sleep(0.1)  # Check every 100ms
            
            # Get the final result
            stdout, stderr = proc.communicate()
        except FileNotFoundError as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Transcriber command '{' '.join(self._cmd)}' not found. "
                "Set Settings.dictation_wispr_cmd."
            ) from exc
        out = stdout.decode("utf-8", errors="ignore").strip()
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="ignore").strip()
            self.debug(f"stdout-tool stderr: {err}")
            raise RuntimeError(f"Transcriber failed ({proc.returncode}): {err or out}")
        return out.replace("\r", " ").replace("\n", " ").strip()
