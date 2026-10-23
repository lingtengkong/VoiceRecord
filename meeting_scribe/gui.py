from __future__ import annotations

import json
import codecs
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QThread, Signal, QProcess, QProcessEnvironment, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QFontDatabase, QIcon, QPixmap, QPainter, QPen, QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QComboBox, QProgressBar, QFrame, QListWidget, QListWidgetItem,
    QPlainTextEdit, QMessageBox, QCheckBox, QSplitter, QSizePolicy, QScrollArea, QLayout, QFileDialog, QDialog,
    QMenu, QInputDialog,
)

from .audio import Recorder, list_devices, recover_session
from .branding import WINDOW_TITLE
from .recording_icon import RecordingIcon
from .summary import session_summary, summary_headline
from .storage import data_root, new_session, read_json, atomic_json, sessions, timestamp
from .storage import recordings_root, set_recordings_root, rename_session, delete_session
from .transcription import MODELS, model_ready, export_transcript
from .transcript_view import TranscriptView
from .translation_dialog import TranslationDialog, TranslationService
from .playback import Playback
from .diarization import runtime_path as diarization_runtime, model_path as diarization_model
from .diarization import save_manual
from .nemotron_stream import asr_path as nemotron_asr_path

PROJECT = Path(__file__).resolve().parent.parent


class AudioDeviceCombo(QComboBox):
    def wheelEvent(self, event):
        # Scrolling the settings pane must not change a live recording source.
        if self.view().isVisible():
            super().wheelEvent(event)
        else:
            event.ignore()

STYLE = """
QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; color: #213348; }
QMainWindow, #root { background: #f1f5f8; }
QDialog, QMessageBox { background-color: #f1f5f8; color: #213348; }
QMenu { background-color: #ffffff; color: #213348; border: 1px solid #d5e1eb; padding: 4px; }
QMenu::item { padding: 7px 22px; }
QMenu::item:selected { background-color: #e0f2ef; color: #006e69; }
QDialog QLabel, QMessageBox QLabel { background-color: transparent; color: #213348; }
#settings, QScrollArea { background: #f1f5f8; border: none; }
#settings QWidget { font-size: 12px; }
#settings QLineEdit, #settings QComboBox { padding: 5px; }
#settings QPushButton { padding: 5px 6px; }
#card { background: white; border: 1px solid #dce5ec; border-radius: 12px; }
#section { font-size: 16px; font-weight: 700; color: #19394f; }
#muted { color: #64778b; font-size: 12px; }
#hint { color: #64778b; font-size: 12px; }
#settings #timer { font-family: 'Consolas'; font-size: 32px; font-weight: 700; color: #15394f; }
QLineEdit, QComboBox, QPlainTextEdit { background: #f8fafc; border: 1px solid #d4dfe7; border-radius: 7px; padding: 8px; }
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus { border-color: #168a83; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView { background: white; selection-background-color: #d9f2eb; }
QPushButton { background: #edf3f7; border: 1px solid #d9e3eb; padding: 9px 12px; border-radius: 7px; font-weight: 600; }
QPushButton:hover { background: #e0ebf2; }
QPushButton:disabled { color: #9ba9b5; background: #f1f4f6; border-color: #e4eaee; }
QPushButton#primary { background: #087f79; border-color: #087f79; color: white; }
QPushButton#primary:hover { background: #076a65; }
QPushButton#primary:disabled { background: #aecac7; border-color: #aecac7; color: #f5f8f7; }
QPushButton#stop { background: #fce9e7; border-color: #eecac5; color: #a34035; }
QPushButton#stop:disabled { background: #f7f2f1; border-color: #e8dfdd; color: #b9a5a2; }
QProgressBar { border: none; background: #eaf0f4; border-radius: 4px; height: 8px; text-align: center; }
QProgressBar::chunk { background: #14988b; border-radius: 4px; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { padding: 4px 8px; border-radius: 5px; margin-bottom: 1px; }
QScrollBar:vertical { background: #eaf0f4; width: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:vertical { background: #92a9b9; min-height: 24px; border-radius: 5px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QListWidget::item:selected { background: #e1f2ef; color: #075f58; }
QListWidget::item:hover { background: #edf4f7; }
QCheckBox { spacing: 7px; }
QStatusBar { background: #e8eff4; color: #4f6378; }
QSplitter::handle { background: transparent; }
"""


class Task(QThread):
    success = Signal(object)
    failure = Signal(str)

    def __init__(self, function):
        super().__init__()
        self.function = function

    def run(self):
        try:
            self.success.emit(self.function())
        except Exception as exc:
            self.failure.emit(str(exc))


def label(text, name=None):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(True)
    return widget


def button(text, callback, name=None):
    widget = QPushButton(text)
    if name:
        widget.setObjectName(name)
    widget.clicked.connect(callback)
    widget.setMinimumHeight(30)
    return widget


def card():
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(6)
    return frame, layout


