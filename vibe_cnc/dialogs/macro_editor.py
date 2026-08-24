"""Macro Editor Dialog"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QTextEdit, QPushButton, QSpinBox, QComboBox, QMessageBox
)

from vibe_cnc.macro_data import load_macros_json, save_macros_json


class MacroEditorDialog(QDialog):
    """Dialog for editing/creating Macros"""

    def __init__(self, macro_model, macro_nr=None, parent=None):
        super().__init__(parent)
        self.macro_model = macro_model
        self.macro_nr = macro_nr
        self.is_new = (macro_nr is None)

        self.setWindowTitle("Edit Macro" if not self.is_new else "New Macro")
        self.setModal(True)
        self.resize(600, 500)

        # Layout
        layout = QVBoxLayout(self)

        # --- Form Fields ---
        form = QFormLayout()

        self.nr_input = QSpinBox()
        self.nr_input.setRange(1, 99999)
        self.nr_input.setValue(9000 if self.is_new else macro_nr)
        form.addRow("Number (P-Value):", self.nr_input)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g. Peck Drilling Cycle")
        form.addRow("Name:", self.name_input)

        self.category_input = QLineEdit()
        self.category_input.setPlaceholderText("e.g. Drilling, Turning, Probing")
        form.addRow("Category:", self.category_input)

        self.call_type_combo = QComboBox()
        self.call_type_combo.addItems(["M98", "G65"])
        form.addRow("Call Type:", self.call_type_combo)

        layout.addLayout(form)

        # --- Description/Code ---
        layout.addWidget(QLabel("Description / Code:"))
        self.description_input = QTextEdit()
        self.description_input.setPlaceholderText("Enter description or G-Code here...")
        self.description_input.setMinimumHeight(200)
        layout.addWidget(self.description_input)

        # --- Buttons ---
        btn_row = QHBoxLayout()
        self.btn_save = QPushButton("Save")
        self.btn_delete = QPushButton("Delete")
        self.btn_cancel = QPushButton("Cancel")

        self.btn_save.setObjectName("Softkey")
        self.btn_delete.setObjectName("Softkey")
        self.btn_cancel.setObjectName("Softkey")

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

        if not self.is_new:
            self._load_macro()

    def _load_macro(self):
        macro_data = self.macro_model.get_macro(self.macro_nr)
        if not macro_data:
            QMessageBox.warning(self, "Error", f"Macro {self.macro_nr} not found.")
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
        nr = self.nr_input.value()
        name = self.name_input.text().strip()
        category = self.category_input.text().strip()
        call_type = self.call_type_combo.currentText()
        description = self.description_input.toPlainText().strip()

        if not name:
            QMessageBox.warning(self, "Validation", "Name must not be empty.")
            return

        macro = {
            "nr": nr,
            "name": name,
            "category": category,
            "call_type": call_type,
            "description": description,
        }

        try:
            payload = load_macros_json()
            macro_table = payload.get("macro_table", [])

            # A number that is already taken must never be written over. The
            # old record would vanish silently. self.macro_nr is None for a new
            # macro, so this covers both creating and renumbering.
            if nr != self.macro_nr and any(m.get("nr") == nr for m in macro_table):
                QMessageBox.warning(self, "Error", f"Macro number {nr} already exists.")
                return

            for i, item in enumerate(macro_table):
                if item.get("nr") == self.macro_nr:
                    macro_table[i] = macro
                    break
            else:
                macro_table.append(macro)

            payload["macro_table"] = macro_table
            save_macros_json(payload)
            self.macro_model.reload()

            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Save failed:\n{e}")

    def delete_macro(self):
        if self.is_new:
            return

        reply = QMessageBox.question(
            self, "Confirm Delete",
            f"Really delete Macro {self.macro_nr}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            payload = load_macros_json()
            payload["macro_table"] = [m for m in payload.get("macro_table", [])
                                      if m.get("nr") != self.macro_nr]
            save_macros_json(payload)
            self.macro_model.reload()

            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Delete failed:\n{e}")
