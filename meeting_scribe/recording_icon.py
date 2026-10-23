"""Logo-only recording status icons, rendered once and reused by Qt/Windows."""
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QObject, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QImage, QLinearGradient, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

SIZES = (16, 24, 32, 48, 64, 128)
FRAME_COUNT = 24
FRAME_MS = 100
SVG_PATH = Path(__file__).resolve().parent.parent / 'assets' / 'tingji.svg'


def logo_image(size, phase=None):
    source = SVG_PATH.read_text(encoding='utf-8')

    def render(color):
        renderer = QSvgRenderer(QByteArray(source.replace('#087F7C', color).encode('utf-8')))
        if not renderer.isValid():
            raise RuntimeError('听记 logo 无法读取。')
        image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        return image

    image = render('#7E7E7E')
    if phase is not None:
        colored = render('#079B90')
        # A moving alpha band reveals the original logo color from left to right.
        center = (-0.45 + 1.9 * phase) * size
        gradient = QLinearGradient(center-size*0.45, 0, center+size*0.45, 0)
        for position, alpha in ((0, 0), (0.35, 200), (0.5, 255), (0.65, 200), (1, 0)):
            gradient.setColorAt(position, QColor(0, 0, 0, alpha))
        painter = QPainter(colored)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        painter.fillRect(colored.rect(), gradient)
        painter.end()
        painter = QPainter(image)
        painter.drawImage(0, 0, colored)
        painter.end()
    return image


@lru_cache(maxsize=FRAME_COUNT+1)
def logo_icon(frame=None):
    icon = QIcon()
    phase = None if frame is None else frame / FRAME_COUNT
    for size in SIZES:
        icon.addPixmap(QPixmap.fromImage(logo_image(size, phase)))
    return icon


class RecordingIcon(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.recording = False
        self.frame = 0
        self.timer = QTimer(self)
        self.timer.setInterval(FRAME_MS)
        self.timer.timeout.connect(self.advance)
        self.apply(logo_icon())

    def apply(self, icon):
        self.window.setWindowIcon(icon)
        # Explicitly update the native QWindow as well as the QWidget default.
        if self.window.windowHandle() is not None:
            self.window.windowHandle().setIcon(icon)

    def set_recording(self, recording):
        if self.recording == recording:
            return
        self.recording = recording
        if recording:
            # Cache frames before starting the timer; never rasterize per tick.
            for frame in range(FRAME_COUNT):
                logo_icon(frame)
            self.frame = 0
            self.advance()
            self.timer.start()
        else:
            self.timer.stop()
            self.apply(logo_icon())

    def advance(self):
        self.apply(logo_icon(self.frame))
        self.frame = (self.frame + 1) % FRAME_COUNT