class MainWindow(QMainWindow):
    def __init__(self, recover_sessions=True):
        super().__init__()
        # Explicit fonts also make headless layout QA render the actual Chinese UI.
        for font in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "consola.ttf"):
            path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / font
            if path.exists():
                QFontDatabase.addApplicationFont(str(path))
        QApplication.instance().setFont(QFont("Microsoft YaHei", 10))
        self.setWindowTitle(WINDOW_TITLE)
        self.recording_icon = RecordingIcon(self)
        self.resize(1180, 920)
        self.setMinimumSize(1000, 760)
        self.recorder = None
        self.task = None
        self.process = None
        self.process_kind = ""
        self.translation_service = TranslationService(self)
        self.translation_popup = None
        self.selected = None
        self.active_session = None
        self.process_buffer = ""
        self.stdout_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.stderr_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.process_errors = ""
        self.process_result = None
        self.process_folder = None
        self.stopping = False
        self.silence_notified = False
        self.long_silence_episode = None
        self.silence_dialog = None
        self.live_process = None
        self.live_folder = None
        self.live_rows = []
        self.batch_rows = []
        self.live_partials = {}
        self.live_buffer = ""
        self.live_errors = ""
        self.live_result = None
        self.live_last_event = 0.
        self.live_cancelled = False
        self.pending_auto = False
        self.job_queue = []
        self.job_options = {}
        self.job_states = {}
        self.queue_path = data_root() / "processing_queue.json"
        if recover_sessions:
            for job in read_json(self.queue_path, default=[]):
                folder = Path(job["folder"])
                if folder.is_dir():
                    if job.get("running"):
                        self.job_states[folder] = "处理被中断，请重新转写"
                    else:
                        self.job_queue.append((folder, job["options"]))
                        self.job_states[folder] = "等待处理"
        self.summary_cancel = None
        self.summarize_after_transcription = False
        self.settings_path = data_root() / "settings.json"
        self.settings = read_json(self.settings_path)
        self.setStyleSheet(STYLE)
        self._build()
        self.capture_mode_changed()
        self.refresh_devices()
        self.refresh_history(recover=recover_sessions)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(150)
        if recover_sessions:
            QTimer.singleShot(0, self.drain_jobs)
        self.statusBar().showMessage("就绪。录音和转写均在本机进行；未启动任何录音。")

    def _build(self):
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(12, 10, 12, 8)
        outer.setSpacing(8)
        columns = QHBoxLayout()
        columns.setSpacing(10)
        outer.addLayout(columns, 1)
        left = QWidget()
        left.setObjectName("settings")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)
        left_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        capture, layout = card()
        left_layout.addWidget(capture)
        heading = QHBoxLayout()
        heading.addWidget(label("01  录制会议", "section"))
        heading.addStretch()
        self.refresh_button = button("刷新设备", self.refresh_devices)
        heading.addWidget(self.refresh_button)
        layout.addLayout(heading)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("会议名称，例如：项目周会")
        layout.addWidget(self.title_edit)
        layout.addWidget(label("录音与转写保存文件夹", "muted"))
        destination_row = QHBoxLayout()
        self.save_folder = QLineEdit(str(recordings_root()))
        self.save_folder.setReadOnly(True)
        self.save_folder.setMinimumWidth(0)
        self.save_folder.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.save_folder.setToolTip(str(recordings_root()))
        destination_row.addWidget(self.save_folder, 1)
        self.choose_folder_button = button("选择文件夹", self.choose_recordings_folder)
        destination_row.addWidget(self.choose_folder_button)
        layout.addLayout(destination_row)
        self.capture_mode = QComboBox()
        self.capture_mode.addItem('双路录音 · 麦克风＋电脑声音', 'dual')
        self.capture_mode.addItem('仅录麦克风 · 含外放会议声音', 'microphone_only')
        self.capture_mode.setCurrentIndex(max(0,self.capture_mode.findData(self.settings.get('capture_mode','dual'))))
        layout.addWidget(self.capture_mode)
        self.capture_hint = label('', 'hint')
        layout.addWidget(self.capture_hint)
        self.capture_mode.currentIndexChanged.connect(self.capture_mode_changed)
        layout.addWidget(label("我的麦克风", "muted"))
        self.mic_combo = AudioDeviceCombo()
        self.mic_combo.activated.connect(lambda _index: self.change_recording_device('microphone'))
        self.mic_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        layout.addWidget(self.mic_combo)
        self.mic_meter = QProgressBar()
        self.mic_meter.setTextVisible(False)
        self.mic_meter.setFixedHeight(7)
        layout.addWidget(self.mic_meter)
        self.system_label = label("会议声音 · 选择会议正在使用的耳机 / 扬声器", "muted")
        layout.addWidget(self.system_label)
        self.system_combo = AudioDeviceCombo()
        self.system_combo.activated.connect(lambda _index: self.change_recording_device('system'))
        self.system_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        layout.addWidget(self.system_combo)
        self.system_meter = QProgressBar()
        self.system_meter.setTextVisible(False)
        self.system_meter.setFixedHeight(7)
        layout.addWidget(self.system_meter)
        self.device_hint = label("戴耳机可减少回声。会议声音会包含所选输出设备上的其他声音。", "hint")
        layout.addWidget(self.device_hint)
        self.silence_hint = label("", "hint")
        self.silence_hint.setWordWrap(True)
        self.silence_hint.setStyleSheet("color: #b42318; font-weight: bold;")
        self.silence_hint.hide()
        layout.addWidget(self.silence_hint)
        clock_row = QHBoxLayout()
        self.clock = label("00:00:00", "timer")
        self.clock.setMinimumHeight(40)
        self.clock.setWordWrap(False)
        clock_row.addWidget(self.clock)
        clock_row.addStretch()
        self.record_state = label("待录音", "muted")
        clock_row.addWidget(self.record_state)
        layout.addLayout(clock_row)
        buttons = QHBoxLayout()
        self.start_button = button("开始录音", self.start_recording, "primary")
        self.pause_button = button("暂停", self.pause_recording)
        self.stop_button = button("结束并保存", self.stop_recording, "stop")
        for b in (self.start_button, self.pause_button, self.stop_button):
            buttons.addWidget(b)
        layout.addLayout(buttons)
        self.live_enabled = QCheckBox("边录边转写（GPU）")
        self.live_enabled.setChecked(self.settings.get("live_enabled", True))
        layout.addWidget(self.live_enabled)
        self.live_engine = QComboBox()
        self.live_engine.addItem('Nemotron 3.5 · 实时文字＋说话者', 'nemotron')
        self.live_engine.setCurrentIndex(0)
        layout.addWidget(self.live_engine)
        self.live_status = label("实时转写在本机运行；临时文字会随上下文修订。", "hint")
        layout.addWidget(self.live_status)
        self.auto_transcribe = QCheckBox("结束后用下方模型转写并生成摘要")
        self.auto_transcribe.setChecked(self.settings.get("auto_summary", True))
        layout.addWidget(self.auto_transcribe)

        transcript, layout = card()
        left_layout.addWidget(transcript)
        layout.addWidget(label("02  本地转写与摘要", "section"))
        options = QHBoxLayout()
        self.model_combo = QComboBox()
        self.model_combo.addItem("large-v3 · 精度优先", "large-v3")
        self.model_combo.addItem("turbo · 速度优先", "turbo")
        self.language_combo = QComboBox()
        for name, value in (("自动识别 / 中英混合", "auto"), ("中文", "zh"), ("English", "en")):
            self.language_combo.addItem(name, value)
        options.addWidget(self.model_combo, 1)
        options.addWidget(self.language_combo, 1)
        layout.addLayout(options)
        self.device_combo = QComboBox()
        self.device_combo.addItem("NVIDIA GPU · INT8 混合精度", "cuda")
        self.device_combo.addItem("CPU · 兼容模式（较慢）", "cpu")
        layout.addWidget(self.device_combo)
        self.vocabulary = QLineEdit()
        self.vocabulary.setPlaceholderText("可选术语提示：人名、公司名、技术缩写，用逗号分隔")
        self.vocabulary.setText(self.settings.get("vocabulary", ""))
        layout.addWidget(self.vocabulary)
        layout.addWidget(label("摘要模型", "muted"))
        self.summary_model = QComboBox()
        self.summary_model.addItem("Qwen3.5 · 4B（默认）", "qwen3.5:4b")
        self.summary_model.addItem("Qwen3.5 · 9B（显存占用较高）", "qwen3.5:9b")
        self.summary_model.setCurrentIndex(max(0,self.summary_model.findData(self.settings.get("summary_model","qwen3.5:4b"))))
        layout.addWidget(self.summary_model)
        actions = QHBoxLayout()
        self.transcribe_button = button("转写", self.start_transcription, "primary")
        self.summarize_button = button("摘要", self.start_summary)
        self.cancel_button = button("取消", self.cancel_process)
        actions.addWidget(self.transcribe_button, 1)
        actions.addWidget(self.summarize_button, 1)
        actions.addWidget(self.cancel_button, 1)
        layout.addLayout(actions)
        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setFixedHeight(14)
        layout.addWidget(self.progress)
        self.model_status = label("", "muted")
        layout.addWidget(self.model_status)
        self.job_status = label("自动转写、区分说话者并生成摘要；摘要需启动本机 Ollama。", "hint")
        layout.addWidget(self.job_status)
        left_layout.addStretch()
        for field in (self.title_edit, self.mic_combo, self.system_combo, self.model_combo,
                      self.language_combo, self.device_combo, self.vocabulary):
            field.setMinimumHeight(30)
            field.setMinimumWidth(0)
            field.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            if isinstance(field, QComboBox):
                field.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(left)
        self.left_scroll = scroll
        scroll.setMinimumWidth(0)
        scroll.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        columns.addWidget(scroll, 1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)
        library, layout = card()
        library.setFixedHeight(218)
        right_layout.addWidget(library)
        row = QHBoxLayout()
        row.addWidget(label("会议记录", "section"))
        row.addStretch()
        row.addWidget(button("打开保存目录", lambda: self.open_path(recordings_root())))
        self.folder_button = button("打开本次文件夹", self.open_session)
        row.addWidget(self.folder_button)
        layout.addLayout(row)
        self.history = QListWidget()
        self.history.setMinimumHeight(60)
        self.history.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.history.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.history.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.history.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.history.currentItemChanged.connect(self.select_session)
        self.history.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.history.customContextMenuRequested.connect(self.history_menu)
        layout.addWidget(self.history, 1)
        self.session_info = label("", "muted")
        self.session_info.hide()
        layout.addWidget(self.session_info)
        preview, layout = card()
        right_layout.addWidget(preview, 3)
        layout.addWidget(label("03  转写与摘要", "section"))
        self.version_combo = QComboBox()
        self.version_combo.addItem("会后稿（说话者标注）", "speakers")
        self.version_combo.addItem("实时草稿", "live")
        self.version_combo.addItem("会后摘要", "summary")
        self.version_combo.currentIndexChanged.connect(self.show_selected_transcript)
        version_row = QHBoxLayout()
        version_row.addWidget(self.version_combo, 1)
        self.edit_speaker_button = button("修改说话者", self.edit_speaker)
        version_row.addWidget(self.edit_speaker_button)
        version_row.addWidget(button("回到最新", lambda: self.preview.verticalScrollBar().setValue(
            self.preview.verticalScrollBar().maximum())))
        layout.addLayout(version_row)
        self.preview = TranscriptView()
        self.preview.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.preview.customContextMenuRequested.connect(self.preview_menu)
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("转写完成后，在这里查看带时间戳的文字。\n\n保留音源标签：\n• 我 / 麦克风\n• 会议声音（可能包含多位参会者）\n\n复制纪要素材前，可先检查或删除敏感内容。")
        layout.addWidget(self.preview, 1)
        self.playback = Playback()
        layout.addWidget(self.playback)
        self.playback.positionChanged.connect(lambda seconds: self.preview.highlight_position(
            seconds, self.playback.follow.isChecked()))
        self.preview.seekRequested.connect(self.playback.seek)
        self.preview.manuallyScrolled.connect(lambda: self.playback.follow.setChecked(False))
        self.playback.follow.toggled.connect(lambda enabled: self.preview.highlight_position(
            self.playback.player.position()/1000, enabled))
        self.partial_view = label("", "hint")
        self.partial_view.setMaximumHeight(100)
        self.partial_view.hide()
        layout.addWidget(self.partial_view)
        row = QHBoxLayout()
        self.copy_button = button("复制纪要素材", self.copy_summary, "primary")
        self.export_button = button("打开 TXT / SRT", self.open_transcript)
        row.addWidget(self.copy_button)
        row.addWidget(self.export_button)
        layout.addLayout(row)
        layout.addWidget(label("上翻保持阅读位置，滚到底部自动跟随。复制后由你决定是否发送给 AI。", "muted"))
        right.setMinimumWidth(0)
        right.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        columns.addWidget(right, 2)
        self.save_footer = label("保存位置：" + str(recordings_root()), "muted")
        self.save_footer.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.save_footer)

    def choose_recordings_folder(self):
        if self.recorder or self.task or self.process or self.live_process:
            return
        selected = QFileDialog.getExistingDirectory(self, "选择录音与转写保存文件夹", str(recordings_root()))
        if not selected:
            return
        try:
            set_recordings_root(Path(selected))
        except OSError as exc:
            self.show_error(f"无法使用该文件夹，保存位置未更改：{exc}")
            return
        folder = str(recordings_root())
        self.save_folder.setText(folder)
        self.save_folder.setToolTip(folder)
        self.save_footer.setText("保存位置：" + folder)
        self.statusBar().showMessage("保存文件夹已更新；新会议保存到此处，已有录音保留原位置。")
        self.refresh_history(preserve_preview=True)

    def refresh_devices(self):
        if self.task or self.stopping:
            return
        try:
            microphones, systems = list_devices()
            for combo, devices, key in ((self.mic_combo, microphones, "microphone"),
                                        (self.system_combo, systems, "system")):
                previous = combo.currentData()
                wanted = previous.name if previous else self.settings.get(key)
                combo.clear()
                for d in devices:
                    combo.addItem(d.name.replace(" [Loopback]", ""), d)
                selected = next((i for i, d in enumerate(devices) if d.name == wanted), None)
                if selected is None and key == "microphone":
                    selected = next((i for i, d in enumerate(devices)
                                     if not any(v in d.name.lower() for v in ("virtual", "steam", "oculus"))), None)
                if selected is None:
                    selected = next((i for i, d in enumerate(devices) if d.default), 0)
                combo.setCurrentIndex(selected)
            self.set_controls()
            self.sync_recording_devices()
        except Exception as exc:
            self.show_error(f"无法枚举音频设备：{exc}")

    def sync_recording_devices(self):
        if not self.recorder:
            return
        for key, combo in (('microphone', self.mic_combo), ('system', self.system_combo)):
            device = self.recorder.devices.get(key)
            if device is None:
                continue
            index = next((i for i in range(combo.count()) if combo.itemData(i).name == device.name), -1)
            if index < 0:
                combo.addItem(device.name + '（当前设备）', device)
                index = combo.count()-1
            combo.setCurrentIndex(index)

    def change_recording_device(self, key):
        if not self.recorder:
            return
        if self.task or self.stopping or not self.recorder.active:
            self.sync_recording_devices()
            return
        combo = self.mic_combo if key == 'microphone' else self.system_combo
        device = combo.currentData()
        if device is None or device == self.recorder.devices.get(key):
            return
        self.device_hint.setText('正在切换录音设备，已有录音保留…')
        recorder = self.recorder
        self.run_task(lambda: recorder.switch_device(key, device), self.recording_device_changed)

    def recording_device_changed(self, _result):
        self.sync_recording_devices()
        self.save_settings()
        self.device_hint.setText('录音设备已切换，请确认音量条有变化。切换时可能有短暂空白。')

    def capture_mode_changed(self, *_args):
        single = self.capture_mode.currentData() == 'microphone_only'
        for widget in (self.system_label, self.system_combo, self.system_meter):
            widget.setVisible(not single)
        self.device_hint.setText('仅录实际拾音；请确保麦克风能听清自己和外放会议声音。' if single
                                else '戴耳机可减少回声。会议声音会包含所选输出设备上的其他声音。')
        self.set_controls()

    def save_settings(self):
        settings = {"auto_summary": self.auto_transcribe.isChecked(), "vocabulary": self.vocabulary.text(),
                    "live_enabled": self.live_enabled.isChecked(), "live_engine": self.live_engine.currentData(), "summary_model": self.summary_model.currentData(),
                    "capture_mode": self.capture_mode.currentData()}
        for combo, key in ((self.mic_combo, "microphone"), (self.system_combo, "system")):
            if combo.currentData():
                settings[key] = combo.currentData().name
        atomic_json(self.settings_path, settings)

    def run_task(self, fn, done):
        self.task = Task(fn)
        self.task.success.connect(done)
        self.task.failure.connect(self.task_failed)
        self.task.finished.connect(self.task_finished)
        self.task.start()
        self.set_controls()

    def task_failed(self, message):
        self.stopping = False
        if self.recorder and self.recorder.active:
            self.sync_recording_devices()
            self.device_hint.setText(message)
        if self.recorder and not self.recorder.active:
            self.recorder = None
            self.record_state.setText("启动失败 · 请检查设备")
            self.refresh_history(select=self.active_session)
        self.show_error(message)

    def task_finished(self):
        if self.task:
            self.task.deleteLater()
        self.task = None
        self.set_controls()
        self.maybe_auto_transcribe()

    def start_recording(self):
        if self.recorder or self.task:
            return
        self.silence_notified = False
        self.long_silence_episode = None
        if self.live_process:
            self.cancel_process()
        self.maybe_auto_transcribe()
        self.playback.player.stop()
        self.save_settings()
        self.left_scroll.verticalScrollBar().setValue(0)
        self.active_session = new_session(self.title_edit.text())
        self.pending_auto = False
        self.live_rows = []
        self.live_partials = {}
        self.preview.clear()
        self.partial_view.hide()
        system = self.system_combo.currentData() if self.capture_mode.currentData() == 'dual' else None
        self.recorder = Recorder(self.active_session, self.mic_combo.currentData(), system)
        self.refresh_history(select=self.active_session, preserve_preview=True)
        self.clock.setText("00:00:00")
        self.record_state.setText("正在打开设备…")
        self.run_task(self.recorder.start, self.recording_started)

    def recording_started(self, _):
        self.update_recording_icon()
        self.record_state.setText("● 正在录音")
        single = self.capture_mode.currentData() == 'microphone_only'
        self.device_hint.setText("请确认麦克风能收到自己和外放会议声音。" if single else "请确认两路音量条都有变化。暂停时不保存声音，续录后时间连续。")
        self.statusBar().showMessage("录音中：可通过上方下拉框更换音源；会议声音应选择会议软件实际使用的耳机或扬声器。")
        if self.process or self.job_queue or self.live_process:
            self.live_status.setText("后台正在处理上一会议，本次仅录音；结束后排队转写。")
        elif self.live_enabled.isChecked():
            if ((self.live_engine.currentData() == 'nemotron' and diarization_runtime().is_file()
                 and (nemotron_asr_path()/'model.safetensors').is_file()
                 and (diarization_model()/'model.safetensors').is_file())
                    or (self.live_engine.currentData() == 'turbo' and model_ready("turbo"))):
                self.start_live()
            else:
                self.live_status.setText("所选实时引擎尚未安装，本次只保存录音；请先配置对应模型。")
        else:
            self.live_status.setText("本次仅录音，可在会后转写。")

    def pause_recording(self):
        if not self.recorder or self.stopping:
            return
        if self.recorder.paused:
            self.recorder.resume()
            self.pause_button.setText("暂停")
            self.record_state.setText("● 正在录音")
        else:
            self.recorder.pause()
            self.pause_button.setText("继续")
            self.record_state.setText("已暂停")
        self.update_recording_icon()

    def stop_recording(self):
        if not self.recorder or self.stopping or self.task:
            return
        self.stopping = True
        self.update_recording_icon()
        self.record_state.setText("正在保存…")
        self.run_task(self.recorder.stop, self.recording_saved)

    def recording_saved(self, meta):
        self.recorder = None
        self.stopping = False
        self.pause_button.setText("暂停")
        self.record_state.setText("已保存" if not meta.get("error") else "已保存 · 请检查")
        if self.live_process:
            self.live_status.setText("录音已保存，实时引擎正在处理末尾文字…")
        else:
            self.refresh_history(select=self.active_session, preserve_preview=True)
        stats = meta.get("audio_stats", {})
        silent = ["麦克风" if k == "microphone" else "会议声音" for k, v in stats.items() if v.get("peak", 0) < .001]
        notes = []
        if meta.get("error"):
            notes.append(meta["error"])
        if silent:
            notes.append(" / ".join(silent) + "未检测到明显声音，请回听并检查设备选择。")
        if meta.get("mix_error"):
            notes.append("混合录音生成失败，但独立音轨已保留。")
        saved = ('已保存麦克风混合声音，可以回听或转写。' if meta.get('capture_mode') == 'microphone_only'
                 else '已保存两条独立音轨和混合录音，可以回听或转写。')
        self.device_hint.setText("；".join(notes) or saved)
        self.statusBar().showMessage(self.device_hint.text())
        self.pending_auto = (self.auto_transcribe.isChecked() and not meta.get("error")
                             and model_ready(self.model_combo.currentData()))
        QTimer.singleShot(200, self.maybe_auto_transcribe)

    def save_job_queue(self):
        jobs = [dict(folder=str(folder), options=options) for folder, options in self.job_queue]
        if self.process and self.process_folder:
            jobs.insert(0, dict(folder=str(self.process_folder), options=self.job_options, running=True))
        try:
            atomic_json(self.queue_path, jobs)
        except OSError as exc:
            self.statusBar().showMessage(f"任务队列暂未保存，关闭前请等待处理完成：{exc}")

    def processing_options(self):
        return dict(model=self.model_combo.currentData(), language=self.language_combo.currentData(),
                    device=self.device_combo.currentData(), vocabulary=self.vocabulary.text(),
                    summary_model=self.summary_model.currentData())

    def maybe_auto_transcribe(self):
        if self.pending_auto and not self.task:
            self.pending_auto = False
            if self.auto_transcribe.isChecked():
                folder = self.active_session
                self.job_queue.append((folder, self.processing_options()))
                self.job_states[folder] = '等待处理'
                self.save_job_queue()
                self.refresh_history(preserve_preview=True)
        self.drain_jobs()

    def drain_jobs(self):
        if self.process or self.live_process or self.task or not self.job_queue:
            return
        folder, options = self.job_queue.pop(0)
        if folder == self.active_session and not self.auto_transcribe.isChecked():
            self.job_states[folder] = "已取消自动处理，录音保留"
            self.save_job_queue()
            QTimer.singleShot(0, self.drain_jobs)
            return
        self.start_transcription(summarize_after=True, folder=folder, options=options)
        if not self.process:
            self.job_states[folder] = "未能启动，请检查模型配置"
            QTimer.singleShot(0, self.drain_jobs)
        self.save_job_queue()

    def update_job_indicators(self):
        pixmap = QPixmap(18, 18)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#00877f'), 2))
        painter.drawArc(3, 3, 12, 12, -int(time.monotonic()*240) % 360 * 16, 260 * 16)
        painter.end()
        spinner = QIcon(pixmap)
        for index in range(self.history.count()):
            item = self.history.item(index)
            folder = item.data(Qt.ItemDataRole.UserRole)
            base = item.data(Qt.ItemDataRole.UserRole + 1) or item.text()
            state = self.job_states.get(folder)
            item.setIcon(QIcon())
            if self.process and folder == self.process_folder:
                state = {'transcribe':'转写中', 'diarize':'区分说话者', 'summary':'提取摘要',
                         'cancelled':'正在取消'}.get(self.process_kind, '处理中')
                item.setIcon(spinner)
            if self.recorder and folder == self.active_session:
                state = '● 录音中'
            item.setText((state + '    ·    ' if state else '') + base)

    def refresh_history(self, recover=False, select=None, preserve_preview=False):
        wanted = select or self.selected
        self.history.blockSignals(True)
        self.history.clear()
        for folder in sessions():
            meta = recover_session(folder) if recover else read_json(folder / "session.json")
            summary = session_summary(folder)
            headline = summary_headline(summary)
            suffix = f"    ·    {headline}" if headline else ''
            if meta.get('error'):
                suffix += '    ·    需检查'
            item = QListWidgetItem(f'{meta.get("title", folder.name)}    ·    {meta.get("created_at", "")[:16].replace("T", "  ")}    ·    {timestamp(meta.get("duration", 0))}{suffix}')
            item.setData(Qt.ItemDataRole.UserRole + 1, item.text())
            item.setToolTip(item.text())
            item.setData(Qt.ItemDataRole.UserRole, folder)
            self.history.addItem(item)
            if folder == wanted:
                self.history.setCurrentItem(item)
        if self.history.count() and self.history.currentRow() < 0:
            self.history.setCurrentRow(0)
        self.history.blockSignals(False)
        item = self.history.currentItem()
        self.selected = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not self.recorder:
            self.show_selected_transcript(preserve=preserve_preview)
        self.update_job_indicators()
        self.set_controls()

    def history_menu(self, position):
        if any(x is not None for x in (self.recorder, self.task, self.process, self.live_process)):
            return
        item = self.history.itemAt(position)
        if item is None:
            return
        self.history.setCurrentItem(item)
        folder = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        rename = menu.addAction("修改名称")
        delete = menu.addAction("删除…")
        action = menu.exec(self.history.viewport().mapToGlobal(position))
        if action == rename:
            self.rename_meeting(folder)
        elif action == delete:
            self.delete_meeting(folder)

    def rename_meeting(self, folder):
        if any(f == folder for f, _ in self.job_queue):
            self.show_error("该会议正在排队，请等处理完成后修改。")
            return
        title = read_json(folder / "session.json").get("title", folder.name)
        text, accepted = QInputDialog.getText(self, "修改会议名称", "会议名称：", text=title)
        if not accepted:
            return
        try:
            rename_session(folder, text)
        except (OSError, ValueError) as exc:
            self.show_error(str(exc))
            return
        self.refresh_history(select=folder, preserve_preview=True)

    def delete_meeting(self, folder):
        if any(f == folder for f, _ in self.job_queue):
            self.show_error("该会议正在排队，请等处理完成后删除。")
            return
        title = read_json(folder / "session.json").get("title", folder.name)
        dialog = QMessageBox(self)
        dialog.setWindowTitle("删除会议记录")
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(f'确定删除“{title}”？\n\n将永久删除这条会议的录音、转写和摘要文件，无法撤销。\n\n文件夹：{folder}')
        confirm = dialog.addButton("删除", QMessageBox.ButtonRole.DestructiveRole)
        cancel = dialog.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel)
        dialog.setEscapeButton(cancel)
        dialog.exec()
        if dialog.clickedButton() != confirm:
            return
        self.playback.load(None)
        try:
            delete_session(folder)
        except (OSError, ValueError) as exc:
            self.refresh_history()
            self.show_error(f"删除未完成：{exc}")
            return
        if self.selected == folder:
            self.selected = None
        self.refresh_history()

    def select_session(self, item, _previous=None):
        self.selected = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.show_selected_transcript()

    def show_selected_transcript(self, *_args, preserve=False):
        self.playback.load(self.selected)
        if not preserve:
            self.preview.clear()
        self.partial_view.hide()
        self.session_info.hide()
        is_summary = self.version_combo.currentData() == 'summary'
        self.copy_button.setText('复制摘要' if is_summary else '复制纪要素材')
        self.export_button.setText('打开摘要文件夹' if is_summary else '打开 TXT / SRT')
        if is_summary:
            folder = self.summary_folder()
            self.preview.clear()
            if folder:
                self.preview.setMarkdown((folder/'summary.md').read_text(encoding='utf-8') if (folder/'summary.md').exists()
                                         else (folder/'summary.txt').read_text(encoding='utf-8-sig'))
            else:
                self.preview.setPlainText('尚未生成摘要。有转写文字后，点击左侧“摘要”生成。')
            self.set_controls()
            return
        if self.selected:
            meta = read_json(self.selected / "session.json")
            self.session_info.setText(meta.get("error") or "")
            self.session_info.setVisible(bool(meta.get("error")))
            path = self.transcript_folder()
            if path and (path / "transcript.txt").exists():
                payload = read_json(path / "transcript.json")
                if "segments" in payload:
                    metadata = payload.get("metadata", {})
                    note = (('Nemotron 3.5 实时草稿 · 说话者及时间戳待核对' if metadata.get('model') == 'nemotron-3.5-asr'
                             else 'Turbo 实时草稿') if metadata.get('mode') == 'live' else '会后转写稿')
                    if metadata.get("diarized"):
                        note += " · Nemotron 说话者标签待核对"
                    if metadata.get('live_replay'):
                        note += ' · 原音轨回放重建'
                    if not metadata.get("complete", True):
                        note += " · 未完成，请会后重转核对"
                    if metadata.get("skipped_ranges"):
                        note += " · 有跳过段落，请会后重转"
                    self.preview.update_rows(payload["segments"], note=note, preserve=preserve)
                else:
                    self.preview.setPlainText((path / "transcript.txt").read_text(encoding="utf-8-sig"))
        self.preview.highlight_position(self.playback.player.position()/1000)
        self.set_controls()

    def transcript_folder(self):
        if self.selected:
            filenames = (["latest_speaker_transcript.json", "latest_transcript.json"] if self.version_combo.currentData() == "speakers"
                         else ["latest_live_transcript.json"] if self.version_combo.currentData() == "live"
                         else ["latest_transcript.json", "latest_live_transcript.json"])
            value = next((read_json(self.selected / filename).get("path") for filename in filenames
                          if read_json(self.selected / filename).get("path")), None)
            if value and Path(value).is_dir():
                return Path(value)
        return None

    def start_transcription(self, *_args, summarize_after=False, folder=None, options=None):
        target = folder or self.selected
        if self.process or self.live_process or not target or (folder is None and (self.recorder or self.task)):
            return
        self.job_options = options or self.processing_options()
        model = self.job_options["model"]
        if not model_ready(model):
            self.show_error("所选转写模型尚未安装，请先运行 Setup.cmd 完成本地模型安装。")
            return
        if not diarization_runtime().is_file() or not (diarization_model() / "model.safetensors").is_file():
            self.show_error("Nemotron 独立环境或模型尚未安装，请先完成本地配置。")
            return
        self.save_settings()
        self.summarize_after_transcription = summarize_after
        self.process_folder = target
        self.batch_rows = []
        if not self.recorder and self.selected == target:
            self.version_combo.setCurrentIndex(self.version_combo.findData("speakers"))
            self.preview.clear()
        args = ["-m", "meeting_scribe.transcription", str(target), "--model", model,
                "--language", self.job_options["language"], "--device", self.job_options["device"],
                "--vocabulary", self.job_options["vocabulary"]]
        self.start_process("transcribe", args)

    def edit_speaker(self):
        if self.process or self.recorder or self.task or self.live_process:
            return
        folder = self.transcript_folder()
        if not folder:
            return
        payload = read_json(folder / 'transcript.json')
        rows = payload.get('segments', [])
        if not rows:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('逐句修改说话者 · 保存为新标注稿')
        dialog.resize(780, 500)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('选择原始句子，填写或选择标签。人工确认将保留；原文或时间戳改变后需重新核对。'))
        listing = QListWidget()
        for row in rows:
            listing.addItem(f"{timestamp(row['start'])} · {row['source']}：{row['text']}")
        layout.addWidget(listing, 1)
        label = QComboBox()
        label.setEditable(True)
        label.addItems(list(dict.fromkeys(['本机麦克风'] + [r['source'] for r in rows]
                                        + [f'参会者 {i}' for i in range(1, 9)])))
        listing.currentRowChanged.connect(lambda i: label.setCurrentText(rows[i]['source']) if i >= 0 else None)
        layout.addWidget(label)
        def save():
            if listing.currentRow() < 0 or not label.currentText().strip():
                return
            try:
                save_manual(self.selected, folder, listing.currentRow(), label.currentText().strip())
            except Exception as exc:
                self.show_error(str(exc))
                return
            dialog.accept()
            self.version_combo.setCurrentIndex(self.version_combo.findData('speakers'))
            self.show_selected_transcript()
        layout.addWidget(button('保存此句标签', save))
        listing.setCurrentRow(0)
        dialog.exec()

    def start_diarization(self, transcript=None, *, folder=None):
        target = folder or self.selected
        if self.process or self.live_process or not target or (folder is None and (self.recorder or self.task)):
            return
        transcript = transcript or self.transcript_folder()
        if not transcript:
            return
        if not diarization_runtime().is_file() or not (diarization_model() / "model.safetensors").is_file():
            self.show_error("Nemotron 独立环境或模型尚未安装，请先完成本地配置。")
            return
        self.process_folder = target
        self.start_process("diarize", ["-m", "meeting_scribe.diarization", str(target),
                                      str(transcript), "--device", self.job_options.get("device", self.device_combo.currentData())])

    def summary_source(self):
        if not self.selected:
            return None
        preferred = self.transcript_folder() if self.version_combo.currentData() != 'summary' else None
        candidates = [preferred]
        for name in ('latest_speaker_transcript.json','latest_transcript.json','latest_live_transcript.json'):
            path = read_json(self.selected/name).get('path')
            if path:
                candidates.append(Path(path))
        for folder in candidates:
            if folder and any(str(r.get('text','')).strip() for r in read_json(folder/'transcript.json').get('segments', [])):
                return folder
        return None

    def start_summary(self, *, session=None, transcript=None):
        if self.process or self.live_process or (session is None and (self.recorder or self.task)):
            return
        folder = transcript or self.summary_source()
        if not folder:
            return
        self.save_settings()
        self.process_folder = session or self.selected
        if session is None:
            self.job_options = self.processing_options()
        self.summary_cancel = folder / "summary_cancel.flag"
        self.summary_cancel.unlink(missing_ok=True)
        self.start_process("summary", ["-m", "meeting_scribe.summary", str(folder),
                           "--model", self.job_options.get("summary_model", self.summary_model.currentData()), "--cancel", str(self.summary_cancel)])

    def summary_folder(self):
        return session_summary(self.selected)

    def view_summary(self):
        folder = self.summary_folder()
        if not folder:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("会后摘要 · AI 初稿")
        dialog.resize(800, 650)
        layout = QVBoxLayout(dialog)
        text = (folder / "summary.txt").read_text(encoding="utf-8-sig")
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(text)
        layout.addWidget(editor)
        actions = QHBoxLayout()
        actions.addWidget(button("复制摘要", lambda: QApplication.clipboard().setText(text)))
        actions.addWidget(button("打开文件夹 / 历史版本", lambda: self.open_path(folder.parent)))
        actions.addWidget(button("关闭", dialog.accept))
        layout.addLayout(actions)
        dialog.exec()

    def download_model(self):
        if self.process or self.recorder or self.task or self.live_process:
            return
        model = self.model_combo.currentData()
        if model_ready(model):
            self.statusBar().showMessage("此模型已在本地，无需再次下载。")
            return
        self.start_process("download", [str(PROJECT / "download_model.py"), model])

    def start_process(self, kind, args):
        self.process_kind = kind
        self.process_result = None
        self.process_errors = ""
        self.process_buffer = ""
        self.stdout_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.stderr_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.process = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("HF_HUB_DISABLE_TELEMETRY", "1")
        if kind == "transcribe":
            env.insert("HF_HUB_OFFLINE", "1")
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(str(PROJECT))
        self.process.readyReadStandardOutput.connect(self.read_process)
        self.process.readyReadStandardError.connect(self.read_errors)
        self.process.finished.connect(self.process_finished)
        self.process.errorOccurred.connect(self.process_error)
        self.progress.setRange(0, 0 if kind in ("download", "summary") else 100)
        self.progress.setValue(0)
        self.job_status.setText("正在下载模型，可取消后续传…" if kind == "download" else "正在启动本地转写…")
        if kind == "summary":
            self.job_status.setText("正在连接本机 Ollama，生成会后摘要…")
        if kind == "diarize":
            self.job_status.setText("正在区分会议声音中的说话者；现有转写稿保留…")
        if not self.recorder:
            self.left_scroll.ensureWidgetVisible(self.job_status)
        executable = diarization_runtime() if kind == "diarize" else Path(sys.executable).with_name("python.exe")
        self.process.start(str(executable), args)
        self.save_job_queue()
        self.set_controls()

    def read_process(self):
        if not self.process:
            return
        self.process_buffer += self.stdout_decoder.decode(bytes(self.process.readAllStandardOutput()))
        while "\n" in self.process_buffer:
            line, self.process_buffer = self.process_buffer.split("\n", 1)
            if self.process_kind == "download":
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = event.get("type")
            if kind == "status":
                self.job_status.setText(event["message"])
            elif kind == "progress":
                self.progress.setRange(0, 100)
                self.progress.setValue(event["value"])
            elif kind == "segment":
                self.batch_rows.append(event)
                if not self.recorder and self.selected == self.process_folder and self.version_combo.currentData() == "speakers":
                    self.preview.update_rows(self.batch_rows, note="正在生成会后转写稿")
            elif kind == "error":
                self.process_errors += event["message"] + "\n"
            elif kind == "done":
                self.process_result = event

    def read_errors(self):
        if self.process:
            self.process_errors = (self.process_errors + self.stderr_decoder.decode(bytes(self.process.readAllStandardError())))[-6000:]

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.process_errors += "本地 Python 进程无法启动。请重新运行安装脚本。"
            self.process_finished(1, QProcess.ExitStatus.CrashExit)

    def process_finished(self, code, _status):
        if not self.process:
            return
        self.read_process()
        self.read_errors()
        kind = self.process_kind
        self.process.deleteLater()
        self.process = None
        self.progress.setRange(0, 100)
        if kind == "cancelled":
            self.summarize_after_transcription = False
            self.progress.setValue(0)
            self.job_status.setText("已取消，原始录音和已有完整稿件保留。")
        elif code == 0 and (kind == "download" or self.process_result):
            self.progress.setValue(100)
            self.job_status.setText("模型已准备好，可断网转写。" if kind == "download" else "转写完成 · TXT / Markdown / SRT 已保存。")
            visible = not self.recorder and self.selected == self.process_folder
            if kind == "summary":
                elapsed = self.process_result.get('elapsed_seconds')
                timing = f" · 用时 {elapsed:.1f} 秒" if isinstance(elapsed, (int, float)) else ''
                self.job_status.setText("摘要已完成" + timing + "，自动生成内容请核对。")
                if visible:
                    self.version_combo.setCurrentIndex(self.version_combo.findData('summary'))
            if kind == "diarize":
                self.job_status.setText("会后稿（说话者标注）已保存；编号可能变化，请核对。")
                if visible:
                    self.version_combo.setCurrentIndex(self.version_combo.findData("speakers"))
                continue_summary = self.summarize_after_transcription
                self.summarize_after_transcription = False
                if continue_summary:
                    self.start_summary(session=self.process_folder, transcript=Path(self.process_result['path']))
            if kind == "transcribe":
                if self.process_result.get("segments") == 0:
                    self.summarize_after_transcription = False
                    self.job_status.setText("转写完成，但未识别出语音。请回听录音并检查音源。")
                else:
                    self.start_diarization(Path(self.process_result['path']), folder=self.process_folder)
            self.refresh_history(preserve_preview=not visible)
        else:
            self.summarize_after_transcription = False
            self.progress.setValue(0)
            message = self.process_errors.strip()[-1600:] or f"进程异常退出（{code}）。"
            self.job_status.setText("转写 / 下载未完成，录音文件保留。GPU 出错时可选择 CPU 重试。")
            if kind == "summary":
                self.job_status.setText("摘要未完成；转写稿和已有摘要保留。")
            if kind == "diarize":
                self.job_status.setText("说话者区分未完成；原始录音和转写稿保留，可切换 CPU 重试。")
            self.show_error(message)
        if not self.process:
            self.job_states[self.process_folder] = ('已取消' if kind == 'cancelled' else
                '处理完成' if code == 0 and self.process_result else '处理失败，可重试')
            self.save_job_queue()
            QTimer.singleShot(0, self.drain_jobs)
        self.set_controls()

    def cancel_process(self):
        if self.live_process:
            self.live_cancelled = True
            self.live_status.setText("正在停止实时预览；录音继续保存。")
            (self.live_folder / "live_cancel.flag").touch()
            process = self.live_process
            # Give inference a chance to checkpoint, but never trap the UI on a stuck GPU.
            QTimer.singleShot(8000, lambda: process.kill() if self.live_process is process else None)
            return
        if self.process:
            if self.process_kind == "summary":
                self.summary_cancel.touch()
                self.process_kind = "cancelled"
                self.job_status.setText("正在取消摘要，等待本机模型结束当前请求…")
                self.cancel_button.setEnabled(False)
                return
            self.process_kind = "cancelled"
            self.job_status.setText("正在取消…")
            self.process.kill()

    def set_controls(self):
        self.update_recording_icon()
        recording = self.recorder is not None
        busy = self.task is not None or self.process is not None
        capture_idle = not recording and self.task is None
        idle = capture_idle and not busy and self.live_process is None
        self.playback.setEnabled(idle and self.playback.path is not None)
        if not idle:
            self.playback.player.stop()
        single = self.capture_mode.currentData() == 'microphone_only'
        self.capture_hint.setText('麦克风采集自己和外放声音；所有人统一按参会者编号。' if single else '麦克风单独标为本机；电脑声音按参会者区分。')
        self.start_button.setEnabled(capture_idle and self.mic_combo.count() > 0 and (single or self.system_combo.count() > 0))
        self.pause_button.setEnabled(recording and self.task is None and not self.stopping)
        self.stop_button.setEnabled(recording and self.task is None and not self.stopping)
        for widget in (self.refresh_button, self.mic_combo, self.system_combo, self.title_edit,
                       self.model_combo, self.language_combo, self.device_combo, self.vocabulary,
                       self.history, self.live_enabled, self.live_engine, self.version_combo,
                       self.choose_folder_button, self.summary_model, self.capture_mode):
            widget.setEnabled(idle)
        # This is a post-recording preference, not a capture/model setting.
        # Keep it editable while recording, paused, saving or draining live ASR.
        self.auto_transcribe.setEnabled(True)
        for widget in (self.refresh_button, self.mic_combo, self.title_edit, self.capture_mode, self.live_enabled):
            widget.setEnabled(capture_idle)
        self.history.setEnabled(not recording and self.task is None)
        self.version_combo.setEnabled(not recording and self.task is None)
        self.system_combo.setEnabled(capture_idle and not single)
        can_change_devices = self.task is None and not self.stopping
        self.refresh_button.setEnabled(can_change_devices)
        self.mic_combo.setEnabled(can_change_devices)
        self.system_combo.setEnabled(can_change_devices and not single)
        self.system_meter.setEnabled(not single)
        self.transcribe_button.setEnabled(idle and self.selected is not None)
        self.summarize_button.setEnabled(idle and self.summary_source() is not None)
        self.edit_speaker_button.setEnabled(idle and self.version_combo.currentData() != 'summary' and self.transcript_folder() is not None)
        self.cancel_button.setEnabled((self.process is not None and self.process_kind != "cancelled") or self.live_process is not None)
        self.cancel_button.setText("停止实时预览" if self.live_process else "取消")
        self.folder_button.setEnabled(idle and self.selected is not None)
        for widget in (self.copy_button, self.export_button):
            widget.setEnabled(idle and (self.summary_folder() is not None if self.version_combo.currentData() == 'summary' else self.transcript_folder() is not None))

    def tick(self):
        self.update_recording_icon()
        self.check_recording_silence()
        self.update_job_indicators()
        model = self.model_combo.currentData()
        self.model_status.setText(f'{model}  ·  ' + ("本地模型已就绪" if model_ready(model) else "尚未下载，首次下载需要联网"))
        if self.recorder and self.recorder.active and not self.stopping:
            self.clock.setText(timestamp(self.recorder.timeline.position()))
            for key, meter in (("microphone", self.mic_meter), ("system", self.system_meter)):
                peak = self.recorder.levels[key]
                import math
                value = max(0, min(100, round((20*math.log10(max(peak, 1e-6)) + 60) / 60 * 100)))
                meter.setValue(value)
            try:
                self.recorder.heartbeat()
            except Exception as exc:
                self.recorder.fail(f"保存状态失败：{exc}")
            if self.recorder.error:
                self.device_hint.setText(self.recorder.error)
                self.stop_recording()
            elif self.recorder.live_warning:
                self.live_status.setText(self.recorder.live_warning)
        elif not self.recorder:
            self.mic_meter.setValue(0)
            self.system_meter.setValue(0)
        if self.live_process and time.monotonic()-self.live_last_event > 12:
            self.live_status.setText("实时识别响应较慢，录音仍独立保存；可停止实时预览后会后重转。")

    def update_recording_icon(self):
        self.recording_icon.set_recording(bool(
            self.recorder and self.recorder.active and not self.recorder.paused
            and not self.stopping and not self.recorder.error))

    def check_recording_silence(self):
        recorder = self.recorder
        if not recorder or not recorder.active or self.stopping or recorder.paused:
            self.silence_hint.hide()
            if self.silence_dialog:
                self.silence_dialog.close()
            return
        key = 'system' if 'system' in recorder.devices else 'microphone'
        silence = recorder.silence_seconds()
        opening_warning = recorder.last_signal[key] == 0 and silence >= 30
        long_warning = silence >= 300
        if not opening_warning and not long_warning:
            self.silence_hint.hide()
            if self.silence_dialog:
                self.silence_dialog.close()
            return
        source = '会议声音（扬声器／耳机）' if key == 'system' else '麦克风'
        episode = (key, recorder.last_signal[key])
        if long_warning:
            message = (f"{source}已连续 5 分钟未检测到明显声音。会议是否已经结束？"
                       "如已结束，请点击“结束并保存”；否则请检查所选音源。"
                       "录音仍在继续。\n当前音源：" + recorder.devices[key].name)
            already_notified = self.long_silence_episode == episode
        else:
            message = (f"录音已开始 30 秒，{source}仍未检测到明显声音。请检查所选设备，"
                       "确认对应音量条有变化。录音仍在继续。\n当前音源：" + recorder.devices[key].name)
            already_notified = self.silence_notified
        self.silence_hint.setText(message)
        self.silence_hint.show()
        if already_notified:
            return
        if long_warning:
            self.long_silence_episode = episode
        else:
            self.silence_notified = True
        if self.silence_dialog is None:
            self.silence_dialog = QMessageBox(QMessageBox.Icon.Warning, "录音未检测到声音", "",
                                             QMessageBox.StandardButton.Ok, self)
            self.silence_dialog.setTextFormat(Qt.TextFormat.PlainText)
            self.silence_dialog.setModal(False)
        self.silence_dialog.setText(message)
        self.silence_dialog.show()
        if long_warning:
            self.silence_dialog.raise_()
            QApplication.beep()
        QApplication.alert(self, 5000)

    def start_live(self):
        self.live_folder = self.active_session
        self.version_combo.setCurrentIndex(self.version_combo.findData("live"))
        self.preview.clear()
        self.live_result = None
        self.live_errors = ""
        self.live_buffer = ""
        self.live_cancelled = False
        self.live_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.live_error_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.live_last_event = time.monotonic()
        self.live_process = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("HF_HUB_OFFLINE", "1")
        env.insert("HF_HUB_DISABLE_TELEMETRY", "1")
        self.live_process.setProcessEnvironment(env)
        self.live_process.setWorkingDirectory(str(PROJECT))
        self.live_process.readyReadStandardOutput.connect(self.read_live)
        self.live_process.readyReadStandardError.connect(self.read_live_errors)
        self.live_process.finished.connect(self.live_finished)
        self.live_process.errorOccurred.connect(self.live_process_error)
        nemotron = self.live_engine.currentData() == 'nemotron'
        self.live_status.setText("正在加载实时模型；原始录音已开始保存…")
        self.live_process.start(str(diarization_runtime() if nemotron else Path(sys.executable).with_name("python.exe")),
                                ["-m", "meeting_scribe.live_nemotron" if nemotron else "meeting_scribe.live", str(self.live_folder),
                                 "--language", self.language_combo.currentData(), "--vocabulary", self.vocabulary.text()])
        self.set_controls()

    def read_live(self):
        if not self.live_process:
            return
        self.live_buffer += self.live_decoder.decode(bytes(self.live_process.readAllStandardOutput()))
        changed = False
        while "\n" in self.live_buffer:
            line, self.live_buffer = self.live_buffer.split("\n", 1)
            try:
                event = json.loads(line)
            except ValueError:
                continue
            self.live_last_event = time.monotonic()
            kind = event.get("type")
            if kind in ("live_status", "live_ready"):
                self.live_status.setText(event["message"])
            elif kind == "live_commit":
                self.live_rows.append(event)
                changed = True
            elif kind == 'live_snapshot':
                self.live_rows = event['rows']
                self.live_partials = {'all':event['partial']}
                changed = True
            elif kind == "live_partial":
                self.live_partials[event["source"]] = event["rows"]
                changed = True
            elif kind == "live_health":
                lag = event["backlog_seconds"]
                state = {"paused": "已暂停", "finished": "正在收尾"}.get(event["state"], "实时识别中")
                self.live_status.setText(f'{state} · 待处理约 {lag:.1f} 秒' +
                    (" · 转写积压，原声继续保存" if lag > 8 else "") +
                    (f' · {event["gaps"]} 处需会后重转' if event["gaps"] else ""))
            elif kind == "live_gap":
                self.live_status.setText(event["message"])
                self.live_partials[event["source"]] = []
                changed = True
            elif kind == "live_error":
                self.live_errors += event["message"]
            elif kind == "live_done":
                self.live_result = event
        if changed and (not self.recorder or self.active_session == self.live_folder):
            provisional = [r for parts in self.live_partials.values() for r in parts]
            self.preview.update_rows(self.live_rows, provisional, note=(
                'Nemotron 3.5 实时草稿 · 说话者及时间戳待核对' if self.live_engine.currentData() == 'nemotron' else 'Turbo 实时草稿'))
            self.partial_view.setText("临时段落会随上下文修订，停顿后归入已确认草稿。")
            self.partial_view.setVisible(bool(provisional))

    def read_live_errors(self):
        if self.live_process:
            self.live_errors = (self.live_errors + self.live_error_decoder.decode(
                bytes(self.live_process.readAllStandardError())))[-4000:]

    def live_process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.live_errors = "实时识别进程无法启动。"
            self.live_finished(1, QProcess.ExitStatus.CrashExit)

    def live_finished(self, code, _status):
        if not self.live_process:
            return
        self.read_live()
        self.read_live_errors()
        self.live_process.deleteLater()
        self.live_process = None
        self.partial_view.hide()
        if not self.live_result:
            draft = read_json(self.live_folder / "live_draft.json")
            if draft:
                try:
                    metadata = draft.get("metadata", {})
                    metadata.update(mode="live", complete=False,
                                    reason="实时识别提前结束；仅恢复已确认段落，后续内容请会后重转。",
                                    skipped_ranges=draft.get("skipped_ranges", []))
                    export_transcript(self.live_folder, draft.get("segments", []), metadata)
                except OSError as exc:
                    self.live_errors += f"\n恢复实时草稿失败：{exc}"
        if self.live_cancelled:
            self.live_status.setText("实时预览已停止；原声独立保存，可会后重新转写。")
        elif code != 0 or not self.live_result:
            self.live_status.setText("实时识别已退出，原声继续保存。可会后转写。")
            self.live_status.setToolTip(self.live_errors or f"进程退出代码：{code}")
            self.statusBar().showMessage(self.live_status.text() + " " + self.live_errors[-160:])
        elif not self.live_result.get("complete", False) or not self.live_result.get("segments", 0):
            self.live_status.setText("实时转写未完成：" + (self.live_result.get("reason") or "未识别到文字，请会后重新转写。"))
        else:
            self.live_status.setText("实时草稿已保存；可会后用 large-v3 重新转写。")
        if not self.recorder:
            self.refresh_history(select=self.live_folder, preserve_preview=True)
        self.set_controls()
        QTimer.singleShot(200, self.maybe_auto_transcribe)

    def show_error(self, message):
        self.statusBar().showMessage(message[:200])
        dialog = QMessageBox(QMessageBox.Icon.Warning, "需要检查", message,
                             QMessageBox.StandardButton.Ok, self)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        dialog.exec()

    def open_path(self, path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_session(self):
        if self.selected:
            self.open_path(self.selected)

    def play_recording(self):
        self.playback.load(self.selected)
        self.playback.toggle_play()

    def open_transcript(self):
        path = self.summary_folder() if self.version_combo.currentData() == 'summary' else self.transcript_folder()
        if path:
            self.open_path(path)

    def preview_menu(self, position):
        text = self.preview.textCursor().selectedText().replace('\u2029', '\n').replace('\u2028', '\n').strip()
        menu = self.preview.createStandardContextMenu()
        menu.addSeparator()
        action = menu.addAction('翻译为中文')
        action.setEnabled(bool(text))
        action.triggered.connect(lambda: self.translate_selection(text, self.preview.viewport().mapToGlobal(position)))
        menu.exec(self.preview.viewport().mapToGlobal(position))
        menu.deleteLater()

    def translate_selection(self, text, position=None):
        from .translation import MAX_TEXT
        if len(text) > MAX_TEXT:
            self.show_error('单次最多翻译6000个字符，请缩小选择范围。')
            return
        if not text.strip():
            return
        if self.translation_popup is not None:
            self.translation_popup.close()
        position = position or self.preview.mapToGlobal(self.preview.rect().center())
        self.translation_popup = TranslationDialog(text, self.translation_service, position, self)
        popup = self.translation_popup
        popup.destroyed.connect(lambda: self.clear_translation_popup(popup))
        self.translation_popup.show()

    def clear_translation_popup(self, popup):
        if self.translation_popup is popup:
            self.translation_popup = None

    def copy_summary(self):
        if self.version_combo.currentData() == 'summary':
            folder = self.summary_folder()
            if folder:
                QApplication.clipboard().setText((folder/'summary.txt').read_text(encoding='utf-8-sig'))
            return
        path = self.transcript_folder()
        if path:
            QApplication.clipboard().setText((path / "summary_input.md").read_text(encoding="utf-8"))
            self.statusBar().showMessage("纪要素材已复制，未发送到任何服务。")

    def closeEvent(self, event):
        if self.recorder or self.task or self.process or self.live_process:
            QMessageBox.information(self, "仍有任务在运行", "请先结束并保存录音，或取消当前转写 / 下载，再关闭窗口。")
            event.ignore()
            return
        self.translation_service.stop()
        self.save_settings()
        self.playback.player.stop()
        event.accept()
