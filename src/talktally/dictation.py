"""Dictation push-to-talk agent for macOS.

- Global hold-to-record hotkey (default: right Option)
- Shows a small microphone HUD next to the cursor while held
- Records mic audio, transcribes via local Wispr/Whisper command, pastes text to current focus

Mac-specific implementation uses Quartz/AppKit via PyObjC. All imports are runtime-guarded.
"""

from __future__ import annotations

import os
import queue
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable

import numpy as np
import sounddevice as sd
import soundfile as sf

from .common.settings import Settings
from .common.transcription import LocalTranscriber


def _dbg(msg: str) -> None:
    if os.environ.get("TALKTALLY_DEBUG") == "1":
        ts = time.strftime("%H:%M:%S")
        print(f"[dictation {ts}] {msg}", flush=True)


_ACCESSIBILITY_WARNED = False


def _warn_accessibility_permissions() -> None:
    global _ACCESSIBILITY_WARNED
    if _ACCESSIBILITY_WARNED:
        return
    _ACCESSIBILITY_WARNED = True
    msg = (
        "Dictation: macOS blocked simulated keystrokes. Enable Accessibility access for "
        "TalkTally (or the Python interpreter) under System Settings ▸ Privacy & Security ▸ Accessibility."
    )
    print(msg, flush=True)


@dataclass
class DictationConfig:
    hotkey_token: str
    wispr_cmd: str = "wispr"
    model: str = "tiny"
    sample_rate: int = 16_000


