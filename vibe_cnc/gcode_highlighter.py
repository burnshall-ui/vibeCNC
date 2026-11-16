import re
from PyQt6.QtCore import QSize, QRect, QTimer, Qt
from PyQt6.QtGui import QColor, QTextCharFormat, QSyntaxHighlighter, QPainter, QTextCursor
from PyQt6.QtWidgets import QWidget, QPlainTextEdit, QFrame, QLabel, QVBoxLayout

from vibe_cnc.gcode_text import uppercase_code

class TitlePanel(QWidget):
    def __init__(self, title: str, content: QWidget, colors: dict):
        super().__init__()
        v = QVBoxLayout(self); v.setContentsMargins(0,0,0,0); v.setSpacing(0)
        self.header = QLabel(title); self.header.setObjectName("PanelTitle")
        self.header.setMinimumHeight(28)
        v.addWidget(self.header); v.addWidget(content)

class GCodeHighlighter(QSyntaxHighlighter):
    def __init__(self, doc, colors: dict):
        super().__init__(doc)
        self.c = colors
        def fmt(color, bold=False):
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold: f.setFontWeight(600)
            return f
        self.fmtN      = fmt(self.c['WHITE'])
        self.fmtG      = fmt(self.c['CRT_GREEN'], True)
        self.fmtAxis   = fmt(self.c['FANUC_YELLOW'])
        self.fmtFS     = fmt(self.c['FANUC_YELLOW'])
        self.fmtTM     = fmt(self.c['CYAN'], True)
        self.fmtCmt    = fmt(self.c['CRT_GREEN_DIM'])

        self.rules = [
            (re.compile(r'\bN\d+\b'),        self.fmtN),
            (re.compile(r'\bG\d+(\.\d+)?\b'),self.fmtG),
            (re.compile(r'\b[XYZABC][+\-]?\d+(\.\d+)?\b'), self.fmtAxis),
            (re.compile(r'\b[FS][+\-]?\d+(\.\d+)?\b'),     self.fmtFS),
            (re.compile(r'\b[TM]\d+\b'),     self.fmtTM),
            (re.compile(r'\([^)]+\)'),       self.fmtCmt),
            (re.compile(r';[^\n]*'),         self.fmtCmt),
        ]

    def highlightBlock(self, text: str):
        # Comments first
        for rx, fmt in self.rules[-2:]:
            for m in rx.finditer(text):
                self.setFormat(m.start(), m.end()-m.start(), fmt)
        for rx, fmt in self.rules[:-2]:
            for m in rx.finditer(text):
                if self.format(m.start()).foreground().color().name() == QColor(self.c['CRT_GREEN_DIM']).name():
                    continue
                self.setFormat(m.start(), m.end()-m.start(), fmt)

class LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.codeEditor = editor
    def sizeHint(self): return QSize(self.codeEditor.lineNumberAreaWidth(), 0)
    def paintEvent(self, event):
        self.codeEditor.lineNumberAreaPaintEvent(event)

