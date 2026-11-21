"""Settings Dialog"""

import os
import yaml
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QPushButton, QTabWidget, QWidget, QDoubleSpinBox, QComboBox,
    QCheckBox, QLineEdit, QSpinBox, QMessageBox, QFileDialog
)

from vibe_cnc.claude_client import (
    AIClient, BACKENDS, DEFAULT_CLAUDE_MODEL, DEFAULT_OLLAMA_MODEL, DEFAULT_OPENAI_BASE_URL,
    NOT_A_VARIABLE_NAME, backend_of, is_variable_name,
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
        self.ai_mode_combo.addItems(list(BACKENDS))
        self.ai_mode_combo.setToolTip("claude: Anthropic's API. ollama: an Ollama server. openai: any "
                                      "server that speaks the OpenAI API (OpenAI, LM Studio, "
                                      "llama.cpp, vLLM, OpenRouter, ...).")
        # As the client reads it: a missing mode is Claude, "anthropic" too.
        # A missing one was taken for Ollama, and saving then switched the app
        # to Ollama; "anthropic" was shown as "claude" only because that is the
        # first entry.
        self.ai_mode_combo.setCurrentText(backend_of(self.cfg.data.get('ai', {})))
        ai_layout.addRow("AI Mode:", self.ai_mode_combo)

        self.ai_offline_check = QCheckBox("Offline Mode (no API calls)")
        self.ai_offline_check.setChecked(self.cfg.data.get('ai', {}).get('offline', False))
        ai_layout.addRow("", self.ai_offline_check)

        self.claude_model_input = QLineEdit()
        self.claude_model_input.setText(
            self.cfg.data.get('ai', {}).get('anthropic', {}).get('model', DEFAULT_CLAUDE_MODEL)
        )
        ai_layout.addRow("Claude Model:", self.claude_model_input)

        self.ollama_model_input = QLineEdit()
        self.ollama_model_input.setText(
            self.cfg.data.get('ai', {}).get('ollama', {}).get('model', DEFAULT_OLLAMA_MODEL)
        )
        ai_layout.addRow("Ollama Model:", self.ollama_model_input)

        # The address matters here as it does not for the others: it picks
        # the service. No model is filled in for it -- what a server offers
        # depends on the server, and Test connection lists it.
        openai = self.cfg.data.get('ai', {}).get('openai') or {}
        self.openai_url_input = QLineEdit()
        self.openai_url_input.setText(openai.get('base_url') or DEFAULT_OPENAI_BASE_URL)
        self.openai_url_input.setToolTip("The API root, usually ending in /v1. LM Studio: "
                                         "http://localhost:1234/v1, llama.cpp: http://localhost:8080/v1")
        ai_layout.addRow("OpenAI-compatible URL:", self.openai_url_input)

        self.openai_model_input = QLineEdit()
        self.openai_model_input.setText(openai.get('model') or "")
        self.openai_model_input.setPlaceholderText("as the server names it -- Test connection lists them")
        ai_layout.addRow("OpenAI-compatible Model:", self.openai_model_input)

        # The name of the variable, never the key: config.yaml is plain text.
        self.openai_key_env_input = QLineEdit()
        self.openai_key_env_input.setText(openai.get('api_key_env') or "OPENAI_API_KEY")
        self.openai_key_env_input.setToolTip("The NAME of the environment variable that holds the key "
                                             "(OpenRouter: OPENROUTER_API_KEY), not the key itself. "
                                             "A local server needs none: leave the variable unset.")
        ai_layout.addRow("OpenAI-compatible Key Variable:", self.openai_key_env_input)

        # Asks the Models API (Claude), the tag list (Ollama) or the model
        # list (OpenAI-compatible) about the model typed above. Nothing is run
        # and nothing is billed.
        self.btn_test_ai = QPushButton("Test connection")
        self.btn_test_ai.setToolTip("Check the selected AI mode: is there a working key (Claude) or a "
                                    "running server (Ollama, OpenAI-compatible), and does it offer "
                                    "the model typed above?")
        self.btn_test_ai.clicked.connect(self._test_ai)
        ai_layout.addRow("", self.btn_test_ai)

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

    def _test_ai(self):
        """Tests the AI mode and model as they stand in the dialog, saved or not."""
        client = AIClient(self.cfg)
        mode = self.ai_mode_combo.currentText()
        if mode == "claude":
            check, args = client.check_claude, (self.claude_model_input.text().strip(),)
        elif mode == "openai":
            check, args = client.check_openai, (self.openai_model_input.text().strip(),
                                                self.openai_url_input.text().strip(),
                                                self.openai_key_env_input.text().strip())
        else:
            check, args = client.check_ollama, (self.ollama_model_input.text().strip(),)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            ok, message = check(*args)
        finally:
            QApplication.restoreOverrideCursor()
        # The messages are HTML, like the rest of the chat.
        box = QMessageBox.information if ok else QMessageBox.warning
        box(self, "Test connection", message.replace("<br>", "\n"))

    def _browse_camotics(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select CAMotics EXE", "", "Executable (*.exe);;All Files (*)"
        )
        if path:
            self.camotics_path_input.setText(path)

    def save_settings(self):
        key_env = self.openai_key_env_input.text().strip() or "OPENAI_API_KEY"
        if not is_variable_name(key_env):
            # Most likely the key itself; it would be written to config.yaml.
            QMessageBox.warning(self, "Not saved", NOT_A_VARIABLE_NAME.replace("❌ ", ""))
            return
        self.cfg.data['machine']['chuck_z_limit'] = self.chuck_z_input.value()
        diameter = self.chuck_diameter_input.value()
        self.cfg.data['machine']['chuck_diameter'] = diameter if diameter > 0 else None
        ai = self.cfg.data.setdefault('ai', {})
        ai['mode'] = self.ai_mode_combo.currentText()
        ai['offline'] = self.ai_offline_check.isChecked()
        # A config from before a backend existed has no section for it.
        ai.setdefault('anthropic', {})['model'] = self.claude_model_input.text().strip()
        ai.setdefault('ollama', {})['model'] = self.ollama_model_input.text().strip()
        openai = ai.setdefault('openai', {})
        openai['base_url'] = self.openai_url_input.text().strip() or DEFAULT_OPENAI_BASE_URL
        openai['model'] = self.openai_model_input.text().strip()
        openai['api_key_env'] = key_env
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
