"""Find & Replace Dialog for G-Code Editor"""

from PyQt6.QtGui import QTextDocument
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QCheckBox, QMessageBox
)


class FindReplaceDialog(QDialog):
    """Find & Replace Dialog for the G-Code Editor"""

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.last_match_pos = -1

        self.setWindowTitle("Find & Replace")
        self.setModal(False)
        self.resize(500, 200)

        layout = QVBoxLayout(self)

        # --- Find Row ---
        find_row = QHBoxLayout()
        find_row.addWidget(QLabel("Find:"))
        self.find_input = QLineEdit()
        self.find_input.setPlaceholderText("Enter text...")
        find_row.addWidget(self.find_input)
        layout.addLayout(find_row)

        # --- Replace Row ---
        replace_row = QHBoxLayout()
        replace_row.addWidget(QLabel("Replace:"))
        self.replace_input = QLineEdit()
        self.replace_input.setPlaceholderText("Replacement text...")
        replace_row.addWidget(self.replace_input)
        layout.addLayout(replace_row)

        # --- Options ---
        options_row = QHBoxLayout()
        self.case_sensitive = QCheckBox("Case Sensitive")
        self.whole_words = QCheckBox("Whole Words Only")
        options_row.addWidget(self.case_sensitive)
        options_row.addWidget(self.whole_words)
        options_row.addStretch()
        layout.addLayout(options_row)

        # --- Buttons ---
        btn_row = QHBoxLayout()
        self.btn_find_next = QPushButton("Next")
        self.btn_find_prev = QPushButton("Previous")
        self.btn_replace = QPushButton("Replace")
        self.btn_replace_all = QPushButton("Replace All")
        self.btn_close = QPushButton("Close")

        btn_row.addWidget(self.btn_find_prev)
        btn_row.addWidget(self.btn_find_next)
        btn_row.addWidget(self.btn_replace)
        btn_row.addWidget(self.btn_replace_all)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_close)
        layout.addLayout(btn_row)

        # --- Signals ---
        self.btn_find_next.clicked.connect(self.find_next)
        self.btn_find_prev.clicked.connect(self.find_previous)
        self.btn_replace.clicked.connect(self.replace_current)
        self.btn_replace_all.clicked.connect(self.replace_all)
        self.btn_close.clicked.connect(self.close)
        self.find_input.returnPressed.connect(self.find_next)
        self.replace_input.returnPressed.connect(self.replace_current)

        self.find_input.setFocus()

    def find_next(self):
        search_text = self.find_input.text()
        if not search_text:
            return

        flags = QTextDocument.FindFlag(0)
        if self.case_sensitive.isChecked():
            flags |= QTextDocument.FindFlag.FindCaseSensitively
        if self.whole_words.isChecked():
            flags |= QTextDocument.FindFlag.FindWholeWords

        cursor = self.editor.textCursor()
        found_cursor = self.editor.document().find(search_text, cursor, flags)

        if not found_cursor.isNull():
            self.editor.setTextCursor(found_cursor)
            self.last_match_pos = found_cursor.position()
        else:
            cursor.movePosition(cursor.MoveOperation.Start)
            found_cursor = self.editor.document().find(search_text, cursor, flags)
            if not found_cursor.isNull():
                self.editor.setTextCursor(found_cursor)
                self.last_match_pos = found_cursor.position()
            else:
                QMessageBox.information(self, "Find", f"'{search_text}' not found.")

    def find_previous(self):
        search_text = self.find_input.text()
        if not search_text:
            return

        flags = QTextDocument.FindFlag.FindBackward
        if self.case_sensitive.isChecked():
            flags |= QTextDocument.FindFlag.FindCaseSensitively
        if self.whole_words.isChecked():
            flags |= QTextDocument.FindFlag.FindWholeWords

        cursor = self.editor.textCursor()
        found_cursor = self.editor.document().find(search_text, cursor, flags)

        if not found_cursor.isNull():
            self.editor.setTextCursor(found_cursor)
            self.last_match_pos = found_cursor.position()
        else:
            cursor.movePosition(cursor.MoveOperation.End)
            found_cursor = self.editor.document().find(search_text, cursor, flags)
            if not found_cursor.isNull():
                self.editor.setTextCursor(found_cursor)
                self.last_match_pos = found_cursor.position()
            else:
                QMessageBox.information(self, "Find", f"'{search_text}' not found.")

    def replace_current(self):
        search_text = self.find_input.text()
        replace_text = self.replace_input.text()

        if not search_text:
            return

        cursor = self.editor.textCursor()
        if cursor.hasSelection() and cursor.selectedText() == search_text:
            cursor.insertText(replace_text)
            self.find_next()
        else:
            self.find_next()

    def replace_all(self):
        search_text = self.find_input.text()
        replace_text = self.replace_input.text()

        if not search_text:
            return

        flags = QTextDocument.FindFlag(0)
        if self.case_sensitive.isChecked():
            flags |= QTextDocument.FindFlag.FindCaseSensitively
        if self.whole_words.isChecked():
            flags |= QTextDocument.FindFlag.FindWholeWords

        count = 0
        cursor = self.editor.textCursor()
        cursor.beginEditBlock()
        cursor.movePosition(cursor.MoveOperation.Start)

        while True:
            found_cursor = self.editor.document().find(search_text, cursor, flags)
            if found_cursor.isNull():
                break
            found_cursor.insertText(replace_text)
            cursor = found_cursor
            count += 1

        cursor.endEditBlock()

        if count > 0:
            QMessageBox.information(self, "Replace", f"{count} occurrence(s) replaced.")
        else:
            QMessageBox.information(self, "Replace", f"'{search_text}' not found.")
