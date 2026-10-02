import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from meeting_scribe import gui
from meeting_scribe.storage import atomic_json, read_json
from meeting_scribe.live_nemotron import BacklogGuard


class BackgroundRecordingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, MEETING_SCRIBE_DATA=self.tmp.name)
        self.env.start()
        self.devices = patch.object(gui.MainWindow, 'refresh_devices')
        self.devices.start()
        self.w = gui.MainWindow(recover_sessions=False)
        self.w.timer.stop()
        self.w.save_settings = MagicMock()
        self.w.show_error = MagicMock()
        self.w.mic_combo.addItem('mic', SimpleNamespace(name='mic'))
        self.w.system_combo.addItem('system', SimpleNamespace(name='system'))
        self.a = Path(self.tmp.name)/'recordings'/'a'
        self.b = self.a.with_name('b')
        for folder in (self.a, self.b):
            folder.mkdir(parents=True)
            atomic_json(folder/'session.json', {'title': folder.name, 'status': 'saved'})
        self.w.selected = self.a

    def tearDown(self):
        self.w.process = self.w.recorder = self.w.task = self.w.live_process = None
        self.w.job_queue.clear()
        self.w.close()
        self.devices.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_recording_buttons_work_during_summary(self):
        w = self.w
        w.process = MagicMock()
        w.process_kind = 'summary'
        w.process_folder = self.a
        w.set_controls()
        self.assertTrue(w.start_button.isEnabled())
        with patch.object(gui, 'Recorder') as recorder, patch.object(w, 'run_task'):
            w.start_recording()
            recorder.assert_called_once()
        w.recording_started(None)
        self.assertIsNone(w.live_process)
        self.assertIn('仅录音', w.live_status.text())
        w.set_controls()
        self.assertTrue(w.pause_button.isEnabled())
        self.assertTrue(w.stop_button.isEnabled())

    def test_saved_second_meeting_queues_while_first_runs(self):
        w = self.w
        w.process = MagicMock()
        w.process_folder = self.a
        w.process_kind = 'summary'
        w.active_session = self.b
        w.pending_auto = True
        w.auto_transcribe.setChecked(True)
        w.maybe_auto_transcribe()
        self.assertEqual([f for f, _ in w.job_queue], [self.b])
        saved = read_json(w.queue_path)
        self.assertEqual(saved[0]['folder'], str(self.a))
        self.assertTrue(saved[0]['running'])
        self.assertEqual(saved[1]['folder'], str(self.b))
        w.update_job_indicators()
        self.assertTrue(any('等待处理' in w.history.item(i).text() for i in range(w.history.count())))

    def test_chain_stays_on_old_meeting_while_recording_new_one(self):
        w = self.w
        w.recorder = MagicMock()
        w.active_session = self.b
        w.selected = self.b
        w.process = MagicMock()
        w.process_folder = self.a
        w.process_kind = 'transcribe'
        w.process_result = {'path': str(self.a/'transcript'), 'segments': 2}
        w.summarize_after_transcription = True
        w.read_process = w.read_errors = MagicMock()
        w.preview.setPlainText("New meeting view")
        w.start_diarization = MagicMock()
        w.process_finished(0, None)
        w.start_diarization.assert_called_once_with(self.a/'transcript', folder=self.a)
        self.assertEqual(w.selected, self.b)
        self.assertEqual(w.preview.toPlainText(), "New meeting view")
        w.process = MagicMock()
        w.process_kind = 'diarize'
        w.process_result = {'path': str(self.a/'speakers')}
        w.start_summary = MagicMock()
        w.process_finished(0, None)
        w.start_summary.assert_called_once_with(session=self.a, transcript=self.a/'speakers')
        self.assertEqual(w.selected, self.b)

    def test_queue_can_launch_during_audio_capture_but_not_live_gpu(self):
        w = self.w
        options = w.processing_options()
        w.job_queue = [(self.a, options), (self.b, options)]
        w.recorder = MagicMock()
        w.live_process = MagicMock()
        w.start_transcription = MagicMock(side_effect=lambda **kw: setattr(w, 'process', MagicMock()))
        w.drain_jobs()
        w.start_transcription.assert_not_called()
        w.live_process = None
        w.drain_jobs()
        w.start_transcription.assert_called_once_with(summarize_after=True, folder=self.a, options=options)
        self.assertEqual([f for f, _ in w.job_queue], [self.b])

    def test_incomplete_empty_live_result_is_not_success(self):
        w = self.w
        w.live_folder = self.a
        w.live_process = MagicMock()
        w.live_result = {'segments': 0, 'complete': False, 'reason': '持续积压'}
        w.read_live = w.read_live_errors = MagicMock()
        w.live_finished(0, None)
        self.assertIn('未完成', w.live_status.text())
        self.assertIn('持续积压', w.live_status.text())

    def test_uncheck_during_live_drain_cancels_current_auto_job(self):
        w = self.w
        w.active_session = self.a
        w.job_queue = [(self.a, w.processing_options())]
        w.auto_transcribe.setChecked(False)
        w.start_transcription = MagicMock()
        w.drain_jobs()
        w.start_transcription.assert_not_called()
        self.assertEqual(w.job_queue, [])
        self.assertIn('已取消', w.job_states[self.a])

    def test_restart_restores_waiting_and_marks_interrupted_job(self):
        w = self.w
        w.process = MagicMock()
        w.process_folder = self.a
        w.job_queue = [(self.b, w.processing_options())]
        w.save_job_queue()
        recovered = gui.MainWindow(recover_sessions=True)
        try:
            recovered.timer.stop()
            self.assertEqual([f for f, _ in recovered.job_queue], [self.b])
            self.assertIn('中断', recovered.job_states[self.a])
        finally:
            recovered.job_queue.clear()
            recovered.close()


class BacklogGuardTests(unittest.TestCase):
    def test_cold_start_and_catching_up_do_not_abort(self):
        guard = BacklogGuard(0)
        self.assertFalse(guard.overloaded(47, 1))
        self.assertFalse(guard.overloaded(65, 89))
        self.assertFalse(guard.overloaded(60, 95))
        self.assertFalse(guard.overloaded(45, 140))

    def test_sustained_overload_aborts_but_recovery_resets(self):
        guard = BacklogGuard(0)
        self.assertFalse(guard.overloaded(45, 91))
        self.assertFalse(guard.overloaded(50, 120))
        self.assertTrue(guard.overloaded(55, 122))
        self.assertFalse(guard.overloaded(20, 123))
        self.assertFalse(guard.overloaded(50, 130))
