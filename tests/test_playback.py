import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from PySide6.QtWidgets import QApplication
from meeting_scribe.transcript_view import TranscriptView


class PlaybackHighlightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_sentence_highlight_inside_merged_paragraph_and_gaps(self):
        view = TranscriptView()
        view.update_rows([
            {'start':0,'end':1,'source':'参会者 1','text':'First sentence.'},
            {'start':1.5,'end':2,'source':'参会者 1','text':'Second sentence.'},
            {'start':1.6,'end':1.9,'source':'本机麦克风','text':'Overlap.'}])
        view.highlight_position(.5)
        self.assertEqual([s.cursor.selectedText() for s in view.extraSelections()], ['First sentence.'])
        view.highlight_position(1.2)
        self.assertEqual(view.extraSelections(), [])
        view.highlight_position(1.7)
        self.assertEqual([s.cursor.selectedText() for s in view.extraSelections()], ['Second sentence.', 'Overlap.'])
        self.assertIn(1.5, view._seek_times.values())
        view.clear()
        view.highlight_position(.5)
        self.assertEqual(view.extraSelections(), [])

    def test_highlight_does_not_move_scroll_when_follow_disabled(self):
        view = TranscriptView()
        view.resize(400, 250)
        view.show()
        view.update_rows([{'start':i*10,'end':i*10+5,'source':str(i),'text':'Words '*30}
                          for i in range(25)], preserve=False)
        self.app.processEvents()
        bar = view.verticalScrollBar()
        bar.setValue(70)
        view.highlight_position(241, follow=False)
        self.assertEqual(bar.value(), 70)
        view.highlight_position(241, follow=True)
        self.assertGreater(bar.value(), 70)
        view.close()
