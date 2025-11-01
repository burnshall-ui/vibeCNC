"""Macro Editor Dialog"""

import sqlite3
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QTextEdit, QPushButton, QSpinBox, QComboBox, QMessageBox
)

from vibe_cnc.macro_model import DB_PATH


class MacroEditorDialog(QDialog):
    """Dialog zum Bearbeiten/Erstellen von Macros"""

    def __init__(self, macro_model, macro_nr=None, parent=None):
        super().__init__(parent)
        self.macro_model = macro_model
        self.macro_nr = macro_nr  # None = Neu erstellen, sonst bearbeiten
        self.is_new = (macro_nr is None)

        self.setWindowTitle("Macro bearbeiten" if not self.is_new else "Neues Macro")
        self.setModal(True)
        self.resize(600, 500)

        # Layout
        layout = QVBoxLayout(self)

        # --- Form Fields ---
        form = QFormLayout()

        # Macro-Nummer
        self.nr_input = QSpinBox()
        self.nr_input.setRange(1, 99999)
        self.nr_input.setValue(9000 if self.is_new else macro_nr)
        form.addRow("Nummer (P-Wert):", self.nr_input)

        # Name
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("z.B. Bohrzyklus Peck")
        form.addRow("Name:", self.name_input)

        # Category
        self.category_input = QLineEdit()
        self.category_input.setPlaceholderText("z.B. Bohren, Drehen, Antasten")
        form.addRow("Kategorie:", self.category_input)

        # Call-Type
        self.call_type_combo = QComboBox()
        self.call_type_combo.addItems(["M98", "G65"])
        form.addRow("Call-Type:", self.call_type_combo)

        layout.addLayout(form)

        # --- Description/Code ---
        layout.addWidget(QLabel("Beschreibung / Code:"))
        self.description_input = QTextEdit()
        self.description_input.setPlaceholderText("Beschreibung oder G-Code hier eingeben...")
        self.description_input.setMinimumHeight(200)
        layout.addWidget(self.description_input)

        # --- Buttons ---
        btn_row = QHBoxLayout()
        self.btn_save = QPushButton("Speichern")
        self.btn_delete = QPushButton("Löschen")
        self.btn_cancel = QPushButton("Abbrechen")

        self.btn_save.setObjectName("Softkey")
        self.btn_delete.setObjectName("Softkey")
        self.btn_cancel.setObjectName("Softkey")

        # Delete-Button nur bei Edit anzeigen
        if self.is_new:
            self.btn_delete.hide()

        btn_row.addWidget(self.btn_save)
        btn_row.addWidget(self.btn_delete)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_cancel)
        layout.addLayout(btn_row)

        # --- Signals ---
        self.btn_save.clicked.connect(self.save_macro)
        self.btn_delete.clicked.connect(self.delete_macro)
        self.btn_cancel.clicked.connect(self.reject)

        # --- Load existing macro ---
        if not self.is_new:
            self._load_macro()

    def _load_macro(self):
        """Lade Macro-Daten aus DB"""
        macro_data = self.macro_model.get_macro(self.macro_nr)
        if not macro_data:
            QMessageBox.warning(self, "Fehler", f"Macro {self.macro_nr} nicht gefunden.")
            self.reject()
            return

        self.nr_input.setValue(macro_data['nr'])
        self.name_input.setText(macro_data['name'])
        self.category_input.setText(macro_data['category'])
        self.description_input.setPlainText(macro_data['description'])

        # Set call-type
        call_type = macro_data['call_type'] or 'M98'
        index = self.call_type_combo.findText(call_type)
        if index >= 0:
            self.call_type_combo.setCurrentIndex(index)

    def save_macro(self):
        """Speichere Macro in DB"""
        nr = self.nr_input.value()
        name = self.name_input.text().strip()
        category = self.category_input.text().strip()
        call_type = self.call_type_combo.currentText()
        description = self.description_input.toPlainText().strip()

        if not name:
            QMessageBox.warning(self, "Validierung", "Name darf nicht leer sein.")
            return

        # DB Update
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()

            if self.is_new:
                # Prüfe ob NR schon existiert
                cur.execute("SELECT COUNT(1) FROM macros WHERE nr=?;", (nr,))
                exists = cur.fetchone()[0] > 0
                if exists:
                    QMessageBox.warning(self, "Fehler", f"Macro-Nummer {nr} existiert bereits.")
                    conn.close()
                    return

                # INSERT
                cur.execute(
                    "INSERT INTO macros (nr, name, category, call_type, description) VALUES (?, ?, ?, ?, ?);",
                    (nr, name, category, call_type, description)
                )
            else:
                # UPDATE
                cur.execute(
                    "UPDATE macros SET nr=?, name=?, category=?, call_type=?, description=? WHERE nr=?;",
                    (nr, name, category, call_type, description, self.macro_nr)
                )

            conn.commit()
            conn.close()

            # Refresh model
            self.macro_model.rows = self.macro_model._load_rows()
            self.macro_model.layoutChanged.emit()

            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Speichern fehlgeschlagen:\n{e}")

    def delete_macro(self):
        """Lösche Macro aus DB"""
        if self.is_new:
            return

        # Bestätigung
        reply = QMessageBox.question(
            self, "Löschen bestätigen",
            f"Macro {self.macro_nr} wirklich löschen?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        # DB DELETE
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("DELETE FROM macros WHERE nr=?;", (self.macro_nr,))
            conn.commit()
            conn.close()

            # Refresh model
            self.macro_model.rows = self.macro_model._load_rows()
            self.macro_model.layoutChanged.emit()

            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Löschen fehlgeschlagen:\n{e}")
