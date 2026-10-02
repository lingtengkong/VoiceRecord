import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from meeting_scribe.gui import MainWindow

class TranscriptionChainTests(unittest.TestCase):
    def state(self,kind='transcribe',segments=3):
        return SimpleNamespace(process=MagicMock(),read_process=MagicMock(),read_errors=MagicMock(),
            process_kind=kind,progress=MagicMock(),job_status=MagicMock(),
            process_result={'path':'new-transcript','segments':segments},process_folder=Path('meeting'),
            refresh_history=MagicMock(),start_diarization=MagicMock(),set_controls=MagicMock(),
            process_errors='test error',show_error=MagicMock(),start_summary=MagicMock(),version_combo=MagicMock(),
            summarize_after_transcription=True, recorder=None, selected=Path("meeting"),
            job_states={}, save_job_queue=MagicMock(), drain_jobs=MagicMock())

    def test_manual_transcription_stops_after_diarization(self):
        state=self.state('diarize')
        state.summarize_after_transcription=False
        MainWindow.process_finished(state,0,None)
        state.start_summary.assert_not_called()

    def test_auto_recording_completion_requests_full_chain(self):
        state=SimpleNamespace(pending_auto=True,live_process=None,recorder=None,task=None,process=None,
            active_session=Path('meeting'),refresh_history=MagicMock(),version_combo=MagicMock(),start_transcription=MagicMock(),
            auto_transcribe=MagicMock(),job_queue=[],job_states={},processing_options=lambda:{},save_job_queue=MagicMock(),drain_jobs=MagicMock())
        MainWindow.maybe_auto_transcribe(state)
        self.assertEqual(state.job_queue, [(Path("meeting"), {})])
        state.drain_jobs.assert_called_once()

    def test_uncheck_while_waiting_prevents_pending_auto_job(self):
        state=SimpleNamespace(pending_auto=True,live_process=None,recorder=None,task=None,process=None,
            active_session=Path('meeting'),refresh_history=MagicMock(),version_combo=MagicMock(),start_transcription=MagicMock(),
            auto_transcribe=MagicMock(),job_queue=[],job_states={},processing_options=lambda:{},save_job_queue=MagicMock(),drain_jobs=MagicMock())
        state.auto_transcribe.isChecked.return_value=False
        MainWindow.maybe_auto_transcribe(state)
        state.start_transcription.assert_not_called()
        self.assertFalse(state.pending_auto)

    def test_diarization_continues_to_summary_and_summary_selects_inline_view(self):
        state=self.state('diarize')
        MainWindow.process_finished(state,0,None)
        state.start_summary.assert_called_once()
        state=self.state('summary')
        MainWindow.process_finished(state,0,None)
        state.version_combo.findData.assert_called_with('summary')
        state.start_summary.assert_not_called()
        state.refresh_history.assert_called_once_with(preserve_preview=False)

    def test_diarization_failure_does_not_summarize(self):
        state=self.state('diarize')
        MainWindow.process_finished(state,1,None)
        state.start_summary.assert_not_called()
    def test_success_chains_exact_new_output(self):
        state=self.state()
        MainWindow.process_finished(state,0,None)
        state.start_diarization.assert_called_once_with(Path('new-transcript'),folder=Path('meeting'))
    def test_cancel_failure_and_empty_do_not_chain(self):
        for kind,code,segments in [('cancelled',0,3),('transcribe',1,3),('transcribe',0,0)]:
            state=self.state(kind,segments)
            MainWindow.process_finished(state,code,None)
            state.start_diarization.assert_not_called()
