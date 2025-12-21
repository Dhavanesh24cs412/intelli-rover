# -*- coding: utf-8 -*-
#!/usr/bin/env python3
"""
Raspberry Pi Bridge FINAL (MATCHED WITH ORCHESTRATOR)

Mic  -> TCP audio -> Laptop
Laptop -> TCP command -> USB serial -> ESP32
Laptop -> TCP TTS -> Speaker (stable, no clipping)
"""

import sounddevice as sd
import socket
import threading
import serial
import numpy as np
import time
import json

# ================= CONFIG =================
MIC_SAMPLE_RATE = 16000
TTS_SAMPLE_RATE = 24000
BLOCK_SIZE = 1024

LAPTOP_IP = "10.185.164.137"

AUDIO_PORT = 50005
CMD_PORT   = 50006
TTS_PORT   = 50007

ESP32_PORT = "/dev/ttyUSB0"
ESP32_BAUD = 115200

# ================= ESP32 SERIAL =================
try:
    esp32 = serial.Serial(ESP32_PORT, ESP32_BAUD, timeout=1)
    time.sleep(2)
    print("? ESP32 connected via USB")
except Exception as e:
    print("? ESP32 serial error:", e)
    esp32 = None

# ================= MIC STREAM =================
def mic_stream():
    while True:
        try:
            sock = socket.socket()
            sock.connect((LAPTOP_IP, AUDIO_PORT))
            print("?? Mic connected to Laptop")

            def callback(indata, frames, time_info, status):
                try:
                    sock.sendall(indata.astype(np.float32).tobytes())
                except Exception:
                    pass

            with sd.InputStream(
                samplerate=MIC_SAMPLE_RATE,
                channels=1,
                blocksize=BLOCK_SIZE,
                dtype="float32",
                callback=callback
            ):
                while True:
                    time.sleep(1)

        except Exception as e:
            print("?? Mic reconnecting:", e)
            time.sleep(2)

# ================= COMMAND SERVER =================
def command_server():
    sock = socket.socket()
    sock.bind(("0.0.0.0", CMD_PORT))
    sock.listen(5)
    print("?? Command server listening")

    while True:
        conn, addr = sock.accept()
        try:
            raw = conn.recv(1024).decode().strip()
            if not raw:
                continue

            print("? CMD from Laptop:", raw)

            action = None
            if raw.startswith("{"):
                msg = json.loads(raw)
                action = msg.get("action")
            else:
                action = raw

            if action and esp32:
                esp32.write((action.lower() + "\n").encode())
                print("? SENT TO ESP32:", action)

        except Exception as e:
            print("? CMD error:", e)
        finally:
            conn.close()

# ================= TTS PLAYBACK =================
def tts_audio_server():
    sock = socket.socket()
    sock.bind(("0.0.0.0", TTS_PORT))
    sock.listen(1)
    print("?? TTS server ready")

    while True:
        conn, addr = sock.accept()
        print("?? TTS stream connected")

        stream = sd.OutputStream(
            samplerate=TTS_SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=BLOCK_SIZE
        )
        stream.start()

        try:
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                audio = np.frombuffer(data, dtype=np.float32)
                stream.write(audio)
        except Exception:
            pass

        stream.stop()
        stream.close()
        conn.close()
        print("?? TTS stream closed")

# ================= START =================
if __name__ == "__main__":
    threading.Thread(target=mic_stream, daemon=True).start()
    threading.Thread(target=tts_audio_server, daemon=True).start()
    command_server()
