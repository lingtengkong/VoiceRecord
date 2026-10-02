import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
import stat
from pathlib import Path
from unittest.mock import patch, MagicMock

from PySide6.QtWidgets import QApplication
from meeting_scribe import gui, storage


class HistoryActionsTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows read-only attributes')
    def test_delete_readonly_files_and_directories(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, MEETING_SCRIBE_DATA=tmp):
            folder = storage.new_session('Read only')
            nested = folder / 'live_transcripts' / 'version1'
            nested.mkdir(parents=True)
            transcript = nested / 'transcript.txt'
            transcript.write_text('sample', encoding='utf-8')
            transcript.chmod(stat.S_IREAD)
            nested.chmod(stat.S_IREAD)
            folder.chmod(stat.S_IREAD)
            storage.delete_session(folder)
            self.assertFalse(folder.exists())

    def test_rename_preserves_files_and_metadata_delete_only_target(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, MEETING_SCRIBE_DATA=tmp):
            first = storage.new_session('First')
            second = storage.new_session('Second')
            audio = first / 'mixed.wav'
            audio.write_bytes(b'test audio')
            before = storage.read_json(first / 'session.json')
            storage.rename_session(first, ' New name ')
            self.assertEqual(storage.read_json(first / 'session.json'), {**before, 'title': 'New name'})
            self.assertEqual(audio.read_bytes(), b'test audio')
            with self.assertRaises(ValueError):
                storage.rename_session(first, '  ')
            with self.assertRaises(ValueError):
                storage.delete_session(storage.recordings_root())
            outside = Path(tmp) / 'outside'
            outside.mkdir()
            storage.atomic_json(outside / 'session.json', before)
            with self.assertRaises(ValueError):
                storage.delete_session(outside)
            storage.delete_session(first)
            self.assertFalse(first.exists())
            self.assertTrue(second.exists())
            self.assertTrue(outside.exists())

    def test_delete_confirmation_and_playback_release(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, MEETING_SCRIBE_DATA=tmp), patch.object(gui.MainWindow, 'refresh_devices'):
            folder = storage.new_session('Test')
            w = gui.MainWindow(recover_sessions=False)
            w.save_settings = lambda: None
            try:
                with patch.object(gui, 'QMessageBox') as box, patch.object(gui, 'delete_session') as delete:
                    dialog = box.return_value
                    confirm, cancel = object(), object()
                    dialog.addButton.side_effect = [confirm, cancel]
                    dialog.clickedButton.return_value = cancel
                    w.delete_meeting(folder)
                    delete.assert_not_called()
                    dialog.setDefaultButton.assert_called_once_with(cancel)
                calls = MagicMock()
                with patch.object(gui, 'QMessageBox') as box, patch.object(gui, 'delete_session', calls.delete), patch.object(w.playback, 'load', calls.load), patch.object(w, 'refresh_history'):
                    dialog = box.return_value
                    confirm, cancel = object(), object()
                    dialog.addButton.side_effect = [confirm, cancel]
                    dialog.clickedButton.return_value = confirm
                    w.delete_meeting(folder)
                    self.assertEqual(calls.mock_calls[0].args, (None,))
                    self.assertEqual(calls.mock_calls[1].args, (folder,))
            finally:
                w.close()


if __name__ == '__main__':
    unittest.main()
