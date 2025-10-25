# TalkTally

Capture microphone, system audio, and a mixed stereo output on macOS, with a simple GUI, hotkeys, and optional local transcription.

- GUI app: choose input device, map channels, pick outputs (Mic, System, Mixed), file names, formats, and output folder.
- Multiple formats: WAV (16/24‑bit), MP3 (via FFmpeg), FLAC (level 0–8), with per‑minute size estimates.
- Global hotkey to start/stop recording and an on‑screen recording timer overlay.
- Dictation (push‑to‑talk): hold a key to record mic, transcribe via a local Whisper/Wispr CLI, and paste the text into the frontmost app.
- Batch transcription: list recordings, transcribe to text files, view/open/copy transcripts.


## Requirements

- macOS (designed and tested for macOS; system‑audio capture relies on an Aggregate Device).
- Python 3.11+
- Python dependencies (installed automatically):
  - numpy, sounddevice, soundfile
- Optional/feature dependencies:
  - FFmpeg (MP3 export): `brew install ffmpeg`
  - Local transcriber CLI (for Dictation and Transcribe Recordings):
    - OpenAI Whisper CLI: `pip install openai-whisper` (provides the `whisper` command)
    - Or any compatible CLI that prints transcript text to stdout; set its command in Settings
  - PyObjC (Quartz/AppKit) for the macOS overlay, hotkeys, and robust dictation paste: `pip install pyobjc`
  - pynput (fallback global hotkeys on non‑Quartz path): `pip install pynput`
  - Loopback driver for system audio capture, e.g. BlackHole 2ch


## Installation

- Recommended (development install):
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -e .[dev]
  ```
- User install (no dev tooling):
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -e .
  ```

Run the app:
- Entry point: `talktally-gui`
- Or: `python -m talktally.gui`

Enable debug logs (optional):
```bash
TALKTALLY_DEBUG=1 talktally-gui
```


## macOS system‑audio setup (Aggregate Device)

To record “system audio” (what you hear from apps), create an Aggregate Device that combines a loopback driver with your microphone:

1) Install a loopback device such as BlackHole 2ch (see the BlackHole docs; commonly installed via Homebrew).
2) Open “Audio MIDI Setup” and create an Aggregate Device that includes:
   - BlackHole 2ch (provides system audio on input channels 0 and 1)
   - Your microphone (often appears as the next input channel)
3) In TalkTally:
   - Select the Aggregate Device as the input device
   - Default channel mapping assumes system: `0,1` and mic: `2`
   - Adjust mappings if your device order differs

Notes:
- System audio capture depends on how your Aggregate Device is configured.
- You can record any subset: Mic, System, and/or Mixed stereo.


## Usage

1) Launch TalkTally and select your input device (Aggregate Device).
2) Map channel indices for Mic and System. Hold Cmd/Ctrl to select multiple in the list.
3) Choose outputs (Mic, System, Mixed), file names, and output directory.
4) Pick a file format and encoding options. A per‑minute storage estimate is shown.
5) Click “Record” (or use the global hotkey) to start; click “Stop” to finish.

Output files:
- Saved under `<output_dir>/recordings/`
- Non‑clobbering names: avoids overwrites and prefixes the finished files with an end‑timestamp
- MP3 export requires FFmpeg; WAV and FLAC are native

Overlay and sounds:
- An on‑screen timer can appear in the top‑right while recording
- Optional start/stop sounds can be enabled in Settings

Global hotkey:
- Enable in “Hotkey & Dictation”; default is `cmd+shift+r`
- On macOS, TalkTally prefers a Quartz hotkey listener; otherwise it falls back to `pynput`


## Dictation (push‑to‑talk)

Dictation lets you hold a key to capture a short mic snippet, transcribe it locally, and paste the text into the focused app.

- Enable “Dictation (press & hold)” in Hotkey & Dictation
- Choose the hold key (default: right Option) and transcriber command (default: `whisper`)
- Choose a Whisper model (e.g., `tiny`, `base`, `small`, `medium`, `large`)
- A small HUD dot appears near the cursor while recording (red) and while transcribing (orange)
- On success, text is pasted into the foreground application

Requirements and permissions:
- Local transcriber CLI (e.g., `openai-whisper`) must be installed and on PATH
- On macOS, grant Accessibility access for paste and hotkeys: System Settings → Privacy & Security → Accessibility
- If Accessibility is missing, TalkTally prints a warning and falls back to AppleScript where possible


## Transcribe existing recordings

Use the “Transcribe Recordings” panel:
- Lists audio files from `<output_dir>/recordings/` (and optionally the output folder)
- Select a recording, pick a model, and click “Transcribe”
- Transcript `.txt` files are saved under `<output_dir>/transcripts/`
- Filenames include a model token, e.g., `my_take__tiny.txt`; multiple runs add `(2)`, `(3)`, etc.
- You can open or copy the transcript text from within the app


## Formats and encoding

- WAV:
  - 44.1 kHz or 48 kHz; 16‑bit or 24‑bit; channel count per output as configured
- MP3:
  - 96–320 kbps CBR using FFmpeg’s `libmp3lame`
- FLAC:
  - Level 0–8; size varies with the chosen level


## Troubleshooting

- MP3 not available: Install FFmpeg and ensure `ffmpeg` is on PATH
- No transcript / command not found: Install `openai-whisper` or set another CLI in Settings
- Paste blocked / hotkey not firing: Grant Accessibility access for TalkTally (or the Python interpreter)
- Device not found: Verify the Aggregate Device name in Audio MIDI Setup and select it in the app
- Wrong channels: Adjust Mic/System channel indices to match your Aggregate Device


## Development

- Setup:
  ```bash
  python3 -m venv .venv && source .venv/bin/activate
  pip install -e .[dev]
  ```
- Lint/format:
  ```bash
  ruff check --fix src tests
  ruff format src tests
  ```
- Tests:
  ```bash
  pytest
  ```
- Build packaging artifacts:
  ```bash
  python -m build
  ```
