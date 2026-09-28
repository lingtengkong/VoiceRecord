"""Qt worker integration: synthetic PCM revealed over time, no real devices."""
import os, sys, time, shutil, json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtWidgets import QApplication
from meeting_scribe.storage import data_root, atomic_json, read_json
import meeting_scribe.gui as gui
import soundfile as sf
import numpy as np

cancel_test='--cancel' in sys.argv
folder=Path('artifacts/nemotron_live_cancel' if cancel_test else 'artifacts/nemotron_live_integration').resolve()
folder.mkdir(parents=True,exist_ok=True)
(folder/'live_cancel.flag').unlink(missing_ok=True)
audio,rate=sf.read(data_root()/'verification/nemotron3/three_speakers.wav',dtype='float32')
sf.write(folder/'system.wav',audio,rate,subtype='PCM_16')
mic=np.zeros_like(audio)
# Independent synthetic microphone speech during the English system turn.
mic[21*rate:26*rate]=audio[:5*rate]
sf.write(folder/'microphone.wav',mic,rate,subtype='PCM_16')
duration=len(audio)/rate
atomic_json(folder/'session.json',{'title':'Nemotron 实时接入验证（合成语音）','status':'saved','duration':duration})
def capture(position,state='recording',paused=False):
    atomic_json(folder/'live_capture.json',{'position':position,'state':state,'updated_at':time.time(),
        'pause_boundaries':[20] if paused else [],'tracks':{k:{'rate':rate,'frames':round(position*rate)} for k in ('microphone','system')}})
capture(0)
app=QApplication([])
gui.sessions=lambda:[folder]
w=gui.MainWindow(recover_sessions=False)
w.save_settings=lambda:None
w.live_engine.setCurrentIndex(w.live_engine.findData('nemotron'))
w.active_session=folder
w.show()
w.start_live()
start=time.monotonic()
last_publish=0
first_text=None
seen_during_capture=False
errors=[]
while w.live_process is not None and time.monotonic()-start<150:
    elapsed=time.monotonic()-start
    # 2x replay stresses scheduling while still publishing many incremental chunks.
    if elapsed<10: position=elapsed*2
    elif elapsed<11: position=20
    else: position=min(duration,(elapsed-1)*2)
    state='finished' if position>=duration else 'paused' if 10<=elapsed<11 else 'recording'
    if elapsed-last_publish>.25:
        capture(position,state,elapsed>=10)
        last_publish=elapsed
    app.processEvents()
    if w.live_rows or any(w.live_partials.values()):
        if first_text is None:first_text=elapsed
        if state!='finished':seen_during_capture=True
        if cancel_test and not w.live_cancelled:
            w.cancel_process()
    time.sleep(.02)
assert w.live_process is None,'Worker timeout'
assert w.live_result, w.live_errors
if cancel_test:
    assert not w.live_result['complete']
    assert sf.info(folder/'system.wav').frames == len(audio)
    assert not w.live_process
    w.close()
    print('PASS: live preview cancellation exits and preserves original synthetic audio; draft marked incomplete.')
    raise SystemExit(0)
assert w.live_result['complete'],w.live_result
result=Path(w.live_result['path'])
payload=read_json(result/'transcript.json')
rows=payload['segments']
assert seen_during_capture,'No output until after recording finished'
assert any(r.get('audio_source')=='我 / 麦克风' for r in rows),rows
assert any('参会者' in r['source'] for r in rows),rows
assert all(0<=r['start']<=r['end']<=duration+.1 for r in rows)
assert not any(r['start']<20<r['end'] for r in rows),'Crossed pause boundary'
w.grab().save(str(Path('artifacts/nemotron_live_preview.png').resolve()))
report={'passed':True,'replay_rate':2,'first_text_wall_seconds_including_load':first_text,
    'wall_seconds':time.monotonic()-start,'audio_seconds':duration,'segments':len(rows),
    'labels':sorted(set(r['source'] for r in rows)), 'metrics':read_json(result/'stream_metrics.json')}
atomic_json(Path('artifacts/nemotron_live_check.json'),report)
print(json.dumps(report,ensure_ascii=False,indent=2))
w.close()
