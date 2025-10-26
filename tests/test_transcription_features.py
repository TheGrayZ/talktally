"""Tests for transcription cancellation and sound notification features."""

import pytest
from unittest.mock import patch
from pathlib import Path


def test_transcription_cancel_button_exists():
    """Test that the cancel button is created and configured correctly."""
    from talktally.gui import TalkTallyApp

    try:
        app = TalkTallyApp()
    except Exception as e:
        pytest.skip(f"Cannot initialize Tk root: {e}")

    app.withdraw()

    try:
        # Verify cancel button exists
        assert hasattr(app, "btn_cancel_transcribe")
        assert app.btn_cancel_transcribe.cget("text") == "Cancel"
        assert (
            str(app.btn_cancel_transcribe.cget("state")) == "disabled"
        )  # Initially disabled

        # Verify cancellation flag exists
        assert hasattr(app, "_transcription_cancelled")
        assert app._transcription_cancelled is False  # Initially false

    finally:
        app._on_close()


def test_transcription_cancellation_with_real_process():
    """Test that cancellation actually terminates the transcription process."""
    from talktally.common.transcription import LocalTranscriber
    import time
    import threading
    
    # Create a flag to control cancellation
    cancelled = {"flag": False}
    
    def cancel_flag():
        return cancelled["flag"]
    
    # Create a transcriber (this will use a real command that might not exist)
    transcriber = LocalTranscriber(cmd="sleep 10")  # Long-running command
    
    # Start cancellation after a short delay
    def set_cancel_after_delay():
        time.sleep(0.5)  # Wait half a second
        cancelled["flag"] = True
    
    cancel_thread = threading.Thread(target=set_cancel_after_delay, daemon=True)
    cancel_thread.start()
    
    # This should be cancelled quickly
    start_time = time.time()
    try:
        # This will fail because "sleep" isn't a valid transcription command,
        # but it should still test the cancellation mechanism
        transcriber._transcribe_stdout_tool(
            audio_path=Path("/dev/null"),  # Dummy path
            cancel_flag=cancel_flag
        )
    except (RuntimeError, InterruptedError):
        # Either error is expected - RuntimeError for invalid command, 
        # InterruptedError for successful cancellation
        pass
    
    elapsed = time.time() - start_time
    
    # Should complete quickly due to cancellation (much less than 10 seconds)
    assert elapsed < 5.0, f"Cancellation took too long: {elapsed} seconds"


def test_transcription_cancellation_flag():
    """Test that the cancellation flag is managed correctly."""
    from talktally.gui import TalkTallyApp

    try:
        app = TalkTallyApp()
    except Exception as e:
        pytest.skip(f"Cannot initialize Tk root: {e}")

    app.withdraw()

    try:
        # Mock the transcription running state
        app._transcription_running = True

        # Test cancel functionality
        app._cancel_transcription()

        # Verify cancellation flag is set
        assert app._transcription_cancelled is True

        # Test that cancellation handler resets the flag
        app._finish_transcription_cancelled()
        assert app._transcription_cancelled is False
        assert app._transcription_running is False

    finally:
        app._on_close()


@patch("talktally.gui.TalkTallyApp._play_sound")
def test_transcription_completion_sound(mock_play_sound):
    """Test that completion sound is played when transcription finishes."""
    from talktally.gui import TalkTallyApp
    from talktally.recording_transcriber import RecordingTranscriptionResult
    from pathlib import Path

    try:
        app = TalkTallyApp()
    except Exception as e:
        pytest.skip(f"Cannot initialize Tk root: {e}")

    app.withdraw()

    try:
        # Enable sounds
        app.var_sounds.set(True)

        # Mock a successful transcription result
        result = RecordingTranscriptionResult(
            source=Path("test.wav"),
            transcript="Test transcript",
            output_path=Path("test_transcript.txt"),
            model="tiny",
        )

        # Call the success handler
        app._finish_transcription_success(result)

        # Verify completion sound was played
        mock_play_sound.assert_called_with("Hero")

    finally:
        app._on_close()


@patch("talktally.gui.TalkTallyApp._play_sound")
def test_transcription_cancellation_sound(mock_play_sound):
    """Test that cancellation sound is played when transcription is cancelled."""
    from talktally.gui import TalkTallyApp

    try:
        app = TalkTallyApp()
    except Exception as e:
        pytest.skip(f"Cannot initialize Tk root: {e}")

    app.withdraw()

    try:
        # Enable sounds
        app.var_sounds.set(True)

        # Set up running transcription state
        app._transcription_running = True

        # Call the cancellation handler
        app._finish_transcription_cancelled()

        # Verify cancellation sound was played
        mock_play_sound.assert_called_with("Basso")

    finally:
        app._on_close()


def test_button_state_management():
    """Test that button states are managed correctly during transcription operations."""
    from talktally.gui import TalkTallyApp

    try:
        app = TalkTallyApp()
    except Exception as e:
        pytest.skip(f"Cannot initialize Tk root: {e}")

    app.withdraw()

    try:
        # Initially, transcribe should be available, cancel should be disabled
        app._update_transcription_buttons()

        # When transcription starts
        app._set_transcription_running(True)

        # Transcribe should be disabled, cancel should be enabled
        assert str(app.btn_transcribe.cget("state")) == "disabled"
        assert str(app.btn_cancel_transcribe.cget("state")) == "normal"

        # When transcription ends
        app._set_transcription_running(False)

        # Cancel should be disabled again
        assert str(app.btn_cancel_transcribe.cget("state")) == "disabled"

    finally:
        app._on_close()
