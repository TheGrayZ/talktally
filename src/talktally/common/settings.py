"""Settings persistence for TalkTally.

Stores and retrieves UI preferences so that they persist across app launches.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
import sys
from pathlib import Path
from typing import Any


APP_NAME = "TalkTally"


def _default_config_dir() -> Path:
    # Allow tests or callers to override location
    override = os.environ.get("TALKTALLY_SETTINGS_PATH")
    if override:
        p = Path(override).expanduser()
        # If the override looks like a file path, use it directly
        if p.suffix:
            return p
        # Else treat as directory and append filename
        return p / "settings.json"

    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    elif sys.platform.startswith("win"):
        base = (
            Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
            / APP_NAME
        )
    else:
        base = (
            Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
            / APP_NAME.lower()
        )
    return base / "settings.json"


@dataclass
class Settings:
    # Device and channel mapping
    device_name: str = ""
    mic_channels: str = "0"
    system_channels: str = "1,2"

    # Output filenames and directory
    output_dir: str = str(Path.cwd())
    mic_filename: str = "mic.wav"
    system_filename: str = "system.wav"
    mixed_filename: str = "mixed.wav"
    output_mic: bool = True
    output_system: bool = True
    output_mixed: bool = True

    # File format and encoding settings (apply to all outputs)
    file_format: str = "wav"  # wav | mp3 | flac
    wav_sample_rate: int = 48_000
    wav_bit_depth: int = 16  # 16 or 24
    mp3_bitrate_kbps: int = 192  # 96..320
    flac_sample_rate: int = 48_000
    flac_bit_depth: int = 16
    flac_level: int = 5  # 0..8

    # Hotkey and alerts (recorder)
    enable_hotkey: bool = False
    hotkey: str = "cmd+shift+r"
    play_sounds: bool = True

    # Dictation (push-to-talk) feature
    dictation_enable: bool = False
    # Default to right Option key on macOS; token is interpreted by platform-specific code
    dictation_hotkey: str = "right_option"
    # Command (or absolute path) to local transcriber; 'whisper' (OpenAI) by default
    dictation_wispr_cmd: str = "whisper"
    # Audio capture settings for dictation
    dictation_sample_rate: int = 16_000


def get_settings_path() -> Path:
    return _default_config_dir()


def load_settings() -> Settings:
    path = get_settings_path()
    try:
        if path.exists():
            raw = json.loads(path.read_text())
        else:
            raw = {}
    except Exception:
        raw = {}

    # Only keep known keys; fall back to defaults for missing/invalid entries
    defaults = asdict(Settings())
    data: dict[str, Any] = {}
    for k, v in defaults.items():
        if k in raw and type(raw[k]) is type(v):  # noqa: E721 - strict type match
            data[k] = raw[k]
        else:
            data[k] = v
    return Settings(**data)


def save_settings(s: Settings) -> None:
    path = get_settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(s), indent=2))
    except Exception:
        # Best-effort persistence; ignore write errors
        pass
