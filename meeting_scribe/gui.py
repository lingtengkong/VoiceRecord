from __future__ import annotations

import json
import codecs
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QThread, Signal, QProcess, QProcessEnvironment, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QFontDatabase
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QComboBox, QProgressBar, QFrame, QListWidget, QListWidgetItem,
    QPlainTextEdit, QMessageBox, QCheckBox, QSplitter, QSizePolicy, QScrollArea, QLayout, QFileDialog,
)

from .audio import Recorder, list_devices, recover_session
from .storage import data_root, new_session, read_json, atomic_json, sessions, timestamp
from .storage import recordings_root, set_recordings_root
from .transcription import MODELS, model_ready, export_transcript
from .transcript_view import TranscriptView

PROJECT = Path(__file__).resolve().parent.parent

STYLE = """
QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; color: #213348; }
QMainWindow, #root { background: #f1f5f8; }
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
        self.setWindowTitle("本地会议录音 · Meeting Scribe")
        self.resize(1180, 920)
        self.setMinimumSize(1000, 760)
        self.recorder = None
        self.task = None
        self.process = None
        self.process_kind = ""
        self.selected = None
        self.active_session = None
        self.process_buffer = ""
        self.stdout_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.stderr_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.process_errors = ""
        self.process_result = None
        self.process_folder = None
        self.stopping = False
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
        self.settings_path = data_root() / "settings.json"
        self.settings = read_json(self.settings_path)
        self.setStyleSheet(STYLE)
        self._build()
        self.refresh_devices()
        self.refresh_history(recover=recover_sessions)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(150)
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
        layout.addWidget(label("我的麦克风", "muted"))
        self.mic_combo = QComboBox()
        self.mic_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        layout.addWidget(self.mic_combo)
        self.mic_meter = QProgressBar()
        self.mic_meter.setTextVisible(False)
        self.mic_meter.setFixedHeight(7)
        layout.addWidget(self.mic_meter)
        layout.addWidget(label("会议声音 · 选择会议正在使用的耳机 / 扬声器", "muted"))
        self.system_combo = QComboBox()
        self.system_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        layout.addWidget(self.system_combo)
        self.system_meter = QProgressBar()
        self.system_meter.setTextVisible(False)
        self.system_meter.setFixedHeight(7)
        layout.addWidget(self.system_meter)
        self.device_hint = label("戴耳机可减少回声。会议声音会包含所选输出设备上的其他声音。", "hint")
        layout.addWidget(self.device_hint)
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
        self.live_enabled = QCheckBox("边录边转写 · Turbo（GPU）")
        self.live_enabled.setChecked(self.settings.get("live_enabled", True))
        layout.addWidget(self.live_enabled)
        self.live_status = label("实时转写在本机运行；临时文字会随上下文修订。", "hint")
        layout.addWidget(self.live_status)
        self.auto_transcribe = QCheckBox("结束后用下方所选模型重新转写")
        self.auto_transcribe.setChecked(self.settings.get("auto_transcribe", False))
        layout.addWidget(self.auto_transcribe)

        transcript, layout = card()
        left_layout.addWidget(transcript)
        layout.addWidget(label("02  本地转写", "section"))
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
        actions = QHBoxLayout()
        self.transcribe_button = button("转写录音", self.start_transcription, "primary")
        self.download_button = button("下载模型", self.download_model)
        self.cancel_button = button("取消", self.cancel_process)
        for b in (self.transcribe_button, self.download_button, self.cancel_button):
            actions.addWidget(b)
        layout.addLayout(actions)
        self.model_status = label("", "muted")
        layout.addWidget(self.model_status)
        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setFixedHeight(14)
        layout.addWidget(self.progress)
        self.job_status = label("模型准备好后，可断网转写。原始录音不会上传。", "hint")
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
        layout.addLayout(row)
        self.history = QListWidget()
        self.history.setMinimumHeight(60)
        self.history.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.history.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.history.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.history.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.history.currentItemChanged.connect(self.select_session)
        layout.addWidget(self.history, 1)
        self.session_info = label("开始一次录音后，会在这里保留音频和转写稿。", "muted")
        layout.addWidget(self.session_info)
        actions = QHBoxLayout()
        self.play_button = button("回听录音", self.play_recording)
        self.folder_button = button("打开本次文件夹", self.open_session)
        actions.addWidget(self.play_button)
        actions.addWidget(self.folder_button)
        layout.addLayout(actions)
        preview, layout = card()
        right_layout.addWidget(preview, 3)
        layout.addWidget(label("03  实时草稿与会后稿", "section"))
        self.version_combo = QComboBox()
        self.version_combo.addItem("会后稿（优先）", "final")
        self.version_combo.addItem("Turbo 实时草稿", "live")
        self.version_combo.currentIndexChanged.connect(self.show_selected_transcript)
        version_row = QHBoxLayout()
        version_row.addWidget(self.version_combo, 1)
        version_row.addWidget(button("回到最新", lambda: self.preview.verticalScrollBar().setValue(
            self.preview.verticalScrollBar().maximum())))
        layout.addLayout(version_row)
        self.preview = TranscriptView()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("转写完成后，在这里查看带时间戳的文字。\n\n保留音源标签：\n• 我 / 麦克风\n• 会议声音（可能包含多位参会者）\n\n复制纪要素材前，可先检查或删除敏感内容。")
        layout.addWidget(self.preview, 1)
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
        except Exception as exc:
            self.show_error(f"无法枚举音频设备：{exc}")

    def save_settings(self):
        settings = {"auto_transcribe": self.auto_transcribe.isChecked(), "vocabulary": self.vocabulary.text(),
                    "live_enabled": self.live_enabled.isChecked()}
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
        if self.recorder or self.task or self.process or self.live_process:
            return
        self.save_settings()
        self.left_scroll.verticalScrollBar().setValue(0)
        self.active_session = new_session(self.title_edit.text())
        self.pending_auto = False
        self.live_rows = []
        self.live_partials = {}
        self.preview.clear()
        self.partial_view.hide()
        self.recorder = Recorder(self.active_session, self.mic_combo.currentData(), self.system_combo.currentData())
        self.clock.setText("00:00:00")
        self.record_state.setText("正在打开设备…")
        self.run_task(self.recorder.start, self.recording_started)

    def recording_started(self, _):
        self.record_state.setText("● 正在录音")
        self.device_hint.setText("请确认两路音量条都有变化。暂停时不保存声音，续录后时间连续。")
        self.statusBar().showMessage("录音中：原声与麦克风分别保存。请勿拔出或切换正在录音的设备。")
        if self.live_enabled.isChecked():
            if model_ready("turbo"):
                self.start_live()
            else:
                self.live_status.setText("Turbo 尚未下载，本次只保存录音。可在会后选择 Turbo 并下载模型。")
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

    def stop_recording(self):
        if not self.recorder or self.stopping or self.task:
            return
        self.stopping = True
        self.record_state.setText("正在保存…")
        self.run_task(self.recorder.stop, self.recording_saved)

    def recording_saved(self, meta):
        self.recorder = None
        self.stopping = False
        self.pause_button.setText("暂停")
        self.record_state.setText("已保存" if not meta.get("error") else "已保存 · 请检查")
        if self.live_process:
            self.live_status.setText("录音已保存，Turbo 正在处理末尾文字…")
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
        self.device_hint.setText("；".join(notes) or "已保存两条独立音轨和混合录音，可以回听或转写。")
        self.statusBar().showMessage(self.device_hint.text())
        self.pending_auto = (self.auto_transcribe.isChecked() and not meta.get("error")
                             and model_ready(self.model_combo.currentData()))
        QTimer.singleShot(200, self.maybe_auto_transcribe)

    def maybe_auto_transcribe(self):
        if self.pending_auto and not any((self.live_process, self.recorder, self.task, self.process)):
            self.pending_auto = False
            self.refresh_history(select=self.active_session)
            self.version_combo.setCurrentIndex(0)
            self.start_transcription()

    def refresh_history(self, recover=False, select=None, preserve_preview=False):
        wanted = select or self.selected
        self.history.blockSignals(True)
        self.history.clear()
        for folder in sessions():
            meta = recover_session(folder) if recover else read_json(folder / "session.json")
            status = {"saved": "已保存", "recovered": "已恢复", "interrupted": "需检查"}.get(meta.get("status"), "未完成")
            item = QListWidgetItem(f'{meta.get("title", folder.name)}    ·    {meta.get("created_at", "")[:16].replace("T", "  ")}    ·    {timestamp(meta.get("duration", 0))}    ·    {status}')
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
        self.show_selected_transcript(preserve=preserve_preview)
        self.set_controls()

    def select_session(self, item, _previous=None):
        self.selected = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.show_selected_transcript()

    def show_selected_transcript(self, *_args, preserve=False):
        if not preserve:
            self.preview.clear()
        self.partial_view.hide()
        if self.selected:
            meta = read_json(self.selected / "session.json")
            self.session_info.setText(meta.get("error") or "两条独立音轨保留；混合录音用于回听。")
            path = self.transcript_folder()
            if path and (path / "transcript.txt").exists():
                payload = read_json(path / "transcript.json")
                if "segments" in payload:
                    metadata = payload.get("metadata", {})
                    note = "Turbo 实时草稿" if metadata.get("mode") == "live" else "会后转写稿"
                    if not metadata.get("complete", True):
                        note += " · 未完成，请会后重转核对"
                    if metadata.get("skipped_ranges"):
                        note += " · 有跳过段落，请会后重转"
                    self.preview.update_rows(payload["segments"], note=note, preserve=preserve)
                else:
                    self.preview.setPlainText((path / "transcript.txt").read_text(encoding="utf-8-sig"))
        self.set_controls()

    def transcript_folder(self):
        if self.selected:
            filenames = (["latest_live_transcript.json"] if self.version_combo.currentData() == "live"
                         else ["latest_transcript.json", "latest_live_transcript.json"])
            value = next((read_json(self.selected / filename).get("path") for filename in filenames
                          if read_json(self.selected / filename).get("path")), None)
            if value and Path(value).is_dir():
                return Path(value)
        return None

    def start_transcription(self):
        if self.process or self.recorder or self.task or self.live_process or not self.selected:
            return
        model = self.model_combo.currentData()
        if not model_ready(model):
            self.show_error("请先下载所选模型。下载只获取模型文件，不会上传录音。")
            return
        self.save_settings()
        self.process_folder = self.selected
        self.batch_rows = []
        self.version_combo.setCurrentIndex(0)
        self.preview.clear()
        args = ["-m", "meeting_scribe.transcription", str(self.selected), "--model", model,
                "--language", self.language_combo.currentData(), "--device", self.device_combo.currentData(),
                "--vocabulary", self.vocabulary.text()]
        self.start_process("transcribe", args)

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
        self.progress.setRange(0, 0 if kind == "download" else 100)
        self.progress.setValue(0)
        self.job_status.setText("正在下载模型，可取消后续传…" if kind == "download" else "正在启动本地转写…")
        self.left_scroll.ensureWidgetVisible(self.job_status)
        self.process.start(str(Path(sys.executable).with_name("python.exe")), args)
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
                self.progress.setValue(event["value"])
            elif kind == "segment":
                self.batch_rows.append(event)
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
            self.progress.setValue(0)
            self.job_status.setText("已取消。原始录音保留；未完成转写如有片段，将保存在 transcription_partial.json。")
        elif code == 0 and (kind == "download" or self.process_result):
            self.progress.setValue(100)
            self.job_status.setText("模型已准备好，可断网转写。" if kind == "download" else "转写完成 · TXT / Markdown / SRT 已保存。")
            if kind == "transcribe":
                self.refresh_history(select=self.process_folder, preserve_preview=True)
                if self.process_result.get("segments") == 0:
                    self.job_status.setText("转写完成，但未识别出语音。请回听录音并检查音源。")
        else:
            self.progress.setValue(0)
            message = self.process_errors.strip()[-1600:] or f"进程异常退出（{code}）。"
            self.job_status.setText("转写 / 下载未完成，录音文件保留。GPU 出错时可选择 CPU 重试。")
            self.show_error(message)
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
            self.process_kind = "cancelled"
            self.job_status.setText("正在取消…")
            self.process.kill()

    def set_controls(self):
        recording = self.recorder is not None
        busy = self.task is not None or self.process is not None
        idle = not recording and not busy and self.live_process is None
        self.start_button.setEnabled(idle and self.mic_combo.count() > 0 and self.system_combo.count() > 0)
        self.pause_button.setEnabled(recording and not busy and not self.stopping)
        self.stop_button.setEnabled(recording and not busy and not self.stopping)
        for widget in (self.refresh_button, self.mic_combo, self.system_combo, self.title_edit,
                       self.model_combo, self.language_combo, self.device_combo, self.vocabulary,
                       self.history, self.auto_transcribe, self.live_enabled, self.version_combo,
                       self.choose_folder_button):
            widget.setEnabled(idle)
        self.transcribe_button.setEnabled(idle and self.selected is not None)
        self.download_button.setEnabled(idle)
        self.cancel_button.setEnabled(self.process is not None or self.live_process is not None)
        self.cancel_button.setText("停止实时预览" if self.live_process else "取消")
        for widget in (self.play_button, self.folder_button):
            widget.setEnabled(idle and self.selected is not None)
        for widget in (self.copy_button, self.export_button):
            widget.setEnabled(idle and self.transcript_folder() is not None)

    def tick(self):
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

    def start_live(self):
        self.live_folder = self.active_session
        self.version_combo.setCurrentIndex(1)
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
        self.live_status.setText("正在加载 Turbo；原始录音已开始保存…")
        self.live_process.start(str(Path(sys.executable).with_name("python.exe")),
                                ["-m", "meeting_scribe.live", str(self.live_folder),
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
        if changed:
            provisional = [r for parts in self.live_partials.values() for r in parts]
            self.preview.update_rows(self.live_rows, provisional, note="Turbo 实时草稿")
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
        else:
            self.live_status.setText("Turbo 实时草稿已保存；可会后用 large-v3 重新转写。")
        if not self.recorder:
            self.refresh_history(select=self.live_folder, preserve_preview=True)
        self.set_controls()
        QTimer.singleShot(200, self.maybe_auto_transcribe)

    def show_error(self, message):
        self.statusBar().showMessage(message[:200])
        QMessageBox.warning(self, "需要检查", message)

    def open_path(self, path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_session(self):
        if self.selected:
            self.open_path(self.selected)

    def play_recording(self):
        if self.selected:
            path = self.selected / "mixed.wav"
            if not path.exists():
                path = self.selected / "system.wav"
            if path.exists():
                self.open_path(path)

    def open_transcript(self):
        path = self.transcript_folder()
        if path:
            self.open_path(path)

    def copy_summary(self):
        path = self.transcript_folder()
        if path:
            QApplication.clipboard().setText((path / "summary_input.md").read_text(encoding="utf-8"))
            self.statusBar().showMessage("纪要素材已复制，未发送到任何服务。")

    def closeEvent(self, event):
        if self.recorder or self.task or self.process or self.live_process:
            QMessageBox.information(self, "仍有任务在运行", "请先结束并保存录音，或取消当前转写 / 下载，再关闭窗口。")
            event.ignore()
            return
        self.save_settings()
        event.accept()
