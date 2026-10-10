import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import soundfile as sf
from PySide6.QtWidgets import QApplication
from meeting_scribe.audio import Recorder, Device
from meeting_scribe.storage import read_json
from meeting_scribe import gui


class DeviceSwitchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.mic = Device(1, 'Old mic', 48000, 1, False)
        self.system = Device(2, 'Speaker', 48000, 2, True)
        self.new = Device(3, 'New mic', 44100, 2, False)
        self.original = MagicMock()
        self.original.get_device_info_by_index.side_effect = lambda i: {
            'name': self.mic.name if i == 1 else self.system.name, 'isLoopbackDevice': i == 2}
        self.original.open.side_effect = [MagicMock(), MagicMock()]
        self.replacement = MagicMock()
        self.replacement.get_device_info_by_index.return_value = {'name': self.new.name, 'isLoopbackDevice': False}
        self.factory = patch('meeting_scribe.audio.pa.PyAudio', side_effect=[self.original, self.replacement])
        self.make_engine = self.factory.start()
        self.rec = Recorder(self.folder, self.mic, self.system)
        self.rec.start()

    def tearDown(self):
        self.rec.stop(create_mix=False)
        self.factory.stop()
        self.tmp.cleanup()

    def test_switch_preserves_existing_audio_other_stream_and_native_rate_conversion(self):
        writer = self.rec.writers['microphone']
        old_stream = self.rec.streams['microphone']
        other = self.rec.streams['system']
        old_callback = self.original.open.call_args_list[0].kwargs['stream_callback']
        writer.push(0., np.full(4800, .25, dtype=np.float32))
        self.rec.timeline.position = MagicMock(return_value=.1)
        self.rec.switch_device('microphone', self.new)
        self.assertIs(self.rec.writers['microphone'], writer)
        self.assertIs(self.rec.streams['system'], other)
        other.close.assert_not_called()
        old_stream.close.assert_called_once()
        self.assertEqual(read_json(self.folder/'live_capture.json')['tracks']['microphone']['rate'], 48000)
        self.assertEqual(read_json(self.folder/'session.json')['device_changes'][0]['previous']['name'], 'Old mic')
        callback = self.replacement.open.call_args.kwargs['stream_callback']
        callback(np.full((2205,2), .5, dtype=np.float32).tobytes(), 2205, {}, 0)
        # A late old-device callback must not append over the replacement source.
        old_callback(np.ones(2400, dtype=np.float32).tobytes(), 2400, {}, 0)
        self.rec.timeline.position.return_value = .15
        self.rec.stop(create_mix=False)
        data, rate = sf.read(self.folder/'microphone.wav')
        self.assertEqual(rate, 48000)
        self.assertEqual(len(data), 7200)
        self.assertAlmostEqual(float(data[1000]), .25, places=3)
        self.assertAlmostEqual(float(data[6000]), .5, places=3)
        self.assertFalse(self.rec.error)
        # Avoid finalizing the same WAV twice in tearDown.
        self.rec.writers.clear()

    def test_failed_replacement_leaves_old_source_running(self):
        stream = self.rec.streams['microphone']
        self.replacement.open.side_effect = OSError('device unavailable')
        with self.assertRaisesRegex(RuntimeError, '原设备继续录音'):
            self.rec.switch_device('microphone', self.new)
        self.assertIs(self.rec.streams['microphone'], stream)
        self.assertEqual(self.rec.devices['microphone'], self.mic)
        self.assertTrue(self.rec.stream_gates['microphone'].is_set())
        stream.close.assert_not_called()
        self.assertFalse(self.rec.switching)
        self.assertFalse(self.rec.error)

    def test_start_failure_and_changed_index_do_not_replace_old_source(self):
        self.replacement.open.return_value.start_stream.side_effect = OSError('cannot start')
        with self.assertRaises(RuntimeError):
            self.rec.switch_device('microphone', self.new)
        self.assertEqual(self.rec.devices['microphone'], self.mic)
        self.replacement.open.return_value.close.assert_called_once()

    def test_paused_switch_does_not_resume_recording(self):
        self.rec.pause()
        self.rec.switch_device('microphone', self.new)
        callback = self.replacement.open.call_args.kwargs['stream_callback']
        with patch.object(self.rec.writers['microphone'], 'push') as push:
            callback(np.ones((2205,2), dtype=np.float32).tobytes(), 2205, {}, 0)
            push.assert_not_called()
        self.assertTrue(self.rec.paused)
        self.assertEqual(self.rec.capture_state, 'paused')

    def test_changed_index_is_rejected_without_closing_old_stream(self):
        self.replacement.get_device_info_by_index.return_value['name'] = 'Different device'
        old = self.rec.streams['microphone']
        with self.assertRaisesRegex(RuntimeError, '音频设备已变化'):
            self.rec.switch_device('microphone', self.new)
        self.replacement.open.assert_not_called()
        old.close.assert_not_called()

    def test_system_switch_keeps_microphone_and_recording_timeline(self):
        new = Device(4, 'New speaker', 96000, 2, True)
        self.replacement.get_device_info_by_index.return_value = {'name': new.name, 'isLoopbackDevice': True}
        mic = self.rec.streams['microphone']
        timeline = self.rec.timeline
        writer = self.rec.writers['system']
        self.rec.switch_device('system', new)
        self.assertIs(self.rec.streams['microphone'], mic)
        self.assertIs(self.rec.timeline, timeline)
        self.assertIs(self.rec.writers['system'], writer)
        self.assertEqual(self.rec.devices['system'], new)
        self.assertEqual(read_json(self.folder/'session.json')['device_changes'][0]['track'], 'system')

    def test_repeated_switch_releases_previous_replacement_engine(self):
        self.rec.switch_device('microphone', self.new)
        third = MagicMock()
        third.get_device_info_by_index.return_value = {'name': self.mic.name, 'isLoopbackDevice': False}
        self.make_engine.side_effect = [third]
        self.rec.switch_device('microphone', self.mic)
        self.replacement.terminate.assert_called_once()
        self.assertIs(self.rec.extra_engines['microphone'], third)
        self.assertEqual(len(read_json(self.folder/'session.json')['device_changes']), 2)


class DeviceSwitchUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_controls_enabled_with_background_job_and_explicit_selection_dispatches(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, MEETING_SCRIBE_DATA=tmp), \
                patch.object(gui.MainWindow, 'refresh_devices'):
            w = gui.MainWindow(recover_sessions=False)
            w.timer.stop()
            mic = Device(1, 'Old mic', 48000, 1, False)
            new = Device(3, 'New mic', 44100, 2, False)
            w.mic_combo.addItem(mic.name, mic)
            w.mic_combo.addItem(new.name, new)
            w.recorder = MagicMock(active=True, devices={'microphone': mic})
            w.process = MagicMock()
            w.process_kind = 'summary'
            try:
                w.set_controls()
                self.assertTrue(w.mic_combo.isEnabled())
                self.assertTrue(w.system_combo.isEnabled())
                self.assertTrue(w.refresh_button.isEnabled())
                with patch.object(w, 'run_task') as run:
                    w.mic_combo.setCurrentIndex(1)
                    run.assert_not_called()
                    w.mic_combo.activated.emit(1)
                    run.call_args.args[0]()
                    w.recorder.switch_device.assert_called_once_with('microphone', new)
                w.sync_recording_devices()
                self.assertEqual(w.mic_combo.currentData(), mic)
                event = MagicMock()
                w.mic_combo.wheelEvent(event)
                event.ignore.assert_called_once()
            finally:
                w.recorder = w.process = None
                w.close()
