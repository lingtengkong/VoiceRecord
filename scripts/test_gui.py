"""Exercise the real Qt process lifecycle and render the finished local demo."""
import os
import sys
import time
from pathlib import Path

project = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
from meeting_scribe.storage import data_root, atomic_json, new_session, read_json
from meeting_scribe.gui import MainWindow
from meeting_scribe.audio import mix_tracks
import shutil

app = QApplication([])
window = MainWindow()
errors = []
window.show_error = errors.append
window.show()
fixture = Path(read_json(project / "artifacts" / "gpu_test.json")["transcript_path"]).parent.parent
demo = new_session("演示 · 合成中英语音（非真实会议）")
for name in ("microphone.wav", "system.wav"):
    shutil.copyfile(fixture / name, demo / name)
meta = read_json(demo / "session.json")
meta.update(status="saved", duration=read_json(fixture / "session.json")["duration"], synthetic_demo=True)
atomic_json(demo / "session.json", meta)
mix_tracks(demo)
window.refresh_history(select=demo)
window.start_transcription()
assert window.process is not None
assert not window.start_button.isEnabled()
deadline = time.monotonic()+120
while window.process is not None and time.monotonic()<deadline:
    app.processEvents()
    time.sleep(.02)
assert window.process is None, "Transcription timed out"
assert not errors, errors
assert window.copy_button.isEnabled()
assert "Friday" in window.preview.toPlainText()
window.copy_summary()
assert "未明确" in app.clipboard().text()
window.left_scroll.verticalScrollBar().setValue(0)
app.processEvents()
window.grab().save(str(project / "artifacts" / "preview.png"))
window.start_transcription()
window.cancel_process()
deadline = time.monotonic()+15
while window.process is not None and time.monotonic()<deadline:
    app.processEvents()
    time.sleep(.02)
assert window.process is None
assert "已取消" in window.job_status.text()
assert window.start_button.isEnabled()
assert (demo / "latest_transcript.json").exists(), "Cancelling must preserve previous transcript"
assert not errors, errors
app.clipboard().clear()
window.close()
atomic_json(project / "artifacts" / "gui_test.json", {
    "qprocess_gpu_transcription": "passed", "cancel_preserves_previous_transcript": "passed",
    "copy_summary": "passed", "capture_disabled_while_transcribing": "passed",
    "demo_session": str(demo), "visual_preview": str(project / "artifacts" / "preview.png")})
print("GUI lifecycle tests passed.")
