import sounddevice as sd
import numpy as np
import time
import queue
import threading
from faster_whisper import WhisperModel

# ================= CONFIG =================
SAMPLE_RATE = 16000
BLOCK_SIZE = 1024

ENERGY_THRESHOLD = 0.025     # critical
SILENCE_TIMEOUT = 1.2
MIN_AUDIO_SEC = 0.7

WHISPER_MODEL = "small.en"
DEVICE = "cuda"
# =========================================

print("Loading Whisper...")
whisper = WhisperModel(
    WHISPER_MODEL,
    device=DEVICE,
    compute_type="float16"
)

audio_queue = queue.Queue()
recorded_audio = []

speaking = False
last_voice_time = 0


def rms_energy(x):
    return np.sqrt(np.mean(x ** 2))


# ---------- AUDIO CALLBACK (NO LOGIC) ----------
def audio_callback(indata, frames, time_info, status):
    audio_queue.put(indata[:, 0].copy())


# ---------- MAIN LOOP ----------
def vad_loop():
    global speaking, last_voice_time, recorded_audio

    print("🎙️ Listening...")

    while True:
        try:
            audio = audio_queue.get(timeout=0.1)
        except queue.Empty:
            continue

        energy = rms_energy(audio)
        now = time.time()

        if energy > ENERGY_THRESHOLD:
            last_voice_time = now

            if not speaking:
                speaking = True
                recorded_audio.clear()
                print("[VAD] 🎤 Speech started")

            recorded_audio.append(audio)

        else:
            if speaking and (now - last_voice_time) > SILENCE_TIMEOUT:
                speaking = False
                print("[VAD] 🔇 Speech ended")

                full_audio = np.concatenate(recorded_audio)
                recorded_audio.clear()

                duration = len(full_audio) / SAMPLE_RATE
                print(f"🧠 Audio length: {duration:.2f}s")

                if duration >= MIN_AUDIO_SEC:
                    transcribe(full_audio)
                else:
                    print("⚠️ Too short, skipped")


# ---------- WHISPER ----------
def transcribe(audio_np):
    print("🧠 Transcribing...")

    segments, _ = whisper.transcribe(
        audio_np,
        language="en",
        vad_filter=True
    )

    text = " ".join(s.text.strip() for s in segments)

    if text.strip():
        print("📝 TRANSCRIPT:", text)
    else:
        print("⚠️ Whisper produced empty output")


# ---------- START ----------
with sd.InputStream(
    samplerate=SAMPLE_RATE,
    channels=1,
    blocksize=BLOCK_SIZE,
    dtype="float32",
    callback=audio_callback
):
    vad_loop()
