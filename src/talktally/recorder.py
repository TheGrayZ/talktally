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
from .common.encoding import replace_extension


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
    # Encoding/format
    file_format: str = "wav"  # wav|mp3|flac
    wav_bit_depth: int = 16
    mp3_bitrate_kbps: int = 192

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

        # Paths of output files
        self._p_mic: Optional[Path] = None
        self._p_sys: Optional[Path] = None
        self._p_mix: Optional[Path] = None
        # Temp WAV paths for mp3 conversion
        self._tmp_mic: Optional[Path] = None
        self._tmp_sys: Optional[Path] = None
        self._tmp_mix: Optional[Path] = None

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
        import shutil

        def _open_soundfile(path: Path, channels: int) -> sf.SoundFile:
            subtype = "PCM_24" if cfg.wav_bit_depth >= 24 else "PCM_16"
            return sf.SoundFile(
                str(path),
                mode="w",
                samplerate=cfg.sample_rate,
                channels=channels,
                subtype=subtype,
            )

        def _final_path(name: str, ext: str) -> Path:
            return unique_path(cfg.output_dir / replace_extension(name, ext))

        # Determine extension by format
        if cfg.file_format == "wav":
            ext = ".wav"
        elif cfg.file_format == "flac":
            ext = ".flac"
        elif cfg.file_format == "mp3":
            ext = ".mp3"
            if shutil.which("ffmpeg") is None:
                raise RuntimeError("MP3 export requires ffmpeg installed and on PATH")
        else:
            raise ValueError(f"Unsupported file format: {cfg.file_format}")

        if cfg.outputs.mic:
            self._q_mic = queue.Queue(maxsize=100)
            final = _final_path(cfg.mic_filename, ext)
            self._p_mic = final
            if cfg.file_format == "mp3":
                self._tmp_mic = unique_path(final.with_suffix(".tmp.wav"))
                self._f_mic = _open_soundfile(self._tmp_mic, channels=2)
            else:
                self._f_mic = _open_soundfile(final, channels=2)
            self._t_mic = threading.Thread(
                target=self._writer, args=(self._q_mic, self._f_mic), daemon=False
            )
            self._t_mic.start()

        if cfg.outputs.system:
            self._q_sys = queue.Queue(maxsize=100)
            channels = len(cfg.system_channels)
            final = _final_path(cfg.system_filename, ext)
            self._p_sys = final
            if cfg.file_format == "mp3":
                self._tmp_sys = unique_path(final.with_suffix(".tmp.wav"))
                self._f_sys = _open_soundfile(self._tmp_sys, channels=channels)
            else:
                self._f_sys = _open_soundfile(final, channels=channels)
            self._t_sys = threading.Thread(
                target=self._writer, args=(self._q_sys, self._f_sys), daemon=False
            )
            self._t_sys.start()

        if cfg.outputs.mixed_stereo:
            self._q_mix = queue.Queue(maxsize=100)
            final = _final_path(cfg.mixed_filename, ext)
            self._p_mix = final
            if cfg.file_format == "mp3":
                self._tmp_mix = unique_path(final.with_suffix(".tmp.wav"))
                self._f_mix = _open_soundfile(self._tmp_mix, channels=2)
            else:
                self._f_mix = _open_soundfile(final, channels=2)
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

        # If MP3: convert temp WAVs to MP3s
        if self._cfg is not None and self._cfg.file_format == "mp3":
            _convert_to_mp3(self._tmp_mic, self._p_mic, self._cfg.mp3_bitrate_kbps)
            _convert_to_mp3(self._tmp_sys, self._p_sys, self._cfg.mp3_bitrate_kbps)
            _convert_to_mp3(self._tmp_mix, self._p_mix, self._cfg.mp3_bitrate_kbps)

        # After files are closed/converted, rename with end timestamp prefix
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
        self._tmp_mic = self._tmp_sys = self._tmp_mix = None
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


def input_channel_count(name: str) -> int:
    """Return the number of input channels for the given device name."""
    try:
        _, info = _find_device_id_by_name(name)
    except RuntimeError:
        return 0
    return int(info.get("max_input_channels", 0) or 0)


def _safe_unlink(p: Optional[Path]) -> None:
    try:
        if p is not None and p.exists():
            p.unlink()
    except Exception:
        pass


def _run_ffmpeg(cmd: list[str]) -> int:
    import subprocess

    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return proc.returncode
    except Exception:
        return 1


def _convert_wav_to_mp3(tmp_wav: Path, out_mp3: Path, bitrate_kbps: int) -> bool:
    # Use libmp3lame if available
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(tmp_wav),
        "-c:a",
        "libmp3lame",
        "-b:a",
        f"{bitrate_kbps}k",
        str(out_mp3),
    ]
    return _run_ffmpeg(cmd) == 0


class _MissingTmp(Exception):
    pass


def _require_tmp(p: Optional[Path]) -> Path:
    if p is None:
        raise _MissingTmp()
    return p


def _convert_to_mp3(
    tmp_path: Optional[Path], final_path: Optional[Path], bitrate_kbps: int
) -> None:
    if tmp_path is None or final_path is None:
        return
    if _convert_wav_to_mp3(tmp_path, final_path, bitrate_kbps):
        _safe_unlink(tmp_path)
    else:
        print(f"FFmpeg conversion failed for {tmp_path}", flush=True)
