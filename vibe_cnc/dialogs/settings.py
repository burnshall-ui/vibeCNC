"""Settings Dialog"""

import os
import yaml
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QPushButton, QTabWidget, QWidget, QDoubleSpinBox, QComboBox,
    QCheckBox, QLineEdit, QSpinBox, QMessageBox, QFileDialog
)


class SettingsDialog(QDialog):
    """Settings Dialog for VibeCNC"""

    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.cfg = config_manager
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.resize(700, 500)

        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # --- Tab 1: Machine ---
        machine_tab = QWidget()
        machine_layout = QFormLayout(machine_tab)

        self.chuck_z_input = QDoubleSpinBox()
        self.chuck_z_input.setRange(-999, 0)
        self.chuck_z_input.setValue(self.cfg.data.get('machine', {}).get('chuck_z_limit', -5.0))
        self.chuck_z_input.setSuffix(" mm")
        self.chuck_z_input.setDecimals(1)
        machine_layout.addRow("Chuck Z-Limit (Collision Boundary):", self.chuck_z_input)

        # Zero means "not measured yet". The collision check then treats every
        # diameter as blocked, which raises false alarms rather than hiding a
        # crash — the safe direction for a value nobody has entered.
        self.chuck_diameter_input = QDoubleSpinBox()
        self.chuck_diameter_input.setRange(0, 2000)
        self.chuck_diameter_input.setValue(
            self.cfg.data.get('machine', {}).get('chuck_diameter') or 0.0)
        self.chuck_diameter_input.setSuffix(" mm")
        self.chuck_diameter_input.setDecimals(1)
        self.chuck_diameter_input.setSpecialValueText("not set — every diameter blocked")
        machine_layout.addRow("Chuck Diameter (over the jaws):", self.chuck_diameter_input)

        self.tabs.addTab(machine_tab, "Machine")

        # --- Tab 2: AI ---
        ai_tab = QWidget()
        ai_layout = QFormLayout(ai_tab)

        self.ai_mode_combo = QComboBox()
        self.ai_mode_combo.addItems(["claude", "ollama"])
        current_mode = self.cfg.data.get('ai', {}).get('mode', 'ollama')
        self.ai_mode_combo.setCurrentText(current_mode)
        ai_layout.addRow("AI Mode:", self.ai_mode_combo)

        self.ai_offline_check = QCheckBox("Offline Mode (no API calls)")
        self.ai_offline_check.setChecked(self.cfg.data.get('ai', {}).get('offline', False))
        ai_layout.addRow("", self.ai_offline_check)

        self.claude_model_input = QLineEdit()
        self.claude_model_input.setText(
            self.cfg.data.get('ai', {}).get('anthropic', {}).get('model', 'claude-sonnet-4-20250514')
        )
        ai_layout.addRow("Claude Model:", self.claude_model_input)

        self.ollama_model_input = QLineEdit()
        self.ollama_model_input.setText(
            self.cfg.data.get('ai', {}).get('ollama', {}).get('model', 'granite3.3:8b')
        )
        ai_layout.addRow("Ollama Model:", self.ollama_model_input)

        self.tabs.addTab(ai_tab, "AI")

        # --- Tab 3: Paths ---
        paths_tab = QWidget()
        paths_layout = QFormLayout(paths_tab)

        self.camotics_path_input = QLineEdit()
        self.camotics_path_input.setText(
            self.cfg.data.get('paths', {}).get('camotics_exe', 'C:\\Program Files (x86)\\CAMotics\\camotics.exe')
        )
        paths_layout.addRow("CAMotics EXE:", self.camotics_path_input)

        camotics_browse = QPushButton("Browse...")
        camotics_browse.clicked.connect(self._browse_camotics)
        paths_layout.addRow("", camotics_browse)

        self.vm_share_input = QLineEdit()
        self.vm_share_input.setText(
            self.cfg.data.get('paths', {}).get('sim_share', '\\\\linuxcnc-vm\\sim\\incoming')
        )
        paths_layout.addRow("VM Share (SMB):", self.vm_share_input)

        self.tabs.addTab(paths_tab, "Paths")

        # --- Tab 4: UI ---
        ui_tab = QWidget()
        ui_layout = QFormLayout(ui_tab)

        self.font_size_input = QSpinBox()
        self.font_size_input.setRange(8, 24)
        self.font_size_input.setValue(self.cfg.data.get('ui', {}).get('font_base_pt', 12))
        self.font_size_input.setSuffix(" pt")
        ui_layout.addRow("Font Size:", self.font_size_input)

        self.tabs.addTab(ui_tab, "UI")

        # --- Buttons ---
        btn_row = QHBoxLayout()
        self.btn_save = QPushButton("Save")
        self.btn_cancel = QPushButton("Cancel")

        self.btn_save.setObjectName("Softkey")
        self.btn_cancel.setObjectName("Softkey")

        btn_row.addWidget(self.btn_save)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_cancel)
        layout.addLayout(btn_row)

        # Signals
        self.btn_save.clicked.connect(self.save_settings)
        self.btn_cancel.clicked.connect(self.reject)

    def _browse_camotics(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select CAMotics EXE", "", "Executable (*.exe);;All Files (*)"
        )
        if path:
            self.camotics_path_input.setText(path)

    def save_settings(self):
        self.cfg.data['machine']['chuck_z_limit'] = self.chuck_z_input.value()
        diameter = self.chuck_diameter_input.value()
        self.cfg.data['machine']['chuck_diameter'] = diameter if diameter > 0 else None
        self.cfg.data['ai']['mode'] = self.ai_mode_combo.currentText()
        self.cfg.data['ai']['offline'] = self.ai_offline_check.isChecked()
        self.cfg.data['ai']['anthropic']['model'] = self.claude_model_input.text().strip()
        self.cfg.data['ai']['ollama']['model'] = self.ollama_model_input.text().strip()
        self.cfg.data['paths']['camotics_exe'] = self.camotics_path_input.text().strip()
        self.cfg.data['paths']['sim_share'] = self.vm_share_input.text().strip()
        self.cfg.data['ui']['font_base_pt'] = self.font_size_input.value()

        try:
            config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config.yaml")
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(self.cfg.data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

            QMessageBox.information(self, "Saved", "Settings saved.\nPlease restart VibeCNC for all changes to take effect.")
            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Save failed:\n{e}")
