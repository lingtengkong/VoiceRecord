import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace
from PySide6.QtCore import QPoint, Qt, Signal, QObject
from PySide6.QtWidgets import QApplication
from meeting_scribe.translation import Translator
from meeting_scribe.translation_dialog import TranslationDialog
from meeting_scribe import gui

class TranslationTests(unittest.TestCase):
    def fake_translator(self, tokens=None):
        translator = Translator.__new__(Translator)
        translator.source = MagicMock()
        translator.source.encode.return_value = ['hello'] * 481
        translator.target = MagicMock()
        translator.target.decode.return_value = '译文'
        translator.engine = MagicMock()
        translator.engine.translate_batch.side_effect = lambda batches, **kw: [SimpleNamespace(hypotheses=[tokens or ['translation', '</s>']]) for _ in batches]
        return translator

    def test_long_text_split_without_loss_and_paragraphs_preserved(self):
        translator = self.fake_translator()
        result = translator.translate('Hello\n\n中文原文')
        self.assertEqual(result, '译文 译文 译文\n\n中文原文')
        batches = translator.engine.translate_batch.call_args.args[0]
        self.assertEqual(sum(len(b)-2 for b in batches), 481)
        self.assertTrue(all(b[0] == '>>cmn_Hans<<' for b in batches))

    def test_incomplete_output_and_oversize_rejected(self):
        translator = self.fake_translator(['partial'])
        with self.assertRaises(RuntimeError): translator.translate('Hello')
        with self.assertRaises(ValueError): translator.translate('a'*6001)
        with self.assertRaises(ValueError): translator.translate('  ')

    def test_selection_menu_preserves_paragraphs_and_source(self):
        app = QApplication.instance() or QApplication([])
        with patch.object(gui, 'sessions', return_value=[]), patch.object(gui.MainWindow, 'refresh_devices'):
            window = gui.MainWindow(recover_sessions=False)
            window.save_settings = lambda: None
            try:
                window.preview.setPlainText('Hello\nWorld')
                window.preview.selectAll()
                position = window.preview.rect().center()
                with patch.object(window, 'translate_selection') as translate_mock, patch.object(window.preview, 'createStandardContextMenu', return_value=MagicMock()) as menu_mock:
                    window.preview_menu(position)
                    menu_mock.return_value.addAction.return_value.triggered.connect.call_args.args[0]()
                    translate_mock.assert_called_once_with('Hello\nWorld', window.preview.viewport().mapToGlobal(position))
                    self.assertEqual(window.preview.toPlainText(), 'Hello\nWorld')
                window.preview.moveCursor(window.preview.textCursor().MoveOperation.End)
                with patch.object(window.preview, 'createStandardContextMenu', return_value=MagicMock()) as menu_mock:
                    window.preview_menu(position)
                    menu_mock.return_value.addAction.return_value.setEnabled.assert_called_with(False)
            finally: window.close()

    def test_floating_window_nonmodal_and_ignores_stale_results(self):
        app = QApplication.instance() or QApplication([])
        class Service(QObject):
            resultReady = Signal(int, str, str)
            def submit(self, text): return 2
        service = Service()
        popup = TranslationDialog('Hello', service, QPoint(100,100))
        self.assertFalse(popup.isModal())
        self.assertTrue(popup.windowFlags() & Qt.WindowType.Tool)
        service.resultReady.emit(1, '旧结果', '')
        self.assertEqual(popup.result.toPlainText(), '')
        service.resultReady.emit(2, '你好', '')
        self.assertTrue(popup.copy.isEnabled())
        self.assertEqual(popup.result.toPlainText(), '你好')
        popup.close()