class DictationAgent:
    """Mac push-to-talk dictation agent orchestrator."""

    def __init__(
        self,
        settings: Settings,
        ui_dispatch: Optional[Callable[[Callable[[], None]], None]] = None,
    ) -> None:
        self._settings = settings
        self._cfg = DictationConfig(
            hotkey_token=settings.dictation_hotkey,
            wispr_cmd=settings.dictation_wispr_cmd,
            model=getattr(settings, "transcriber_model", "tiny"),
            sample_rate=settings.dictation_sample_rate,
        )
        self._listener: Optional[object] = None
        self._capturer = _MicCapturer()
        self._overlay = _MicHud()
        self._transcribing = threading.Event()
        self._lock = threading.Lock()
        self._ui_dispatch = ui_dispatch

    # ---- Lifecycle ----
    def start(self) -> None:
        if self._listener is not None:
            return
        if os.name != "posix":
            return
        if os.uname().sysname != "Darwin":  # type: ignore[attr-defined]
            return
        try:
            _dbg("starting quartz hold listener")
            self._listener = self._start_quartz_hold_listener(self._cfg.hotkey_token)
        except Exception as e:  # noqa: BLE001
            print(f"Dictation: failed to start hotkey listener: {e}")
            self._listener = None

    def stop(self) -> None:
        lst = self._listener
        self._listener = None
        try:
            if lst is not None:
                _dbg("stopping listener")
                stop = getattr(lst, "stop", None)
                if callable(stop):
                    stop()
        except Exception:
            pass
        try:
            self._dispatch(self._overlay.hide)
        except Exception:
            pass

    def restart(self, settings: Settings) -> None:
        self.stop()
        self.__init__(settings)
        self.start()

    # ---- Quartz implementation ----
    def _dispatch(self, fn: Callable[[], None]) -> None:
        # Prefer GUI-provided dispatcher; else try PyObjC AppHelper.callAfter; else run inline
        if self._ui_dispatch is not None:
            try:
                self._ui_dispatch(fn)
                return
            except Exception:
                pass
        try:
            from PyObjCTools.AppHelper import callAfter  # type: ignore

            callAfter(fn)
            return
        except Exception:
            pass
        try:
            fn()
        except Exception:
            pass

    def _start_quartz_hold_listener(self, token: str):  # noqa: ANN001
        import Quartz  # type: ignore

        keycode = _mac_keycode_from_token(token)
        _dbg(f"listener ready for keycode={keycode}")

        pressed = {"down": False}

        def callback(_proxy, type_, event, _refcon):  # noqa: ANN001
            try:
                if type_ == Quartz.kCGEventFlagsChanged:
                    kc = Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGKeyboardEventKeycode
                    )
                    flags = Quartz.CGEventGetFlags(event)
                    is_down = (
                        flags & Quartz.kCGEventFlagMaskAlternate
                    ) == Quartz.kCGEventFlagMaskAlternate
                    _dbg(
                        f"flagsChanged kc={kc} flags=0x{int(flags):x} alt={'1' if is_down else '0'} pressed={'1' if pressed['down'] else '0'}"
                    )
                    # Treat any alt flag off as release to avoid stuck state
                    if is_down and not pressed["down"]:
                        pressed["down"] = True
                        _dbg("hold start -> down")
                        self._on_hold_start()
                    elif not is_down and pressed["down"]:
                        pressed["down"] = False
                        _dbg("hold end -> up")
                        self._on_hold_end()
            except Exception as e:
                _dbg(f"callback error: {e}")
            return event

        mask = 1 << Quartz.kCGEventFlagsChanged

        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            mask,
            callback,
            None,
        )
        if not tap:
            raise RuntimeError(
                "Dictation: Failed to create event tap; allow Accessibility permissions"
            )
        run_loop_source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)

        def run_loop_thread() -> None:
            rl = Quartz.CFRunLoopGetCurrent()
            Quartz.CFRunLoopAddSource(rl, run_loop_source, Quartz.kCFRunLoopCommonModes)
            Quartz.CGEventTapEnable(tap, True)
            _dbg("event tap enabled")
            Quartz.CFRunLoopRun()

        t = threading.Thread(
            target=run_loop_thread, name="DictationHotkey", daemon=True
        )
        t.start()

        class _Listener:
            def stop(self_nonlocal) -> None:  # noqa: ANN001
                try:
                    Quartz.CGEventTapEnable(tap, False)
                except Exception:
                    pass
                try:
                    Quartz.CFRunLoopSourceInvalidate(run_loop_source)
                except Exception:
                    pass
                try:
                    Quartz.CFRunLoopStop(Quartz.CFRunLoopGetCurrent())
                except Exception:
                    pass

        return _Listener()

    # ---- Handlers ----
    def _on_hold_start(self) -> None:
        with self._lock:
            _dbg(
                f"_on_hold_start: transcribing={self._transcribing.is_set()} running={self._capturer.is_running()}"
            )
            if self._transcribing.is_set():
                _dbg("transcription in progress; ignoring start")
                return
            if self._capturer.is_running():
                _dbg("capture already running; ignoring start")
                return
            try:
                _dbg("_on_hold_start: starting capture")
                self._capturer.start(self._cfg.sample_rate)
                self._dispatch(self._overlay.show_recording_near_cursor)
            except Exception as e:  # noqa: BLE001
                print(f"Dictation: failed to start capture: {e}")

    def _on_hold_end(self) -> None:
        # Stop capture, then transcribe & paste in background
        try:
            _dbg("_on_hold_end: stopping capture")
            wav_path = self._capturer.stop()
        except Exception as e:  # noqa: BLE001
            print(f"Dictation: failed to stop capture: {e}")
            self._dispatch(self._overlay.hide)
            return

        # Show transcribing HUD while we process
        self._dispatch(self._overlay.show_transcribing_near_cursor)

        def worker() -> None:
            if not wav_path:
                _dbg("no wav_path to transcribe")
                return
            # Allow concurrent transcriptions; mark busy for visibility only
            self._transcribing.set()
            try:
                _dbg(f"transcribe begin: cmd={self._cfg.wispr_cmd}")
                transcriber = LocalTranscriber(
                    cmd=self._cfg.wispr_cmd,
                    model=self._cfg.model,
                    debug=_dbg,
                )
                text = transcriber.transcribe(wav_path)
                _dbg(f"transcribe done len={len(text) if text else 0}")
                if text:
                    _dbg(f"pasting first20='{text[:20]}'…")
                    _paste_text(text)
                    # Hide after clipboard has the text
                    self._dispatch(self._overlay.hide)
                else:
                    _dbg("empty transcript; skipping paste")
                    self._dispatch(self._overlay.hide)
            except Exception as e:  # noqa: BLE001
                print(f"Dictation: transcription failed: {e}")
            finally:
                try:
                    Path(wav_path).unlink(missing_ok=True)
                except Exception:
                    pass
                self._transcribing.clear()
                _dbg("transcribe finished & cleaned")

        threading.Thread(target=worker, name="DictationTranscribe", daemon=True).start()


# ---- Helpers ----


def _mac_keycode_from_token(token: str) -> int:
    t = (token or "right_option").strip().lower()
    # Common aliases
    aliases = {
        "right_option": 61,
        "ralt": 61,
        "ropt": 61,
        "left_option": 58,
        "lalt": 58,
        "lopt": 58,
        "caps_lock": 57,
        "caps": 57,
        "f17": 64,
        "f18": 79,
        "f19": 80,
        "f20": 90,
    }
    if t in aliases:
        return aliases[t]
    if t.startswith("keycode:"):
        try:
            return int(t.split(":", 1)[1])
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"Invalid keycode token: {token}") from e
    raise ValueError(
        f"Unsupported dictation hotkey token '{token}'. Use 'right_option', 'left_option', or 'keycode:<n>'."
    )


