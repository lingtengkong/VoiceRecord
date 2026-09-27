import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import numpy as np
import soundfile as sf

from meeting_scribe.live import Track, next_window, LiveEngine, rows_from_segments, read_audio
from meeting_scribe.transcription import export_transcript
from meeting_scribe.storage import read_json
from meeting_scribe.storage import atomic_json


def capture(position, state="recording", boundaries=()):
    return {"position": position, "state": state, "pause_boundaries": list(boundaries),
            "tracks": {key: {"rate": 16000, "frames": round(position*16000)}
                       for key in ("microphone", "system")}}


class LiveTests(unittest.TestCase):
    def test_windows_readers_do_not_break_manifest_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            atomic_json(path, {"frame": 0})
            stop = threading.Event()
            def reader():
                while not stop.is_set():
                    read_json(path)
            thread = threading.Thread(target=reader)
            thread.start()
            try:
                for frame in range(60):
                    atomic_json(path, {"frame": frame})
                self.assertEqual(read_json(path)["frame"], 59)
            finally:
                stop.set()
                thread.join()

    def test_pause_flushes_and_context_never_crosses_resume(self):
        track = Track("system", cursor=4, seen=5)
        left, end, hard, boundary = next_window(track, capture(12, boundaries=[5.5]))
        self.assertEqual(end, 5.5)
        self.assertTrue(hard)
        track.cursor = track.context_floor = 5.5
        track.seen = 5.5
        left, end, hard, boundary = next_window(track, capture(8, boundaries=[5.5]))
        self.assertEqual(left, 5.5)
        self.assertFalse(hard)

    def test_tail_under_update_interval_is_not_lost(self):
        track = Track("system", cursor=10, seen=10)
        self.assertIsNone(next_window(track, capture(10.7)))
        self.assertTrue(next_window(track, capture(10.7, "finished"))[2])

    def test_window_limit_does_not_stall_between_update_ticks(self):
        track = Track("system", cursor=0, seen=11.25)
        window = next_window(track, capture(30, "finished", [14.27]))
        self.assertIsNotNone(window)
        self.assertEqual(window[1], 13)

    def test_context_word_ownership_no_duplicate(self):
        segment = NS(words=[NS(start=0, end=.4, word=" prior"),
                            NS(start=1, end=1.3, word=" new"), NS(start=2.1, end=2.4, word=" later")])
        rows = rows_from_segments([segment], 9, 10, 11, "会议声音")
        self.assertEqual(rows[0]["text"], "new")

    def test_partial_is_replaced_then_committed_once(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            sf.write(folder / "system.wav", np.full(7*16000, .1), 16000, subtype="PCM_16")
            class Model:
                count = 0
                def transcribe(self, audio, **kwargs):
                    self.count += 1
                    text = " draft" if self.count == 1 else " corrected"
                    return iter([NS(words=[NS(start=.5, end=1.5, word=text)])]), None
            events = []
            engine = LiveEngine(folder, Model(), on_event=lambda kind, **kw: events.append((kind, kw)))
            def vad(data, options):
                return [{"start": 0, "end": len(data)}]
            with patch("faster_whisper.vad.get_speech_timestamps", vad):
                state = capture(3)
                state["tracks"].pop("microphone")
                engine.step(state)
                self.assertFalse(engine.rows)
                self.assertEqual(engine.tracks["system"].partial[0]["text"], "draft")
                state.update(position=6, state="finished")
                state["tracks"]["system"]["frames"] = 6*16000
                engine.step(state)
                engine.step(state)
                self.assertEqual([r["text"] for r in engine.rows], ["corrected"])
                self.assertEqual(sum(k=="live_commit" for k, _ in events), 1)

    def test_backlog_is_explicit_and_recording_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            path = folder / "system.wav"
            sf.write(path, np.zeros(45*16000), 16000, subtype="PCM_16")
            before = path.read_bytes()
            engine = LiveEngine(folder, None, on_event=lambda *_a, **_kw: None)
            state = capture(45, "finished")
            state["tracks"].pop("microphone")
            with patch("faster_whisper.vad.get_speech_timestamps", return_value=[]):
                engine.step(state)
            self.assertEqual(engine.skipped[0]["end"], 37)
            self.assertEqual(before, path.read_bytes())
            result = engine.finish()
            self.assertIn("需会后重转", (result / "transcript.txt").read_text(encoding="utf-8-sig"))
            self.assertFalse((folder / "latest_transcript.json").exists())

    def test_live_export_does_not_overwrite_final(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            final = export_transcript(folder, [], {"complete": True})
            live = export_transcript(folder, [], {"mode": "live", "complete": False, "reason": "取消"})
            self.assertEqual(read_json(folder / "latest_transcript.json")["path"], str(final))
            self.assertEqual(read_json(folder / "latest_live_transcript.json")["path"], str(live))
            self.assertIn("未完成", (live / "transcript.txt").read_text(encoding="utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
