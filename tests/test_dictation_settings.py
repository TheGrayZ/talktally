import json
import queue
from pathlib import Path

import pytest
import numpy as np

from talktally.common.settings import load_settings, save_settings, Settings
from talktally.dictation import DictationAgent, _mac_keycode_from_token


def test_dictation_settings_defaults_and_roundtrip(tmp_path: Path, monkeypatch) -> None:
    settings_file = tmp_path / "settings.json"
    monkeypatch.setenv("TALKTALLY_SETTINGS_PATH", str(settings_file))

    s = load_settings()
    assert isinstance(s, Settings)
    # Defaults
    assert getattr(s, "dictation_enable", True) in (True, False)
    assert getattr(s, "dictation_hotkey", "right_option")
    assert getattr(s, "dictation_model", "tiny") == "tiny"
    assert getattr(s, "transcriber_model", "tiny") == "tiny"
    assert getattr(s, "dictation_append_space", False) is False

    # Change and save
    s.dictation_enable = False
    s.dictation_hotkey = "left_option"
    s.dictation_model = "base"
    s.transcriber_model = "small"
    s.dictation_append_space = True
    save_settings(s)

    s2 = load_settings()
    assert s2.dictation_enable is False
    assert s2.dictation_hotkey == "left_option"
    assert s2.dictation_model == "base"
    assert s2.transcriber_model == "small"
    assert s2.dictation_append_space is True

    # Verify JSON persisted
    data = json.loads(settings_file.read_text())
    assert data["dictation_hotkey"] == "left_option"
    assert data["dictation_model"] == "base"
    assert data["transcriber_model"] == "small"
    assert data["dictation_append_space"] is True
    assert "dictation_wispr_args" not in data


def test_dictation_agent_uses_dictation_model() -> None:
    settings = Settings()
    settings.dictation_model = "medium"
    settings.transcriber_model = "large"

    agent = DictationAgent(settings)

    assert agent._cfg.model == "medium"  # type: ignore[attr-defined]


def test_mic_hud_view_class_reuse() -> None:
    from types import SimpleNamespace

    from talktally.dictation import _MicHud

    stored_classes = {}
    call_counter = {"NSClassFromString": 0}

    class FakeColor:
        def set(self) -> None:
            return None

    def color_with_alpha(*_args):
        return FakeColor()

    def clear_color():
        return FakeColor()

    class FakePath:
        def fill(self) -> None:
            return None

    def bezier_path_with_oval(_rect):
        return FakePath()

    fake_appkit = SimpleNamespace(
        NSView=type("NSView", (), {}),
        NSColor=SimpleNamespace(
            colorWithCalibratedRed_green_blue_alpha_=color_with_alpha,
            clearColor=clear_color,
        ),
        NSBezierPath=SimpleNamespace(
            bezierPathWithOvalInRect_=bezier_path_with_oval,
        ),
        NSMakeRect=lambda *args: args,
        NSStatusWindowLevel=0,
    )

    def ns_class_from_string(name: str):
        call_counter["NSClassFromString"] += 1
        return stored_classes.get(name)

    fake_appkit.NSClassFromString = ns_class_from_string

    original_shared = _MicHud._shared_view_class
    _MicHud._shared_view_class = None
    try:
        hud1 = object.__new__(_MicHud)
        hud1._avail = True
        hud1._window = None
        hud1._view_class = None
        hud1._AppKit = fake_appkit
        first_class = hud1._resolve_view_class(fake_appkit)
        assert first_class is not None
        stored_classes[_MicHud._VIEW_CLASS_NAME] = first_class

        hud2 = object.__new__(_MicHud)
        hud2._avail = True
        hud2._window = None
        hud2._view_class = None
        hud2._AppKit = fake_appkit
        second_class = hud2._resolve_view_class(fake_appkit)

        assert second_class is first_class
        assert call_counter["NSClassFromString"] == 1
        assert _MicHud._shared_view_class is first_class
    finally:
        _MicHud._shared_view_class = original_shared


def test_mic_capturer_stop_with_full_queue() -> None:
    import threading
    from types import SimpleNamespace

    from talktally.dictation import _MicCapturer

    capturer = _MicCapturer()

    class FakeStream:
        def __init__(self) -> None:
            self.stopped = False
            self.closed = False

        def stop(self) -> None:
            self.stopped = True

        def close(self) -> None:
            self.closed = True

    capturer._stream = FakeStream()  # type: ignore[attr-defined]
    capturer._tmp_path = "temp.wav"  # type: ignore[attr-defined]
    capturer._f = SimpleNamespace(close=lambda: None)  # type: ignore[attr-defined]

    full_queue = queue.Queue(maxsize=1)
    full_queue.put(np.ones(1))
    capturer._q = full_queue  # type: ignore[attr-defined]

    class FakeWriter(threading.Thread):
        def __init__(self) -> None:
            super().__init__(daemon=True)
            self.join_called = False

        def join(self, timeout: float | None = None) -> None:  # noqa: ANN001
            self.join_called = True

    capturer._writer = FakeWriter()  # type: ignore[attr-defined]

    result = capturer.stop()

    assert result == "temp.wav"
    assert capturer._stream is None  # type: ignore[attr-defined]


def test_dictation_append_space_controls_output(monkeypatch, tmp_path) -> None:
    from types import SimpleNamespace
    import talktally.dictation as dictation_mod

    captured: list[str] = []

    def fake_paste(text: str) -> None:
        captured.append(text)

    monkeypatch.setattr(dictation_mod, "_paste_text", fake_paste)

    class StubTranscriber:
        def __init__(self, cmd, model, debug):  # noqa: ANN001
            self.cmd = cmd
            self.model = model
            self.debug = debug

        def transcribe(self, path: str) -> str:  # noqa: ANN001
            return "Hello"

    monkeypatch.setattr(dictation_mod, "LocalTranscriber", StubTranscriber)

    class ImmediateThread:
        def __init__(self, target, name=None, daemon=None):  # noqa: ANN001
            self._target = target

        def start(self) -> None:
            self._target()

        def join(self, timeout: float | None = None) -> None:  # noqa: ANN001
            return None

    monkeypatch.setattr(dictation_mod.threading, "Thread", ImmediateThread)

    class StubCapturer:
        def __init__(self, path: Path) -> None:
            self._path = str(path)

        def stop(self) -> str:
            return self._path

        def is_running(self) -> bool:
            return False

    overlay = SimpleNamespace(
        show_transcribing_near_cursor=lambda: None,
        hide=lambda: None,
        show_recording_near_cursor=lambda: None,
    )

    def run_case(append_space: bool) -> None:
        settings = Settings()
        settings.dictation_append_space = append_space
        agent = DictationAgent(settings)
        agent._capturer = StubCapturer(tmp_path / "sample.wav")  # type: ignore[attr-defined]
        agent._overlay = overlay  # type: ignore[attr-defined]
        agent._on_hold_end()

    run_case(True)
    run_case(False)

    assert captured == ["Hello ", "Hello"]


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
