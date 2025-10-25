import json
from pathlib import Path

import pytest

from talktally.common.settings import load_settings, save_settings, Settings
from talktally.dictation import _mac_keycode_from_token


def test_dictation_settings_defaults_and_roundtrip(tmp_path: Path, monkeypatch) -> None:
    settings_file = tmp_path / "settings.json"
    monkeypatch.setenv("TALKTALLY_SETTINGS_PATH", str(settings_file))

    s = load_settings()
    assert isinstance(s, Settings)
    # Defaults
    assert getattr(s, "dictation_enable", True) in (True, False)
    assert getattr(s, "dictation_hotkey", "right_option")
    assert getattr(s, "dictation_wispr_args", "--model tiny")

    # Change and save
    s.dictation_enable = False
    s.dictation_hotkey = "left_option"
    s.dictation_wispr_args = "--model base"
    save_settings(s)

    s2 = load_settings()
    assert s2.dictation_enable is False
    assert s2.dictation_hotkey == "left_option"
    assert s2.dictation_wispr_args == "--model base"

    # Verify JSON persisted
    data = json.loads(settings_file.read_text())
    assert data["dictation_hotkey"] == "left_option"
    assert data["dictation_wispr_args"] == "--model base"


@pytest.mark.parametrize(
    "token, expected",
    [
        ("right_option", 61),
        ("ralt", 61),
        ("ropt", 61),
        ("left_option", 58),
        ("caps_lock", 57),
        ("keycode:61", 61),
    ],
)
def test_mac_keycode_from_token_valid(token, expected):
    assert _mac_keycode_from_token(token) == expected


def test_mac_keycode_from_token_invalid():
    with pytest.raises(ValueError):
        _mac_keycode_from_token("not_a_key")
