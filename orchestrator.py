#!/usr/bin/env python3
"""
orchestrator.py - Pi side

Flow:
- record audio via arecord
- upload file to Deepgram STT (file upload)
- send transcript to OpenRouter LLM (expect JSON reply)
- speak reply via Deepgram TTS (fallback espeak)
- if LLM returns a command, send JSON or simple word to ESP32 via serial
- read telemetry from ESP32 (T|F:...|L:...|R:...)
- block forward if telemetry shows obstacle < SAFETY_MIN_FRONT_CM or telemetry stale
"""

import os, time, json, subprocess, threading
from dotenv import load_dotenv
import requests
import serial

load_dotenv()

# Config
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
DEEPGRAM_STT_URL = os.getenv("DEEPGRAM_STT_URL", "https://api.deepgram.com/v1/listen")
DEEPGRAM_TTS_URL = os.getenv("DEEPGRAM_TTS_URL", "https://api.deepgram.com/v1/tts")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "amazon/nova-2-lite-v1:free")

ESP32_SERIAL_PORT = os.getenv("ESP32_SERIAL_PORT", "/dev/ttyUSB0")
ESP32_BAUD = int(os.getenv("ESP32_BAUD", "115200"))

RECORD_SECS = int(os.getenv("RECORD_SECS", "5"))
WAV_RATE = int(os.getenv("WAV_RATE", "16000"))
WAV_FILE = os.getenv("WAV_FILE", "input.wav")
TTS_OUT = os.getenv("TTS_OUT", "out.mp3")

SAFETY_MIN_FRONT_CM = float(os.getenv("SAFETY_MIN_FRONT_CM", "15.0"))
TELEM_FRESH_TIMEOUT = float(os.getenv("TELEM_FRESH_TIMEOUT", "2.0"))

# Serial
ser = None
serial_lock = threading.Lock()
last_telemetry = {}
last_telem_time = 0.0

def open_serial():
    global ser
    try:
        ser = serial.Serial(ESP32_SERIAL_PORT, ESP32_BAUD, timeout=1)
        print("Opened serial", ESP32_SERIAL_PORT)
    except Exception as e:
        print("Failed to open serial:", e)
        ser = None

def serial_reader():
    global ser, last_telemetry, last_telem_time
    if ser is None:
        return
    while True:
        try:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if not line:
                continue
            # telemetry lines: T|F:12.34|L:23.45|R:67.89
            if line.startswith("T|"):
                parts = line.split('|')
                data = {}
                for p in parts[1:]:
                    if ':' in p:
                        k,v = p.split(':',1)
                        try:
                            data[k.strip()] = float(v)
                        except:
                            data[k.strip()] = v
                last_telemetry = data
                last_telem_time = time.time()
                
            else:
                print("[ESP32]", line)
        except Exception as e:
            print("Serial read error:", e)
            time.sleep(0.5)

def start_serial_thread():
    open_serial()
    t = threading.Thread(target=serial_reader, daemon=True)
    t.start()
    time.sleep(0.2)

def send_command(cmd_obj):
    """Send command JSON or short string to ESP32 over serial."""
    global ser
    s = ""
    if isinstance(cmd_obj, dict):
        s = json.dumps(cmd_obj)
    else:
        s = str(cmd_obj)
    s = s.strip()
    if not s:
        return
    if ser is None:
        print("Serial not open, cannot send:", s)
        return
    try:
        with serial_lock:
            ser.write((s + "\n").encode('utf-8'))
            ser.flush()
        print("Sent to ESP32:", s)
    except Exception as e:
        print("Failed to send serial:", e)

# ---------------- STT (Deepgram file upload) ----------------
def record_wav(path=WAV_FILE, secs=RECORD_SECS, rate=WAV_RATE, device=None):
    dev_arg = f"-D {device}" if device else ""
    cmd = f"arecord -f S16_LE -r {rate} {dev_arg} -d {secs} {path}"
    print("Recording:", cmd)
    subprocess.run(cmd, shell=True, check=True)
    return path

def deepgram_stt_file_upload(path):
    if not DEEPGRAM_API_KEY:
        print("DEEPGRAM_API_KEY not set.")
        return ""
    headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}
    params = {"punctuate":"true"}
    with open(path, "rb") as fh:
        print("Uploading to Deepgram STT...")
        try:
            r = requests.post(DEEPGRAM_STT_URL, headers=headers, params=params, data=fh, timeout=30)
            r.raise_for_status()
            j = r.json()
            # typical structure: results.channels[0].alternatives[0].transcript
            transcript = j.get("results",{}).get("channels",[{}])[0].get("alternatives",[{}])[0].get("transcript","")
            return transcript
        except Exception as e:
            print("Deepgram STT error:", e)
            return ""

