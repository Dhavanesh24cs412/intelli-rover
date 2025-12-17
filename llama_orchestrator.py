#!/usr/bin/env python3
"""
orchestrator.py - Pi side (LOCAL OLLAMA VERSION)

Flow:
- record audio via arecord
- send audio to Deepgram STT
- send transcript to Laptop Ollama LLM (JSON output required)
- speak reply via Deepgram TTS or espeak fallback
- send ESP32 motor commands via serial
- read ESP32 telemetry (NOT PRINTED)
- block dangerous movements using sensor readings
"""

import os, time, json, subprocess, threading
from dotenv import load_dotenv
import requests
import serial

load_dotenv()

# ----------------------------- CONFIG -----------------------------
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
DEEPGRAM_STT_URL = os.getenv("DEEPGRAM_STT_URL", "https://api.deepgram.com/v1/listen")
DEEPGRAM_TTS_URL = os.getenv("DEEPGRAM_TTS_URL", "https://api.deepgram.com/v1/speak")

ESP32_SERIAL_PORT = os.getenv("ESP32_SERIAL_PORT", "/dev/ttyUSB0")
ESP32_BAUD = int(os.getenv("ESP32_BAUD", "115200"))

# IMPORTANT: CHANGE THIS TO YOUR LAPTOP'S IP 
LAPTOP_IP = "192.168.1.8"        # <-- REPLACE WITH YOUR ACTUAL IP
OLLAMA_MODEL = "phi3:latest"   # You can change to: llama3, mistral, deepseek-r1:7b

RECORD_SECS = int(os.getenv("RECORD_SECS", "5"))
WAV_RATE = int(os.getenv("WAV_RATE", "16000"))
WAV_FILE = os.getenv("WAV_FILE", "input.wav")
TTS_OUT = os.getenv("TTS_OUT", "out.mp3")

SAFETY_MIN_FRONT_CM = float(os.getenv("SAFETY_MIN_FRONT_CM", "15.0"))
TELEM_FRESH_TIMEOUT = float(os.getenv("TELEM_FRESH_TIMEOUT", "2.0"))

# ----------------------------- SERIAL -----------------------------
ser = None
serial_lock = threading.Lock()
last_telemetry = {}
last_telem_time = 0.0


def open_serial():
    """Open ESP32 serial connection."""
    global ser
    try:
        ser = serial.Serial(ESP32_SERIAL_PORT, ESP32_BAUD, timeout=1)
        print("Opened serial", ESP32_SERIAL_PORT)
    except Exception as e:
        print("Failed to open serial:", e)
        ser = None


def serial_reader():
    """Background thread: read ESP32 telemetry but DO NOT PRINT."""
    global ser, last_telemetry, last_telem_time

    if ser is None:
        return

    while True:
        try:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if not line:
                continue

            if line.startswith("T|"):
                parts = line.split('|')
                data = {}

                for p in parts[1:]:
                    if ':' in p:
                        k, v = p.split(':', 1)
                        try:
                            data[k.strip()] = float(v)
                        except:
                            data[k.strip()] = v

                last_telemetry = data
                last_telem_time = time.time()

            # we DO NOT print telemetry anymore

        except Exception as e:
            print("Serial read error:", e)
            time.sleep(0.5)


def start_serial_thread():
    open_serial()
    t = threading.Thread(target=serial_reader, daemon=True)
    t.start()
    time.sleep(0.2)


def send_command(cmd_obj):
    """Send motor command to ESP32."""
    global ser
    if ser is None:
        print("Serial not open, cannot send command.")
        return

    if isinstance(cmd_obj, dict):
        s = json.dumps(cmd_obj)
    else:
        s = str(cmd_obj)

    try:
        with serial_lock:
            ser.write((s + "\n").encode('utf-8'))
            ser.flush()
        print("Sent to ESP32:", s)
    except Exception as e:
        print("Failed to send serial:", e)


