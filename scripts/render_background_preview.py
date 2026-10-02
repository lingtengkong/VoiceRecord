"""Render a synthetic background-processing state without recording or inference."""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
root = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtWidgets import QApplication
from meeting_scribe import gui
from meeting_scribe.storage import atomic_json
app=QApplication([])
with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,MEETING_SCRIBE_DATA=tmp), patch.object(gui.MainWindow,'refresh_devices'):
    for name in ('上午项目会议','下午需求讨论'):
        folder=Path(tmp)/'recordings'/name
        folder.mkdir(parents=True)
        atomic_json(folder/'session.json',dict(title=name,created_at='2026-10-02T14:00:00',duration=3600,status='saved'))
    w=gui.MainWindow(recover_sessions=False)
    w.timer.stop()
    w.mic_combo.addItem('麦克风',SimpleNamespace(name='mic'))
    w.system_combo.addItem('电脑声音',SimpleNamespace(name='system'))
    w.process=object()
    w.process_kind='summary'
    w.process_folder=Path(tmp)/'recordings'/'上午项目会议'
    w.job_states[Path(tmp)/'recordings'/'下午需求讨论']='等待处理'
    w.job_status.setText('后台正在提取上一会议摘要；可以开始下一场录音。')
    w.live_status.setText('后台有模型任务时，新会议自动仅录音，避免 GPU 争用。')
    w.title_edit.setText('下一场会议')
    w.set_controls()
    w.update_job_indicators()
    w.show()
    app.processEvents()
    dest=root/'artifacts'/'background-processing-preview.png'
    w.grab().save(str(dest))
    print(dest)
    assert w.start_button.isEnabled()
    w.process=None
    w.close()