class GCodeEditor(QPlainTextEdit):
    def __init__(self, colors: dict):
        super().__init__()
        self.c = colors
        self.error_lines = []  # List of error lines
        self.sim_current_line = None  # Current simulation line (yellow marker)

        self.setFrameStyle(QFrame.Shape.NoFrame)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(' '))

        self._lineArea = LineNumberArea(self)
        self.blockBlink = True
        self._blinkTimer = QTimer(self)
        self._blinkTimer.timeout.connect(self._toggleBlock)
        self._blinkTimer.start(530)

        self.updateRequest.connect(self._updateLineNumberArea)
        self.cursorPositionChanged.connect(self._highlightCurrentLine)
        # Skip highlighting on init to avoid PyQt6 ExtraSelection issues
        
        # Connect textChanged for auto-uppercase
        self.textChanged.connect(self._auto_uppercase)
        self._fixTimer = QTimer(self)
        self._fixTimer.setSingleShot(True)
        self._fixTimer.timeout.connect(self._apply_uppercase)

    def lineNumberAreaWidth(self):
        digits = max(3, len(str(self.blockCount())))
        space = 12 + self.fontMetrics().horizontalAdvance('9') * digits
        return space

    def resizeEvent(self, e):
        super().resizeEvent(e)
        cr = self.contentsRect()
        self._lineArea.setGeometry(QRect(cr.left(), cr.top(), self.lineNumberAreaWidth(), cr.height()))

    def _updateLineNumberArea(self, rect, dy):
        if dy:
            self._lineArea.scroll(0, dy)
        else:
            self._lineArea.update(0, rect.y(), self._lineArea.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self.setViewportMargins(self.lineNumberAreaWidth(), 0, 0, 0)

    def lineNumberAreaPaintEvent(self, event):
        painter = QPainter(self._lineArea)
        painter.fillRect(event.rect(), QColor("#111"))
        block = self.firstVisibleBlock()
        blockNumber = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(blockNumber + 1)
                line_num = blockNumber + 1

                # Yellow simulation marker (vertical bar on the left)
                if self.sim_current_line == line_num:
                    marker_rect = QRect(0, top, 4, int(self.blockBoundingRect(block).height()))
                    painter.fillRect(marker_rect, QColor(self.c['FANUC_YELLOW']))

                # Mark error lines red
                if line_num in self.error_lines:
                    painter.setPen(QColor("#FF4444"))
                else:
                    painter.setPen(QColor("#7f7f7f"))
                fm = self.fontMetrics()
                # The line number, as any editor shows it. It used to read "N5"
                # for what is line 5 -- easy to take for the block's own N word,
                # which is a different number -- and the extra character did
                # not fit the width worked out for the digits alone.
                painter.drawText(0, top, self._lineArea.width()-6, fm.height(),
                                 0x0082, number)  # AlignRight | AlignVCenter
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            blockNumber += 1

    def set_error_lines(self, line_numbers: list):
        """Marks error lines with a red background"""
        self.error_lines = line_numbers
        self._lineArea.update()  # Update Line-Numbers
        self._highlightCurrentLine()  # Update Highlighting

    def clear_error_lines(self):
        """Removes all error markers"""
        self.error_lines = []
        self._lineArea.update()
        self._highlightCurrentLine()

    def set_sim_line(self, line_number: int):
        """Sets the yellow simulation marker to a line"""
        self.sim_current_line = line_number
        self._lineArea.update()

    def clear_sim_line(self):
        """Removes the simulation marker"""
        self.sim_current_line = None
        self._lineArea.update()

    def _highlightCurrentLine(self):
        # Simplified highlighting without ExtraSelections for PyQt6 compatibility
        # Error highlighting is handled in lineNumberAreaPaintEvent (red line numbers)
        # Current line highlighting is cosmetic and can be disabled
        
        # Just clear any existing extra selections
        self.setExtraSelections([])

    def _toggleBlock(self):
        self.blockBlink = not getattr(self, 'blockBlink', True)
        self._highlightCurrentLine()
    
    def _auto_uppercase(self):
        """Notices code that is not in capitals and schedules the fix.

        Typing is capitalised before it reaches the document -- see
        keyPressEvent. This is for everything else: a loaded file, a paste, the
        replace dialog. It does not edit here, because a change made from
        inside textChanged leaves Qt's undo unable to undo anything at all.
        """
        block = self.document().begin()
        while block.isValid():
            text = block.text()
            if uppercase_code(text) != text:
                self._fixTimer.start(0)
                return
            block = block.next()

    def _apply_uppercase(self):
        """Upper-cases the code, never a comment, replacing only what changes.

        The earlier version swapped in the whole text with setPlainText, which
        uppercased comments too, threw the undo history away and sent the
        scroll position back to the top.
        """
        # Consecutive changed letters are merged into one run, so a lowercase
        # program is a handful of edits rather than one per letter.
        runs = []  # (position in the document, replacement)
        block = self.document().begin()
        while block.isValid():
            text = block.text()
            fixed = uppercase_code(text)
            if fixed != text:
                start = None
                for index, (old, new) in enumerate(zip(text, fixed)):
                    if old != new and start is None:
                        start = index
                    elif old == new and start is not None:
                        runs.append((block.position() + start, fixed[start:index]))
                        start = None
                if start is not None:
                    runs.append((block.position() + start, fixed[start:]))
            block = block.next()
        if not runs:
            return

        # The replacements are as long as what they replace, so a position
        # worked out before the first edit is still right for the last.
        caret = self.textCursor()
        anchor, position_now = caret.anchor(), caret.position()
        edit = QTextCursor(self.document())
        edit.beginEditBlock()
        for position, letters in runs:
            edit.setPosition(position)
            edit.setPosition(position + len(letters), QTextCursor.MoveMode.KeepAnchor)
            edit.insertText(letters)
        edit.endEditBlock()

        # A caret standing exactly where a run begins is pushed past it by the
        # insert, although nothing was typed. Put it back.
        caret = self.textCursor()
        caret.setPosition(anchor)
        caret.setPosition(position_now, QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(caret)

    def _typed_in_capitals(self, event) -> bool:
        """Types the key as a capital when the code wants one; True if it did.

        Done before the letter enters the document, so it is one edit and undo
        takes it back in one step. Inside a comment, or with a shortcut held,
        the key is left to behave as usual.
        """
        shortcut = (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier
                    | Qt.KeyboardModifier.AltModifier)
        typed = event.text()
        if (not typed or not typed.isprintable() or event.modifiers() & shortcut
                or self.overwriteMode()):
            return False

        cursor = self.textCursor()
        start = cursor.selectionStart()
        block = self.document().findBlock(start)
        # The text before the caret says whether it is inside a comment.
        before = block.text()[:start - block.position()]
        cased = uppercase_code(before + typed)[len(before):]
        if cased == typed:
            return False
        cursor.insertText(cased)  # replaces a selection, as typing does
        self.setTextCursor(cursor)
        return True

    def keyPressEvent(self, event):
        """Handle Enter key to add semicolon; type code in capitals"""
        if event.key() == Qt.Key.Key_Return or event.key() == Qt.Key.Key_Enter:
            # Get current line
            cursor = self.textCursor()
            current_line = cursor.block().text()

            # Check if line doesn't already end with semicolon or is empty
            if current_line.strip() and not current_line.strip().endswith(';'):
                # Add semicolon before pressing Enter
                cursor.movePosition(cursor.MoveOperation.EndOfLine)
                cursor.insertText(' ;')

            # Process Enter normally
            super().keyPressEvent(event)
        elif self._typed_in_capitals(event):
            return
        else:
            super().keyPressEvent(event)
