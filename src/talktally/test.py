# requirements:
#   pip install sounddevice soundfile numpy

import sounddevice as sd
import soundfile as sf
import numpy as np
import queue
import threading
import time

# === CONFIG ===
AGGREGATE_DEVICE_NAME = "Meeting Recorder"  # rename if you used a custom name
SAMPLE_RATE = 48000
BLOCKSIZE = 1024
DURATION_SECONDS = 60 * 60  # 1 hour max guard; Ctrl+C to stop earlier

# Channel mapping:
# Adjust these indices to match your Aggregate Device channel layout.
# Common case: Mic is mono on channel 1, BlackHole is stereo on channels 2 and 3.
MIC_CHANNELS = [0]  # zero-based indices (CoreAudio channel 1 => index 0)
SYSTEM_CHANNELS = [1, 2]  # CoreAudio channels 2–3 => indices 1,2

# Output files
MIC_WAV_PATH = "mic.wav"
SYSTEM_WAV_PATH = "system.wav"
# Optional mixed output
WRITE_MIXED_STEREO = True
MIXED_WAV_PATH = "mixed.wav"

# === END CONFIG ===


# Find the aggregate device id
def find_device_id_by_name(name: str):
    devs = sd.query_devices()
    for i, d in enumerate(devs):
        if d.get("name") == name and d.get("max_input_channels", 0) > 0:
            return i, d
    raise RuntimeError(
        f"Input device named '{name}' not found or has no input channels.\n"
        f"Available input devices: {[d['name'] for d in devs if d['max_input_channels'] > 0]}"
    )


device_id, device_info = find_device_id_by_name(AGGREGATE_DEVICE_NAME)
total_in = device_info["max_input_channels"]
if total_in < max(MIC_CHANNELS + SYSTEM_CHANNELS) + 1:
    raise RuntimeError(
        f"Device '{AGGREGATE_DEVICE_NAME}' reports {total_in} input channels, "
        f"but your mapping needs {max(MIC_CHANNELS + SYSTEM_CHANNELS) + 1}."
    )

print(
    f"Using '{AGGREGATE_DEVICE_NAME}' (id {device_id}) with {total_in} input channels @ {SAMPLE_RATE} Hz"
)

# Queues for thread-safe writing
q_mic = queue.Queue(maxsize=100)
q_sys = queue.Queue(maxsize=100)
q_mix = queue.Queue(maxsize=100) if WRITE_MIXED_STEREO else None

# Open WAV writers
mic_file = sf.SoundFile(
    MIC_WAV_PATH,
    mode="w",
    samplerate=SAMPLE_RATE,
    channels=len(MIC_CHANNELS),
    subtype="PCM_16",
)
sys_file = sf.SoundFile(
    SYSTEM_WAV_PATH,
    mode="w",
    samplerate=SAMPLE_RATE,
    channels=len(SYSTEM_CHANNELS),
    subtype="PCM_16",
)
mix_file = (
    sf.SoundFile(
        MIXED_WAV_PATH, mode="w", samplerate=SAMPLE_RATE, channels=2, subtype="PCM_16"
    )
    if WRITE_MIXED_STEREO
    else None
)

stop_flag = threading.Event()


def writer_thread(q, outfile):
    while not stop_flag.is_set():
        try:
            block = q.get(timeout=0.2)
        except queue.Empty:
            continue
        outfile.write(block)


def audio_callback(indata, frames, time_info, status):
    if status:
        print("Audio status:", status, flush=True)
    # indata shape: (frames, total_in_channels)
    mic_block = indata[:, MIC_CHANNELS]
    sys_block = indata[:, SYSTEM_CHANNELS]

    # Put into queues
    q_mic.put(mic_block.copy(), block=False)
    q_sys.put(sys_block.copy(), block=False)

    if q_mix is not None:
        # Simple mix to stereo:
        #   Left: mic (mono mix) + system left
        #   Right: mic (mono mix) + system right (or same if system is mono)
        mic_mono = mic_block.mean(axis=1, keepdims=True)  # (frames,1)
        if sys_block.shape[1] == 1:
            sys_l = sys_block
            sys_r = sys_block
        else:
            sys_l = sys_block[:, [0]]
            sys_r = sys_block[:, [1]]

        mixed_l = mic_mono + sys_l
        mixed_r = mic_mono + sys_r
        mixed = np.concatenate([mixed_l, mixed_r], axis=1)
        # Optional: simple limiting to avoid clipping
        mixed = np.clip(mixed, -1.0, 1.0)
        q_mix.put(mixed.copy(), block=False)


# Start writer threads
t_mic = threading.Thread(target=writer_thread, args=(q_mic, mic_file), daemon=True)
t_sys = threading.Thread(target=writer_thread, args=(q_sys, sys_file), daemon=True)
t_mx = (
    threading.Thread(target=writer_thread, args=(q_mix, mix_file), daemon=True)
    if WRITE_MIXED_STEREO
    else None
)
t_mic.start()

t_sys.start()

if t_mx:
    t_mx.start()

print("Recording…  Press Ctrl+C to stop.")
try:
    with sd.InputStream(
        device=device_id,
        channels=total_in,
        samplerate=SAMPLE_RATE,
        blocksize=BLOCKSIZE,
        dtype="float32",
        callback=audio_callback,
    ):
        start = time.time()
        while time.time() - start < DURATION_SECONDS and not stop_flag.is_set():
            time.sleep(0.2)
except KeyboardInterrupt:
    print("\nStopping…")
finally:
    stop_flag.set()
    # Drain queues briefly
    time.sleep(0.5)
    mic_file.close()
    sys_file.close()
    if WRITE_MIXED_STEREO:
        mix_file.close()
    print(
        f"Saved: {MIC_WAV_PATH}, {SYSTEM_WAV_PATH}"
        + (f", {MIXED_WAV_PATH}" if WRITE_MIXED_STEREO else "")
    )