# ----------------------------- AUDIO / STT -----------------------------
def record_wav(path=WAV_FILE, secs=RECORD_SECS, rate=WAV_RATE, device=None):
    cmd = f"arecord -f S16_LE -r {rate} -d {secs} {path}"
    print("Recording:", cmd)
    subprocess.run(cmd, shell=True, check=True)
    return path


def deepgram_stt_file_upload(path):
    """Convert speech to text via Deepgram."""
    if not DEEPGRAM_API_KEY:
        print("Deepgram key missing.")
        return ""

    headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}
    params = {"punctuate": "true"}

    with open(path, "rb") as fh:
        print("Uploading to STT...")
        try:
            r = requests.post(DEEPGRAM_STT_URL, headers=headers, params=params, data=fh, timeout=30)
            r.raise_for_status()
            j = r.json()
            return j.get("results", {}).get("channels", [{}])[0].get("alternatives", [{}])[0].get("transcript", "")
        except Exception as e:
            print("Deepgram STT error:", e)
            return ""


# ----------------------------- OLLAMA LLM -----------------------------
def call_local_llm(prompt_text):
    """Send prompt to laptop's Ollama server."""
    url = f"http://{LAPTOP_IP}:11434/api/generate"
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt_text,
        "stream": False
    }

    try:
        r = requests.post(url, json=payload, timeout=30)
        r.raise_for_status()
        reply = r.json().get("response", "")

        # Try to extract JSON
        st = reply.find("{")
        en = reply.rfind("}") + 1
        if st != -1 and en > st:
            return json.loads(reply[st:en])
        else:
            return {"speech": reply, "command": None}

    except Exception as e:
        print("Ollama LLM error:", e)
        return {"speech": "LLM server unreachable", "command": None}


# ----------------------------- SAFETY -----------------------------
def safety_allows(cmd):
    """Ensure rover does not hit obstacles."""
    if not cmd:
        return True, ""

    act = cmd.get("action", "").lower()

    if act == "forward":
        # telemetry freshness
        if (time.time() - last_telem_time) > TELEM_FRESH_TIMEOUT:
            return False, "Telemetry stale"

        # check front distance
        val = last_telemetry.get("F")
        if val is None:
            return False, "No front distance"

        if val < SAFETY_MIN_FRONT_CM:
            return False, f"Obstacle at {val} cm"

    return True, ""


# ----------------------------- MAIN LOOP -----------------------------
def main():
    start_serial_thread()
    print("Orchestrator ready. Using laptop Ollama LLM.")

    try:
        while True:
            # 1) Record audio
            try:
                record_wav()
            except Exception as e:
                print("Record failed:", e)
                continue

            # 2) Convert to text
            transcript = deepgram_stt_file_upload(WAV_FILE)
            if not transcript:
                print("No transcript.")
                continue

            print("Transcript:", transcript)

            # 3) Build strict JSON prompt
            system_prompt = """
You are the rover assistant. ALWAYS reply ONLY in JSON like this:
{"speech":"<reply>", "command":{"action":"forward|backward|left|right|stop|turn", "params":{}}}
If no action needed, set "command" to null.
"""

            full_prompt = system_prompt + "\nUser said: " + transcript

            # 4) Call local LLM
            llm_out = call_local_llm(full_prompt)
            print("LLM ->", llm_out)

            # 5) Safety check
            cmd = llm_out.get("command", None)
            allowed, reason = safety_allows(cmd)

            if cmd:
                if allowed:
                    send_command(cmd)
                else:
                    print("Blocked:", reason)
                    llm_out["speech"] = f"Cannot perform action: {reason}"

            # 6) Speak response
            speech = llm_out.get("speech", "")
            if speech:
                ok = deepgram_tts(speech, TTS_OUT)
                if ok:
                    speak_file(TTS_OUT)
                else:
                    print("TTS failed; using espeak")
                    speak_offline(speech)

    except KeyboardInterrupt:
        print("Stopping.")


if __name__ == "__main__":
    main()
