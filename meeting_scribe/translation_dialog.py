"""Reusable offline translation subprocess and a nonmodal floating result panel."""
import json
import sys
from pathlib import Path
from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal, Qt, QPoint
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton

class TranslationService(QObject):
    resultReady = Signal(int, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert('PYTHONIOENCODING', 'utf-8')
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(str(Path(__file__).resolve().parent.parent))
        self.process.started.connect(self.flush_pending)
        self.process.readyReadStandardOutput.connect(self.read_results)
        self.process.errorOccurred.connect(self.failed)
        self.process.finished.connect(self.finished)
        self.pending = []
        self.waiting = set()
        self.buffer = b''
        self.sequence = 0
        self.stopping = False

    def submit(self, text):
        self.sequence += 1
        request_id = self.sequence
        self.waiting.add(request_id)
        self.pending.append(json.dumps({'id': request_id, 'text': text}, ensure_ascii=False).encode('utf-8') + b'\n')
        if self.process.state() == QProcess.ProcessState.NotRunning:
            executable = Path(sys.executable)
            console = executable.with_name('python.exe')
            self.process.start(str(console if console.exists() else executable), ['-m', 'meeting_scribe.translation'])
        elif self.process.state() == QProcess.ProcessState.Running:
            self.flush_pending()
        return request_id

    def flush_pending(self):
        for payload in self.pending:
            self.process.write(payload)
        self.pending.clear()

    def read_results(self):
        self.buffer += bytes(self.process.readAllStandardOutput())
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            try:
                payload = json.loads(line.decode('utf-8'))
                request_id = payload['id']
                self.waiting.discard(request_id)
                self.resultReady.emit(request_id, payload.get('text', ''), payload.get('error', ''))
            except (ValueError, KeyError):
                self.fail_waiting('翻译进程返回了无法读取的结果。')

    def fail_waiting(self, message):
        for request_id in list(self.waiting):
            self.resultReady.emit(request_id, '', message)
        self.waiting.clear()
        self.pending.clear()
        self.buffer = b''

    def failed(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.fail_waiting('翻译进程无法启动：' + self.process.errorString())

    def finished(self, _code, _status):
        self.read_results()
        if not self.stopping:
            self.fail_waiting('翻译进程已退出，请重试。')

    def stop(self):
        self.stopping = True
        self.process.kill()
        self.process.waitForFinished(1000)

class TranslationDialog(QDialog):
    def __init__(self, text, service, position, parent=None):
        super().__init__(parent, Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle('中文翻译')
        self.resize(420, 260)
        layout = QVBoxLayout(self)
        self.status = QLabel('正在翻译…')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        layout.addWidget(self.result, 1)
        row = QHBoxLayout()
        self.copy = QPushButton('复制译文')
        self.copy.setEnabled(False)
        self.copy.clicked.connect(lambda: QApplication.clipboard().setText(self.result.toPlainText()))
        row.addWidget(self.copy)
        row.addStretch()
        close = QPushButton('关闭')
        close.clicked.connect(self.close)
        row.addWidget(close)
        layout.addLayout(row)
        self.service = service
        service.resultReady.connect(self.completed)
        self.request_id = service.submit(text)
        screen = QGuiApplication.screenAt(position) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        self.move(QPoint(max(area.left(), min(position.x()+12, area.right()-self.width()-12)),
                         max(area.top(), min(position.y()+12, area.bottom()-self.height()-36))))

    def completed(self, request_id, text, error):
        if request_id != self.request_id:
            return
        if error:
            self.status.setText('翻译失败：' + error)
        elif not text.strip():
            self.status.setText('模型未返回译文，请重试。')
        else:
            self.result.setPlainText(text)
            self.copy.setEnabled(True)
            self.status.setText('中文译文')

    def closeEvent(self, event):
        self.service.resultReady.disconnect(self.completed)
        super().closeEvent(event)
