"""Find & Replace Dialog for G-Code Editor"""

from PyQt6.QtGui import QTextDocument
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QCheckBox, QMessageBox
)


class FindReplaceDialog(QDialog):
    """Find & Replace Dialog für den G-Code Editor"""

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.last_match_pos = -1

        self.setWindowTitle("Suchen & Ersetzen")
        self.setModal(False)  # Non-modal, damit man weiter im Editor arbeiten kann
        self.resize(500, 200)

        # Layout
        layout = QVBoxLayout(self)

        # --- Find Row ---
        find_row = QHBoxLayout()
        find_row.addWidget(QLabel("Suchen:"))
        self.find_input = QLineEdit()
        self.find_input.setPlaceholderText("Text eingeben...")
        find_row.addWidget(self.find_input)
        layout.addLayout(find_row)

        # --- Replace Row ---
        replace_row = QHBoxLayout()
        replace_row.addWidget(QLabel("Ersetzen:"))
        self.replace_input = QLineEdit()
        self.replace_input.setPlaceholderText("Ersetzungstext...")
        replace_row.addWidget(self.replace_input)
        layout.addLayout(replace_row)

        # --- Options ---
        options_row = QHBoxLayout()
        self.case_sensitive = QCheckBox("Groß-/Kleinschreibung")
        self.whole_words = QCheckBox("Nur ganze Wörter")
        options_row.addWidget(self.case_sensitive)
        options_row.addWidget(self.whole_words)
        options_row.addStretch()
        layout.addLayout(options_row)

        # --- Buttons ---
        btn_row = QHBoxLayout()
        self.btn_find_next = QPushButton("Weiter")
        self.btn_find_prev = QPushButton("Zurück")
        self.btn_replace = QPushButton("Ersetzen")
        self.btn_replace_all = QPushButton("Alle ersetzen")
        self.btn_close = QPushButton("Schließen")

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

        # Focus auf Find-Input
        self.find_input.setFocus()

    def find_next(self):
        """Suche nächstes Vorkommen"""
        search_text = self.find_input.text()
        if not search_text:
            return

        # Suchoptionen
        flags = QTextDocument.FindFlag(0)
        if self.case_sensitive.isChecked():
            flags |= QTextDocument.FindFlag.FindCaseSensitively
        if self.whole_words.isChecked():
            flags |= QTextDocument.FindFlag.FindWholeWords

        # Suche ab aktueller Cursor-Position
        cursor = self.editor.textCursor()
        found_cursor = self.editor.document().find(search_text, cursor, flags)

        if not found_cursor.isNull():
            self.editor.setTextCursor(found_cursor)
            self.last_match_pos = found_cursor.position()
        else:
            # Wrap around - von Anfang suchen
            cursor.movePosition(cursor.MoveOperation.Start)
            found_cursor = self.editor.document().find(search_text, cursor, flags)
            if not found_cursor.isNull():
                self.editor.setTextCursor(found_cursor)
                self.last_match_pos = found_cursor.position()
            else:
                QMessageBox.information(self, "Suchen", f"'{search_text}' nicht gefunden.")

    def find_previous(self):
        """Suche vorheriges Vorkommen"""
        search_text = self.find_input.text()
        if not search_text:
            return

        # Suchoptionen
        flags = QTextDocument.FindFlag.FindBackward
        if self.case_sensitive.isChecked():
            flags |= QTextDocument.FindFlag.FindCaseSensitively
        if self.whole_words.isChecked():
            flags |= QTextDocument.FindFlag.FindWholeWords

        # Suche rückwärts ab aktueller Cursor-Position
        cursor = self.editor.textCursor()
        found_cursor = self.editor.document().find(search_text, cursor, flags)

        if not found_cursor.isNull():
            self.editor.setTextCursor(found_cursor)
            self.last_match_pos = found_cursor.position()
        else:
            # Wrap around - von Ende suchen
            cursor.movePosition(cursor.MoveOperation.End)
            found_cursor = self.editor.document().find(search_text, cursor, flags)
            if not found_cursor.isNull():
                self.editor.setTextCursor(found_cursor)
                self.last_match_pos = found_cursor.position()
            else:
                QMessageBox.information(self, "Suchen", f"'{search_text}' nicht gefunden.")

    def replace_current(self):
        """Ersetze aktuell markierte Fundstelle"""
        search_text = self.find_input.text()
        replace_text = self.replace_input.text()

        if not search_text:
            return

        cursor = self.editor.textCursor()
        if cursor.hasSelection() and cursor.selectedText() == search_text:
            cursor.insertText(replace_text)
            # Suche nächstes Vorkommen
            self.find_next()
        else:
            # Kein Match selektiert - suche erstmal
            self.find_next()

    def replace_all(self):
        """Ersetze alle Vorkommen"""
        search_text = self.find_input.text()
        replace_text = self.replace_input.text()

        if not search_text:
            return

        # Suchoptionen
        flags = QTextDocument.FindFlag(0)
        if self.case_sensitive.isChecked():
            flags |= QTextDocument.FindFlag.FindCaseSensitively
        if self.whole_words.isChecked():
            flags |= QTextDocument.FindFlag.FindWholeWords

        # Zähle Ersetzungen
        count = 0
        cursor = self.editor.textCursor()
        cursor.beginEditBlock()  # Undo-Block für alle Ersetzungen

        # Start vom Anfang
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
            QMessageBox.information(self, "Ersetzen", f"{count} Vorkommen ersetzt.")
        else:
            QMessageBox.information(self, "Ersetzen", f"'{search_text}' nicht gefunden.")
