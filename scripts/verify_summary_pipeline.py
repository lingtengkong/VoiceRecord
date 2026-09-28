"""Exercise ASR, diarization and real local Ollama on a synthetic sample."""
import os,sys,time
from pathlib import Path
project=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(project))
os.environ['QT_QPA_PLATFORM']='offscreen'
import soundfile as sf
from PySide6.QtWidgets import QApplication,QPushButton
import meeting_scribe.gui as gui
from meeting_scribe.storage import atomic_json,read_json
from meeting_scribe.summary import summary_headline
session=project/'artifacts/summary_pipeline_integration'
session.mkdir(exist_ok=True)
audio,rate=sf.read(project/'artifacts/diarization_integration/system.wav',dtype='float32')
sf.write(session/'microphone.wav',audio[:rate*18],rate)
atomic_json(session/'session.json',dict(title='合成会议测试',created_at='2026-09-28T04:00:00',duration=18,status='saved',capture_mode='microphone_only'))
gui.sessions=lambda:[session]
app=QApplication([])
w=gui.MainWindow(recover_sessions=False)
w.save_settings=lambda:None
errors=[]
w.show_error=errors.append
w.show()
assert w.auto_transcribe.isChecked()
assert not any(b.text() in ('下载模型','查看摘要','生成会后摘要') for b in w.findChildren(QPushButton))
w.model_combo.setCurrentIndex(w.model_combo.findData('turbo'))
w.summary_model.setCurrentIndex(w.summary_model.findData('qwen3.5:4b'))
w.start_transcription()
stages=[]
deadline=time.monotonic()+300
while w.process is not None and time.monotonic()<deadline:
    app.processEvents()
    if w.process_kind not in stages:
        stages.append(w.process_kind);print('STAGE',w.process_kind,flush=True)
    time.sleep(.03)
if w.process is not None:
    w.cancel_process()
    raise RuntimeError('pipeline timed out')
assert not errors,errors
assert stages==['transcribe','diarize','summary'],stages
assert w.version_combo.currentData()=='summary'
folder=w.summary_folder()
headline=summary_headline(folder)
assert headline and len(headline)<=28
assert headline in w.preview.toPlainText()
assert headline in w.history.item(0).text()
assert '已保存' not in w.history.item(0).text()
assert w.copy_button.text()=='复制摘要'
assert not w.preview._playback_ranges
w.copy_summary()
assert headline in app.clipboard().text()
w.grab().save(str(project/'artifacts/summary_pipeline_ui.png'))
w.version_combo.setCurrentIndex(w.version_combo.findData('speakers'))
assert w.preview._playback_ranges
w.version_combo.setCurrentIndex(w.version_combo.findData('summary'))
assert not w.preview._playback_ranges
print('PASS: three real stages, headline:',headline,'; inline view, history, copy, view switching',flush=True)
w.close()
