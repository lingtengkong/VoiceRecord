import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
from PySide6.QtWidgets import QApplication
import meeting_scribe.gui as gui
from meeting_scribe.storage import atomic_json

class SummaryButtonTests(unittest.TestCase):
    def test_auto_summary_checkbox_remains_editable_during_capture_and_pause(self):
        app=QApplication.instance() or QApplication([])
        with patch.object(gui,'sessions',return_value=[]), patch.object(gui.MainWindow,'refresh_devices'):
            w=gui.MainWindow(recover_sessions=False)
            w.save_settings=lambda:None
            try:
                for paused in (False,True):
                    w.recorder=MagicMock(paused=paused)
                    w.live_process=MagicMock()
                    w.set_controls()
                    self.assertTrue(w.auto_transcribe.isEnabled())
                    self.assertFalse(w.model_combo.isEnabled())
                    w.auto_transcribe.setChecked(True)
                    w.auto_transcribe.click()
                    self.assertFalse(w.auto_transcribe.isChecked())
                w.recorder=None
                w.set_controls()
                self.assertTrue(w.auto_transcribe.isEnabled())
                w.live_process=None
                w.process=MagicMock()
                w.set_controls()
                self.assertTrue(w.auto_transcribe.isEnabled())
            finally:
                w.recorder=w.process=w.live_process=None
                w.close()

    def test_empty_transcript_disabled_and_summary_view_reuses_speaker_text(self):
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp, patch.object(gui,'sessions',return_value=[]), patch.object(gui.MainWindow,'refresh_devices'):
            w=gui.MainWindow(recover_sessions=False)
            w.save_settings=lambda:None
            w.start_process=MagicMock()
            try:
                folder=Path(tmp)
                w.selected=folder
                w.set_controls()
                self.assertFalse(w.summarize_button.isEnabled())
                transcript=folder/'speaker_transcripts'/'v1'
                transcript.mkdir(parents=True)
                atomic_json(folder/'latest_speaker_transcript.json',{'path':str(transcript)})
                atomic_json(transcript/'transcript.json',{'segments':[{'text':'  '}]})
                w.set_controls()
                self.assertFalse(w.summarize_button.isEnabled())
                atomic_json(transcript/'transcript.json',{'segments':[{'text':'Test meeting text'}]})
                w.version_combo.setCurrentIndex(w.version_combo.findData('summary'))
                w.set_controls()
                self.assertTrue(w.summarize_button.isEnabled())
                w.summarize_button.click()
                kind,args=w.start_process.call_args.args
                self.assertEqual(kind,'summary')
                self.assertEqual(args[2],str(transcript))
            finally:w.close()