class _MicCapturer:
    """Capture microphone to a temp WAV file while running."""

    def __init__(self) -> None:
        self._stream: Optional[sd.InputStream] = None
        self._q: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
        self._writer: Optional[threading.Thread] = None
        self._f: Optional[sf.SoundFile] = None
        self._tmp_path: Optional[str] = None
        self._stop = threading.Event()

    def start(self, sample_rate: int = 16_000) -> None:
        if self._stream is not None:
            _dbg("MicCapturer.start called while running; ignoring")
            return
        # Prepare temp wav
        fd, p = tempfile.mkstemp(prefix="dictation_", suffix=".wav")
        os.close(fd)
        self._tmp_path = p
        self._f = sf.SoundFile(
            p, mode="w", samplerate=sample_rate, channels=1, subtype="PCM_16"
        )

        # Writer thread
        self._stop.clear()
        self._writer = threading.Thread(
            target=self._drain, name="DictationWriter", daemon=True
        )
        self._writer.start()

        # Mic stream
        self._stream = sd.InputStream(
            channels=1,
            samplerate=sample_rate,
            dtype="float32",
            callback=self._on_audio,
        )
        self._stream.start()
        _dbg(f"MicCapturer started sr={sample_rate}, tmp={p}")

    def stop(self) -> Optional[str]:
        if self._stream is None:
            _dbg("MicCapturer.stop called when not running")
            return self._tmp_path
        self._stop.set()
        try:
            self._stream.stop()
        finally:
            self._stream.close()
            self._stream = None
        # Signal writer to finish
        self._q.put_nowait(np.array([]))
        if self._writer is not None:
            self._writer.join(timeout=1.5)
        if self._f is not None:
            try:
                self._f.close()
            except Exception:
                pass
            self._f = None
        _dbg(f"MicCapturer stopped -> {self._tmp_path}")
        return self._tmp_path

    def is_running(self) -> bool:
        return self._stream is not None

    def _on_audio(self, indata: np.ndarray, frames: int, time_info, status):  # type: ignore[override]
        if status:
            pass
        # mono float32 -> queue
        try:
            self._q.put(indata.copy(), block=False)
        except queue.Full:
            pass

    def _drain(self) -> None:
        while not self._stop.is_set():
            try:
                block = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            if block.size == 0:
                break
            if self._f is not None:
                # clip and write as float -> SoundFile handles conversion
                self._f.write(np.clip(block, -1.0, 1.0))


class _MicHud:
    """Small HUD near cursor using AppKit, best-effort if PyObjC available.

    States:
    - recording: red dot
    - transcribing: orange dot
    """

    def __init__(self) -> None:
        self._avail = False
        self._window = None
        self._view_class = None
        try:
            import AppKit  # type: ignore

            self._AppKit = AppKit
            self._avail = True
        except Exception:
            self._AppKit = None  # type: ignore

    def _ensure_window(self) -> None:
        if not self._avail:
            return
        AppKit = self._AppKit
        assert AppKit is not None
        if self._window is None:
            rect = AppKit.NSMakeRect(0, 0, 28, 28)
            style = AppKit.NSWindowStyleMaskBorderless
            self._window = (
                AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                    rect, style, AppKit.NSBackingStoreBuffered, False
                )
            )
            self._window.setOpaque_(False)
            self._window.setBackgroundColor_(AppKit.NSColor.clearColor())
            self._window.setLevel_(AppKit.NSStatusWindowLevel)
            self._window.setIgnoresMouseEvents_(True)
            # Prepare custom view class once to avoid redefinition
            if self._view_class is None:

                def drawRect_(self_view, _rect):  # noqa: N802
                    AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                        *(getattr(self_view, "tt_color", (0.88, 0.14, 0.14, 0.9)))
                    ).set()
                    path = AppKit.NSBezierPath.bezierPathWithOvalInRect_(
                        AppKit.NSMakeRect(4, 4, 20, 20)
                    )
                    path.fill()

                self._view_class = type(
                    "TalkTallyMicHUDView", (AppKit.NSView,), {"drawRect_": drawRect_}
                )
            view = self._view_class.alloc().initWithFrame_(rect)
            # default red
            setattr(view, "tt_color", (0.88, 0.14, 0.14, 0.9))
            self._window.setContentView_(view)

    def _show_with_color_near_cursor(
        self, color: tuple[float, float, float, float]
    ) -> None:
        if not self._avail:
            return
        AppKit = self._AppKit
        assert AppKit is not None
        # Ensure execution on Cocoa main thread
        try:
            if not AppKit.NSThread.isMainThread():
                from PyObjCTools.AppHelper import callAfter  # type: ignore

                callAfter(lambda: self._show_with_color_near_cursor(color))
                return
        except Exception:
            pass
        self._ensure_window()
        view = self._window.contentView()  # type: ignore[attr-defined]
        setattr(view, "tt_color", color)
        view.setNeedsDisplay_(True)
        # Position near mouse
        loc = AppKit.NSEvent.mouseLocation()
        x = loc.x + 12
        y = loc.y - 12
        self._window.setFrameOrigin_((x, y))
        self._window.orderFrontRegardless()

    def show_recording_near_cursor(self) -> None:
        self._show_with_color_near_cursor((0.88, 0.14, 0.14, 0.9))

    def show_transcribing_near_cursor(self) -> None:
        self._show_with_color_near_cursor((0.96, 0.62, 0.12, 0.95))

    # Backwards compatibility
    def show_near_cursor(self) -> None:
        self.show_recording_near_cursor()

    def hide(self) -> None:
        if not self._avail or self._window is None:
            return
        # Ensure execution on main thread
        try:
            AppKit = self._AppKit
            assert AppKit is not None
            if not AppKit.NSThread.isMainThread():
                from PyObjCTools.AppHelper import callAfter  # type: ignore

                callAfter(self.hide)
                return
        except Exception:
            pass
        self._window.orderOut_(None)