# ---------------- OpenRouter call ----------------
def call_openrouter(prompt_text):
    if not OPENROUTER_API_KEY:
        return {"speech":"No OpenRouter API key configured.", "command": None}
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type":"application/json"}
    system = (
        "You are the rover assistant. ALWAYS respond with a JSON object only:\n"
        '{"speech":"<text reply>","command":{"action":"forward|backward|left|right|stop|turn","params":{}}}\n'
        "If no command, set command to null."
    )
    payload = {"model": OPENROUTER_MODEL,
               "messages":[{"role":"system","content":system},{"role":"user","content":prompt_text}],
               "max_tokens":256, "temperature":0.1}
    try:
        r = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        j = r.json()
        raw = j["choices"][0]["message"]["content"]
        # extract JSON block
        st = raw.find("{")
        en = raw.rfind("}")+1
        if st!=-1 and en>st:
            js = raw[st:en]
            return json.loads(js)
        else:
            return {"speech": raw, "command": None}
    except Exception as e:
        print("OpenRouter error:", e)
        return {"speech":"I could not reach the language server.", "command": None}

# ---------------- Deepgram TTS (or fallback) ----------------
def deepgram_tts(text, out_file=TTS_OUT):
    if not DEEPGRAM_API_KEY:
        return False
    headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}", "Content-Type":"application/json"}
    payload = {"voice":"alloy","text":text}
    try:
        r = requests.post(DEEPGRAM_TTS_URL, headers=headers, json=payload, stream=True, timeout=30)
        if r.status_code != 200:
            print("Deepgram TTS HTTP", r.status_code, r.text)
            return False
        with open(out_file, "wb") as fh:
            for chunk in r.iter_content(chunk_size=4096):
                if chunk:
                    fh.write(chunk)
        return True
    except Exception as e:
        print("Deepgram TTS error:", e)
        return False

def speak_file(path):
    try:
        subprocess.run(["mpg123","-q",path], check=False)
    except Exception as e:
        print("Playback error:", e)

def speak_offline(text):
    try:
        subprocess.run(["espeak","-s150", text], check=False)
    except Exception as e:
        print("espeak error:", e)

# ---------------- Safety ----------------
def safety_allows(cmd):
    # only check forward
    if not cmd:
        return True, ""
    act = cmd.get("action","").lower()
    if act == "forward":
        # telemetry freshness
        if (time.time() - last_telem_time) > TELEM_FRESH_TIMEOUT:
            return False, "Telemetry stale"
        # front reading key 'F'
        val = None
        if isinstance(last_telemetry, dict):
            for k in ("F","dist_f","dist_front","front"):
                if k in last_telemetry:
                    try:
                        val = float(last_telemetry[k]); break
                    except:
                        pass
        if val is None:
            return False, "No front distance"
        if val < SAFETY_MIN_FRONT_CM:
            return False, f"Obstacle at {val} cm"
    return True, ""

# ---------------- Main loop ----------------
def main():
    start_serial_thread()
    print("Orchestrator ready. Press Ctrl+C to stop.")
    try:
        while True:
            # 1) record
            try:
                record_wav()
            except Exception as e:
                print("Record failed:", e); time.sleep(0.5); continue

            # 2) STT
            transcript = deepgram_stt_file_upload(WAV_FILE)
            if not transcript:
                print("No transcript; retrying...")
                time.sleep(0.2); continue
            print("Transcript:", transcript)

            # 3) LLM
            prompt = f'User said: "{transcript}". If this should trigger rover motion, return JSON: {{"speech":"<reply>","command":{{"action":"forward|backward|left|right|stop|turn","params":{{}}}}}}. If no action, command:null.'
            llm_out = call_openrouter(prompt)
            print("LLM ->", llm_out)

            # 4) Check command & safety
            cmd = llm_out.get("command", None)
            allowed, reason = safety_allows(cmd)
            if cmd:
                if allowed:
                    # Send to ESP32: either JSON with action or simple word
                    # Normalize: ensure action key exists
                    action = cmd.get("action","").lower() if isinstance(cmd,dict) else str(cmd).lower()
                    # If there's a 'dir' param for 'turn', include as JSON
                    send_command(cmd)
                else:
                    print("Blocked cmd:", reason)
                    # overwrite speech to inform user
                    llm_out["speech"] = f"Cannot perform action for safety: {reason}"

            # 5) TTS
            speech = llm_out.get("speech","")
            if speech:
                ok = deepgram_tts(speech, TTS_OUT)
                if ok:
                    speak_file(TTS_OUT)
                else:
                    print("Deepgram TTS failed; using espeak")
                    speak_offline(speech)

            time.sleep(0.2)

    except KeyboardInterrupt:
        print("Stopping.")

if __name__ == "__main__":
    main()
