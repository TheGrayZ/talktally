import json
from pathlib import Path

from talktally.common.settings import load_settings, save_settings, Settings


def test_settings_load_defaults_and_roundtrip(tmp_path: Path, monkeypatch) -> None:
    # Direct settings file to temp location
    settings_file = tmp_path / "settings.json"
    monkeypatch.setenv("TALKTALLY_SETTINGS_PATH", str(settings_file))

    s = load_settings()
    # Defaults
    assert isinstance(s, Settings)
    assert s.mic_filename == "mic.wav"

    # Modify and save
    s.device_name = "MyDevice"
    s.output_dir = str(tmp_path)
    s.output_mic = False
    save_settings(s)

    # Reload and verify persistence
    s2 = load_settings()
    assert s2.device_name == "MyDevice"
    assert s2.output_dir == str(tmp_path)
    assert s2.output_mic is False

    # Check file contents are valid JSON
    data = json.loads(settings_file.read_text())
    assert data["device_name"] == "MyDevice"


def test_unknown_keys_are_ignored(tmp_path: Path, monkeypatch) -> None:
    settings_file = tmp_path / "settings.json"
    monkeypatch.setenv("TALKTALLY_SETTINGS_PATH", str(settings_file))

    # Write a file with extra keys and wrong-typed values
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(
        json.dumps(
            {
                "device_name": "Dev",
                "output_mic": True,
                "unknown_key": 123,
                "mic_filename": 42,  # wrong type; should fallback to default
            }
        )
    )

    s = load_settings()
    assert s.device_name == "Dev"
    assert s.output_mic is True
    # Falls back to default for wrong type
    assert s.mic_filename == "mic.wav"
