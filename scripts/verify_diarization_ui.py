"""Local synthetic integration check; no recording or user transcript access."""
import os
import sys
import time
from pathlib import Path

project = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtWidgets import QApplication
from meeting_scribe.storage import data_root, read_json, atomic_json
from meeting_scribe.transcription import export_transcript
import meeting_scribe.gui as gui
import soundfile as sf
from scipy.signal import resample_poly

fixture = data_root() / 'verification/nemotron3'
session = project / 'artifacts/diarization_integration'
session.mkdir(parents=True, exist_ok=True)
audio, rate = sf.read(fixture / 'three_speakers.wav', dtype='float32')
sf.write(session / 'system.wav', resample_poly(audio, 3, 1), 48000)
atomic_json(session / 'session.json', {'title':'合成说话者接入测试（非真实会议）',
            'status':'saved', 'duration':len(audio)/rate})
rows = [{'start':t['start'], 'end':t['end'], 'source':'会议声音',
         'text':f'合成发言 {i+1}，用于检查原文和时间戳是否保留。'}
        for i,t in enumerate(read_json(fixture / 'reference.json')['turns'])]
rows.insert(0, {'start':0,'end':1,'source':'我 / 麦克风','text':'本机合成测试文字。'})
original = export_transcript(session, rows, {})
gui.sessions = lambda: [session]
app = QApplication([])
window = gui.MainWindow(recover_sessions=False)
window.save_settings = lambda: None
# This component check stops before Ollama; verify_summary_pipeline covers the full chain.
window.start_summary=lambda:None
errors = []
window.show_error = errors.append
window.show()
window.device_combo.setCurrentIndex(window.device_combo.findData('cuda'))
window.start_diarization()
assert window.process is not None and not window.transcribe_button.isEnabled()
deadline = time.monotonic()+120
while window.process is not None and time.monotonic() < deadline:
    app.processEvents()
    time.sleep(.02)
assert window.process is None, 'Worker did not finish'
assert not errors, errors
assert window.version_combo.currentData() == 'speakers', window.job_status.text()
result = window.transcript_folder()
output = read_json(result / 'transcript.json')['segments']
assert [(r['start'],r['end'],r['text']) for r in rows] == [(r['start'],r['end'],r['text']) for r in output]
assert output[0]['source'] == '本机麦克风'
assert any('参会者' in r['source'] for r in output)
assert read_json(session / 'latest_transcript.json')['path'] == str(original)
assert (result / 'speaker_turns.json').is_file()
assert window.transcribe_button.isEnabled()
app.processEvents()
window.grab().save(str(project / 'artifacts/diarization_gui_preview.png'))
atomic_json(project / 'artifacts/diarization_ui_check.json',
            {'passed':True,'session':str(session),'result':str(result),
             'labels':[r['source'] for r in output], 'sample_rate':48000})
print('PASS: GPU worker through GUI; 48kHz resampling; labels; unchanged words/times; preserved original; preview rendered.')
window.close()
