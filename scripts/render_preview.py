"""Render the actual Qt widgets for layout verification; does not start audio capture."""
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
from meeting_scribe.gui import MainWindow

app = QApplication([])
window = MainWindow()
window.show()
app.processEvents()
(root / "artifacts").mkdir(exist_ok=True)
window.grab().save(str(root / "artifacts" / "preview.png"))
print("UI rendered:", root / "artifacts" / "preview.png")
window.close()
