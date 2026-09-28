"""Exercise the real speaker edit dialog using a temporary synthetic session."""
import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QComboBox, QPushButton
from meeting_scribe.storage import atomic_json, read_json
from meeting_scribe.transcription import export_transcript
import meeting_scribe.gui as gui

app = QApplication([])
with tempfile.TemporaryDirectory() as temp:
    session = Path(temp)
    atomic_json(session/'session.json', {'title':'人工标签界面测试', 'status':'saved'})
    export_transcript(session, [{'start':0,'end':.36,'source':'会议声音','text':'Yeah.'}], {})
    gui.sessions = lambda: [session]
    window = gui.MainWindow(recover_sessions=False)
    window.save_settings = lambda: None
    window.show()
    def edit():
        dialog = app.activeModalWidget()
        dialog.findChild(QComboBox).setCurrentText('参会者 2')
        dialog.grab().save(str(Path('artifacts/speaker_edit_preview.png').resolve()))
        next(b for b in dialog.findChildren(QPushButton) if b.text() == '保存此句标签').click()
    QTimer.singleShot(100, edit)
    window.edit_speaker()
    assert window.version_combo.currentData() == 'speakers'
    row = read_json(window.transcript_folder()/'transcript.json')['segments'][0]
    assert row['source'] == '参会者 2' and row['speaker_manual']
    assert '参会者 2' in window.preview.toPlainText()
    window.close()
print('PASS: real dialog saves a new manual-label version and displays it.')
