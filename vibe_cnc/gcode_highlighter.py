import re
from PyQt6.QtCore import QSize, QRect
from PyQt6.QtGui import QColor, QTextCharFormat, QSyntaxHighlighter, QPainter
from PyQt6.QtWidgets import QWidget, QPlainTextEdit, QFrame, QLabel, QVBoxLayout

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
        from PyQt6.QtCore import QTimer
        self.blockBlink = True
        self._blinkTimer = QTimer(self)
        self._blinkTimer.timeout.connect(self._toggleBlock)
        self._blinkTimer.start(530)

        self.updateRequest.connect(self._updateLineNumberArea)
        self.cursorPositionChanged.connect(self._highlightCurrentLine)
        # Skip highlighting on init to avoid PyQt6 ExtraSelection issues
        
        # Connect textChanged for auto-uppercase
        self.textChanged.connect(self._auto_uppercase)

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
                painter.drawText(0, top, self._lineArea.width()-6, fm.height(),
                                 0x0082, "N" + number)  # AlignRight | AlignVCenter
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
        """Auto-uppercase all text"""
        cursor = self.textCursor()
        position = cursor.position()
        
        # Get current text
        text = self.toPlainText()
        
        # Convert to uppercase
        text_upper = text.upper()
        
        # Only update if text changed
        if text != text_upper:
            self.blockSignals(True)  # Prevent recursion
            self.setPlainText(text_upper)
            self.blockSignals(False)
            
            # Restore cursor position
            cursor.setPosition(min(position, len(text_upper)))
            self.setTextCursor(cursor)
    
    def keyPressEvent(self, event):
        """Handle Enter key to add semicolon"""
        from PyQt6.QtCore import Qt
        
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
        else:
            super().keyPressEvent(event)

