"""Audio recording engine for TalkTally.

Encapsulates sounddevice stream, channel routing, and optional output writers.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd
import soundfile as sf

from .common.fs import unique_path, prefixed_with_end_timestamp


# Sentinel used to signal writer threads to finish after draining
_SENTINEL: None = None


@dataclass
class OutputSelection:
    mic: bool = True
    system: bool = True
    mixed_stereo: bool = True  # downmix mic+system to stereo


@dataclass
class RecorderConfig:
    device_name: str
    sample_rate: int = 48_000
    blocksize: int = 1024
    mic_channels: list[int] = None  # zero-based indices
    system_channels: list[int] = None  # zero-based indices
    output_dir: Path = Path(".")
    mic_filename: str = "mic.wav"
    system_filename: str = "system.wav"
    mixed_filename: str = "mixed.wav"
    outputs: OutputSelection = field(default_factory=OutputSelection)

    def __post_init__(self) -> None:
        # Default mapping for an Aggregate Device with BlackHole 2ch (system audio) + built-in mic:
        # - System audio on channels 0 and 1 (BlackHole stereo)
        # - Mic on channel 2
        if self.mic_channels is None:
            self.mic_channels = [2]
        if self.system_channels is None:
            self.system_channels = [0, 1]
        self.output_dir = Path(self.output_dir)


class AudioRecorder:
    """Manage an audio recording session from an Aggregate Device.

    Usage:
      rec = AudioRecorder()
      rec.start(config)
      ...
      rec.stop()
    """

    def __init__(self) -> None:
        self._device_id: Optional[int] = None
        self._total_in: Optional[int] = None
        self._stop = threading.Event()
        self._stream: Optional[sd.InputStream] = None

        # Queues and files for optional outputs
        self._q_mic: Optional[queue.Queue] = None
        self._q_sys: Optional[queue.Queue] = None
        self._q_mix: Optional[queue.Queue] = None
        self._t_mic: Optional[threading.Thread] = None
        self._t_sys: Optional[threading.Thread] = None
        self._t_mix: Optional[threading.Thread] = None
        self._f_mic: Optional[sf.SoundFile] = None
        self._f_sys: Optional[sf.SoundFile] = None
        self._f_mix: Optional[sf.SoundFile] = None
        self._start_time: Optional[float] = None

        # Paths of open output files (for post-stop renaming)
        self._p_mic: Optional[Path] = None
        self._p_sys: Optional[Path] = None
        self._p_mix: Optional[Path] = None

        self._cfg: Optional[RecorderConfig] = None

    # ---------- Public API ----------
    def start(self, cfg: RecorderConfig) -> None:
        if self._stream is not None:
            raise RuntimeError("Recorder already running")
        self._cfg = cfg
        self._device_id, devinfo = _find_device_id_by_name(cfg.device_name)
        self._total_in = int(devinfo["max_input_channels"])  # type: ignore[index]
        needed = max(cfg.mic_channels + cfg.system_channels) + 1
        if self._total_in < needed:
            raise RuntimeError(
                f"Device '{cfg.device_name}' has {self._total_in} input channels, "
                f"but mapping requires {needed}."
            )

        # Prepare queues and files based on selection
        if cfg.outputs.mic:
            self._q_mic = queue.Queue(maxsize=100)
            mic_path = unique_path(cfg.output_dir / cfg.mic_filename)
            self._p_mic = Path(mic_path)
            self._f_mic = sf.SoundFile(
                mic_path,
                mode="w",
                samplerate=cfg.sample_rate,
                channels=2,
                subtype="PCM_16",
            )
            self._t_mic = threading.Thread(
                target=self._writer, args=(self._q_mic, self._f_mic), daemon=False
            )
            self._t_mic.start()

        if cfg.outputs.system:
            self._q_sys = queue.Queue(maxsize=100)
            sys_path = unique_path(cfg.output_dir / cfg.system_filename)
            self._p_sys = Path(sys_path)
            self._f_sys = sf.SoundFile(
                sys_path,
                mode="w",
                samplerate=cfg.sample_rate,
                channels=len(cfg.system_channels),
                subtype="PCM_16",
            )
            self._t_sys = threading.Thread(
                target=self._writer, args=(self._q_sys, self._f_sys), daemon=False
            )
            self._t_sys.start()

        if cfg.outputs.mixed_stereo:
            self._q_mix = queue.Queue(maxsize=100)
            mix_path = unique_path(cfg.output_dir / cfg.mixed_filename)
            self._p_mix = Path(mix_path)
            self._f_mix = sf.SoundFile(
                mix_path,
                mode="w",
                samplerate=cfg.sample_rate,
                channels=2,
                subtype="PCM_16",
            )
            self._t_mix = threading.Thread(
                target=self._writer, args=(self._q_mix, self._f_mix), daemon=False
            )
            self._t_mix.start()

        # Start stream
        self._stop.clear()
        self._stream = sd.InputStream(
            device=self._device_id,
            channels=self._total_in,
            samplerate=cfg.sample_rate,
            blocksize=cfg.blocksize,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()
        self._start_time = time.time()

    def stop(self) -> None:
        if self._stream is None:
            return
        self._stop.set()
        end_ts = time.time()
        try:
            self._stream.stop()
        finally:
            self._stream.close()
            self._stream = None

        # Signal writer threads to finish after draining
        for q in (self._q_mic, self._q_sys, self._q_mix):
            if q is not None:
                try:
                    q.put_nowait(_SENTINEL)
                except Exception:
                    pass

        # Join writer threads to avoid races with file.close()
        for t in (self._t_mic, self._t_sys, self._t_mix):
            if t is not None:
                t.join(timeout=2.0)

        # Now it is safe to close files
        for f in (self._f_mic, self._f_sys, self._f_mix):
            if f is not None:
                f.close()

        # After files are closed, rename them with end timestamp prefix
        for p in (self._p_mic, self._p_sys, self._p_mix):
            if p is not None and p.exists():
                try:
                    target = prefixed_with_end_timestamp(p, end_ts)
                    p.rename(target)
                except Exception as e:  # pragma: no cover - best-effort rename
                    print(f"Failed to rename {p} with timestamp: {e}", flush=True)

        self._f_mic = self._f_sys = self._f_mix = None
        self._q_mic = self._q_sys = self._q_mix = None
        self._t_mic = self._t_sys = self._t_mix = None
        self._p_mic = self._p_sys = self._p_mix = None
        self._cfg = None
        self._start_time = None

    def is_running(self) -> bool:
        return self._stream is not None

    def elapsed_seconds(self) -> int:
        """Return elapsed recording time in whole seconds (0 if not running)."""
        if self._start_time is None:
            return 0
        return max(0, int(time.time() - self._start_time))

    # ---------- Internals ----------
    def _writer(self, q: queue.Queue, outfile: sf.SoundFile) -> None:
        while True:
            try:
                block = q.get(timeout=0.2)
            except queue.Empty:
                if self._stop.is_set():
                    # No more data expected; continue waiting for sentinel or exit soon
                    continue
                else:
                    continue
            if block is _SENTINEL:
                break
            outfile.write(block)

    def _callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:  # type: ignore[override]
        if status:
            print("Audio status:", status, flush=True)
        assert self._cfg is not None
        mic_block = indata[:, self._cfg.mic_channels]
        sys_block = indata[:, self._cfg.system_channels]

        if self._q_mic is not None:
            mic_mono = mic_block.mean(axis=1, keepdims=True)
            mic_stereo = np.concatenate([mic_mono, mic_mono], axis=1)
            mic_stereo = np.clip(mic_stereo, -1.0, 1.0)
            try:
                self._q_mic.put(mic_stereo.copy(), block=False)
            except queue.Full:
                pass
        if self._q_sys is not None:
            try:
                self._q_sys.put(sys_block.copy(), block=False)
            except queue.Full:
                pass
        if self._q_mix is not None:
            mic_mono = mic_block.mean(axis=1, keepdims=True)
            if sys_block.shape[1] == 1:
                sys_l = sys_block
                sys_r = sys_block
            else:
                sys_l = sys_block[:, [0]]
                sys_r = sys_block[:, [1]]
            mixed_l = mic_mono + sys_l
            mixed_r = mic_mono + sys_r
            mixed = np.concatenate([mixed_l, mixed_r], axis=1)
            mixed = np.clip(mixed, -1.0, 1.0)
            try:
                self._q_mix.put(mixed.copy(), block=False)
            except queue.Full:
                pass


def _find_device_id_by_name(name: str) -> tuple[int, dict]:
    devs = sd.query_devices()
    for i, d in enumerate(devs):
        if d.get("name") == name and d.get("max_input_channels", 0) > 0:
            return i, d
    raise RuntimeError(
        f"Input device named '{name}' not found or has no input channels.\n"
        f"Available input devices: {[d['name'] for d in devs if d.get('max_input_channels', 0) > 0]}"
    )


def list_input_devices() -> list[str]:
    """Return names of input-capable devices."""
    devs = sd.query_devices()
    return [d["name"] for d in devs if d.get("max_input_channels", 0) > 0]
