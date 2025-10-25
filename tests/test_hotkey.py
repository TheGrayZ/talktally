import sys
from types import SimpleNamespace

import pytest

try:
    import tkinter as tk  # noqa: F401
    TK_AVAILABLE = True
except Exception:  # pragma: no cover - environment without Tk
    TK_AVAILABLE = False


pytestmark = pytest.mark.skipif(not TK_AVAILABLE, reason="Tkinter not available in test environment")


def _install_fake_pynput(monkeypatch, capture_container):
    """Install a fake 'pynput.keyboard.GlobalHotKeys' that records mapping and allows triggering."""
    class FakeGlobalHotKeys:
        last_instance = None

        def __init__(self, mapping):
            self.mapping = mapping
            FakeGlobalHotKeys.last_instance = self

        def start(self):  # pragma: no cover - no behavior needed
            pass

        def stop(self):  # pragma: no cover - no behavior needed
            pass

    fake_keyboard = SimpleNamespace(GlobalHotKeys=FakeGlobalHotKeys)
    fake_pynput = SimpleNamespace(keyboard=fake_keyboard)
    monkeypatch.setitem(sys.modules, "pynput", fake_pynput)
    capture_container["FakeGlobalHotKeys"] = FakeGlobalHotKeys


def test_format_pynput_hotkey(monkeypatch):
    from talktally.gui import TalkTallyApp

    try:
        app = TalkTallyApp()
    except Exception as e:  # e.g., TclError in headless env
        pytest.skip(f"Cannot initialize Tk root: {e}")

    try:
        assert app._format_pynput_hotkey("cmd+shift+r") == "<cmd>+<shift>+r"
        assert app._format_pynput_hotkey("Ctrl + Alt + S") == "<ctrl>+<alt>+s"
        assert app._format_pynput_hotkey("option+X") == "<alt>+x"
        assert app._format_pynput_hotkey("meta+1") == "<cmd>+1"
        assert app._format_pynput_hotkey(" shift + a ") == "<shift>+a"
    finally:
        app._on_close()


def test_hotkey_listener_dispatches_via_after(monkeypatch):
    from talktally.gui import TalkTallyApp

    # Force code path to use pynput (so we don't require Accessibility for Quartz)
    monkeypatch.setenv("TALKTALLY_FORCE_PYNPUT", "1")

    capture = {}
    _install_fake_pynput(monkeypatch, capture)

    try:
        app = TalkTallyApp()
    except Exception as e:
        pytest.skip(f"Cannot initialize Tk root: {e}")

    # Hide window during test
    app.withdraw()

    # Record calls to after without scheduling Tk timers
    calls = []

    def fake_after(delay_ms, callback, *args, **kwargs):
        calls.append((delay_ms, callback, args, kwargs))
        return "after-id"

    monkeypatch.setattr(app, "after", fake_after, raising=False)

    # Configure and start listener
    app.hotkey_var.set("cmd+shift+r")
    app._start_hotkey_listener()

    FakeGlobalHotKeys = capture["FakeGlobalHotKeys"]
    ghk = FakeGlobalHotKeys.last_instance
    assert ghk is not None, "GlobalHotKeys was not constructed"

    # Trigger the registered hotkey callback manually
    assert len(ghk.mapping) == 1
    cb = next(iter(ghk.mapping.values()))
    cb()  # should call app.after(0, app._toggle)

    assert calls, "Expected Tk.after to be invoked from hotkey callback"
    delay_ms, callback, _a, _k = calls[-1]
    assert delay_ms == 0
    assert callback == app._toggle

    # Cleanup
    app._stop_hotkey_listener()
    app._on_close()
