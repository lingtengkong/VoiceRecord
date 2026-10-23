"""Verify native Windows icon delivery without starting audio or model workers."""
import ctypes
import json
import os
import sys
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'windows' if sys.platform == 'win32' else 'offscreen'
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QMainWindow
from meeting_scribe.branding import WINDOWS_APP_ID
from meeting_scribe.recording_icon import RecordingIcon, logo_image, logo_icon


def main():
    if sys.platform == 'win32':
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_ID + '.IconCheck')
    app = QApplication([])
    app.setWindowIcon(logo_icon())
    window = QMainWindow()
    window.setWindowTitle('听记 · 图标检查（不录音）')
    window.resize(360, 100)
    status = RecordingIcon(window)
    # Allocate a native window without showing a taskbar button or recording.
    hwnd = int(window.winId())
    handles = []
    native = None
    if sys.platform == 'win32':
        native = ctypes.WinDLL('user32', use_last_error=True)
        native.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        native.SendMessageW.restype = ctypes.c_void_p

    def check():
        app.processEvents()
        if native:
            small = native.SendMessageW(hwnd, 0x007F, 0, 0)
            large = native.SendMessageW(hwnd, 0x007F, 1, 0)
            assert small and large, 'Windows did not receive both icon sizes'
            handles.append((small, large))

    check()
    status.set_recording(True)
    for _ in range(12):
        status.advance()
        check()
    assert not native or len(set(handles)) > 1, 'Windows native icon did not change'
    status.set_recording(False)
    assert not status.timer.isActive()
    check()

    out = Path(__file__).resolve().parent.parent / 'artifacts' / 'recording_icon'
    out.mkdir(parents=True, exist_ok=True)
    strip = QImage(6*96, 112, QImage.Format.Format_ARGB32_Premultiplied)
    strip.fill(QColor('#202124'))
    painter = QPainter(strip)
    for index, frame in enumerate((None, 3, 7, 12, 17, 21)):
        image = logo_image(64, None if frame is None else frame/24)
        painter.drawImage(index*96+16, 12, image)
        image.save(str(out / f'frame-{index}.png'))
        painter.setPen(QColor('white'))
        painter.drawText(index*96, 80, 96, 24, Qt.AlignmentFlag.AlignCenter,
                         'Idle' if frame is None else f'{frame/10:.1f}s')
    painter.end()
    strip.save(str(out / 'preview.png'))
    window.close()
    print(json.dumps({'native_windows_icons': bool(native), 'updates_verified': len(handles),
                      'animation_stopped': not status.timer.isActive(), 'preview': str(out / 'preview.png')}))


if __name__ == '__main__':
    main()
