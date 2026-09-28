import os,sys,time
from pathlib import Path
project=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(project))
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtWidgets import QApplication,QPushButton
import meeting_scribe.gui as gui
from meeting_scribe.storage import read_json
session=project/'artifacts/diarization_integration'
assert (session/'system.wav').is_file()
gui.sessions=lambda:[session]
app=QApplication([])
w=gui.MainWindow(recover_sessions=False)
w.save_settings=lambda:None
# This component check stops before Ollama; verify_summary_pipeline covers the full chain.
w.start_summary=lambda:None
errors=[]
w.show_error=errors.append
w.show()
w.model_combo.setCurrentIndex(w.model_combo.findData('turbo'))
w.start_transcription()
stages=[]
deadline=time.monotonic()+180
while w.process is not None and time.monotonic()<deadline:
    app.processEvents()
    if w.process_kind not in stages:stages.append(w.process_kind)
    time.sleep(.02)
assert w.process is None,'timeout'
assert not errors,errors
assert stages==['transcribe','diarize'],stages
assert w.version_combo.currentData()=='speakers'
assert read_json(w.transcript_folder()/'transcript.json')['metadata']['diarized']
buttons=[b.text() for b in w.findChildren(QPushButton)]
assert '回听录音' not in buttons
assert not any('Nemotron（会后）' in b for b in buttons)
assert not w.session_info.isVisible()
assert w.history.height()>130,w.history.height()
w.grab().save(str(project/'artifacts/automatic_speakers_ui.png'))
print('PASS: real GPU ASR -> diarization; speaker output selected; compact history height',w.history.height())
w.close()
