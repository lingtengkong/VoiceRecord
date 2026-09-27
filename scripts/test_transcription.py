"""Synthetic bilingual fixture, real large-v3 CUDA inference, network blocked."""
import os
import sys
import socket
import time
from pathlib import Path

project = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project))
from meeting_scribe.storage import new_session, atomic_json, read_json
from meeting_scribe.transcription import transcribe_session
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly
from math import gcd


def blocked(*_a, **_kw):
    raise AssertionError("Network access attempted during local transcription")


def at_16k(path):
    data, rate = sf.read(path, dtype="float32", always_2d=True)
    data = data.mean(axis=1)
    divisor = gcd(rate, 16000)
    return resample_poly(data, 16000 // divisor, rate // divisor)


folder = new_session("验证样例 · 合成中英会议", project / "artifacts" / "test_sessions")
en = at_16k(project / "artifacts" / "english.wav")
zh = at_16k(project / "artifacts" / "chinese.wav")
assert len(en) > 16000 and len(zh) > 16000, "Speech fixtures are missing"
sf.write(folder / "microphone.wav", np.concatenate((np.zeros(16000), zh, np.zeros(16000))), 16000)
sf.write(folder / "system.wav", np.concatenate((np.zeros(32000), en, np.zeros(16000))), 16000)
atomic_json(folder / "session.json", {"title": "验证样例 · 合成中英会议", "status": "saved", "duration": max(len(en), len(zh))/16000+3})
socket.socket.connect = blocked
socket.create_connection = blocked
begin = time.monotonic()
result = transcribe_session(folder, "large-v3", "auto", "cuda", "")
transcript = read_json(result / "transcript.json")
text = " ".join(row["text"] for row in transcript["segments"])
assert "Friday" in text, text
assert "麦克风" in text, text
assert "local" in text.lower(), text
assert len({row["source"] for row in transcript["segments"]}) == 2
report = {"device": "RTX 4060 Ti 8GB", "model": "large-v3", "compute_type": "int8_float16",
          "elapsed_seconds": round(time.monotonic()-begin, 2), "network_blocked": True,
          "fixture": "Windows speech synthesis; NOT real meeting accuracy evaluation",
          "transcript_path": str(result), "segments": len(transcript["segments"])}
atomic_json(project / "artifacts" / "gpu_test.json", report)
print(report)
