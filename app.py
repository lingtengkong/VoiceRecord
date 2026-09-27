import os
import sys
import traceback

os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["DO_NOT_TRACK"] = "1"

from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import QApplication, QMessageBox
from meeting_scribe.storage import data_root


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Meeting Scribe")
    app.setOrganizationName("LocalMeetingScribe")
    lock = QLockFile(str(data_root() / "app.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, "本地会议录音", "程序已在运行，请查看任务栏中的窗口。")
        return
    from meeting_scribe.gui import MainWindow
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        error = traceback.format_exc()
        (data_root() / "startup_error.log").write_text(error, encoding="utf-8")
        raise
