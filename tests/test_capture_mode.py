import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock,patch
from meeting_scribe.audio import Recorder,Device
from meeting_scribe.sources import audio_sources,speaker_audio_key
from meeting_scribe.storage import read_json,atomic_json
from meeting_scribe.live import LiveEngine
from meeting_scribe.diarization import assign_speakers

class CaptureModeTests(unittest.TestCase):
    def test_single_capture_opens_only_mic_and_saves_mode_without_mix(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            mic=Device(2,'test microphone',16000,1,False)
            engine=MagicMock()
            engine.get_device_info_by_index.return_value={'name':mic.name,'isLoopbackDevice':False}
            with patch('meeting_scribe.audio.pa.PyAudio',return_value=engine):
                rec=Recorder(folder,mic,None)
                rec.start()
                try:
                    self.assertEqual(engine.open.call_count,1)
                    self.assertEqual(engine.open.call_args.kwargs['input_device_index'],2)
                    rec.pause()
                    rec.resume()
                finally: rec.stop()
            self.assertEqual(read_json(folder/'session.json')['capture_mode'],'microphone_only')
            self.assertEqual(list(read_json(folder/'live_capture.json')['tracks']),['microphone'])
            self.assertTrue((folder/'microphone.wav').is_file())
            self.assertFalse((folder/'system.wav').exists())
            self.assertFalse((folder/'mixed.wav').exists())
            live=LiveEngine(folder,MagicMock())
            self.assertEqual(list(live.tracks),['microphone'])
            source=audio_sources(folder)['microphone']
            rows=assign_speakers([dict(start=0,end=1,source=source,text='hello')],[dict(Start=0,End=1,Speaker=1)])
            self.assertEqual(rows[0]['source'],'参会者 2')
            self.assertEqual(speaker_audio_key(folder),'microphone')
    def test_legacy_sessions_remain_dual_and_local_mic_label_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            self.assertEqual(set(audio_sources(folder)),{'microphone','system'})
            self.assertEqual(speaker_audio_key(folder),'system')
            row=dict(start=0,end=1,source=audio_sources(folder)['microphone'],text='hello')
            self.assertEqual(assign_speakers([row],[])[0]['source'],'本机麦克风')
