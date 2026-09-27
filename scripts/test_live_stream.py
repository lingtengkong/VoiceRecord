"""Replay synthetic bilingual audio at wall-clock speed into durable recording files.

Tests GPU live partial/commit, two sources, pause boundary, end flush and offline
execution. Does not access the microphone, play sound, or join a meeting.
"""
import socket
import sys
import threading
import time
from pathlib import Path

project = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project))
import numpy as np
import soundfile as sf
from meeting_scribe.audio import TrackWriter
from meeting_scribe.storage import new_session, data_root, read_json, atomic_json
import meeting_scribe.live as live

folder = new_session("实时转写验证 · 合成中英", data_root() / "verification")
fixture = Path(read_json(project / "artifacts" / "gpu_test.json")["transcript_path"]).parent.parent
clips = {key: sf.read(fixture / f"{key}.wav", dtype="float32")[0] for key in live.SOURCES}
length = max(len(x) for x in clips.values())
clips = {key: np.pad(x, (0, length-len(x))) for key, x in clips.items()}
rate = 16000
errors = []
writers = {key: TrackWriter(folder / f"{key}.wav", rate, errors.append) for key in clips}
events = []
start = time.monotonic()


def observe(kind, **event):
    events.append({"type": kind, "elapsed": time.monotonic()-start, **event})
    if kind in ("live_ready", "live_commit", "live_partial", "live_done", "live_gap"):
        print({"type": kind, "elapsed": round(time.monotonic()-start, 2), **event}, flush=True)


live.emit = observe


def publish(position, state, boundaries):
    atomic_json(folder / "live_capture.json", {
        "state": state, "position": position, "updated_at": time.time(), "pause_boundaries": boundaries,
        "tracks": {key: {"rate": rate, "frames": w.published_frames} for key, w in writers.items()}})


def feeder():
    boundaries = []
    position = 0.
    for repeat in range(2):
        for first in range(0, length, 4000):
            for key, writer in writers.items():
                writer.push(position, clips[key][first:first+4000])
            position += min(4000, length-first)/rate
            publish(position, "recording", boundaries)
            time.sleep(.25)
        boundaries.append(position)
        for writer in writers.values():
            writer.seal(position)
        for _ in range(8):
            publish(position, "paused", boundaries)
            time.sleep(.25)
    for writer in writers.values():
        writer.finish(position)
    publish(position, "finished", boundaries)


def blocked(*_args, **_kwargs):
    raise AssertionError("Network attempted by live worker")


socket.socket.connect = blocked
socket.create_connection = blocked
thread = threading.Thread(target=feeder)
thread.start()
result = live.run_live(folder)
thread.join()
assert not errors, errors
transcript = read_json(result / "transcript.json")
all_text = " ".join(r["text"] for r in transcript["segments"])
assert all_text.count("Friday") == 2, all_text
assert "麦克风" in all_text, all_text
assert {row["source"] for row in transcript["segments"]} == set(live.SOURCES.values())
assert any(e["type"] == "live_partial" and e.get("rows") for e in events), "No provisional text"
assert not any(e["type"] == "live_gap" for e in events), "Unexpected backlog"
partials = [e for e in events if e["type"] == "live_partial" and e.get("rows")]
first = min(e["elapsed"] for e in partials)
report = {"fixture": "Synthetic Chinese/English, wall-clock replay, NOT actual meeting accuracy",
          "network_blocked": True, "model": "turbo", "gpu": "RTX 4060 Ti 8GB",
          "first_provisional_seconds_including_model_load": round(first, 2),
          "max_inference_seconds": max(e.get("inference_seconds", 0) for e in events),
          "total_wall_seconds": round(time.monotonic()-start, 2), "session": str(folder),
          "result": str(result), "events": events}
atomic_json(project / "artifacts" / "live_stream_test.json", report)
print("Live stream test PASSED", flush=True)
