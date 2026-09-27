"""Render current UI with explicit synthetic validation data; no microphone access."""
import os
import sys
from pathlib import Path

project = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
from meeting_scribe.storage import read_json
import meeting_scribe.gui as gui

demo = Path(read_json(project / "artifacts" / "live_gui_test.json")["sessions"][0])
gui.sessions = lambda: [demo]
app = QApplication([])
window = gui.MainWindow(recover_sessions=False)
window.version_combo.setCurrentIndex(1)
window.show()
app.processEvents()
window.grab().save(str(project / "artifacts" / "live_preview.png"))
# Render new provisional typography using the recorded synthetic event stream.
report = read_json(project / "artifacts" / "live_stream_test.json")
event = next(e for e in report["events"] if e["type"] == "live_partial" and e.get("rows"))
window.preview.setPlainText("【Turbo 实时草稿 · 已确认段落】\n\n【临时文字 · 可能修订】\n" +
                           "\n".join(r["source"]+"："+r["text"] for r in event["rows"]))
window.partial_view.setText("临时段落会随上下文修订，停顿后归入已确认草稿。")
window.partial_view.show()
app.processEvents()
window.grab().save(str(project / "artifacts" / "live_partial_layout.png"))
print("Rendered current UI with synthetic test transcript.")
