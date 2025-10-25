"""Tkinter GUI for TalkTally on macOS.

Features:
- Record/Stop toggle
- Choose outputs (Mic, System, Mixed)
- Choose filenames and output directory; auto-rename if exists
- Select input device by name
"""

from __future__ import annotations

import os
import sys
import time
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from typing import Optional

from .recorder import AudioRecorder, RecorderConfig, OutputSelection, list_input_devices
from .recorder import input_channel_count
from .common.settings import Settings, load_settings, save_settings
from .recording_transcriber import (
    list_recordings,
    transcribe_recording,
    RecordingTranscriptionResult,
)

WHISPER_MODELS = [
    "tiny",
    "tiny.en",
    "base",
    "base.en",
    "small",
    "small.en",
    "medium",
    "large",
    "large-v2",
]


def _dbg(msg: str) -> None:
    if os.environ.get("TALKTALLY_DEBUG") == "1":
        ts = time.strftime("%H:%M:%S")
        print(f"[gui {ts}] {msg}", flush=True)


class TalkTallyApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("TalkTally Recorder")
        self.resizable(True, True)

        # Load persisted settings before creating UI variables
        self._settings: Settings = load_settings()
        self._saving_suspended: bool = False  # avoid save storms during init

        self.rec = AudioRecorder()
        self._overlay: Optional[tk.Toplevel] = None
        self._overlay_timer_var = tk.StringVar(value="00:00")
        self._overlay_job: Optional[str] = None

        self._hotkey_listener = None  # type: ignore[assignment]
        self._dictation: object | None = None

        self._build_ui()
        self._create_overlay()
        self._refresh_devices()
        self._apply_device_selection()
        self._fit_to_content()
        self._bind_setting_traces()
        self._force_pynput = os.environ.get("TALKTALLY_FORCE_PYNPUT") == "1"
        if self.enable_hotkey.get():
            self._start_hotkey_listener()
        if getattr(self._settings, "dictation_enable", False) and not self._force_pynput:
            self._start_dictation_agent()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 6}

        # Record button styling
        style = ttk.Style(self)
        style.configure("Record.TButton", font=("Helvetica", 14), padding=8)

        # Device selection
        dev_frame = ttk.LabelFrame(self, text="Input Device (Aggregate)")
        dev_frame.pack(fill="x", **pad)
        self.device_var = tk.StringVar(value=self._settings.device_name)
        self.device_cb = ttk.Combobox(
            dev_frame, textvariable=self.device_var, state="readonly"
        )
        self.device_cb.pack(side="left", fill="x", expand=True, padx=8, pady=8)
        self.device_cb.bind("<<ComboboxSelected>>", lambda _e: self._on_device_selected())
        ttk.Button(dev_frame, text="Refresh", command=self._refresh_devices).pack(
            side="right", padx=8, pady=8
        )

        # Channel mapping
        self.mic_ch_var = tk.StringVar(value=self._settings.mic_channels)
        self.sys_ch_var = tk.StringVar(value=self._settings.system_channels)
        ch_frame = ttk.LabelFrame(self, text="Channel Mapping")
        ch_frame.pack(fill="x", **pad)
        ch_frame.columnconfigure(1, weight=1)
        ch_frame.columnconfigure(3, weight=1)
        self.channel_info_var = tk.StringVar(value="")
        ttk.Label(ch_frame, textvariable=self.channel_info_var).grid(
            row=0, column=0, columnspan=4, sticky="w", padx=8, pady=(4, 0)
        )
        ttk.Label(ch_frame, text="Mic channels:").grid(
            row=1, column=0, sticky="nw", padx=8, pady=(6, 2)
        )
        self.mic_listbox = tk.Listbox(
            ch_frame,
            height=5,
            selectmode="extended",
            exportselection=False,
            width=10,
        )
        self.mic_listbox.grid(row=1, column=1, sticky="nwe", padx=(0, 12), pady=(6, 2))
        self.mic_listbox.bind("<<ListboxSelect>>", lambda _e: self._on_channel_select("mic"))
        ttk.Label(ch_frame, text="System channels:").grid(
            row=1, column=2, sticky="nw", padx=0, pady=(6, 2)
        )
        self.sys_listbox = tk.Listbox(
            ch_frame,
            height=5,
            selectmode="extended",
            exportselection=False,
            width=10,
        )
        self.sys_listbox.grid(row=1, column=3, sticky="nwe", padx=(0, 8), pady=(6, 2))
        self.sys_listbox.bind("<<ListboxSelect>>", lambda _e: self._on_channel_select("system"))
        ttk.Label(
            ch_frame, text="Hold ⌘ (or Ctrl) to pick multiple channels."
        ).grid(row=2, column=0, columnspan=4, sticky="w", padx=8, pady=(0, 6))

        # Outputs
        out_frame = ttk.LabelFrame(self, text="Output Settings")
        out_frame.pack(fill="x", **pad)
        self.var_mic = tk.BooleanVar(value=self._settings.output_mic)
        self.var_sys = tk.BooleanVar(value=self._settings.output_system)
        self.var_mix = tk.BooleanVar(value=self._settings.output_mixed)
        ttk.Checkbutton(out_frame, text="Mic track", variable=self.var_mic).grid(
            row=0, column=0, sticky="w", padx=8, pady=4
        )
        ttk.Checkbutton(out_frame, text="System track", variable=self.var_sys).grid(
            row=1, column=0, sticky="w", padx=8, pady=4
        )
        ttk.Checkbutton(out_frame, text="Mixed (stereo)", variable=self.var_mix).grid(
            row=2, column=0, sticky="w", padx=8, pady=4
        )

        ttk.Label(out_frame, text="Mic filename:").grid(
            row=0, column=1, sticky="e", padx=4
        )
        ttk.Label(out_frame, text="System filename:").grid(
            row=1, column=1, sticky="e", padx=4
        )
        ttk.Label(out_frame, text="Mixed filename:").grid(
            row=2, column=1, sticky="e", padx=4
        )
        self.var_mic_file = tk.StringVar(value=self._settings.mic_filename)
        self.var_sys_file = tk.StringVar(value=self._settings.system_filename)
        self.var_mix_file = tk.StringVar(value=self._settings.mixed_filename)
        ttk.Entry(out_frame, textvariable=self.var_mic_file, width=22).grid(
            row=0, column=2, sticky="w"
        )
        ttk.Entry(out_frame, textvariable=self.var_sys_file, width=22).grid(
            row=1, column=2, sticky="w"
        )
        ttk.Entry(out_frame, textvariable=self.var_mix_file, width=22).grid(
            row=2, column=2, sticky="w"
        )

        # Output directory chooser
        dir_frame = ttk.Frame(out_frame)
        dir_frame.grid(row=3, column=0, columnspan=3, sticky="we", padx=8, pady=6)
        ttk.Label(dir_frame, text="Output directory:").grid(row=0, column=0, sticky="w")
        self.var_outdir = tk.StringVar(
            value=self._settings.output_dir or str(Path.cwd())
        )
        self.entry_outdir = ttk.Entry(dir_frame, textvariable=self.var_outdir, width=44)
        self.entry_outdir.grid(row=0, column=1, sticky="we", padx=6)
        dir_frame.columnconfigure(1, weight=1)
        ttk.Button(dir_frame, text="Browse…", command=self._browse_dir).grid(
            row=0, column=2, padx=4
        )

        # Format & encoding settings next to output path
        fmt_frame = ttk.Frame(out_frame)
        fmt_frame.grid(row=4, column=0, columnspan=3, sticky="we", padx=8, pady=4)

        ttk.Label(fmt_frame, text="Format:").grid(row=0, column=0, sticky="e")
        self.var_format = tk.StringVar(
            value=self._settings.file_format
            if hasattr(self._settings, "file_format")
            else "wav"
        )
        self.cb_format = ttk.Combobox(
            fmt_frame,
            state="readonly",
            width=8,
            values=["wav", "mp3", "flac"],
            textvariable=self.var_format,
        )
        self.cb_format.grid(row=0, column=1, sticky="w", padx=4)

        # WAV settings
        self.var_wav_sr = tk.IntVar(
            value=getattr(self._settings, "wav_sample_rate", 48000)
        )
        self.var_wav_bd = tk.IntVar(value=getattr(self._settings, "wav_bit_depth", 16))
        self.wav_sr_cb = ttk.Combobox(
            fmt_frame,
            state="readonly",
            width=8,
            values=[44100, 48000],
            textvariable=self.var_wav_sr,
        )
        self.wav_bd_cb = ttk.Combobox(
            fmt_frame,
            state="readonly",
            width=6,
            values=[16, 24],
            textvariable=self.var_wav_bd,
        )

        # MP3 settings
        self.var_mp3_kbps = tk.IntVar(
            value=getattr(self._settings, "mp3_bitrate_kbps", 192)
        )
        self.mp3_kbps_cb = ttk.Combobox(
            fmt_frame,
            state="readonly",
            width=8,
            values=[96, 128, 160, 192, 256, 320],
            textvariable=self.var_mp3_kbps,
        )

        # FLAC settings
        self.var_flac_level = tk.IntVar(value=getattr(self._settings, "flac_level", 5))
        self.flac_level_cb = ttk.Combobox(
            fmt_frame,
            state="readonly",
            width=6,
            values=list(range(0, 9)),
            textvariable=self.var_flac_level,
        )

        # Estimated storage per minute
        self.estimate_var = tk.StringVar(value="")
        self.estimate_lbl = ttk.Label(fmt_frame, textvariable=self.estimate_var)

        # Initial layout and estimate
        self._refresh_encoding_controls()
        self._update_storage_estimate()
        self._refresh_channel_selectors()

        # Record button + status
        ctrl = ttk.Frame(self)
        ctrl.pack(fill="x", **pad)
        self.btn = ttk.Button(
            ctrl, text="🔴 Record", style="Record.TButton", command=self._toggle
        )
        self.btn.pack(side="left", padx=6)
        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(ctrl, textvariable=self.status_var).pack(side="left", padx=12)

        # Model settings shared by dictation & transcription
        current_model = getattr(self._settings, "transcriber_model", "tiny") or "tiny"
        self.model_var = tk.StringVar(value=current_model)
        model_choices = list(WHISPER_MODELS)
        if current_model not in model_choices:
            model_choices.append(current_model)
        model_frame = ttk.LabelFrame(self, text="Model Settings")
        model_frame.pack(fill="x", **pad)
        model_frame.columnconfigure(1, weight=1)
        ttk.Label(model_frame, text="Whisper model:").grid(
            row=0, column=0, sticky="e", padx=8, pady=6
        )
        self.model_combo = ttk.Combobox(
            model_frame,
            state="readonly",
            values=model_choices,
            textvariable=self.model_var,
            width=16,
        )
        self.model_combo.grid(row=0, column=1, sticky="w", padx=6, pady=6)
        ttk.Label(
            model_frame,
            text="Applies to push-to-talk dictation and batch transcription.",
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 6))

        # Hotkey & dictation settings
        self.enable_hotkey = tk.BooleanVar(value=self._settings.enable_hotkey)
        self.hotkey_var = tk.StringVar(value=self._settings.hotkey)
        self.var_sounds = tk.BooleanVar(value=self._settings.play_sounds)
        self.dictation_enable = tk.BooleanVar(
            value=getattr(self._settings, "dictation_enable", False)
        )
        self.dictation_hotkey = tk.StringVar(
            value=getattr(self._settings, "dictation_hotkey", "right_option")
        )
        self.dictation_wispr_cmd = tk.StringVar(
            value=getattr(self._settings, "dictation_wispr_cmd", "whisper")
        )

        hotkey_frame = ttk.LabelFrame(self, text="Hotkey & Dictation")
        hotkey_frame.pack(fill="x", **pad)
        hotkey_frame.columnconfigure(2, weight=1)
        ttk.Checkbutton(
            hotkey_frame,
            text="Start recording",
            variable=self.enable_hotkey,
            command=self._toggle_hotkey_listener,
        ).grid(row=0, column=0, sticky="w", padx=8, pady=4)
        ttk.Label(hotkey_frame, text="Recorder hotkey:").grid(
            row=0, column=1, sticky="e", pady=4
        )
        rec_hotkey_entry = ttk.Entry(hotkey_frame, textvariable=self.hotkey_var, width=22)
        rec_hotkey_entry.grid(row=0, column=2, sticky="w", padx=6, pady=4)
        rec_hotkey_entry.bind("<FocusOut>", lambda _e: self._restart_hotkey_if_enabled())

        ttk.Checkbutton(
            hotkey_frame,
            text="Enable dictation (press & hold)",
            variable=self.dictation_enable,
            command=self._toggle_dictation_agent,
        ).grid(row=1, column=0, sticky="w", padx=8, pady=4)
        ttk.Label(
            hotkey_frame,
            text="Dictation hold key:",
        ).grid(row=1, column=1, sticky="e", pady=4)
        dict_hotkey_entry = ttk.Entry(
            hotkey_frame, textvariable=self.dictation_hotkey, width=28
        )
        dict_hotkey_entry.grid(row=1, column=2, sticky="w", padx=6, pady=4)
        dict_hotkey_entry.bind("<FocusOut>", lambda _e: self._restart_dictation_if_enabled())

        ttk.Label(hotkey_frame, text="Transcriber command:").grid(
            row=2, column=1, sticky="e", pady=4
        )
        cmd_entry = ttk.Entry(
            hotkey_frame, textvariable=self.dictation_wispr_cmd, width=28
        )
        cmd_entry.grid(row=2, column=2, sticky="w", padx=6, pady=4)
        cmd_entry.bind("<FocusOut>", lambda _e: self._restart_dictation_if_enabled())

        ttk.Checkbutton(
            hotkey_frame, text="Play start/stop sounds", variable=self.var_sounds
        ).grid(row=3, column=0, sticky="w", padx=8, pady=4)
        ttk.Label(
            hotkey_frame,
            text="An overlay appears in the top-right while recording.",
        ).grid(row=3, column=1, columnspan=2, sticky="w", padx=6, pady=4)

        # Transcription panel
        trans_section = ttk.LabelFrame(self, text="Transcribe Recordings")
        trans_section.pack(fill="both", expand=True, **pad)
        self._build_transcription_panel(trans_section)

    def _build_transcription_panel(self, parent: tk.Widget) -> None:
        container = ttk.Frame(parent)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(1, weight=1)
        container.rowconfigure(4, weight=2)

        btns = ttk.Frame(container)
        btns.grid(row=0, column=0, sticky="we", pady=(8, 6), padx=12)
        self.btn_refresh_transcripts = ttk.Button(
            btns, text="Refresh", command=self._refresh_transcription_list
        )
        self.btn_refresh_transcripts.pack(side="left")
        ttk.Button(btns, text="Open Folder", command=self._open_output_dir).pack(
            side="left", padx=(6, 0)
        )

        list_frame = ttk.Frame(container)
        list_frame.grid(row=1, column=0, sticky="nsew", padx=12)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        columns = ("name", "modified", "size")
        self.transcription_tree = ttk.Treeview(
            list_frame,
            columns=columns,
            show="headings",
            height=6,
            selectmode="browse",
        )
        self.transcription_tree.heading("name", text="File")
        self.transcription_tree.heading("modified", text="Modified")
        self.transcription_tree.heading("size", text="Size")
        self.transcription_tree.column("name", width=220, anchor="w")
        self.transcription_tree.column("modified", width=140, anchor="center")
        self.transcription_tree.column("size", width=80, anchor="e")
        vsb = ttk.Scrollbar(
            list_frame, orient="vertical", command=self.transcription_tree.yview
        )
        self.transcription_tree.configure(yscrollcommand=vsb.set)
        self.transcription_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        self.transcription_tree.bind(
            "<<TreeviewSelect>>", lambda _e: self._on_transcription_select()
        )
        self.transcription_tree.bind(
            "<Double-1>", lambda _e: self._start_transcription()
        )
        self._transcription_index: dict[str, Path] = {}
        self._transcription_thread: threading.Thread | None = None
        self._last_transcript_path: Path | None = None

        actions = ttk.Frame(container)
        actions.grid(row=2, column=0, sticky="we", pady=6, padx=12)
        self.btn_transcribe = ttk.Button(
            actions, text="Transcribe", command=self._start_transcription
        )
        self.btn_transcribe.pack(side="left")
        self.btn_open_transcript = ttk.Button(
            actions,
            text="Open Transcript",
            command=self._open_transcript,
            state="disabled",
        )
        self.btn_open_transcript.pack(side="left", padx=6)
        self.btn_copy_transcript = ttk.Button(
            actions,
            text="Copy Text",
            command=self._copy_transcript,
            state="disabled",
        )
        self.btn_copy_transcript.pack(side="left")

        status_frame = ttk.Frame(container)
        status_frame.grid(row=3, column=0, sticky="we", padx=12)
        status_frame.columnconfigure(1, weight=1)
        self.transcription_progress = ttk.Progressbar(
            status_frame, mode="indeterminate", length=140
        )
        self.transcription_progress.grid(row=0, column=0, sticky="w")
        self.transcription_status_var = tk.StringVar(
            value="Select a recording to transcribe."
        )
        ttk.Label(
            status_frame, textvariable=self.transcription_status_var, anchor="w"
        ).grid(row=0, column=1, sticky="we", padx=(8, 0))

        text_frame = ttk.Frame(container)
        text_frame.grid(row=4, column=0, sticky="nsew", pady=(4, 12), padx=12)
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)
        self.transcription_text = tk.Text(
            text_frame,
            height=10,
            wrap="word",
            state="disabled",
            font=("Helvetica", 12),
        )
        text_vsb = ttk.Scrollbar(
            text_frame, orient="vertical", command=self.transcription_text.yview
        )
        self.transcription_text.configure(yscrollcommand=text_vsb.set)
        self.transcription_text.grid(row=0, column=0, sticky="nsew")
        text_vsb.grid(row=0, column=1, sticky="ns")

        self._refresh_transcription_list()

    # ------- UI Callbacks -------
    def _refresh_devices(self) -> None:
        names = list_input_devices()
        self.device_cb["values"] = names
        # Try select existing value; else first
        cur = self.device_var.get()
        if cur and cur in names:
            self.device_cb.set(cur)
        elif names:
            self.device_cb.set(names[0])
        self._refresh_channel_selectors()

    def _on_device_selected(self) -> None:
        self._refresh_channel_selectors()

    def _refresh_channel_selectors(self) -> None:
        if not hasattr(self, "mic_listbox"):
            return
        device = self.device_var.get()
        total = input_channel_count(device) if device else 0
        try:
            mic_selected = self._parse_indices(self.mic_ch_var.get())
        except ValueError:
            mic_selected = []
        try:
            sys_selected = self._parse_indices(self.sys_ch_var.get())
        except ValueError:
            sys_selected = []
        max_index = 0
        for seq in (mic_selected, sys_selected):
            if seq:
                max_index = max(max_index, max(seq) + 1)
        if total <= 0:
            total = max(max_index, 4)
        else:
            total = max(total, max_index)
        self._populate_channel_listbox(self.mic_listbox, mic_selected, total, "mic")
        self._populate_channel_listbox(self.sys_listbox, sys_selected, total, "system")
        if device and total:
            self.channel_info_var.set(
                f"{total} input channels detected for '{device}'."
            )
        elif total:
            self.channel_info_var.set(f"{total} input channels detected.")
        else:
            self.channel_info_var.set(
                "Device channel count unknown; selections are preserved."
            )

    def _populate_channel_listbox(
        self, listbox: tk.Listbox, selected: list[int], total: int, kind: str
    ) -> None:
        values = [str(i) for i in range(total)]
        current = listbox.get(0, tk.END)
        if list(current) != values:
            listbox.delete(0, tk.END)
            for value in values:
                listbox.insert(tk.END, value)
        cleaned = sorted({idx for idx in selected if 0 <= idx < total})
        listbox.selection_clear(0, tk.END)
        for idx in cleaned:
            listbox.selection_set(idx)
        self._update_channel_var(kind, cleaned)

    def _on_channel_select(self, kind: str) -> None:
        listbox = self.mic_listbox if kind == "mic" else self.sys_listbox
        try:
            selected = [
                int(listbox.get(i)) for i in listbox.curselection()  # type: ignore[arg-type]
            ]
        except ValueError:
            selected = []
        self._update_channel_var(kind, sorted(selected))

    def _update_channel_var(self, kind: str, values: list[int]) -> None:
        var = self.mic_ch_var if kind == "mic" else self.sys_ch_var
        formatted = ",".join(str(v) for v in values)
        if var.get() != formatted:
            var.set(formatted)

    def _browse_dir(self) -> None:
        d = filedialog.askdirectory(initialdir=self.var_outdir.get() or str(Path.cwd()))
        if d:
            self.var_outdir.set(d)

    def _parse_indices(self, s: str) -> list[int]:
        try:
            return [int(x.strip()) for x in s.split(",") if x.strip() != ""]
        except Exception:
            raise ValueError(
                "Channel indices must be a comma-separated list of integers, e.g. '0' or '1,2'"
            )

    # ------- Transcription helpers -------
    def _refresh_transcription_list(self) -> None:
        if not hasattr(self, "transcription_tree"):
            return
        directory = Path(self.var_outdir.get()).expanduser()
        files = list_recordings(directory)
        tree = self.transcription_tree
        for item in tree.get_children():
            tree.delete(item)
        self._transcription_index.clear()
        for path in files:
            try:
                stat = path.stat()
                modified = self._format_mtime(stat.st_mtime)
                size = self._format_bytes(stat.st_size)
            except Exception:
                modified = "?"
                size = "?"
            iid = tree.insert(
                "",
                "end",
                values=(path.name, modified, size),
            )
            self._transcription_index[iid] = path
        if files:
            first = tree.get_children()[0]
            tree.selection_set(first)
            tree.focus(first)
            self.transcription_status_var.set("Select a recording to transcribe.")
        else:
            self.transcription_status_var.set(
                "No recordings found in the output folder."
            )
        self._update_transcription_buttons()

    def _on_transcription_select(self) -> None:
        path = self._get_selected_recording()
        if path is not None:
            self.transcription_status_var.set(f"Ready to transcribe {path.name}.")
        self._update_transcription_buttons()

    def _get_selected_recording(self) -> Path | None:
        if not hasattr(self, "transcription_tree"):
            return None
        selection = self.transcription_tree.selection()
        if not selection:
            return None
        return self._transcription_index.get(selection[0])

    def _start_transcription(self) -> None:
        if not hasattr(self, "transcription_tree"):
            return
        if self._transcription_thread is not None and self._transcription_thread.is_alive():
            return
        path = self._get_selected_recording()
        if path is None:
            messagebox.showinfo("No recording", "Select a recording first.")
            return
        self._last_transcript_path = None
        self._show_transcription_text("")
        self.transcription_status_var.set(f"Transcribing {path.name}…")
        self._set_transcription_running(True)
        thread = threading.Thread(
            target=self._run_transcription_thread,
            args=(path,),
            daemon=True,
        )
        self._transcription_thread = thread
        thread.start()

    def _run_transcription_thread(self, audio_path: Path) -> None:
        try:
            result = transcribe_recording(
                audio_path,
                cmd=self.dictation_wispr_cmd.get() or "whisper",
                model=self.model_var.get() or None,
                debug=_dbg,
            )
        except Exception as exc:  # noqa: BLE001
            _dbg(f"transcription failed: {exc}")
            self.after(
                0,
                lambda err=exc: self._finish_transcription_error(audio_path, err),
            )
            return
        self.after(0, lambda: self._finish_transcription_success(result))

    def _finish_transcription_success(
        self, result: RecordingTranscriptionResult
    ) -> None:
        self._transcription_thread = None
        self._set_transcription_running(False)
        text = result.transcript or ""
        self._show_transcription_text(text)
        self._last_transcript_path = result.output_path
        if result.output_path and result.output_path.exists():
            self.transcription_status_var.set(
                f"Transcript saved to {result.output_path.name}"
            )
        elif text:
            self.transcription_status_var.set("Transcript ready (not saved).")
        else:
            self.transcription_status_var.set("Transcript appears to be empty.")
        self._update_transcription_buttons()

    def _finish_transcription_error(self, audio_path: Path, error: Exception) -> None:
        self._transcription_thread = None
        self._set_transcription_running(False)
        self.transcription_status_var.set(f"Transcription failed: {error}")
        messagebox.showerror(
            "Transcription failed",
            f"Could not transcribe '{audio_path.name}':\n{error}",
        )
        self._update_transcription_buttons()

    def _set_transcription_running(self, running: bool) -> None:
        if running:
            self.transcription_progress.start(12)
            self.btn_transcribe.configure(state="disabled")
            self.btn_refresh_transcripts.configure(state="disabled")
            self.btn_open_transcript.configure(state="disabled")
            self.btn_copy_transcript.configure(state="disabled")
        else:
            self.transcription_progress.stop()
            self.btn_refresh_transcripts.configure(state="normal")
            self._update_transcription_buttons()

    def _update_transcription_buttons(self) -> None:
        if not hasattr(self, "btn_transcribe"):
            return
        running = (
            self._transcription_thread is not None
            and self._transcription_thread.is_alive()
        )
        has_selection = self._get_selected_recording() is not None
        self.btn_transcribe.configure(
            state="normal" if has_selection and not running else "disabled"
        )
        has_text = bool(self._get_transcription_text())
        self.btn_copy_transcript.configure(
            state="normal" if has_text and not running else "disabled"
        )
        path = self._last_transcript_path
        self.btn_open_transcript.configure(
            state="normal"
            if (not running and path is not None and path.exists())
            else "disabled"
        )

    def _show_transcription_text(self, text: str) -> None:
        if not hasattr(self, "transcription_text"):
            return
        self.transcription_text.configure(state="normal")
        self.transcription_text.delete("1.0", tk.END)
        if text:
            self.transcription_text.insert("1.0", text)
        self.transcription_text.configure(state="disabled")

    def _get_transcription_text(self) -> str:
        if not hasattr(self, "transcription_text"):
            return ""
        return self.transcription_text.get("1.0", tk.END).strip()

    def _copy_transcript(self) -> None:
        text = self._get_transcription_text()
        if not text:
            messagebox.showinfo("No transcript", "Transcribe a recording first.")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.transcription_status_var.set("Transcript copied to clipboard.")

    def _open_transcript(self) -> None:
        path = self._last_transcript_path
        if path is None or not path.exists():
            messagebox.showinfo("Transcript unavailable", "Generate a transcript first.")
            return
        try:
            subprocess.run(["open", str(path)], check=False)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Open transcript", str(exc))

    def _open_output_dir(self) -> None:
        directory = Path(self.var_outdir.get()).expanduser()
        try:
            directory.mkdir(parents=True, exist_ok=True)
            subprocess.run(["open", str(directory)], check=False)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Open folder", str(exc))

    @staticmethod
    def _format_mtime(ts: float) -> str:
        try:
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
        except Exception:
            return "?"

    @staticmethod
    def _format_bytes(size: int) -> str:
        units = ["B", "KB", "MB", "GB", "TB"]
        val = float(size)
        for unit in units:
            if abs(val) < 1024.0:
                return f"{val:.1f} {unit}"
            val /= 1024.0
        return f"{val:.1f} PB"

    def _toggle(self) -> None:
        if self.rec.is_running():
            self._stop()
        else:
            self._start()

    def _start(self) -> None:
        try:
            dev = self.device_var.get()
            if not dev:
                messagebox.showerror("No device", "Please select an input device.")
                return
            mic_ch = self._parse_indices(self.mic_ch_var.get())
            sys_ch = self._parse_indices(self.sys_ch_var.get())
            outdir = Path(self.var_outdir.get()).expanduser()
            outdir.mkdir(parents=True, exist_ok=True)

            cfg = RecorderConfig(
                device_name=dev,
                sample_rate=int(self.var_wav_sr.get()),
                mic_channels=mic_ch,
                system_channels=sys_ch,
                output_dir=outdir,
                mic_filename=self.var_mic_file.get(),
                system_filename=self.var_sys_file.get(),
                mixed_filename=self.var_mix_file.get(),
                outputs=OutputSelection(
                    mic=self.var_mic.get(),
                    system=self.var_sys.get(),
                    mixed_stereo=self.var_mix.get(),
                ),
                file_format=self.var_format.get(),
                wav_bit_depth=int(self.var_wav_bd.get()),
                mp3_bitrate_kbps=int(self.var_mp3_kbps.get()),
            )
            if not (cfg.outputs.mic or cfg.outputs.system or cfg.outputs.mixed_stereo):
                messagebox.showerror("No outputs", "Enable at least one output.")
                return

            self.rec.start(cfg)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Failed to start", str(e))
            return

        if self.var_sounds.get():
            self._play_sound("Glass")
        self._set_controls_enabled(False)
        self.btn.configure(text="⏹ Stop")
        self.status_var.set("Recording…")
        self._show_overlay()
        self._schedule_overlay_update()

    def _stop(self) -> None:
        try:
            self.rec.stop()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Error while stopping", str(e))
        finally:
            if self.var_sounds.get():
                self._play_sound("Submarine")
            self._hide_overlay()
            self._set_controls_enabled(True)
            self.btn.configure(text="🔴 Record")
            self.status_var.set("Saved")

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = "!disabled" if enabled else "disabled"
        for w in [
            self.device_cb,
            self.entry_outdir,
        ]:
            w.state([state])

    def _fit_to_content(self) -> None:
        # Size window to fit current content and allow resizing
        self.update_idletasks()
        w = max(600, self.winfo_reqwidth() + 20)
        h = max(520, self.winfo_reqheight() + 20)
        self.minsize(w, h)
        self.geometry(f"{w}x{h}")

    # ------- Overlay -------
    def _create_overlay(self) -> None:
        self._overlay = tk.Toplevel(self)
        self._overlay.withdraw()
        self._overlay.overrideredirect(True)
        self._overlay.attributes("-topmost", True)
        self._overlay.attributes("-alpha", 0.9)

        # Use classic Tk widgets so we can query/set background reliably
        bg = self._overlay.cget("bg")
        frame = tk.Frame(self._overlay, bg=bg)
        frame.pack(fill="both", expand=True)
        canvas = tk.Canvas(
            frame, width=18, height=18, highlightthickness=0, bg=bg, bd=0
        )
        canvas.pack(side="left", padx=8, pady=6)
        canvas.create_oval(2, 2, 16, 16, fill="#e02424", outline="")
        tk.Label(frame, textvariable=self._overlay_timer_var, bg=bg).pack(
            side="left", padx=6
        )

    def _place_overlay_top_right(self) -> None:
        if not self._overlay:
            return
        width, height = 120, 32
        x = self.winfo_screenwidth() - width - 20
        y = 20
        self._overlay.geometry(f"{width}x{height}+{x}+{y}")

    def _show_overlay(self) -> None:
        if not self._overlay:
            return
        self._place_overlay_top_right()
        self._overlay.deiconify()

    def _hide_overlay(self) -> None:
        if not self._overlay:
            return
        self._overlay.withdraw()
        if self._overlay_job is not None:
            try:
                self.after_cancel(self._overlay_job)
            except Exception:
                pass
            self._overlay_job = None
        self._overlay_timer_var.set("00:00")

    def _schedule_overlay_update(self) -> None:
        secs = self.rec.elapsed_seconds()
        mm = secs // 60
        ss = secs % 60
        self._overlay_timer_var.set(f"{mm:02d}:{ss:02d}")
        self._overlay_job = self.after(1000, self._schedule_overlay_update)

    # ------- Settings binding -------
    def _bind_setting_traces(self) -> None:
        # Prevent floods during initial setup
        self._saving_suspended = True
        try:

            def bind(var, setter):
                var.trace_add("write", lambda *_: setter())

            bind(
                self.device_var,
                lambda: self._save_field("device_name", self.device_var.get()),
            )
            bind(
                self.mic_ch_var,
                lambda: (
                    self._save_field("mic_channels", self.mic_ch_var.get()),
                    self._update_storage_estimate(),
                ),
            )
            bind(
                self.sys_ch_var,
                lambda: (
                    self._save_field("system_channels", self.sys_ch_var.get()),
                    self._update_storage_estimate(),
                ),
            )
            bind(
                self.var_outdir,
                lambda: (
                    self._save_field("output_dir", self.var_outdir.get()),
                    self._refresh_transcription_list(),
                ),
            )
            bind(
                self.var_mic_file,
                lambda: self._save_field("mic_filename", self.var_mic_file.get()),
            )
            bind(
                self.var_sys_file,
                lambda: self._save_field("system_filename", self.var_sys_file.get()),
            )
            bind(
                self.var_mix_file,
                lambda: self._save_field("mixed_filename", self.var_mix_file.get()),
            )
            bind(
                self.var_mic,
                lambda: (
                    self._save_field("output_mic", self.var_mic.get()),
                    self._update_storage_estimate(),
                ),
            )
            bind(
                self.var_sys,
                lambda: (
                    self._save_field("output_system", self.var_sys.get()),
                    self._update_storage_estimate(),
                ),
            )
            bind(
                self.var_mix,
                lambda: (
                    self._save_field("output_mixed", self.var_mix.get()),
                    self._update_storage_estimate(),
                ),
            )
            bind(
                self.enable_hotkey,
                lambda: self._save_field("enable_hotkey", self.enable_hotkey.get()),
            )
            bind(
                self.hotkey_var,
                lambda: self._save_field("hotkey", self.hotkey_var.get()),
            )
            bind(
                self.var_sounds,
                lambda: self._save_field("play_sounds", self.var_sounds.get()),
            )
            # Dictation bindings
            bind(
                self.dictation_enable,
                lambda: self._save_field(
                    "dictation_enable", self.dictation_enable.get()
                ),
            )
            bind(
                self.dictation_hotkey,
                lambda: self._save_field(
                    "dictation_hotkey", self.dictation_hotkey.get()
                ),
            )
            bind(
                self.dictation_wispr_cmd,
                lambda: self._save_field(
                    "dictation_wispr_cmd", self.dictation_wispr_cmd.get()
                ),
            )
            bind(
                self.model_var,
                lambda: (
                    self._save_field("transcriber_model", self.model_var.get()),
                    self._restart_dictation_if_enabled(),
                ),
            )

            # Encoding bindings
            bind(self.var_format, self._on_format_change)
            bind(
                self.var_wav_sr,
                lambda: (
                    self._save_field("wav_sample_rate", int(self.var_wav_sr.get())),
                    self._update_storage_estimate(),
                ),
            )
            bind(
                self.var_wav_bd,
                lambda: (
                    self._save_field("wav_bit_depth", int(self.var_wav_bd.get())),
                    self._update_storage_estimate(),
                ),
            )
            bind(
                self.var_mp3_kbps,
                lambda: (
                    self._save_field("mp3_bitrate_kbps", int(self.var_mp3_kbps.get())),
                    self._update_storage_estimate(),
                ),
            )
            bind(
                self.var_flac_level,
                lambda: (
                    self._save_field("flac_level", int(self.var_flac_level.get())),
                    self._update_storage_estimate(),
                ),
            )
        finally:
            self._saving_suspended = False

    def _save_field(self, key: str, value) -> None:  # noqa: ANN001
        if self._saving_suspended:
            return
        try:
            setattr(self._settings, key, value)
            save_settings(self._settings)
        except Exception:
            pass

    def _update_settings_from_ui(self) -> None:
        # Gather all fields from current UI variables
        self._settings.device_name = self.device_var.get()
        self._settings.mic_channels = self.mic_ch_var.get()
        self._settings.system_channels = self.sys_ch_var.get()
        self._settings.output_dir = self.var_outdir.get()
        self._settings.mic_filename = self.var_mic_file.get()
        self._settings.system_filename = self.var_sys_file.get()
        self._settings.mixed_filename = self.var_mix_file.get()
        self._settings.output_mic = self.var_mic.get()
        self._settings.output_system = self.var_sys.get()
        self._settings.output_mixed = self.var_mix.get()
        # Encoding
        self._settings.file_format = (
            getattr(self, "var_format").get() if hasattr(self, "var_format") else "wav"
        )
        self._settings.wav_sample_rate = (
            int(getattr(self, "var_wav_sr").get())
            if hasattr(self, "var_wav_sr")
            else 48000
        )
        self._settings.wav_bit_depth = (
            int(getattr(self, "var_wav_bd").get())
            if hasattr(self, "var_wav_bd")
            else 16
        )
        self._settings.mp3_bitrate_kbps = (
            int(getattr(self, "var_mp3_kbps").get())
            if hasattr(self, "var_mp3_kbps")
            else 192
        )
        self._settings.flac_level = (
            int(getattr(self, "var_flac_level").get())
            if hasattr(self, "var_flac_level")
            else 5
        )
        # Hotkey and alerts
        self._settings.enable_hotkey = self.enable_hotkey.get()
        self._settings.hotkey = self.hotkey_var.get()
        self._settings.play_sounds = self.var_sounds.get()
        # Dictation
        self._settings.dictation_enable = self.dictation_enable.get()
        self._settings.dictation_hotkey = self.dictation_hotkey.get()
        self._settings.dictation_wispr_cmd = self.dictation_wispr_cmd.get()
        self._settings.transcriber_model = self.model_var.get()

    def _apply_device_selection(self) -> None:
        # Ensure device combobox reflects saved value when available
        cur = self._settings.device_name
        names = list(self.device_cb["values"]) or []
        if cur and cur in names:
            self.device_cb.set(cur)
            self._refresh_channel_selectors()

    def _on_format_change(self) -> None:
        fmt = self.var_format.get()
        self._save_field("file_format", fmt)
        # Update filename extensions to match selected format
        from .common.encoding import format_default_extension, replace_extension

        new_ext = format_default_extension(fmt)
        self.var_mic_file.set(replace_extension(self.var_mic_file.get(), new_ext))
        self.var_sys_file.set(replace_extension(self.var_sys_file.get(), new_ext))
        self.var_mix_file.set(replace_extension(self.var_mix_file.get(), new_ext))
        self._refresh_encoding_controls()
        self._update_storage_estimate()

    def _refresh_encoding_controls(self) -> None:
        # Clear previous placements
        for w in [
            self.wav_sr_cb,
            self.wav_bd_cb,
            self.mp3_kbps_cb,
            self.flac_level_cb,
            self.estimate_lbl,
        ]:
            try:
                w.grid_forget()
            except Exception:
                pass
        # Place controls based on selected format
        fmt = self.var_format.get()
        # the combobox is at (0,1); we add settings starting at column 2
        if fmt == "wav":
            ttk.Label(self.cb_format.master, text="Sample rate:").grid(
                row=0, column=2, sticky="e"
            )
            self.wav_sr_cb.grid(row=0, column=3, sticky="w", padx=4)
            ttk.Label(self.cb_format.master, text="Bit depth:").grid(
                row=0, column=4, sticky="e"
            )
            self.wav_bd_cb.grid(row=0, column=5, sticky="w", padx=4)
        elif fmt == "mp3":
            ttk.Label(self.cb_format.master, text="Bitrate:").grid(
                row=0, column=2, sticky="e"
            )
            self.mp3_kbps_cb.grid(row=0, column=3, sticky="w", padx=4)
        elif fmt == "flac":
            ttk.Label(self.cb_format.master, text="Level:").grid(
                row=0, column=2, sticky="e"
            )
            self.flac_level_cb.grid(row=0, column=3, sticky="w", padx=4)
        # Storage estimate label at end
        ttk.Label(self.cb_format.master, text="Estimated per minute:").grid(
            row=0, column=6, sticky="e", padx=(12, 4)
        )
        self.estimate_lbl.grid(row=0, column=7, sticky="w")

    def _update_storage_estimate(self) -> None:
        from .common.encoding import (
            wav_bytes_per_minute,
            mp3_bytes_per_minute,
            flac_bytes_per_minute,
            human_readable_bytes,
        )

        # Determine enabled outputs and channel counts
        total = 0
        # mic: writer outputs stereo
        if self.var_mic.get():
            if self.var_format.get() == "wav":
                total += wav_bytes_per_minute(
                    2, int(self.var_wav_sr.get()), int(self.var_wav_bd.get())
                )
            elif self.var_format.get() == "mp3":
                total += mp3_bytes_per_minute(int(self.var_mp3_kbps.get()))
            else:
                total += flac_bytes_per_minute(
                    2,
                    int(self.var_wav_sr.get()),
                    int(self.var_wav_bd.get()),
                    int(self.var_flac_level.get()),
                )
        # system: channel count from mapping
        if self.var_sys.get():
            try:
                chs = len(self._parse_indices(self.sys_ch_var.get()))
            except Exception:
                chs = 2
            if self.var_format.get() == "wav":
                total += wav_bytes_per_minute(
                    chs, int(self.var_wav_sr.get()), int(self.var_wav_bd.get())
                )
            elif self.var_format.get() == "mp3":
                total += mp3_bytes_per_minute(int(self.var_mp3_kbps.get()))
            else:
                total += flac_bytes_per_minute(
                    chs,
                    int(self.var_wav_sr.get()),
                    int(self.var_wav_bd.get()),
                    int(self.var_flac_level.get()),
                )
        # mixed stereo
        if self.var_mix.get():
            if self.var_format.get() == "wav":
                total += wav_bytes_per_minute(
                    2, int(self.var_wav_sr.get()), int(self.var_wav_bd.get())
                )
            elif self.var_format.get() == "mp3":
                total += mp3_bytes_per_minute(int(self.var_mp3_kbps.get()))
            else:
                total += flac_bytes_per_minute(
                    2,
                    int(self.var_wav_sr.get()),
                    int(self.var_wav_bd.get()),
                    int(self.var_flac_level.get()),
                )
        self.estimate_var.set(human_readable_bytes(total))

    # ------- Hotkey -------
    def _toggle_hotkey_listener(self) -> None:
        if self.enable_hotkey.get():
            self._start_hotkey_listener()
        else:
            self._stop_hotkey_listener()

    def _restart_hotkey_if_enabled(self) -> None:
        if self.enable_hotkey.get():
            self._stop_hotkey_listener()
            self._start_hotkey_listener()

    def _start_hotkey_listener(self) -> None:
        # Prefer a Quartz-based listener on macOS to avoid TIS calls on background threads
        use_pynput = self._force_pynput or sys.platform != "darwin"

        if not use_pynput and sys.platform == "darwin":
            try:
                self._start_quartz_hotkey()
                return
            except Exception:
                # Fall back to pynput if Quartz path fails
                pass

        # Fallback: pynput GlobalHotKeys
        try:
            from pynput import keyboard  # type: ignore
        except Exception:
            messagebox.showwarning(
                "Hotkey unavailable",
                "Global hotkeys require 'pynput' or macOS Quartz bindings.\n"
                "Install with: pip install pynput",
            )
            self.enable_hotkey.set(False)
            return

        if self._hotkey_listener is not None:
            try:
                # Support both custom and pynput listeners
                stop = getattr(self._hotkey_listener, "stop", None)
                if callable(stop):
                    stop()
            except Exception:
                pass
            self._hotkey_listener = None

        hotkey_str = self.hotkey_var.get().strip() or "cmd+shift+r"
        # Ensure Tkinter interactions happen on the main thread; pynput callbacks run in a worker thread
        mapping = {
            self._format_pynput_hotkey(hotkey_str): (
                lambda: self.after(0, self._toggle)
            )
        }
        self._hotkey_listener = keyboard.GlobalHotKeys(mapping)
        self._hotkey_listener.start()

    def _stop_hotkey_listener(self) -> None:
        if self._hotkey_listener is not None:
            try:
                stop = getattr(self._hotkey_listener, "stop", None)
                if callable(stop):
                    stop()
            except Exception:
                pass
            self._hotkey_listener = None

    def _format_pynput_hotkey(self, s: str) -> str:
        # Convert 'cmd+shift+r' -> '<cmd>+<shift>+r'
        parts = [p.strip().lower() for p in s.replace(" ", "").split("+") if p]
        out = []
        for p in parts:
            if p in {"cmd", "command", "meta"}:
                out.append("<cmd>")
            elif p in {"ctrl", "control"}:
                out.append("<ctrl>")
            elif p in {"alt", "option"}:
                out.append("<alt>")
            elif p in {"shift"}:
                out.append("<shift>")
            else:
                out.append(p)
        return "+".join(out)

    # ------- macOS Quartz hotkey (fallback-free, avoids TIS on worker threads) -------
    def _start_quartz_hotkey(self) -> None:
        try:
            import Quartz  # type: ignore
        except Exception:
            raise

        hotkey = (
            (self.hotkey_var.get().strip() or "cmd+shift+r").lower().replace(" ", "")
        )
        mods_required: int = 0
        key_required: Optional[int] = None

        MODS = {
            "cmd": Quartz.kCGEventFlagMaskCommand,
            "command": Quartz.kCGEventFlagMaskCommand,
            "meta": Quartz.kCGEventFlagMaskCommand,
            "ctrl": Quartz.kCGEventFlagMaskControl,
            "control": Quartz.kCGEventFlagMaskControl,
            "alt": Quartz.kCGEventFlagMaskAlternate,
            "option": Quartz.kCGEventFlagMaskAlternate,
            "shift": Quartz.kCGEventFlagMaskShift,
        }

        # mac virtual keycodes for a-z and 0-9
        KEYCODES = {
            "a": 0,
            "s": 1,
            "d": 2,
            "f": 3,
            "h": 4,
            "g": 5,
            "z": 6,
            "x": 7,
            "c": 8,
            "v": 9,
            "b": 11,
            "q": 12,
            "w": 13,
            "e": 14,
            "r": 15,
            "y": 16,
            "t": 17,
            "1": 18,
            "2": 19,
            "3": 20,
            "4": 21,
            "6": 22,
            "5": 23,
            "9": 25,
            "7": 26,
            "8": 28,
            "0": 29,
            "o": 31,
            "u": 32,
            "i": 34,
            "p": 35,
            "l": 37,
            "j": 38,
            "k": 40,
            "n": 45,
            "m": 46,
        }

        parts = [p for p in hotkey.split("+") if p]
        for p in parts:
            if p in MODS:
                mods_required |= MODS[p]
            elif p in KEYCODES:
                key_required = KEYCODES[p]
            else:
                raise ValueError(f"Unsupported hotkey token: {p}")

        if key_required is None:
            raise ValueError("Hotkey must include a non-modifier key, e.g. 'r'")

        fired = {"value": False}

        def callback(proxy, type_, event, refcon):  # noqa: ANN001
            try:
                if type_ == Quartz.kCGEventKeyDown:
                    flags = Quartz.CGEventGetFlags(event)
                    kc = Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGKeyboardEventKeycode
                    )
                    if (flags & mods_required) == mods_required and kc == key_required:
                        if not fired["value"]:
                            fired["value"] = True
                            # back to Tk main thread
                            self.after(0, self._toggle)
                elif type_ == Quartz.kCGEventKeyUp:
                    kc = Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGKeyboardEventKeycode
                    )
                    if kc == key_required:
                        fired["value"] = False
            except Exception:
                pass
            return event

        mask = (1 << Quartz.kCGEventKeyDown) | (1 << Quartz.kCGEventKeyUp)

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
                "Failed to create event tap; check Accessibility permissions in System Settings"
            )

        run_loop_source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)

        def run_loop_thread() -> None:
            rl = Quartz.CFRunLoopGetCurrent()
            Quartz.CFRunLoopAddSource(rl, run_loop_source, Quartz.kCFRunLoopCommonModes)
            Quartz.CGEventTapEnable(tap, True)
            Quartz.CFRunLoopRun()

        t = threading.Thread(target=run_loop_thread, name="HotkeyQuartz", daemon=True)
        t.start()

        class _QuartzListener:
            def stop(self_nonlocal) -> None:  # noqa: ANN001
                try:
                    Quartz.CGEventTapEnable(tap, False)
                except Exception:
                    pass
                try:
                    Quartz.CFRunLoopSourceInvalidate(run_loop_source)
                except Exception:
                    pass
                # Best effort: post a stop to the thread's run loop
                try:
                    Quartz.CFRunLoopStop(Quartz.CFRunLoopGetCurrent())
                except Exception:
                    pass

        self._hotkey_listener = _QuartzListener()

    # ------- Sounds -------
    def _play_sound(self, name: str) -> None:
        # Use macOS system sounds via afplay to avoid extra deps
        import subprocess

        sound_path = f"/System/Library/Sounds/{name}.aiff"
        try:
            subprocess.Popen(["afplay", sound_path])
        except Exception:
            pass

    # ------- Dictation agent -------
    def _toggle_dictation_agent(self) -> None:
        if self.dictation_enable.get():
            self._start_dictation_agent()
        else:
            self._stop_dictation_agent()

    def _restart_dictation_if_enabled(self) -> None:
        if self.dictation_enable.get():
            self._stop_dictation_agent()
            self._start_dictation_agent()

    def _start_dictation_agent(self) -> None:
        if getattr(self, "_force_pynput", False):
            messagebox.showinfo(
                "Dictation unavailable",
                "Dictation requires macOS Accessibility access and the Quartz APIs. "
                "Unset TALKTALLY_FORCE_PYNPUT to enable dictation.",
            )
            self.dictation_enable.set(False)
            return
        try:
            # Lazy import to avoid importing mac-specific modules during tests
            from .dictation import DictationAgent  # local import

            if self._dictation is None:
                self._dictation = DictationAgent(
                    self._settings, ui_dispatch=lambda f: self.after(0, f)
                )
            else:
                # type: ignore[attr-defined]
                self._dictation.restart(self._settings)  # type: ignore[call-arg]
            # type: ignore[attr-defined]
            self._dictation.start()  # type: ignore[call-arg]
        except Exception:
            # Non-fatal if dictation cannot start (e.g., no permissions)
            pass

    def _stop_dictation_agent(self) -> None:
        try:
            if self._dictation is not None:
                # type: ignore[attr-defined]
                self._dictation.stop()  # type: ignore[call-arg]
        except Exception:
            pass

    # ------- Lifecycle -------
    def _on_close(self) -> None:
        # Persist latest settings (including geometry) before closing
        try:
            self._update_settings_from_ui()
            save_settings(self._settings)
        except Exception:
            pass

        self._stop_hotkey_listener()
        try:
            if self.rec.is_running():
                self.rec.stop()
        except Exception:
            pass
        try:
            self._stop_dictation_agent()
        except Exception:
            pass
        self.destroy()


def main() -> None:
    app = TalkTallyApp()
    app.mainloop()


if __name__ == "__main__":
    main()
