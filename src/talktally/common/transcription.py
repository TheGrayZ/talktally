"""Shared transcription helpers for TalkTally."""

from __future__ import annotations

import json
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
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


@dataclass(slots=True)
class LocalTranscriber:
    """Best-effort interface to local whisper/wispr style CLIs."""

    cmd: str | Sequence[str] = "whisper"
    extra_args: str | Sequence[str] = ""
    debug: Callable[[str], None] = _default_debug

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
        self.debug(
            "transcriber command = "
            + " ".join(self._cmd)
            + (f" extra={' '.join(self._extra)}" if self._extra else "")
        )

    def transcribe(self, audio_path: str | Path) -> str:
        """Return normalized single-line transcript text."""
        src = Path(audio_path)
        if not src.exists():
            raise FileNotFoundError(f"Audio file '{src}' not found.")

        cmd_name = Path(self._cmd[0]).name.lower()
        if cmd_name == "whisper":
            return self._transcribe_whisper(src)
        return self._transcribe_stdout_tool(src)

    # ---- whisper CLI ----
    def _transcribe_whisper(self, audio_path: Path) -> str:
        tmpdir = Path(tempfile.mkdtemp(prefix="talktally_whisper_"))
        txt_path = tmpdir / (audio_path.stem + ".txt")
        json_path = tmpdir / (audio_path.stem + ".json")
        self.debug(f"whisper tmpdir={tmpdir} expect_txt={txt_path.name}")
        try:
            cmd = list(self._cmd) + [str(audio_path)]
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
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
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
            err = proc.stderr.decode("utf-8", errors="ignore").strip()
            out = proc.stdout.decode("utf-8", errors="ignore").strip()
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
                    text = " ".join(seg.get("text", "") for seg in segments if seg).strip()
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
    def _transcribe_stdout_tool(self, audio_path: Path) -> str:
        try:
            cmd = list(self._cmd) + [str(audio_path)]
            if self._extra:
                cmd.extend(self._extra)
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except FileNotFoundError as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Transcriber command '{' '.join(self._cmd)}' not found. "
                "Set Settings.dictation_wispr_cmd."
            ) from exc
        out = proc.stdout.decode("utf-8", errors="ignore").strip()
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="ignore").strip()
            self.debug(f"stdout-tool stderr: {err}")
            raise RuntimeError(f"Transcriber failed ({proc.returncode}): {err or out}")
        return out.replace("\r", " ").replace("\n", " ").strip()

