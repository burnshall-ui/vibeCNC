"""Settings Dialog"""

import os
import yaml
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QPushButton, QTabWidget, QWidget, QDoubleSpinBox, QComboBox,
    QCheckBox, QLineEdit, QSpinBox, QMessageBox, QFileDialog
)


class SettingsDialog(QDialog):
    """Settings Dialog für VibeCNC"""

    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.cfg = config_manager
        self.setWindowTitle("Einstellungen")
        self.setModal(True)
        self.resize(700, 500)

        layout = QVBoxLayout(self)

        # Tab Widget
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # --- Tab 1: Maschine ---
        machine_tab = QWidget()
        machine_layout = QFormLayout(machine_tab)

        self.chuck_z_input = QDoubleSpinBox()
        self.chuck_z_input.setRange(-999, 0)
        self.chuck_z_input.setValue(self.cfg.data.get('machine', {}).get('chuck_z_limit', -5.0))
        self.chuck_z_input.setSuffix(" mm")
        self.chuck_z_input.setDecimals(1)
        machine_layout.addRow("Chuck Z-Limit (Kollisionsgrenze):", self.chuck_z_input)

        self.tabs.addTab(machine_tab, "⚙️ Maschine")

        # --- Tab 2: KI ---
        ai_tab = QWidget()
        ai_layout = QFormLayout(ai_tab)

        self.ai_mode_combo = QComboBox()
        self.ai_mode_combo.addItems(["claude", "ollama"])
        current_mode = self.cfg.data.get('ai', {}).get('mode', 'ollama')
        self.ai_mode_combo.setCurrentText(current_mode)
        ai_layout.addRow("KI-Modus:", self.ai_mode_combo)

        self.ai_offline_check = QCheckBox("Offline-Modus (keine API-Calls)")
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

        self.tabs.addTab(ai_tab, "🤖 KI")

        # --- Tab 3: Pfade ---
        paths_tab = QWidget()
        paths_layout = QFormLayout(paths_tab)

        self.camotics_path_input = QLineEdit()
        self.camotics_path_input.setText(
            self.cfg.data.get('paths', {}).get('camotics_exe', 'C:\\Program Files (x86)\\CAMotics\\camotics.exe')
        )
        paths_layout.addRow("CAMotics EXE:", self.camotics_path_input)

        camotics_browse = QPushButton("Durchsuchen...")
        camotics_browse.clicked.connect(self._browse_camotics)
        paths_layout.addRow("", camotics_browse)

        self.vm_share_input = QLineEdit()
        self.vm_share_input.setText(
            self.cfg.data.get('paths', {}).get('sim_share', '\\\\linuxcnc-vm\\sim\\incoming')
        )
        paths_layout.addRow("VM-Share (SMB):", self.vm_share_input)

        self.tabs.addTab(paths_tab, "📁 Pfade")

        # --- Tab 4: UI ---
        ui_tab = QWidget()
        ui_layout = QFormLayout(ui_tab)

        self.font_size_input = QSpinBox()
        self.font_size_input.setRange(8, 24)
        self.font_size_input.setValue(self.cfg.data.get('ui', {}).get('font_base_pt', 12))
        self.font_size_input.setSuffix(" pt")
        ui_layout.addRow("Font-Größe:", self.font_size_input)

        self.tabs.addTab(ui_tab, "🎨 UI")

        # --- Buttons ---
        btn_row = QHBoxLayout()
        self.btn_save = QPushButton("Speichern")
        self.btn_cancel = QPushButton("Abbrechen")

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
        """Browse-Dialog für CAMotics EXE"""
        path, _ = QFileDialog.getOpenFileName(
            self, "CAMotics EXE auswählen", "", "Executable (*.exe);;Alle Dateien (*)"
        )
        if path:
            self.camotics_path_input.setText(path)

    def save_settings(self):
        """Speichere Settings in config.yaml"""
        # Update config dict
        self.cfg.data['machine']['chuck_z_limit'] = self.chuck_z_input.value()
        self.cfg.data['ai']['mode'] = self.ai_mode_combo.currentText()
        self.cfg.data['ai']['offline'] = self.ai_offline_check.isChecked()
        self.cfg.data['ai']['anthropic']['model'] = self.claude_model_input.text().strip()
        self.cfg.data['ai']['ollama']['model'] = self.ollama_model_input.text().strip()
        self.cfg.data['paths']['camotics_exe'] = self.camotics_path_input.text().strip()
        self.cfg.data['paths']['sim_share'] = self.vm_share_input.text().strip()
        self.cfg.data['ui']['font_base_pt'] = self.font_size_input.value()

        # Schreibe YAML
        try:
            config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config.yaml")
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(self.cfg.data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

            QMessageBox.information(self, "Gespeichert", "Einstellungen wurden gespeichert.\nBitte VibeCNC neu starten, damit alle Änderungen wirksam werden.")
            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Speichern fehlgeschlagen:\n{e}")
