"""Tkinter GUI for TalkTally on macOS.

Features:
- Record/Stop toggle
- Choose outputs (Mic, System, Mixed)
- Choose filenames and output directory; auto-rename if exists
- Select input device by name
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from typing import Optional

from .recorder import AudioRecorder, RecorderConfig, OutputSelection, list_input_devices


class TalkTallyApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("TalkTally Recorder")
        self.resizable(True, True)

        self.rec = AudioRecorder()
        self._overlay: Optional[tk.Toplevel] = None
        self._overlay_timer_var = tk.StringVar(value="00:00")
        self._overlay_job: Optional[str] = None

        self._hotkey_listener = None  # type: ignore[assignment]

        self._build_ui()
        self._create_overlay()
        self._refresh_devices()
        self._fit_to_content()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 6}

        # Record button styling
        style = ttk.Style(self)
        style.configure("Record.TButton", font=("Helvetica", 14), padding=8)

        # Device selection
        dev_frame = ttk.LabelFrame(self, text="Input Device (Aggregate)")
        dev_frame.pack(fill="x", **pad)
        self.device_var = tk.StringVar()
        self.device_cb = ttk.Combobox(dev_frame, textvariable=self.device_var, state="readonly")
        self.device_cb.pack(side="left", fill="x", expand=True, padx=8, pady=8)
        ttk.Button(dev_frame, text="Refresh", command=self._refresh_devices).pack(side="right", padx=8, pady=8)

        # Channel mapping
        ch_frame = ttk.LabelFrame(self, text="Channel Mapping (zero-based indices)")
        ch_frame.pack(fill="x", **pad)
        ttk.Label(ch_frame, text="Mic channels:").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        ttk.Label(ch_frame, text="System channels:").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        self.mic_ch_var = tk.StringVar(value="0")
        self.sys_ch_var = tk.StringVar(value="1,2")
        ttk.Entry(ch_frame, textvariable=self.mic_ch_var, width=20).grid(row=0, column=1, sticky="w", padx=8, pady=4)
        ttk.Entry(ch_frame, textvariable=self.sys_ch_var, width=20).grid(row=1, column=1, sticky="w", padx=8, pady=4)

        # Outputs
        out_frame = ttk.LabelFrame(self, text="Outputs")
        out_frame.pack(fill="x", **pad)
        self.var_mic = tk.BooleanVar(value=True)
        self.var_sys = tk.BooleanVar(value=True)
        self.var_mix = tk.BooleanVar(value=True)
        ttk.Checkbutton(out_frame, text="Mic WAV", variable=self.var_mic).grid(row=0, column=0, sticky="w", padx=8, pady=4)
        ttk.Checkbutton(out_frame, text="System WAV", variable=self.var_sys).grid(row=1, column=0, sticky="w", padx=8, pady=4)
        ttk.Checkbutton(out_frame, text="Downmix (stereo)", variable=self.var_mix).grid(row=2, column=0, sticky="w", padx=8, pady=4)

        ttk.Label(out_frame, text="Mic filename:").grid(row=0, column=1, sticky="e", padx=4)
        ttk.Label(out_frame, text="System filename:").grid(row=1, column=1, sticky="e", padx=4)
        ttk.Label(out_frame, text="Mixed filename:").grid(row=2, column=1, sticky="e", padx=4)
        self.var_mic_file = tk.StringVar(value="mic.wav")
        self.var_sys_file = tk.StringVar(value="system.wav")
        self.var_mix_file = tk.StringVar(value="mixed.wav")
        ttk.Entry(out_frame, textvariable=self.var_mic_file, width=22).grid(row=0, column=2, sticky="w")
        ttk.Entry(out_frame, textvariable=self.var_sys_file, width=22).grid(row=1, column=2, sticky="w")
        ttk.Entry(out_frame, textvariable=self.var_mix_file, width=22).grid(row=2, column=2, sticky="w")

        # Output directory chooser
        dir_frame = ttk.Frame(out_frame)
        dir_frame.grid(row=3, column=0, columnspan=3, sticky="we", padx=8, pady=6)
        ttk.Label(dir_frame, text="Output directory:").pack(side="left")
        self.var_outdir = tk.StringVar(value=str(Path.cwd()))
        self.entry_outdir = ttk.Entry(dir_frame, textvariable=self.var_outdir, width=44)
        self.entry_outdir.pack(side="left", padx=6)
        ttk.Button(dir_frame, text="Browse…", command=self._browse_dir).pack(side="left")

        # Record button + status
        ctrl = ttk.Frame(self)
        ctrl.pack(fill="x", **pad)
        self.btn = ttk.Button(ctrl, text="🔴 Record", style="Record.TButton", command=self._toggle)
        self.btn.pack(side="left", padx=6)
        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(ctrl, textvariable=self.status_var).pack(side="left", padx=12)

        # Hotkey + sounds
        extras = ttk.LabelFrame(self, text="Hotkey & Alerts")
        extras.pack(fill="x", **pad)
        self.enable_hotkey = tk.BooleanVar(value=False)
        self.hotkey_var = tk.StringVar(value="cmd+shift+r")
        ttk.Checkbutton(extras, text="Enable global hotkey", variable=self.enable_hotkey, command=self._toggle_hotkey_listener).grid(row=0, column=0, sticky="w", padx=8, pady=4)
        ttk.Label(extras, text="Hotkey (e.g. cmd+shift+r):").grid(row=0, column=1, sticky="e")
        e = ttk.Entry(extras, textvariable=self.hotkey_var, width=22)
        e.grid(row=0, column=2, sticky="w", padx=6)
        e.bind("<FocusOut>", lambda _e: self._restart_hotkey_if_enabled())

        self.var_sounds = tk.BooleanVar(value=True)
        ttk.Checkbutton(extras, text="Play start/stop sounds", variable=self.var_sounds).grid(row=1, column=0, sticky="w", padx=8, pady=4)
        ttk.Label(extras, text="Indicator overlay shows in top-right while recording").grid(row=1, column=1, columnspan=2, sticky="w", padx=6)

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

    def _browse_dir(self) -> None:
        d = filedialog.askdirectory(initialdir=self.var_outdir.get() or str(Path.cwd()))
        if d:
            self.var_outdir.set(d)

    def _parse_indices(self, s: str) -> list[int]:
        try:
            return [int(x.strip()) for x in s.split(',') if x.strip() != '']
        except Exception:
            raise ValueError("Channel indices must be a comma-separated list of integers, e.g. '0' or '1,2'")

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
        canvas = tk.Canvas(frame, width=18, height=18, highlightthickness=0, bg=bg, bd=0)
        canvas.pack(side="left", padx=8, pady=6)
        canvas.create_oval(2, 2, 16, 16, fill="#e02424", outline="")
        tk.Label(frame, textvariable=self._overlay_timer_var, bg=bg).pack(side="left", padx=6)

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
        try:
            from pynput import keyboard  # type: ignore
        except Exception:
            messagebox.showwarning(
                "Hotkey unavailable",
                "Global hotkeys require the 'pynput' package and macOS Accessibility permission.\n"
                "Install with: pip install pynput",
            )
            self.enable_hotkey.set(False)
            return

        if self._hotkey_listener is not None:
            try:
                self._hotkey_listener.stop()
            except Exception:
                pass
            self._hotkey_listener = None

        hotkey_str = self.hotkey_var.get().strip() or "cmd+shift+r"
        mapping = {self._format_pynput_hotkey(hotkey_str): self._toggle}
        self._hotkey_listener = keyboard.GlobalHotKeys(mapping)
        self._hotkey_listener.start()

    def _stop_hotkey_listener(self) -> None:
        if self._hotkey_listener is not None:
            try:
                self._hotkey_listener.stop()
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

    # ------- Sounds -------
    def _play_sound(self, name: str) -> None:
        # Use macOS system sounds via afplay to avoid extra deps
        import subprocess
        sound_path = f"/System/Library/Sounds/{name}.aiff"
        try:
            subprocess.Popen(["afplay", sound_path])
        except Exception:
            pass

    # ------- Lifecycle -------
    def _on_close(self) -> None:
        self._stop_hotkey_listener()
        try:
            if self.rec.is_running():
                self.rec.stop()
        except Exception:
            pass
        self.destroy()


def main() -> None:
    app = TalkTallyApp()
    app.mainloop()


if __name__ == "__main__":
    main()
