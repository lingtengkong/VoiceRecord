"""Render the editable SVG into Windows ICO sizes using the existing Qt runtime."""
import os
import struct
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

app = QApplication([])
assets = Path(__file__).resolve().parent.parent / 'assets'
renderer = QSvgRenderer(str(assets / 'tingji.svg'))
assert renderer.isValid()
sizes = (16, 24, 32, 48, 64, 128, 256)
images = []
for size in sizes:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    encoded = QByteArray()
    buffer = QBuffer(encoded)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, 'PNG')
    images.append(bytes(encoded))
    if size == 128:
        assert image.save(str(assets / 'tingji.png'))
offset = 6 + 16 * len(sizes)
entries = []
for size, data in zip(sizes, images):
    entries.append(struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
    offset += len(data)
(assets / 'tingji.ico').write_bytes(struct.pack('<HHH', 0, 1, len(sizes)) + b''.join(entries) + b''.join(images))
print('Created PNG preview and Windows ICO with sizes:', sizes)
