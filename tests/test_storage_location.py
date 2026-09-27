import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from meeting_scribe.storage import new_session, recordings_root, set_recordings_root, sessions
from meeting_scribe.transcription import export_transcript, model_path


class StorageLocationTests(unittest.TestCase):
    def test_switch_keeps_history_and_exports_with_recording(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"MEETING_SCRIBE_DATA": temp}):
            old = new_session("old")
            target = Path(temp) / "chosen"
            set_recordings_root(target)
            self.assertEqual(recordings_root(), target.resolve())
            new = new_session("new")
            self.assertEqual(new.parent, target.resolve())
            exported = export_transcript(new, [], {})
            self.assertEqual(exported.parent.parent, new)
            self.assertEqual(set(sessions()), {old, new})
            self.assertEqual(model_path("turbo"), Path(temp) / "models" / "turbo")
            set_recordings_root(Path(temp) / "third")
            self.assertEqual(set(sessions()), {old, new})

    def test_invalid_destination_preserves_current_setting(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"MEETING_SCRIBE_DATA": temp}):
            original = recordings_root()
            invalid = Path(temp) / "file.txt"
            invalid.write_text("keep")
            with self.assertRaises(OSError):
                set_recordings_root(invalid)
            self.assertEqual(recordings_root(), original)
            self.assertEqual(invalid.read_text(), "keep")
