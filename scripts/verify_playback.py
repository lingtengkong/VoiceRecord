"""Real Qt multimedia integration using synthetic audio, muted during checks."""
import os
import sys
import time
import tempfile
import wave
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QMediaPlayer
from meeting_scribe.storage import atomic_json
from meeting_scribe.transcription import export_transcript
import meeting_scribe.gui as gui

app = QApplication([])
def wait_for(predicate, timeout=8):
    deadline = time.monotonic()+timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.02)
    assert predicate(), 'Timed out waiting for multimedia state'

with tempfile.TemporaryDirectory() as temp:
    folder = Path(temp)
    with wave.open(str(folder/'mixed.wav'), 'wb') as audio:
        audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        audio.writeframes(b'\0\0'*16000*6)
    atomic_json(folder/'session.json', {'title':'回听与高亮测试（合成音频）','status':'saved','duration':6})
    export_transcript(folder, [
        {'start':0,'end':2,'source':'参会者 1','text':'这是第一句，播放时应单独高亮。'},
        {'start':2,'end':4,'source':'参会者 1','text':'这是第二句，可以点击跳转回听。'},
        {'start':4,'end':6,'source':'参会者 2','text':'这是另一位参会者的发言。'}], {})
    gui.sessions = lambda: [folder]
    window = gui.MainWindow(recover_sessions=False)
    window.save_settings = lambda: None
    window.show()
    playback = window.playback
    playback.audio.setMuted(True)
    wait_for(lambda: playback.player.duration() == 6000)
    window.play_recording()
    wait_for(lambda: playback.player.position() > 250)
    assert window.preview.extraSelections()
    playback.toggle_play()
    assert playback.player.playbackState() == QMediaPlayer.PlaybackState.PausedState
    playback.seek(2.4)
    wait_for(lambda: any('第二句' in s.cursor.selectedText() for s in window.preview.extraSelections()))
    playback.speed.setCurrentIndex(3)
    assert playback.player.playbackRate() == 1.5
    # Click inside sentence three's rendered anchor.
    target = next(r for r in window.preview._playback_ranges if r[0] == 4)
    cursor = QTextCursor(window.preview.document())
    cursor.setPosition(target[2]+2)
    point = window.preview.cursorRect(cursor).center()
    QTest.mouseClick(window.preview.viewport(), Qt.MouseButton.LeftButton, pos=point)
    wait_for(lambda: playback.player.position() == 4000)
    window.grab().save(str(Path('artifacts/playback_preview.png').resolve()))
    playback.player.setPosition(5800)
    playback.toggle_play()
    wait_for(lambda: playback.player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia)
    window.selected = None
    window.show_selected_transcript()
    assert playback.path is None and not playback.isEnabled()
    assert not window.preview.extraSelections()
    window.close()
print('PASS: WAV decode, clock advance, pause, seek, speed, sentence click/highlight, end-of-media, session reset. Audio muted.')
