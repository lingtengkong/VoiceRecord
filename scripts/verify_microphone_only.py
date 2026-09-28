"""Synthetic one-track regression: GUI batch pipeline plus both live engines."""
import os,sys,time,shutil,subprocess
from pathlib import Path
project=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(project))
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtWidgets import QApplication
import meeting_scribe.gui as gui
from meeting_scribe.storage import atomic_json,read_json
from meeting_scribe.diarization import runtime_path
import soundfile as sf
session=project/'artifacts/microphone_only_integration'
session.mkdir(exist_ok=True)
shutil.copyfile(project/'artifacts/diarization_integration/system.wav',session/'microphone.wav')
info=sf.info(session/'microphone.wav')
atomic_json(session/'session.json',dict(title='单麦克风合成验证',capture_mode='microphone_only',duration=info.duration,status='saved'))
atomic_json(session/'live_capture.json',dict(state='finished',position=info.duration,updated_at=time.time(),pause_boundaries=[30.0],tracks={'microphone':dict(rate=info.samplerate,frames=info.frames)}))
gui.sessions=lambda:[session]
app=QApplication([])
w=gui.MainWindow(recover_sessions=False)
w.save_settings=lambda:None
# This component check stops before Ollama; verify_summary_pipeline covers the full chain.
w.start_summary=lambda:None
errors=[]
w.show_error=errors.append
w.show()
w.capture_mode.setCurrentIndex(w.capture_mode.findData('microphone_only'))
w.system_combo.clear()
assert w.start_button.isEnabled()
assert not w.system_combo.isEnabled()
assert w.playback.path==session/'microphone.wav'
w.model_combo.setCurrentIndex(w.model_combo.findData('turbo'))
w.start_transcription()
deadline=time.monotonic()+180
while w.process is not None and time.monotonic()<deadline:
    app.processEvents();time.sleep(.02)
assert w.process is None,'timeout'
assert not errors,errors
assert w.version_combo.currentData()=='speakers'
rows=read_json(w.transcript_folder()/'transcript.json')['segments']
assert rows and all(r['source']!='本机麦克风' for r in rows)
assert any('参会者' in r['source'] for r in rows)
w.grab().save(str(project/'artifacts/microphone_only_ui.png'))
w.close()
print('PASS: one-track GUI batch + diarization + playback, no output device required',flush=True)
for runtime,module,extra in [(sys.executable,'meeting_scribe.live',[]),(str(runtime_path()),'meeting_scribe.live_nemotron',['--replay'])]:
    p=subprocess.run([runtime,'-m',module,str(session),*extra],cwd=project,capture_output=True,text=True,encoding='utf-8',timeout=180)
    assert p.returncode==0,p.stderr[-1500:]+p.stdout[-1500:]
    data=read_json(Path(read_json(session/'latest_live_transcript.json')['path'])/'transcript.json')
    assert data['metadata']['complete']
    assert data['segments'] and all(r['source'] not in ('本机麦克风','我 / 麦克风') for r in data['segments'])
    print('PASS:',module,'one track, pause boundary, completion',flush=True)
assert not (session/'system.wav').exists() and not (session/'mixed.wav').exists()
