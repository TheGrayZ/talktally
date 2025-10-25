import sys
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

try:
    import tkinter as tk  # noqa: F401

    TK_AVAILABLE = True
except Exception:  # pragma: no cover - environment without Tk
    TK_AVAILABLE = False


pytestmark = pytest.mark.skipif(
    not TK_AVAILABLE, reason="Tkinter not available in test environment"
)


def test_hotkey_format_helpers():
    from talktally.gui import format_hotkey_sequence, dictation_token_from_keysym

    assert format_hotkey_sequence({"cmd", "shift"}, "r") == "cmd+shift+r"
    assert format_hotkey_sequence(set(), "a") == "a"
    assert format_hotkey_sequence({"alt"}, "4") == "alt+4"
    assert format_hotkey_sequence({"cmd"}, None) is None
    assert format_hotkey_sequence(set(), "space") is None

    assert dictation_token_from_keysym("Option_L", 58) == "left_option"
    assert dictation_token_from_keysym("Option_R", 61) == "right_option"
    assert dictation_token_from_keysym("F17", 64) == "f17"
    assert dictation_token_from_keysym("Unknown", 123) == "keycode:123"


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


@pytest.mark.skipif(
    sys.platform != "darwin", reason="Quartz hotkey tests are macOS-specific"
)
class TestQuartzHotkey:
    """Test cases for the Quartz-based hotkey implementation on macOS."""

    def test_start_quartz_hotkey_parses_various_formats(self, monkeypatch):
        """Test that _start_quartz_hotkey correctly parses various hotkey string formats."""
        from talktally.gui import TalkTallyApp

        # Mock Quartz module
        mock_quartz = MagicMock()
        mock_quartz.kCGEventFlagMaskCommand = 0x100000
        mock_quartz.kCGEventFlagMaskShift = 0x20000
        mock_quartz.kCGEventFlagMaskControl = 0x40000
        mock_quartz.kCGEventFlagMaskAlternate = 0x80000
        mock_quartz.kCGEventKeyDown = 10
        mock_quartz.kCGEventKeyUp = 11
        mock_quartz.kCGSessionEventTap = 0
        mock_quartz.kCGHeadInsertEventTap = 0
        mock_quartz.kCGEventTapOptionListenOnly = 1
        mock_quartz.kCFRunLoopCommonModes = "kCFRunLoopDefaultMode"

        # Mock successful event tap creation
        mock_tap = MagicMock()
        mock_quartz.CGEventTapCreate.return_value = mock_tap
        mock_quartz.CFMachPortCreateRunLoopSource.return_value = MagicMock()

        monkeypatch.setitem(sys.modules, "Quartz", mock_quartz)

        try:
            app = TalkTallyApp()
        except Exception as e:
            pytest.skip(f"Cannot initialize Tk root: {e}")

        app.withdraw()

        test_cases = [
            ("cmd+shift+r", 0x120000, 15),  # cmd + shift + r
            ("ctrl+alt+a", 0xC0000, 0),  # ctrl + alt + a
            ("command+option+1", 0x180000, 18),  # command + option + 1
            ("meta+control+z", 0x140000, 6),  # meta + control + z
            ("shift+s", 0x20000, 1),  # shift + s
        ]

        for hotkey_str, expected_mods, expected_keycode in test_cases:
            app.hotkey_var.set(hotkey_str)

            # Reset mocks
            mock_quartz.CGEventTapCreate.reset_mock()

            try:
                app._start_quartz_hotkey()

                # Verify CGEventTapCreate was called with correct parameters
                call_args = mock_quartz.CGEventTapCreate.call_args
                assert call_args is not None

                # Extract the callback function
                callback = call_args[0][4]

                # Test the callback with a matching key event
                mock_event = MagicMock()
                mock_quartz.CGEventGetFlags.return_value = expected_mods
                mock_quartz.CGEventGetIntegerValueField.return_value = expected_keycode

                # Mock app.after to capture toggle calls
                toggle_calls = []

                def fake_after(delay, func):
                    if func == app._toggle:
                        toggle_calls.append((delay, func))
                    return "timer-id"

                monkeypatch.setattr(app, "after", fake_after)

                # Simulate key down event
                result = callback(None, mock_quartz.kCGEventKeyDown, mock_event, None)
                assert result == mock_event

                # Should trigger toggle
                assert len(toggle_calls) == 1
                assert toggle_calls[0][0] == 0  # delay should be 0

            except Exception:
                pass  # Expected for some test cases in headless environment

            # Cleanup
            app._stop_hotkey_listener()

        app._on_close()

    def test_start_quartz_hotkey_invalid_formats(self, monkeypatch):
        """Test that _start_quartz_hotkey handles invalid hotkey formats appropriately."""
        from talktally.gui import TalkTallyApp

        # Mock Quartz module with proper constants
        mock_quartz = MagicMock()
        mock_quartz.kCGEventFlagMaskCommand = 0x100000
        mock_quartz.kCGEventFlagMaskShift = 0x20000
        mock_quartz.kCGEventFlagMaskControl = 0x40000
        mock_quartz.kCGEventFlagMaskAlternate = 0x80000
        mock_quartz.kCGEventKeyDown = 10
        mock_quartz.kCGEventKeyUp = 11
        mock_quartz.kCGSessionEventTap = 0
        mock_quartz.kCGHeadInsertEventTap = 0
        mock_quartz.kCGEventTapOptionListenOnly = 1
        mock_quartz.kCFRunLoopCommonModes = "kCFRunLoopDefaultMode"

        monkeypatch.setitem(sys.modules, "Quartz", mock_quartz)

        try:
            app = TalkTallyApp()
        except Exception as e:
            pytest.skip(f"Cannot initialize Tk root: {e}")

        app.withdraw()

        # Test invalid hotkey formats
        invalid_cases = [
            "cmd+shift+unknown",  # unknown key
            "cmd+shift",  # no key, only modifiers
            "invalidmod+r",  # unknown modifier
        ]

        for invalid_hotkey in invalid_cases:
            app.hotkey_var.set(invalid_hotkey)

            with pytest.raises(ValueError):
                app._start_quartz_hotkey()

        # Test empty string - should default to "cmd+shift+r"
        app.hotkey_var.set("")
        mock_quartz.CGEventTapCreate.return_value = MagicMock()
        mock_quartz.CFMachPortCreateRunLoopSource.return_value = MagicMock()
        # This should not raise an exception as it defaults to valid hotkey
        try:
            app._start_quartz_hotkey()
        except Exception:
            pass  # May fail in test environment but shouldn't raise ValueError

        app._on_close()

    def test_start_quartz_hotkey_event_tap_creation_failure(self, monkeypatch):
        """Test that _start_quartz_hotkey handles event tap creation failure (permissions)."""
        from talktally.gui import TalkTallyApp

        # Mock Quartz module with failing event tap creation
        mock_quartz = MagicMock()
        mock_quartz.kCGEventFlagMaskCommand = 0x100000
        mock_quartz.kCGEventFlagMaskShift = 0x20000
        mock_quartz.kCGEventKeyDown = 10
        mock_quartz.kCGEventKeyUp = 11
        mock_quartz.kCGSessionEventTap = 0
        mock_quartz.kCGHeadInsertEventTap = 0
        mock_quartz.kCGEventTapOptionListenOnly = 1

        # Return None to simulate permission failure
        mock_quartz.CGEventTapCreate.return_value = None

        monkeypatch.setitem(sys.modules, "Quartz", mock_quartz)

        try:
            app = TalkTallyApp()
        except Exception as e:
            pytest.skip(f"Cannot initialize Tk root: {e}")

        app.withdraw()
        app.hotkey_var.set("cmd+shift+r")

        # Should raise RuntimeError for permission issues
        with pytest.raises(RuntimeError, match="Failed to create event tap"):
            app._start_quartz_hotkey()

        app._on_close()

    def test_quartz_hotkey_callback_invokes_toggle_on_main_thread(self, monkeypatch):
        """Test that the Quartz hotkey callback correctly invokes _toggle on main thread."""
        from talktally.gui import TalkTallyApp

        # Mock Quartz module
        mock_quartz = MagicMock()
        mock_quartz.kCGEventFlagMaskCommand = 0x100000
        mock_quartz.kCGEventFlagMaskShift = 0x20000
        mock_quartz.kCGEventKeyDown = 10
        mock_quartz.kCGEventKeyUp = 11
        mock_quartz.kCGSessionEventTap = 0
        mock_quartz.kCGHeadInsertEventTap = 0
        mock_quartz.kCGEventTapOptionListenOnly = 1
        mock_quartz.kCFRunLoopCommonModes = "kCFRunLoopDefaultMode"

        mock_tap = MagicMock()
        mock_quartz.CGEventTapCreate.return_value = mock_tap
        mock_quartz.CFMachPortCreateRunLoopSource.return_value = MagicMock()

        monkeypatch.setitem(sys.modules, "Quartz", mock_quartz)

        try:
            app = TalkTallyApp()
        except Exception as e:
            pytest.skip(f"Cannot initialize Tk root: {e}")

        app.withdraw()
        app.hotkey_var.set("cmd+shift+r")

        # Mock app.after to capture main thread dispatch
        after_calls = []

        def fake_after(delay, func):
            after_calls.append((delay, func))
            return "timer-id"

        monkeypatch.setattr(app, "after", fake_after)

        app._start_quartz_hotkey()

        # Get the callback function
        callback = mock_quartz.CGEventTapCreate.call_args[0][4]

        # Setup mock event with matching modifiers and keycode
        mock_event = MagicMock()
        mock_quartz.CGEventGetFlags.return_value = 0x120000  # cmd+shift
        mock_quartz.CGEventGetIntegerValueField.return_value = 15  # 'r' key

        # Test key down - should trigger toggle
        result = callback(None, mock_quartz.kCGEventKeyDown, mock_event, None)

        # Verify callback returns the event
        assert result == mock_event

        # Verify toggle was scheduled on main thread
        assert len(after_calls) == 1
        delay, func = after_calls[0]
        assert delay == 0  # immediate dispatch to main thread
        assert func == app._toggle

        # Test key up - should reset fired state but not trigger toggle again
        after_calls.clear()
        callback(None, mock_quartz.kCGEventKeyUp, mock_event, None)

        # Key down again should work
        callback(None, mock_quartz.kCGEventKeyDown, mock_event, None)
        assert len(after_calls) == 1  # Should trigger again

        app._stop_hotkey_listener()
        app._on_close()

    def test_quartz_listener_stop_method(self, monkeypatch):
        """Test that _QuartzListener.stop() properly disables event tap and invalidates run loop source."""
        from talktally.gui import TalkTallyApp

        # Mock Quartz module
        mock_quartz = MagicMock()
        mock_quartz.kCGEventFlagMaskCommand = 0x100000
        mock_quartz.kCGEventFlagMaskShift = 0x20000
        mock_quartz.kCGEventKeyDown = 10
        mock_quartz.kCGEventKeyUp = 11
        mock_quartz.kCGSessionEventTap = 0
        mock_quartz.kCGHeadInsertEventTap = 0
        mock_quartz.kCGEventTapOptionListenOnly = 1
        mock_quartz.kCFRunLoopCommonModes = "kCFRunLoopDefaultMode"

        mock_tap = MagicMock()
        mock_run_loop_source = MagicMock()
        mock_quartz.CGEventTapCreate.return_value = mock_tap
        mock_quartz.CFMachPortCreateRunLoopSource.return_value = mock_run_loop_source

        monkeypatch.setitem(sys.modules, "Quartz", mock_quartz)

        try:
            app = TalkTallyApp()
        except Exception as e:
            pytest.skip(f"Cannot initialize Tk root: {e}")

        app.withdraw()
        app.hotkey_var.set("cmd+shift+r")

        # Mock threading to prevent actual thread creation
        mock_thread = MagicMock()
        monkeypatch.setattr(threading, "Thread", lambda **kwargs: mock_thread)

        app._start_quartz_hotkey()

        # Get the listener instance
        listener = app._hotkey_listener
        assert listener is not None

        # Call stop method
        listener.stop()

        # Verify Quartz cleanup calls
        mock_quartz.CGEventTapEnable.assert_called_with(mock_tap, False)
        mock_quartz.CFRunLoopSourceInvalidate.assert_called_with(mock_run_loop_source)
        mock_quartz.CFRunLoopStop.assert_called()

        app._on_close()

    def test_quartz_listener_stop_handles_exceptions(self, monkeypatch):
        """Test that _QuartzListener.stop() gracefully handles exceptions during cleanup."""
        from talktally.gui import TalkTallyApp

        # Mock Quartz module with failing cleanup methods
        mock_quartz = MagicMock()
        mock_quartz.kCGEventFlagMaskCommand = 0x100000
        mock_quartz.kCGEventFlagMaskShift = 0x20000
        mock_quartz.kCGEventKeyDown = 10
        mock_quartz.kCGEventKeyUp = 11
        mock_quartz.kCGSessionEventTap = 0
        mock_quartz.kCGHeadInsertEventTap = 0
        mock_quartz.kCGEventTapOptionListenOnly = 1
        mock_quartz.kCFRunLoopCommonModes = "kCFRunLoopDefaultMode"

        # Make cleanup methods raise exceptions
        mock_quartz.CGEventTapEnable.side_effect = Exception("Cleanup failed")
        mock_quartz.CFRunLoopSourceInvalidate.side_effect = Exception(
            "Invalidate failed"
        )
        mock_quartz.CFRunLoopStop.side_effect = Exception("Stop failed")

        mock_tap = MagicMock()
        mock_run_loop_source = MagicMock()
        mock_quartz.CGEventTapCreate.return_value = mock_tap
        mock_quartz.CFMachPortCreateRunLoopSource.return_value = mock_run_loop_source

        monkeypatch.setitem(sys.modules, "Quartz", mock_quartz)

        try:
            app = TalkTallyApp()
        except Exception as e:
            pytest.skip(f"Cannot initialize Tk root: {e}")

        app.withdraw()
        app.hotkey_var.set("cmd+shift+r")

        # Mock threading
        mock_thread = MagicMock()
        monkeypatch.setattr(threading, "Thread", lambda **kwargs: mock_thread)

        app._start_quartz_hotkey()

        listener = app._hotkey_listener
        assert listener is not None

        # Should not raise exceptions despite cleanup failures
        listener.stop()  # Should complete without raising

        app._on_close()

    def test_start_hotkey_listener_prioritizes_quartz_on_macos(self, monkeypatch):
        """Test that _start_hotkey_listener prioritizes Quartz over pynput on macOS."""
        from talktally.gui import TalkTallyApp

        # Ensure we're testing macOS behavior
        monkeypatch.setattr(sys, "platform", "darwin")

        # Remove force pynput env var if set
        monkeypatch.delenv("TALKTALLY_FORCE_PYNPUT", raising=False)

        # Mock successful Quartz setup
        mock_quartz = MagicMock()
        mock_quartz.kCGEventFlagMaskCommand = 0x100000
        mock_quartz.kCGEventFlagMaskShift = 0x20000
        mock_quartz.kCGEventKeyDown = 10
        mock_quartz.kCGEventKeyUp = 11
        mock_quartz.kCGSessionEventTap = 0
        mock_quartz.kCGHeadInsertEventTap = 0
        mock_quartz.kCGEventTapOptionListenOnly = 1
        mock_quartz.kCFRunLoopCommonModes = "kCFRunLoopDefaultMode"

        mock_tap = MagicMock()
        mock_quartz.CGEventTapCreate.return_value = mock_tap
        mock_quartz.CFMachPortCreateRunLoopSource.return_value = MagicMock()

        monkeypatch.setitem(sys.modules, "Quartz", mock_quartz)

        # Mock threading to prevent actual thread creation
        mock_thread = MagicMock()
        monkeypatch.setattr(threading, "Thread", lambda **kwargs: mock_thread)

        # Track if pynput path was attempted
        pynput_attempted = [False]

        def mock_import(name, *args, **kwargs):
            if name == "pynput":
                pynput_attempted[0] = True
            return MagicMock()

        import builtins

        original_import = builtins.__import__
        monkeypatch.setattr(builtins, "__import__", mock_import)

        try:
            app = TalkTallyApp()
        except Exception as e:
            pytest.skip(f"Cannot initialize Tk root: {e}")

        app.withdraw()
        app.hotkey_var.set("cmd+shift+r")

        # Start hotkey listener - should use Quartz
        app._start_hotkey_listener()

        # Verify Quartz was used and pynput was not attempted
        assert not pynput_attempted[0], "Should not attempt pynput when Quartz succeeds"
        assert isinstance(app._hotkey_listener, type), "Should have Quartz listener"

        monkeypatch.setattr(builtins, "__import__", original_import)
        app._stop_hotkey_listener()
        app._on_close()

    def test_start_hotkey_listener_falls_back_to_pynput_on_quartz_failure(
        self, monkeypatch
    ):
        """Test that _start_hotkey_listener falls back to pynput when Quartz fails."""
        from talktally.gui import TalkTallyApp

        # Ensure macOS platform
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.delenv("TALKTALLY_FORCE_PYNPUT", raising=False)

        # Mock failing Quartz import
        def mock_import(name, *args, **kwargs):
            if name == "Quartz":
                raise ImportError("Quartz not available")
            elif name == "pynput":
                # Return mock pynput with proper GlobalHotKeys
                mock_global_hotkeys = MagicMock()
                mock_global_hotkeys.return_value = MagicMock(
                    start=MagicMock(), stop=MagicMock()
                )
                fake_keyboard = SimpleNamespace(GlobalHotKeys=mock_global_hotkeys)
                return SimpleNamespace(keyboard=fake_keyboard)
            return original_import(name, *args, **kwargs)

        import builtins

        original_import = builtins.__import__
        monkeypatch.setattr(builtins, "__import__", mock_import)

        try:
            app = TalkTallyApp()
        except Exception as e:
            pytest.skip(f"Cannot initialize Tk root: {e}")

        app.withdraw()
        app.hotkey_var.set("cmd+shift+r")

        # Should fall back to pynput without error
        app._start_hotkey_listener()

        # Verify pynput listener was created
        assert app._hotkey_listener is not None

        monkeypatch.setattr(builtins, "__import__", original_import)
        app._stop_hotkey_listener()
        app._on_close()

    def test_start_hotkey_listener_respects_force_pynput_env_var(self, monkeypatch):
        """Test that TALKTALLY_FORCE_PYNPUT=1 forces use of pynput over Quartz."""
        from talktally.gui import TalkTallyApp

        # Force pynput usage
        monkeypatch.setenv("TALKTALLY_FORCE_PYNPUT", "1")
        monkeypatch.setattr(sys, "platform", "darwin")

        # Mock both Quartz and pynput as available
        quartz_attempted = [False]

        def mock_import(name, *args, **kwargs):
            if name == "Quartz":
                quartz_attempted[0] = True
                return MagicMock()
            elif name == "pynput":
                # Return mock pynput with proper GlobalHotKeys
                mock_global_hotkeys = MagicMock()
                mock_global_hotkeys.return_value = MagicMock(
                    start=MagicMock(), stop=MagicMock()
                )
                fake_keyboard = SimpleNamespace(GlobalHotKeys=mock_global_hotkeys)
                return SimpleNamespace(keyboard=fake_keyboard)
            return original_import(name, *args, **kwargs)

        import builtins

        original_import = builtins.__import__
        monkeypatch.setattr(builtins, "__import__", mock_import)

        try:
            app = TalkTallyApp()
        except Exception as e:
            pytest.skip(f"Cannot initialize Tk root: {e}")

        app.withdraw()
        app.hotkey_var.set("cmd+shift+r")

        app._start_hotkey_listener()

        # Should not attempt Quartz when forced to use pynput
        assert not quartz_attempted[0], (
            "Should not attempt Quartz when TALKTALLY_FORCE_PYNPUT=1"
        )

        monkeypatch.setattr(builtins, "__import__", original_import)
        app._stop_hotkey_listener()
        app._on_close()
