import os
import sys
import traceback

os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["DO_NOT_TRACK"] = "1"

from PySide6.QtCore import QLockFile
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox
from meeting_scribe.storage import data_root
from meeting_scribe.branding import APP_NAME, ICON_PATH, WINDOWS_APP_ID


def main():
    if os.name == 'nt':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_ID)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(QIcon(str(ICON_PATH)))
    app.setOrganizationName("LocalMeetingScribe")
    lock = QLockFile(str(data_root() / "app.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, APP_NAME, "程序已在运行，请查看任务栏中的窗口。")
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
