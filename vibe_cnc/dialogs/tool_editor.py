"""Tool Editor Dialog"""

import os
import json
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QComboBox, QMessageBox, QDoubleSpinBox
)

from vibe_cnc.tool_model import load_tools_json

# Get the base directory (parent of vibe_cnc/dialogs)
HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class ToolEditorDialog(QDialog):
    """Dialog zum Bearbeiten/Erstellen von Tools"""

    def __init__(self, tool_model, tool_num=None, parent=None):
        super().__init__(parent)
        self.tool_model = tool_model
        self.tool_num = tool_num
        self.is_new = (tool_num is None)

        self.setWindowTitle("Tool bearbeiten" if not self.is_new else "Neues Tool")
        self.setModal(True)
        self.resize(500, 600)

        layout = QVBoxLayout(self)

        # --- Form Fields ---
        form = QFormLayout()

        # Tool Number
        self.t_input = QSpinBox()
        self.t_input.setRange(1, 999)
        self.t_input.setValue(1 if self.is_new else tool_num)
        form.addRow("Tool-Nummer (T):", self.t_input)

        # Name
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("z.B. CNMG1204P-S Außen")
        form.addRow("Name:", self.name_input)

        # Type
        self.type_combo = QComboBox()
        self.type_combo.addItems([
            "turn_rough", "turn_finish", "groove", "drill",
            "thread_form", "boring", "parting", "other"
        ])
        form.addRow("Typ:", self.type_combo)

        # Diameter (optional)
        self.d_mm_input = QDoubleSpinBox()
        self.d_mm_input.setRange(0, 999)
        self.d_mm_input.setDecimals(2)
        self.d_mm_input.setSuffix(" mm")
        self.d_mm_input.setSpecialValueText("(leer)")
        form.addRow("Durchmesser (d):", self.d_mm_input)

        # Insert Radius (optional)
        self.insert_radius_input = QDoubleSpinBox()
        self.insert_radius_input.setRange(0, 99)
        self.insert_radius_input.setDecimals(2)
        self.insert_radius_input.setSuffix(" mm")
        self.insert_radius_input.setSpecialValueText("(leer)")
        form.addRow("Eckenradius:", self.insert_radius_input)

        # Holder (optional)
        self.holder_input = QLineEdit()
        self.holder_input.setPlaceholderText("z.B. PCLNR2525")
        form.addRow("Halter:", self.holder_input)

        layout.addLayout(form)

        # --- Limits Section ---
        layout.addWidget(QLabel("<b>Bearbeitungsgrenzen:</b>"))
        limits_form = QFormLayout()

        self.vc_max_input = QDoubleSpinBox()
        self.vc_max_input.setRange(0, 9999)
        self.vc_max_input.setDecimals(0)
        self.vc_max_input.setSuffix(" m/min")
        self.vc_max_input.setValue(150)
        limits_form.addRow("Vc max:", self.vc_max_input)

        self.ap_max_input = QDoubleSpinBox()
        self.ap_max_input.setRange(0, 99)
        self.ap_max_input.setDecimals(2)
        self.ap_max_input.setSuffix(" mm")
        self.ap_max_input.setValue(2.0)
        limits_form.addRow("Ap max (Zustellung):", self.ap_max_input)

        self.f_max_input = QDoubleSpinBox()
        self.f_max_input.setRange(0, 9.99)
        self.f_max_input.setDecimals(3)
        self.f_max_input.setSuffix(" mm/U")
        self.f_max_input.setValue(0.25)
        limits_form.addRow("F max (Vorschub):", self.f_max_input)

        layout.addLayout(limits_form)

        # --- Buttons ---
        btn_row = QHBoxLayout()
        self.btn_save = QPushButton("Speichern")
        self.btn_delete = QPushButton("Löschen")
        self.btn_cancel = QPushButton("Abbrechen")

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
        self.btn_save.clicked.connect(self.save_tool)
        self.btn_delete.clicked.connect(self.delete_tool)
        self.btn_cancel.clicked.connect(self.reject)

        # --- Load existing tool ---
        if not self.is_new:
            self._load_tool()

    def _load_tool(self):
        """Lade Tool-Daten aus JSON"""
        tool_data = self.tool_model.get_tool_info(self.tool_num)
        if not tool_data:
            QMessageBox.warning(self, "Fehler", f"Tool {self.tool_num} nicht gefunden.")
            self.reject()
            return

        self.t_input.setValue(tool_data.get('t', 1))
        self.name_input.setText(tool_data.get('name', ''))

        tool_type = tool_data.get('type', 'other')
        index = self.type_combo.findText(tool_type)
        if index >= 0:
            self.type_combo.setCurrentIndex(index)

        self.d_mm_input.setValue(tool_data.get('d_mm', 0))
        self.insert_radius_input.setValue(tool_data.get('insert_radius_mm', 0))
        self.holder_input.setText(tool_data.get('holder', ''))

        limits = tool_data.get('limits', {})
        self.vc_max_input.setValue(limits.get('vc_max', 150))
        self.ap_max_input.setValue(limits.get('ap_max', 2.0))
        self.f_max_input.setValue(limits.get('f_max', 0.25))

    def save_tool(self):
        """Speichere Tool in tools.json"""
        t = self.t_input.value()
        name = self.name_input.text().strip()

        if not name:
            QMessageBox.warning(self, "Validierung", "Name darf nicht leer sein.")
            return

        # Build tool dict
        tool_data = {
            "t": t,
            "name": name,
            "type": self.type_combo.currentText(),
            "limits": {
                "vc_max": self.vc_max_input.value(),
                "ap_max": self.ap_max_input.value(),
                "f_max": self.f_max_input.value()
            }
        }

        # Optional fields
        if self.d_mm_input.value() > 0:
            tool_data["d_mm"] = self.d_mm_input.value()
        if self.insert_radius_input.value() > 0:
            tool_data["insert_radius_mm"] = self.insert_radius_input.value()
        if self.holder_input.text().strip():
            tool_data["holder"] = self.holder_input.text().strip()

        # Load JSON
        try:
            tools_json_path = os.path.join(HERE, "tools", "tools.json")
            j = load_tools_json()

            # Find existing tool or add new
            tool_table = j.get("tool_table", [])
            found = False
            for i, item in enumerate(tool_table):
                if item.get("t") == self.tool_num if not self.is_new else item.get("t") == t:
                    # Update existing
                    tool_table[i] = tool_data
                    found = True
                    break

            if not found:
                # Check if T already exists (for new tools)
                if self.is_new and any(item.get("t") == t for item in tool_table):
                    QMessageBox.warning(self, "Fehler", f"Tool-Nummer {t} existiert bereits.")
                    return
                # Add new
                tool_table.append(tool_data)

            j["tool_table"] = tool_table

            # Save JSON
            with open(tools_json_path, "w", encoding="utf-8") as f:
                json.dump(j, f, indent=2, ensure_ascii=False)

            # Refresh model
            self.tool_model.rows = self.tool_model._load_tools()
            self.tool_model.tool_data = self.tool_model._load_tool_data()
            self.tool_model.layoutChanged.emit()

            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Speichern fehlgeschlagen:\n{e}")

    def delete_tool(self):
        """Lösche Tool aus tools.json"""
        if self.is_new:
            return

        reply = QMessageBox.question(
            self, "Löschen bestätigen",
            f"Tool {self.tool_num} wirklich löschen?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            tools_json_path = os.path.join(HERE, "tools", "tools.json")
            j = load_tools_json()

            tool_table = j.get("tool_table", [])
            tool_table = [item for item in tool_table if item.get("t") != self.tool_num]
            j["tool_table"] = tool_table

            # Save JSON
            with open(tools_json_path, "w", encoding="utf-8") as f:
                json.dump(j, f, indent=2, ensure_ascii=False)

            # Refresh model
            self.tool_model.rows = self.tool_model._load_tools()
            self.tool_model.tool_data = self.tool_model._load_tool_data()
            self.tool_model.layoutChanged.emit()

            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Löschen fehlgeschlagen:\n{e}")
