"""Embedded local audio playback; no external player or network source."""
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QSlider, QLabel, QComboBox, QCheckBox
from .storage import timestamp


class Playback(QWidget):
    positionChanged = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.audio.setVolume(.8)
        self.path = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderReleased.connect(lambda: self.player.setPosition(self.slider.value()))
        layout.addWidget(self.slider)
        row = QHBoxLayout()
        self.toggle = QPushButton('播放')
        self.toggle.clicked.connect(self.toggle_play)
        row.addWidget(self.toggle)
        for text, delta in [('−5秒', -5000), ('+5秒', 5000)]:
            button = QPushButton(text)
            button.clicked.connect(lambda checked=False, d=delta: self.player.setPosition(
                max(0, min(self.player.duration(), self.player.position()+d))))
            row.addWidget(button)
        self.clock = QLabel('00:00:00 / 00:00:00')
        row.addWidget(self.clock)
        row.addStretch()
        self.follow = QCheckBox('跟随播放')
        self.follow.setChecked(True)
        row.addWidget(self.follow)
        self.speed = QComboBox()
        for value in [.75, 1, 1.25, 1.5, 2]:
            self.speed.addItem(f'{value:g}×', value)
        self.speed.setCurrentIndex(1)
        self.speed.currentIndexChanged.connect(lambda: self.player.setPlaybackRate(self.speed.currentData()))
        row.addWidget(self.speed)
        layout.addLayout(row)
        self.player.durationChanged.connect(lambda duration: (self.slider.setRange(0, duration), self.update_position(self.player.position())))
        self.player.positionChanged.connect(self.update_position)
        self.player.playbackStateChanged.connect(lambda state: self.toggle.setText(
            '暂停' if state == QMediaPlayer.PlaybackState.PlayingState else '播放'))
        self.player.errorOccurred.connect(lambda *_: self.clock.setText('回听失败：'+self.player.errorString()))

    def load(self, folder):
        path = next((folder/name for name in ('mixed.wav', 'system.wav', 'microphone.wav')
                     if (folder/name).is_file()), None) if folder else None
        if path != self.path:
            self.player.stop()
            self.path = path
            self.player.setSource(QUrl.fromLocalFile(str(path)) if path else QUrl())
            self.update_position(0)
        self.setEnabled(path is not None)

    def toggle_play(self):
        if not self.path or not self.isEnabled():
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            if self.player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia:
                self.player.setPosition(0)
            self.player.play()

    def seek(self, seconds):
        if self.path and self.isEnabled():
            self.player.setPosition(round(seconds*1000))

    def update_position(self, milliseconds):
        if not self.slider.isSliderDown():
            self.slider.setValue(milliseconds)
        self.clock.setText(f'{timestamp(milliseconds/1000)} / {timestamp(self.player.duration()/1000)}')
        self.positionChanged.emit(milliseconds/1000)
