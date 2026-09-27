"""A reading-first transcript view with anchored scrolling."""
from html import escape

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QTextBlockUserData, QTextCursor
from PySide6.QtWidgets import QTextEdit

from .storage import timestamp


class Anchor(QTextBlockUserData):
    def __init__(self, key):
        super().__init__()
        self.key = key


def group_rows(rows, provisional=False):
    groups = []
    for row in sorted(rows, key=lambda r: (r["start"], r.get("end", r["start"]))):
        source = row["source"]
        if (groups and groups[-1]["source"] == source
                and row["start"] - groups[-1]["end"] <= 8
                and row["start"] - groups[-1]["start"] <= 45
                and len(groups[-1]["text"]) < 900):
            previous = groups[-1]
            text = row["text"].strip()
            # English needs a space; Chinese does not need one between fragments.
            joiner = " " if previous["text"][-1:].isascii() or text[:1].isascii() else ""
            previous["text"] += joiner + text
            previous["end"] = max(previous["end"], row.get("end", row["start"]))
        else:
            groups.append({"start": row["start"], "end": row.get("end", row["start"]),
                           "source": source, "text": row["text"].strip(),
                           "key": ("partial" if provisional else "row", source, round(row["start"], 3))})
    return groups


class TranscriptView(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAcceptRichText(False)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("""
            QTextEdit { background: #f8fafc; border: 1px solid #d4dfe7;
                        border-radius: 7px; padding: 10px; font-size: 14px; }
            QScrollBar:vertical { background: #e9eff4; width: 12px; margin: 2px; border-radius: 5px; }
            QScrollBar::handle:vertical { background: #92a8b8; min-height: 30px; border-radius: 4px; }
            QScrollBar::handle:vertical:hover { background: #637f92; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        """)
        self._render_signature = None

    def clear(self):
        self._render_signature = None
        super().clear()

    def appendPlainText(self, text):
        # Compatibility for legacy callers; all current transcript paths use rows.
        bar = self.verticalScrollBar()
        follow, position = bar.maximum() == bar.value(), bar.value()
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(("\n" if self.toPlainText() else "") + text)
        bar.setValue(bar.maximum() if follow else position)

    def update_rows(self, rows, partial=(), note="", preserve=True):
        groups = group_rows(rows) + group_rows(partial, provisional=True)
        signature = (tuple((g["key"], g["text"]) for g in groups), note)
        if signature == self._render_signature:
            return
        bar = self.verticalScrollBar()
        follow = preserve and bar.maximum() == bar.value()
        old_value = bar.value()
        block = self.cursorForPosition(QPoint(2, 2)).block()
        data = block.userData()
        anchor = data.key if data else None
        offset = old_value - self.document().documentLayout().blockBoundingRect(block).top()
        selection = self.textCursor()
        old_position, old_anchor = selection.position(), selection.anchor()
        paragraphs, keys = [], []
        if note:
            paragraphs.append('<p style="margin:0 0 16px 0;color:#738497;font-size:11px;">' + escape(note) + '</p>')
            keys.append(("note",))
        for group in groups:
            temporary = group["key"][0] == "partial"
            metadata = timestamp(group["start"]) + "  ·  " + group["source"]
            if temporary:
                metadata += "  ·  临时文字"
            paragraphs.append('<p style="margin:0 0 16px 0;line-height:145%;">'
                              '<span style="color:#7c8c9b;font-size:10px;">' + escape(metadata) + '</span><br/>'
                              '<span style="font-size:14px;color:' + ('#526d80' if temporary else '#213348') + ';">'
                              + escape(group["text"]).replace("\n", "<br/>") + '</span></p>')
            keys.append(group["key"])
        self.setHtml("".join(paragraphs))
        self._render_signature = signature
        block = self.document().begin()
        anchored_top = None
        for key in keys:
            if not block.isValid():
                break
            block.setUserData(Anchor(key))
            if key == anchor:
                anchored_top = self.document().documentLayout().blockBoundingRect(block).top()
            block = block.next()
        # Force layout before restoring pixel position; setTextCursor can scroll,
        # so selection is restored first and reading position last.
        self.document().documentLayout().documentSize()
        if preserve and not follow:
            cursor = QTextCursor(self.document())
            maximum = max(0, self.document().characterCount()-1)
            cursor.setPosition(min(old_anchor, maximum))
            cursor.setPosition(min(old_position, maximum), QTextCursor.MoveMode.KeepAnchor)
            self.setTextCursor(cursor)
        if not preserve:
            bar.setValue(0)
        elif follow:
            bar.setValue(bar.maximum())
        else:
            bar.setValue(round(anchored_top+offset) if anchored_top is not None else old_value)
