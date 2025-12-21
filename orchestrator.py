#!/usr/bin/env python3
"""
VOICE-CONTROLLED AI ROVER – SESSION-AWARE ORCHESTRATOR (FINAL)

One student per session
Context memory + invigilation ready
"""

import socket, time, queue, threading, json, requests, re
import numpy as np
from collections import deque
from faster_whisper import WhisperModel
from TTS.api import TTS

# ================= CONFIG =================
SAMPLE_RATE = 16000
BLOCK_SIZE = 1024
BYTES_PER_SAMPLE = 4

TCP_AUDIO_PORT = 50005
TCP_CMD_PORT   = 50006
TCP_TTS_PORT   = 50007

PI_IP = "10.185.164.130"

WHISPER_MODEL = "medium.en"
DEVICE = "cuda"

OLLAMA_URL = "http://10.185.164.137:11434/api/generate"
OLLAMA_MODEL = "mistral:7b-instruct"

SPEAKER_WAV = "speaker.wav"

MIN_ENERGY = 0.03
MIN_SPEECH_SEC = 0.7
MAX_SPEECH_SEC = 4.0
SILENT_FRAMES = 10
POST_TTS_COOLDOWN = 0.8

VALID_ACTIONS = {"forward", "backward", "left", "right", "stop"}

# ================= INIT =================
print("🧠 Loading Whisper...")
whisper = WhisperModel(WHISPER_MODEL, device=DEVICE, compute_type="float16")

print("🔊 Loading XTTS...")
tts = TTS(
    model_name="tts_models/multilingual/multi-dataset/xtts_v2",
    progress_bar=False
).to("cuda")
print("🔊 XTTS ready")

audio_q = queue.Queue(maxsize=300)
tts_q = queue.Queue()

tts_stop = threading.Event()
tts_playing = threading.Event()
last_tts_time = 0.0

# ================= MEMORY =================

conversation_buffer = deque(maxlen=10)

session = {
    "mode": "chat",          # chat | test
    "user": {
        "name": None
    },
    "test": {
        "current_question": None,
        "questions_asked": [],
        "answers": [],
        "response_times": []
    }
}

# ================= UTILS =================
def rms(x):
    return float(np.sqrt(np.mean(x * x)))

def speak(text):
    if text:
        tts_q.put(text)
        conversation_buffer.append({"role": "assistant", "text": text})

def send_cmd(action):
    payload = {"mode": "manual", "action": action}
    with socket.socket() as s:
        s.connect((PI_IP, TCP_CMD_PORT))
        s.sendall((json.dumps(payload) + "\n").encode())
    print("⚡ CMD:", action)

# ================= AUDIO =================
def audio_server():
    sock = socket.socket()
    sock.bind(("0.0.0.0", TCP_AUDIO_PORT))
    sock.listen(1)
    conn, _ = sock.accept()
    print("🎤 Pi audio connected")

    buf = b""
    while True:
        d = conn.recv(4096)
        if not d:
            break
        buf += d
        while len(buf) >= BLOCK_SIZE * BYTES_PER_SAMPLE:
            chunk = buf[:BLOCK_SIZE * BYTES_PER_SAMPLE]
            buf = buf[BLOCK_SIZE * BYTES_PER_SAMPLE:]
            audio_q.put(np.frombuffer(chunk, dtype=np.float32))

# ================= TTS =================
def tts_worker():
    global last_tts_time
    while True:
        text = tts_q.get()
        if not text:
            continue

        tts_stop.clear()
        tts_playing.set()

        try:
            sock = socket.socket()
            sock.connect((PI_IP, TCP_TTS_PORT))

            sock.sendall(np.zeros(2400, dtype=np.float32).tobytes())

            wav = tts.tts(text=text, language="en", speaker_wav=SPEAKER_WAV)
            wav = np.asarray(wav, dtype=np.float32)

            for i in range(0, len(wav), 1024):
                if tts_stop.is_set():
                    break
                sock.sendall(wav[i:i+1024].tobytes())

            sock.close()
        except Exception as e:
            print("🔇 TTS error:", e)

        tts_playing.clear()
        last_tts_time = time.time()

# ================= LLM =================
def call_llm(text):
    context = "\n".join(
        f"{m['role']}: {m['text']}" for m in conversation_buffer
    )

    prompt = (
        "You are a communication companion.\n"
        "Respond briefly.\n\n"
        f"Known facts:\nUser name: {session['user']['name']}\n\n"
        f"Recent conversation:\n{context}\n\n"
        f"User: {text}\n\n"
        "Return ONLY valid JSON:\n"
        "{\"speech\":\"<reply>\","
        "\"command\":{\"action\":\"forward|backward|left|right|stop|turn|null\",\"direction\":\"left|right|null\"}}"
    )

    r = requests.post(
        OLLAMA_URL,
        json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "format": "json",
            "stream": False,
            "temperature": 0.1
        },
        timeout=15
    )

    data = r.json()
    resp = data.get("response")
    return resp if isinstance(resp, dict) else json.loads(resp)

# ================= MAIN =================
def main():
    buf = []
    silent = 0
    speaking = False
    t0 = 0

    print("🎧 Listening (Session-aware)...")

    while True:
        a = audio_q.get()

        if time.time() - last_tts_time < POST_TTS_COOLDOWN:
            continue

        e = rms(a)

        if not speaking:
            if e > MIN_ENERGY:
                buf = [a]
                t0 = time.time()
                speaking = True
                silent = 0
                if tts_playing.is_set():
                    tts_stop.set()
        else:
            buf.append(a)
            silent = silent + 1 if e < MIN_ENERGY else 0

            dur = time.time() - t0
            if silent >= SILENT_FRAMES or dur > MAX_SPEECH_SEC:
                speaking = False
                if dur < MIN_SPEECH_SEC:
                    continue

                audio = np.concatenate(buf)
                segments, _ = whisper.transcribe(audio, language="en")
                text = " ".join(s.text for s in segments).strip()

                if not text:
                    continue

                print("📝 USER:", text)
                conversation_buffer.append({"role": "user", "text": text})

                # ---------- MEMORY RULES ----------
                name_match = re.search(r"my name is (\w+)", text.lower())
                if name_match:
                    session["user"]["name"] = name_match.group(1).capitalize()
                    speak(f"Nice to meet you, {session['user']['name']}.")
                    continue

                if "what is my name" in text.lower() and session["user"]["name"]:
                    speak(f"Your name is {session['user']['name']}.")
                    continue

                if "repeat the question" in text.lower():
                    q = session["test"]["current_question"]
                    if q:
                        speak(q)
                    else:
                        speak("I haven't asked a question yet.")
                    continue

                # ---------- LLM ----------
                try:
                    out = call_llm(text)
                except Exception as e:
                    print("❌ LLM error:", e)
                    speak("Sorry, please repeat.")
                    continue

                print("🧠 JSON:", out)

                speech = out.get("speech", "")
                cmd = out.get("command", {})

                speak(speech)

                action = cmd.get("action")

                if action == "turn":
                    direction = cmd.get("direction")
                    if direction in ("left", "right"):
                        send_cmd(direction)

                elif action in VALID_ACTIONS:
                    send_cmd(action)

# ================= START =================
if __name__ == "__main__":
    threading.Thread(target=audio_server, daemon=True).start()
    threading.Thread(target=tts_worker, daemon=True).start()
    main()
