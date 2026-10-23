import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QWidget
from meeting_scribe.recording_icon import FRAME_COUNT, RecordingIcon, logo_image, logo_icon
from meeting_scribe import gui


class RecordingIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_idle_gray_and_colored_band_moves_left_to_right(self):
        idle = logo_image(64)
        for x in range(64):
            for y in range(64):
                c = idle.pixelColor(x, y)
                self.assertEqual(c.red(), c.green())
                self.assertEqual(c.green(), c.blue())

        def color_center(frame):
            image = logo_image(64, frame / FRAME_COUNT)
            total = weighted = 0
            for x in range(64):
                for y in range(64):
                    c = image.pixelColor(x, y)
                    weight = max(0, c.green()-c.red()) * c.alpha()
                    total += weight
                    weighted += weight * x
            self.assertGreater(total, 0)
            return weighted / total
        self.assertLess(color_center(6), color_center(18))

    def test_animation_reuses_frames_and_stops_on_idle(self):
        window = QWidget()
        status = RecordingIcon(window)
        self.assertFalse(status.timer.isActive())
        idle_key = window.windowIcon().cacheKey()
        status.set_recording(True)
        keys = []
        for _ in range(FRAME_COUNT):
            keys.append(window.windowIcon().cacheKey())
            status.advance()
        self.assertGreater(len(set(keys)), 1)
        self.assertEqual(window.windowIcon().cacheKey(), keys[0])
        with patch('meeting_scribe.recording_icon.logo_image', side_effect=AssertionError('rendered again')):
            status.advance()
        status.set_recording(False)
        self.assertFalse(status.timer.isActive())
        self.assertEqual(window.windowIcon().cacheKey(), idle_key)
        window.close()

    def test_only_active_unpaused_healthy_recording_animates(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, MEETING_SCRIBE_DATA=tmp), \
                patch.object(gui.MainWindow, 'refresh_devices'):
            window = gui.MainWindow(recover_sessions=False)
            window.timer.stop()
            window.save_settings = lambda: None
            try:
                for active, paused, stopping, error, expected in (
                    (False, False, False, '', False),
                    (True, False, False, '', True),
                    (True, True, False, '', False),
                    (True, False, False, '', True),
                    (True, False, True, '', False),
                    (True, False, False, 'device lost', False),
                ):
                    window.recorder = SimpleNamespace(active=active, paused=paused, error=error)
                    window.stopping = stopping
                    window.update_recording_icon()
                    self.assertEqual(window.recording_icon.timer.isActive(), expected)
                window.recorder = None
                window.update_recording_icon()
                self.assertFalse(window.recording_icon.timer.isActive())
            finally:
                window.recorder = None
                window.stopping = False
                window.close()
