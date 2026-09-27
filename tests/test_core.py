import struct
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

from meeting_scribe.audio import TrackWriter, Timeline, mix_tracks, recover_session
from meeting_scribe.storage import new_session, atomic_json, read_json
from meeting_scribe.transcription import export_transcript


class RecordingTests(unittest.TestCase):
    def test_late_audio_preserves_silence_and_tail(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "system.wav"
            errors = []
            writer = TrackWriter(path, 16000, errors.append)
            writer.push(2, np.full(8000, .3, dtype=np.float32))
            writer.finish(4)
            data, rate = sf.read(path)
            self.assertEqual(rate, 16000)
            self.assertEqual(len(data), 64000)
            self.assertLess(np.max(np.abs(data[:32000])), 1e-6)
            self.assertGreater(data[33000], .29)
            self.assertLess(np.max(np.abs(data[40000:])), 1e-6)
            self.assertFalse(errors)

    def test_pause_excluded_from_timestamp(self):
        with patch("meeting_scribe.audio.time.monotonic", return_value=100):
            clock = Timeline()
        with patch("meeting_scribe.audio.time.monotonic", return_value=110):
            clock.pause()
        self.assertEqual(clock.position(120), 10)
        with patch("meeting_scribe.audio.time.monotonic", return_value=125):
            clock.resume()
        self.assertEqual(clock.position(130), 15)

    def test_mix_different_sample_rates_without_clipping(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sf.write(root / "microphone.wav", np.full(44100, .8), 44100, subtype="PCM_16")
            sf.write(root / "system.wav", np.full(96000, .8), 48000, subtype="PCM_16")
            path = mix_tracks(root)
            samples, rate = sf.read(path)
            self.assertEqual(rate, 48000)
            self.assertEqual(len(samples), 96000)
            self.assertAlmostEqual(float(np.mean(samples[100:47000])), .8, places=3)
            self.assertAlmostEqual(float(np.mean(samples[49000:])), .4, places=3)
            self.assertLess(float(np.max(samples)), 1)

    def test_crash_recovery_patches_header_not_samples(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = new_session("恢复测试", Path(temp))
            path = folder / "microphone.wav"
            with wave.open(str(path), "wb") as f:
                f.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                f.writeframes(np.ones(16000, dtype="<i2").tobytes())
            payload = path.read_bytes()[44:]
            with path.open("r+b") as f:
                f.seek(4)
                f.write(struct.pack("<I", 36))
                f.seek(40)
                f.write(struct.pack("<I", 0))
            atomic_json(folder / "session.json", {"status": "recording", "title": "恢复测试"})
            result = recover_session(folder)
            self.assertEqual(result["status"], "recovered")
            self.assertEqual(sf.info(path).frames, 16000)
            self.assertEqual(payload, path.read_bytes()[44:])

    def test_transcript_order_timestamps_and_noninvented_speakers(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = new_session("测试会议", Path(temp))
            rows = [{"start": 12.5, "end": 13.1, "source": "会议声音", "text": "下周再讨论"},
                    {"start": 1, "end": 2, "source": "我 / 麦克风", "text": "先检查录音"}]
            output = export_transcript(folder, rows, {"complete": True})
            text = (output / "transcript.txt").read_text(encoding="utf-8-sig")
            self.assertLess(text.index("先检查录音"), text.index("下周再讨论"))
            self.assertIn("00:00:12,500", (output / "transcript.srt").read_text(encoding="utf-8-sig"))
            self.assertIn("未明确", (output / "summary_input.md").read_text(encoding="utf-8"))
            self.assertEqual(len(read_json(output / "transcript.json")["segments"]), 2)


if __name__ == "__main__":
    unittest.main()
