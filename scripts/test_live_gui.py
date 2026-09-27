"""Exercise live GUI + actual WASAPI capture, failure isolation, and final rerun.

All test recordings go to local verification/, never to the project/OneDrive.
"""
import os
import sys
import threading
import time
from pathlib import Path

project = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import numpy as np
import soundfile as sf
import pyaudiowpatch as pa
from scipy.signal import resample_poly
from PySide6.QtWidgets import QApplication
from meeting_scribe.storage import data_root, new_session, read_json, atomic_json
import meeting_scribe.gui as gui

created = []
def test_session(title):
    path = new_session(title, data_root() / "verification")
    created.append(path)
    return path

gui.new_session = test_session
gui.sessions = lambda: list(reversed(created))
app = QApplication([])
window = gui.MainWindow(recover_sessions=False)
errors = []
window.show_error = errors.append
window.live_enabled.setChecked(True)
window.auto_transcribe.setChecked(True)
window.title_edit.setText("界面实测 · 系统语音与实时转写")
window.show()
handoffs = []
original_transcribe = window.start_transcription
def check_handoff():
    assert window.live_process is None, "Final model loaded while Turbo still alive"
    handoffs.append(time.monotonic())
    original_transcribe()
window.start_transcription = check_handoff


def pump(seconds=0, until=None, timeout=60):
    deadline = time.monotonic()+(timeout if until else seconds)
    while time.monotonic() < deadline:
        app.processEvents()
        if until and until():
            return
        time.sleep(.015)
    if until:
        raise AssertionError("UI condition timed out: " + str(errors) + " / " + window.live_status.text())


def play(path):
    audio, rate = sf.read(path, dtype="float32", always_2d=True)
    with pa.PyAudio() as engine:
        host = engine.get_host_api_info_by_type(pa.paWASAPI)
        info = engine.get_device_info_by_index(host["defaultOutputDevice"])
        out_rate = int(info["defaultSampleRate"])
        from math import gcd
        divisor = gcd(rate, out_rate)
        data = resample_poly(audio.mean(axis=1), out_rate//divisor, rate//divisor) * .6
        channels = min(2, info["maxOutputChannels"])
        stream = engine.open(format=pa.paFloat32, channels=channels, rate=out_rate,
                             output=True, output_device_index=info["index"])
        try:
            stream.write(np.repeat(data[:, None], channels, axis=1).astype(np.float32).tobytes())
        finally:
            stream.stop_stream()
            stream.close()


window.start_recording()
pump(until=lambda: window.task is None and window.live_process is not None)
assert window.stop_button.isEnabled() and window.pause_button.isEnabled()
speaker = threading.Thread(target=play, args=(project / "artifacts" / "english.wav",))
speaker.start()
pump(until=lambda: bool(window.live_rows) or any(window.live_partials.values()), timeout=25)
window.grab().save(str(project / "artifacts" / "live_running_preview.png"))
pump(until=lambda: not speaker.is_alive(), timeout=25)
pump(.8)
window.pause_recording()
assert window.recorder.paused
pump(1.2)
window.pause_recording()
assert not window.recorder.paused
speaker = threading.Thread(target=play, args=(project / "artifacts" / "chinese.wav",))
speaker.start()
pump(until=lambda: not speaker.is_alive(), timeout=30)
pump(.8)
first = window.active_session
window.stop_recording()
pump(until=lambda: window.recorder is None and window.task is None and window.live_process is None
     and window.process is None and not window.pending_auto, timeout=100)
assert not errors, errors
assert handoffs, "Automatic final transcription not started"
assert (first / "latest_live_transcript.json").exists()
assert (first / "latest_transcript.json").exists()
live_folder = Path(read_json(first / "latest_live_transcript.json")["path"])
live_data = read_json(live_folder / "transcript.json")
system_text = " ".join(r["text"] for r in live_data["segments"] if r["source"]=="会议声音")
assert "Friday" in system_text, system_text
assert "麦克风" in system_text, system_text
window.version_combo.setCurrentIndex(1)
assert "Turbo 实时草稿" in window.preview.toPlainText()
window.version_combo.setCurrentIndex(0)
assert "Turbo 实时草稿" not in window.preview.toPlainText()

# A recognizer crash and a user cancellation must not stop or block capture.
window.auto_transcribe.setChecked(False)
window.title_edit.setText("界面实测 · 识别故障隔离")
window.start_recording()
pump(until=lambda: window.task is None and window.live_process is not None)
pump(1)
before = window.recorder.writers["microphone"].frames
window.live_process.kill()
pump(until=lambda: window.live_process is None)
pump(1.2)
assert window.recorder.active
assert window.recorder.writers["microphone"].frames > before
assert window.stop_button.isEnabled()
window.start_live()
pump(.5)
window.cancel_process()
pump(until=lambda: window.live_process is None, timeout=15)
assert window.recorder.active
assert window.stop_button.isEnabled()
second = window.active_session
window.stop_recording()
pump(until=lambda: window.recorder is None and window.task is None)
assert read_json(second / "session.json")["status"] == "saved"
assert not errors, errors
window.refresh_history(select=first)
window.version_combo.setCurrentIndex(1)
window.left_scroll.verticalScrollBar().setValue(0)
app.processEvents()
window.grab().save(str(project / "artifacts" / "live_preview.png"))
window.close()
atomic_json(project / "artifacts" / "live_gui_test.json", {
    "actual_wasapi_capture": "passed", "gpu_live_preview": "passed", "pause_resume": "passed",
    "final_model_waits_for_turbo_exit": "passed", "versions_separate": "passed",
    "recognizer_crash_does_not_stop_recording": "passed", "cancel_does_not_stop_recording": "passed",
    "sessions": [str(first), str(second)], "system_transcript": system_text,
    "limits": "Monitor microphone and desktop speakers, synthetic speech. Bluetooth headset not tested."})
print("Live GUI and real capture tests PASSED", flush=True)
