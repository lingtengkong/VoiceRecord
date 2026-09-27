import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication
from meeting_scribe.transcript_view import TranscriptView, group_rows


class TranscriptViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.view = TranscriptView()
        self.view.resize(470, 330)
        self.view.show()
        self.rows = [{"start": i*12, "end": i*12+8,
                      "source": "会议声音" if i%2 else "我 / 麦克风",
                      "text": f"Paragraph {i}. " + "The meeting content is the focus. "*5}
                     for i in range(35)]
        self.view.update_rows(self.rows, note="test", preserve=False)
        self.app.processEvents()

    def tearDown(self):
        self.view.close()

    def anchor(self):
        block = self.view.cursorForPosition(QPoint(2, 2)).block()
        return block.userData().key, round(self.view.document().documentLayout().blockBoundingRect(block).top()
                                          - self.view.verticalScrollBar().value())

    def test_reading_anchor_survives_new_lines_and_earlier_insertions(self):
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum()//2+9)
        self.app.processEvents()
        anchor = self.anchor()
        for i in range(8):
            rows = self.rows + [{"start": 500+i, "end": 501+i, "source": "会议声音", "text": "A new sentence."}]
            # A delayed result from the other source arrives before the viewport.
            rows.append({"start": 3, "end": 4, "source": "会议声音", "text": "Earlier result."})
            self.view.update_rows(rows)
            self.app.processEvents()
            self.assertEqual(self.anchor(), anchor)

    def test_follow_only_when_at_bottom(self):
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())
        extended = self.rows + [{"start": 600, "end": 610, "source": "会议声音", "text": "new "*150}]
        self.view.update_rows(extended)
        self.app.processEvents()
        self.assertEqual(bar.value(), bar.maximum())
        bar.setValue(bar.maximum()-150)
        saved = self.anchor()
        self.view.update_rows(extended, [{"start": 610, "end": 615, "source": "会议声音", "text": "temporary "*120}])
        self.app.processEvents()
        self.assertEqual(self.anchor(), saved)

    def test_same_source_groups_without_dropping_text_or_merging_speakers(self):
        rows = [{"start": 0, "end": 2, "source": "会议声音", "text": "First sentence."},
                {"start": 2, "end": 4, "source": "会议声音", "text": "Second sentence."},
                {"start": 4, "end": 6, "source": "我 / 麦克风", "text": "Reply."}]
        groups = group_rows(rows)
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]["text"], "First sentence. Second sentence.")
        self.view.update_rows(rows, preserve=False)
        self.assertEqual(self.view.toPlainText().count("会议声音"), 1)

    def test_transcript_markup_is_plain_text(self):
        self.view.update_rows([{"start": 0, "end": 1, "source": "会议声音", "text": "<b>literal</b> & text"}])
        self.assertIn("<b>literal</b> & text", self.view.toPlainText())


if __name__ == "__main__":
    unittest.main()
