#!/usr/bin/env python3
"""
Raspberry Pi Bridge PRODUCTION SAFE

Mic ? Laptop (STT)
Laptop ? TTS ? Speaker
Laptop ? Command ? ESP32 (USB)

Hardened against:
- TCP resets
- Callback crashes
- Stream instability
"""

import sounddevice as sd
import socket
import threading
import serial
import numpy as np
import time
import json
import errno

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
esp32 = None
try:
    esp32 = serial.Serial(ESP32_PORT, ESP32_BAUD, timeout=1)
    time.sleep(2)
    print("? ESP32 connected")
except Exception as e:
    print("? ESP32 serial error:", e)

# ================= MIC AUDIO STREAM =================
def mic_stream():
    while True:
        try:
            sock = socket.socket()
            sock.connect((LAPTOP_IP, AUDIO_PORT))
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            print("?? Mic connected to Laptop")

            stop_stream = threading.Event()

            def callback(indata, frames, time_info, status):
                if stop_stream.is_set():
                    return
                try:
                    sock.sendall(indata.tobytes())
                except OSError as e:
                    # Expected when Laptop resets connection
                    if e.errno in (errno.EPIPE, errno.ECONNRESET):
                        stop_stream.set()
                    else:
                        stop_stream.set()

            with sd.InputStream(
                samplerate=MIC_SAMPLE_RATE,
                channels=1,
                blocksize=BLOCK_SIZE,
                dtype="float32",
                callback=callback
            ):
                while not stop_stream.is_set():
                    time.sleep(0.1)

            sock.close()
            print("?? Mic stream reset")

        except Exception as e:
            print("?? Mic connection failed, retrying:", e)
            time.sleep(1.5)

# ================= COMMAND SERVER =================
def command_server():
    sock = socket.socket()
    sock.bind(("0.0.0.0", CMD_PORT))
    sock.listen(5)
    print("?? CMD server ready")

    while True:
        conn, _ = sock.accept()
        try:
            raw = conn.recv(1024).decode().strip()
            if not raw:
                continue

            print("?? CMD:", raw)

            try:
                msg = json.loads(raw)
                action = msg.get("action")
            except:
                action = raw.lower()

            if action and esp32:
                esp32.write((action + "\n").encode())
                print("?? ESP32:", action)

        finally:
            conn.close()

# ================= TTS PLAYBACK =================
def tts_server():
    sock = socket.socket()
    sock.bind(("0.0.0.0", TTS_PORT))
    sock.listen(1)
    print("?? TTS server ready")

    while True:
        conn, addr = sock.accept()
        print("?? TTS stream from", addr)

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
                stream.write(np.frombuffer(data, dtype=np.float32))
        except Exception:
            pass

        stream.stop()
        stream.close()
        conn.close()
        print("?? TTS stream ended")

# ================= START =================
if __name__ == "__main__":
    threading.Thread(target=mic_stream, daemon=True).start()
    threading.Thread(target=tts_server, daemon=True).start()
    command_server()