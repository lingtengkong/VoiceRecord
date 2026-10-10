import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from meeting_scribe import gui
from meeting_scribe.audio import Device, Recorder


class SilenceWarningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, MEETING_SCRIBE_DATA=self.tmp.name)
        self.env.start()
        self.clock = patch('meeting_scribe.audio.time.monotonic', return_value=100.)
        self.now = self.clock.start()
        self.mic = Device(1, 'Test microphone', 48000, 1, False)
        self.system = Device(2, 'Test speaker [Loopback]', 48000, 2, True)
        self.recorder = Recorder(Path(self.tmp.name), self.mic, self.system)
        self.recorder.active = True

    def tearDown(self):
        self.clock.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_only_system_signal_resets_dual_track_silence(self):
        self.now.return_value = 129.9
        self.assertAlmostEqual(self.recorder.silence_seconds(), 29.9)
        self.now.return_value = 130.
        self.assertEqual(self.recorder.silence_seconds(), 30.)
        self.recorder.last_signal['microphone'] = 129.
        self.assertEqual(self.recorder.silence_seconds(), 30.)
        self.recorder.last_signal['system'] = 125.
        self.assertEqual(self.recorder.silence_seconds(), 5.)

    def test_pause_time_excluded_and_inactive_is_silent_check_disabled(self):
        self.now.return_value = 110.
        self.recorder.timeline.pause()
        self.recorder.paused = True
        self.now.return_value = 210.
        self.assertEqual(self.recorder.silence_seconds(), 0.)
        self.recorder.timeline.resume()
        self.recorder.paused = False
        self.now.return_value = 230.
        self.assertEqual(self.recorder.silence_seconds(), 30.)

        self.recorder.active = False
        self.assertEqual(self.recorder.silence_seconds(), 0.)

    def test_microphone_only_ignores_other_track(self):
        self.recorder.devices = {'microphone': self.mic}
        self.recorder.last_signal['system'] = 129.
        self.now.return_value = 130.
        self.assertEqual(self.recorder.silence_seconds(), 30.)
        self.recorder.last_signal['microphone'] = 129.
        self.assertEqual(self.recorder.silence_seconds(), 1.)

    def test_modeless_warning_once_per_silent_period_and_recording_continues(self):
        with patch.object(gui.MainWindow, 'refresh_devices'):
            w = gui.MainWindow(recover_sessions=False)
        w.timer.stop()
        w.recorder = self.recorder
        try:
            self.now.return_value = 129.9
            w.check_recording_silence()
            self.assertIsNone(w.silence_dialog)
            self.now.return_value = 130.
            self.recorder.last_signal['microphone'] = 129.
            w.check_recording_silence()
            self.assertTrue(w.silence_dialog.isVisible())
            self.assertFalse(w.silence_dialog.isModal())
            self.assertIn('Test speaker', w.silence_dialog.text())
            self.assertIn('会议声音', w.silence_dialog.text())
            self.assertNotIn('Test microphone', w.silence_dialog.text())
            self.assertTrue(self.recorder.active)
            w.silence_dialog.accept()
            self.now.return_value = 190.
            w.check_recording_silence()
            self.assertFalse(w.silence_dialog.isVisible())
            self.assertFalse(w.silence_hint.isHidden())
            self.recorder.last_signal['system'] = 190.
            w.check_recording_silence()
            self.assertTrue(w.silence_hint.isHidden())
            self.now.return_value = 220.
            w.check_recording_silence()
            self.assertTrue(w.silence_dialog.isVisible())
            self.recorder.paused = True
            w.check_recording_silence()
            self.assertFalse(w.silence_dialog.isVisible())
            self.assertTrue(w.silence_hint.isHidden())
        finally:
            w.recorder = None
            w.close()