def _paste_text(s: str) -> None:
    """Paste text into current focused field via NSPasteboard + Cmd+V gesture."""
    _dbg(f"paste via AppKit len={len(s)}")
    try:
        import AppKit  # type: ignore
        import Quartz  # type: ignore
    except Exception:
        _dbg("AppKit/Quartz unavailable, using AppleScript fallback")
        _paste_text_applescript(s)
        return

    def _send_cmd_v_system_events() -> bool:
        osa = (
            'tell application "System Events"\n'
            "  key code 9 using {command down}\n"
            "end tell"
        )
        try:
            proc = subprocess.run(
                ["osascript", "-l", "AppleScript", "-e", osa],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if proc.returncode != 0:
                err = proc.stderr.decode("utf-8", errors="ignore").strip()
                _dbg(f"System Events paste failed rc={proc.returncode}: {err}")
                if "not berechtigt" in err.lower() or "not authorized" in err.lower():
                    _warn_accessibility_permissions()
                return False
            _dbg("System Events paste keystroke sent")
            return True
        except Exception as exc:  # noqa: BLE001
            _dbg(f"System Events paste error: {exc}")
            return False

    pasteboard_type = getattr(AppKit, "NSPasteboardTypeString", "public.utf8-plain-text")

    # If accessibility permissions are missing, CGEvent posts are ignored. Fall back immediately.
    try:
        if hasattr(Quartz, "AXIsProcessTrusted") and not Quartz.AXIsProcessTrusted():
            _dbg("accessibility permission missing; using AppleScript fallback")
            _paste_text_applescript(s)
            return
    except Exception:
        pass

    def _perform_paste() -> None:
        pb = AppKit.NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.declareTypes_owner_([pasteboard_type], None)
        if not pb.setString_forType_(s, pasteboard_type):
            raise RuntimeError("Failed to populate NSPasteboard with transcript text.")

        # Give the pasteboard a moment to propagate the new owner before pasting.
        time.sleep(0.05)

        if _paste_text_accessibility(s):
            return

        if not _send_cmd_v_system_events():
            raise RuntimeError("System Events keystroke failed")

    try:
        # Ensure pasteboard interaction runs on the main thread when AppKit is available
        if hasattr(AppKit, "NSThread") and not AppKit.NSThread.isMainThread():
            done = threading.Event()
            err: list[Exception] = []
            executed = {"value": False}

            def _runner() -> None:
                if executed["value"]:
                    done.set()
                    return
                executed["value"] = True
                try:
                    _perform_paste()
                except Exception as exc:  # noqa: BLE001
                    err.append(exc)
                finally:
                    done.set()

            try:
                from PyObjCTools.AppHelper import callAfter  # type: ignore

                callAfter(_runner)
                if not done.wait(timeout=2.0):
                    _dbg("paste: main-thread dispatch timed out; running inline")
                    executed["value"] = True
                    _perform_paste()
                    done.set()
            except Exception:
                _perform_paste()
            if err:
                raise err[0]
        else:
            _perform_paste()
    except Exception as e:  # noqa: BLE001
        _dbg(f"paste: AppKit/Quartz path failed ({e}); falling back to AppleScript")
        _paste_text_applescript(s)


def _paste_text_applescript(s: str) -> None:
    try:
        _dbg("paste via AppleScript")
        # Write to pasteboard via pbcopy
        proc1 = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
        proc1.stdin.write(s.encode("utf-8"))  # type: ignore[union-attr]
        proc1.stdin.close()  # type: ignore[union-attr]
        proc1.wait(timeout=1.0)
        # Trigger Cmd+V via osascript, preferring key code for layout independence
        osa = (
            'tell application "System Events"\n'
            "  key code 9 using {command down}\n"
            "end tell"
        )
        proc2 = subprocess.run(
            ["osascript", "-l", "AppleScript", "-e", osa],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc2.returncode != 0:
            err = proc2.stderr.decode("utf-8", errors="ignore").strip()
            _dbg(f"AppleScript paste keystroke failed rc={proc2.returncode}: {err}")
        else:
            _dbg("AppleScript paste sent")
    except Exception as e:
        _dbg(f"AppleScript paste error: {e}")


def _paste_text_accessibility(s: str) -> bool:
    try:
        import Quartz  # type: ignore
    except Exception:
        return False

    try:
        trusted = True
        if hasattr(Quartz, "AXIsProcessTrustedWithOptions"):
            trusted = Quartz.AXIsProcessTrustedWithOptions(
                {Quartz.kAXTrustedCheckOptionPrompt: True}
            )
        elif hasattr(Quartz, "AXIsProcessTrusted"):
            trusted = Quartz.AXIsProcessTrusted()
        if not trusted:
            _warn_accessibility_permissions()
    except Exception:
        pass

    kAXErrorSuccess = getattr(Quartz, "kAXErrorSuccess", 0)
    system = Quartz.AXUIElementCreateSystemWide()
    try:
        err, focused = Quartz.AXUIElementCopyAttributeValue(
            system, Quartz.kAXFocusedUIElementAttribute
        )
    except Exception as exc:  # noqa: BLE001
        _dbg(f"AX paste: unable to obtain focused element: {exc}")
        return False
    if err != kAXErrorSuccess or focused is None:
        _dbg(f"AX paste: focused element unavailable (err={err})")
        return False

    try:
        err, settable = Quartz.AXUIElementIsAttributeSettable(
            focused, Quartz.kAXValueAttribute
        )
    except Exception:
        err = kAXErrorSuccess
        settable = True
    if err == kAXErrorSuccess and not settable:
        _dbg("AX paste: focused element is read-only")
        return False

    existing = ""
    try:
        err, current = Quartz.AXUIElementCopyAttributeValue(
            focused, Quartz.kAXValueAttribute
        )
        if err == kAXErrorSuccess and isinstance(current, str):
            existing = current
        elif err == kAXErrorSuccess and current is not None:
            existing = str(current)
    except Exception as exc:  # noqa: BLE001
        _dbg(f"AX paste: unable to read current value ({exc})")
        err = -1

    start = len(existing)
    length = 0
    try:
        err, range_val = Quartz.AXUIElementCopyAttributeValue(
            focused, Quartz.kAXSelectedTextRangeAttribute
        )
        if err == kAXErrorSuccess and range_val is not None:
            ok, rng = Quartz.AXValueGetValue(
                range_val, Quartz.kAXValueCFRangeType
            )
            if ok and isinstance(rng, tuple) and len(rng) == 2:
                start, length = int(rng[0]), int(rng[1])
    except Exception:
        pass

    try:
        before = existing[:start]
        after = existing[start + length :]
    except Exception:
        before = existing
        after = ""
    new_value = before + s + after

    err = Quartz.AXUIElementSetAttributeValue(
        focused, Quartz.kAXValueAttribute, new_value
    )
    if err != kAXErrorSuccess:
        if hasattr(Quartz, "kAXErrorNotAuthorized") and err == Quartz.kAXErrorNotAuthorized:
            _warn_accessibility_permissions()
        _dbg(f"AX paste: failed to set value (err={err})")
        return False

    try:
        new_loc = start + len(s)
        new_range = Quartz.AXValueCreate(
            Quartz.kAXValueCFRangeType, (new_loc, 0)
        )
        if new_range is not None:
            Quartz.AXUIElementSetAttributeValue(
                focused, Quartz.kAXSelectedTextRangeAttribute, new_range
            )
    except Exception:
        pass

    _dbg("AX paste succeeded")
    return True
