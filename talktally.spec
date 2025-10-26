# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

# Get the source directory
src_dir = Path(os.getcwd()) / "src"

a = Analysis(
    ["launcher.py"],
    pathex=[str(src_dir)],
    binaries=[],
    datas=[
        # Include any data files if needed
    ],
    hiddenimports=[
        'talktally.recorder',
        'talktally.common.settings',
        'talktally.recording_transcriber',
        'talktally.common.encoding',
        'talktally.common.fs',
        'talktally.common.transcription',
        'talktally.dictation',
        'numpy',
        'sounddevice',
        'soundfile',
        'tkinter',
        'threading',
        'subprocess',
        # macOS-specific modules
        'Foundation',
        'AppKit',
        'Quartz',
        'CoreFoundation',
        'objc',
        'PyObjCTools',
        # Audio-related modules
        'numpy._core',
        'numpy.core',
        # Additional imports for completeness
        'collections.abc',
        'six',
        'six.moves',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='TalkTally',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # Set to False for GUI app
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # Add path to .icns file if you have one
)

# For macOS, create an app bundle
app = BUNDLE(
    exe,
    name='TalkTally.app',
    icon=None,  # Add path to .icns file if you have one
    bundle_identifier='com.talktally.recorder',
    info_plist={
        'NSMicrophoneUsageDescription': 'TalkTally needs microphone access to record audio.',
        'NSAppleEventsUsageDescription': 'TalkTally needs accessibility permissions for dictation features.',
        'CFBundleDisplayName': 'TalkTally',
        'CFBundleVersion': '0.1.0',
        'CFBundleShortVersionString': '0.1.0',
    },
)