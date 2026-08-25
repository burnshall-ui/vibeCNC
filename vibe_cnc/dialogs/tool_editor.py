"""Tool Editor Dialog"""

import os
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QComboBox, QMessageBox, QDoubleSpinBox
)

from vibe_cnc.tool_data import (
    NOSE_DIRECTION_LABELS,
    load_tools_json,
    save_tools_json,
)

# Get the base directory (parent of vibe_cnc/dialogs)
HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class ToolEditorDialog(QDialog):
    """Dialog for editing/creating Tools"""

    def __init__(self, tool_model, tool_num=None, parent=None):
        super().__init__(parent)
        self.tool_model = tool_model
        self.tool_num = tool_num
        self.is_new = (tool_num is None)

        self.setWindowTitle("Edit Tool" if not self.is_new else "New Tool")
        self.setModal(True)
        self.resize(500, 600)

        layout = QVBoxLayout(self)

        # --- Form Fields ---
        form = QFormLayout()

        self.t_input = QSpinBox()
        self.t_input.setRange(1, 999)
        self.t_input.setValue(1 if self.is_new else tool_num)
        form.addRow("Tool Number (T):", self.t_input)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g. CNMG1204P-S External")
        form.addRow("Name:", self.name_input)

        self.type_combo = QComboBox()
        self.type_combo.addItems([
            "turn_rough", "turn_finish", "groove", "drill",
            "thread_form", "boring", "parting", "other"
        ])
        form.addRow("Type:", self.type_combo)

        self.d_mm_input = QDoubleSpinBox()
        self.d_mm_input.setRange(0, 999)
        self.d_mm_input.setDecimals(2)
        self.d_mm_input.setSuffix(" mm")
        self.d_mm_input.setSpecialValueText("(empty)")
        form.addRow("Diameter (d):", self.d_mm_input)

        self.insert_radius_input = QDoubleSpinBox()
        self.insert_radius_input.setRange(0, 99)
        self.insert_radius_input.setDecimals(2)
        self.insert_radius_input.setSuffix(" mm")
        self.insert_radius_input.setSpecialValueText("(empty)")
        form.addRow("Insert Radius:", self.insert_radius_input)

        # Where the imaginary tool nose sits relative to the centre of the
        # nose radius. "Not set" stays a choice of its own: the tip number
        # moves the compensated path, so picking one for the operator would be
        # worse than the lint hint that asks them to.
        self.nose_dir_combo = QComboBox()
        self.nose_dir_combo.addItem("(not set)", None)
        for number in sorted(NOSE_DIRECTION_LABELS):
            self.nose_dir_combo.addItem(NOSE_DIRECTION_LABELS[number], number)
        form.addRow("Nose Direction:", self.nose_dir_combo)

        self.holder_input = QLineEdit()
        self.holder_input.setPlaceholderText("e.g. PCLNR2525")
        form.addRow("Holder:", self.holder_input)

        layout.addLayout(form)

        # --- Limits Section ---
        layout.addWidget(QLabel("<b>Machining Limits:</b>"))
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
        limits_form.addRow("Ap max (DOC):", self.ap_max_input)

        self.f_max_input = QDoubleSpinBox()
        self.f_max_input.setRange(0, 9.99)
        self.f_max_input.setDecimals(3)
        self.f_max_input.setSuffix(" mm/rev")
        self.f_max_input.setValue(0.25)
        limits_form.addRow("F max (Feed):", self.f_max_input)

        layout.addLayout(limits_form)

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
        self.btn_save.clicked.connect(self.save_tool)
        self.btn_delete.clicked.connect(self.delete_tool)
        self.btn_cancel.clicked.connect(self.reject)

        if not self.is_new:
            self._load_tool()

    def _load_tool(self):
        tool_data = self.tool_model.get_tool_info(self.tool_num)
        if not tool_data:
            QMessageBox.warning(self, "Error", f"Tool {self.tool_num} not found.")
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

        # findData returns -1 for a missing or out-of-range tip number, and
        # index 0 is "(not set)" -- so both land on the same honest answer.
        nose = tool_data.get('nose_direction')
        self.nose_dir_combo.setCurrentIndex(max(self.nose_dir_combo.findData(nose), 0))

        limits = tool_data.get('limits', {})
        self.vc_max_input.setValue(limits.get('vc_max', 150))
        self.ap_max_input.setValue(limits.get('ap_max', 2.0))
        self.f_max_input.setValue(limits.get('f_max', 0.25))

    def save_tool(self):
        t = self.t_input.value()
        name = self.name_input.text().strip()

        if not name:
            QMessageBox.warning(self, "Validation", "Name must not be empty.")
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
        if self.nose_dir_combo.currentData() is not None:
            tool_data["nose_direction"] = self.nose_dir_combo.currentData()

        # Load JSON
        try:
            j = load_tools_json()

            tool_table = j.get("tool_table", [])

            # A number that is already taken must never be written over. The
            # old record would vanish silently, and with it the insert radius
            # that nose compensation depends on. self.tool_num is None for a
            # new tool, so this covers both creating and renumbering.
            if t != self.tool_num and any(item.get("t") == t for item in tool_table):
                QMessageBox.warning(self, "Error", f"Tool number {t} already exists.")
                return

            for i, item in enumerate(tool_table):
                if item.get("t") == self.tool_num:
                    tool_table[i] = tool_data
                    break
            else:
                tool_table.append(tool_data)

            j["tool_table"] = tool_table

            save_tools_json(j)
            self.tool_model.reload()

            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Save failed:\n{e}")

    def delete_tool(self):
        if self.is_new:
            return

        reply = QMessageBox.question(
            self, "Confirm Delete",
            f"Really delete Tool {self.tool_num}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            j = load_tools_json()

            tool_table = j.get("tool_table", [])
            tool_table = [item for item in tool_table if item.get("t") != self.tool_num]
            j["tool_table"] = tool_table

            save_tools_json(j)
            self.tool_model.reload()

            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Delete failed:\n{e}")
