"""The AI tab of the settings dialog: "Test connection".

It checks the model as typed in the dialog, saved or not, and says the result
in a box. The checks themselves are tested in test_ai_client; here they are
replaced, so nothing reaches a network.
"""
import os
import unittest
from types import SimpleNamespace
from unittest import mock

import yaml
from PyQt6.QtWidgets import QApplication, QMessageBox

from vibe_cnc.claude_client import AIClient, BACKENDS
from vibe_cnc.dialogs import settings as settings_module
from vibe_cnc.dialogs.settings import SettingsDialog

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestConnectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def dialog(self, mode):
        with open(os.path.join(ROOT, "config.example.yaml"), encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        data["ai"]["mode"] = mode
        dialog = SettingsDialog(SimpleNamespace(data=data))
        self.addCleanup(dialog.deleteLater)
        return dialog

    def press(self, dialog, **checks):
        with mock.patch.multiple(AIClient, **checks), \
                mock.patch.object(QMessageBox, "information") as info, \
                mock.patch.object(QMessageBox, "warning") as warning:
            dialog.btn_test_ai.click()
        return info, warning

    def test_claude_is_tested_with_the_model_typed_in_the_dialog(self):
        dialog = self.dialog("claude")
        dialog.claude_model_input.setText("claude-haiku-5-5")
        check_claude = mock.Mock(return_value=(True, "✅ Claude API reachable."))

        info, warning = self.press(dialog, check_claude=check_claude)

        check_claude.assert_called_once_with("claude-haiku-5-5")
        info.assert_called_once()
        warning.assert_not_called()

    def test_ollama_is_tested_with_its_own_model_when_that_is_the_mode(self):
        dialog = self.dialog("ollama")
        dialog.ollama_model_input.setText("qwen3.5:4b")
        check_ollama = mock.Mock(return_value=(True, "✅ Ollama reachable."))
        check_claude = mock.Mock()

        self.press(dialog, check_ollama=check_ollama, check_claude=check_claude)

        check_ollama.assert_called_once_with("qwen3.5:4b")
        check_claude.assert_not_called()

    def test_a_failure_is_shown_as_a_warning_with_what_went_wrong(self):
        dialog = self.dialog("claude")
        failed = mock.Mock(return_value=(False, "❌ Claude API: model 'x' was not found (404)."))

        info, warning = self.press(dialog, check_claude=failed)

        warning.assert_called_once()
        self.assertIn("was not found", warning.call_args.args[2])
        info.assert_not_called()

    def test_the_busy_cursor_does_not_stay_on(self):
        dialog = self.dialog("claude")

        self.press(dialog, check_claude=mock.Mock(return_value=(True, "ok")))

        self.assertIsNone(QApplication.overrideCursor())

    def test_the_dialog_shows_the_backend_the_app_will_ask(self):
        for mode, shown in (("anthropic", "claude"), ("Ollama", "ollama"), ("ollama", "ollama")):
            with self.subTest(mode=mode):
                self.assertEqual(self.dialog(mode).ai_mode_combo.currentText(), shown)

    def test_a_config_without_a_mode_is_shown_as_claude(self):
        # The client takes a missing mode for Claude; the dialog took it for
        # Ollama, and saving then switched the app to Ollama.
        dialog = self.dialog("claude")
        del dialog.cfg.data["ai"]["mode"]
        fresh = SettingsDialog(dialog.cfg)
        self.addCleanup(fresh.deleteLater)

        self.assertEqual(fresh.ai_mode_combo.currentText(), "claude")

    def test_anthropic_is_tested_as_claude(self):
        dialog = self.dialog("anthropic")
        check_claude = mock.Mock(return_value=(True, "ok"))
        check_ollama = mock.Mock()

        self.press(dialog, check_claude=check_claude, check_ollama=check_ollama)

        check_claude.assert_called_once()
        check_ollama.assert_not_called()

    def test_the_dialog_offers_the_current_models_when_the_config_names_none(self):
        data = {"machine": {"chuck_z_limit": -5.0, "chuck_diameter": None},
                "ai": {"mode": "claude", "offline": False, "anthropic": {}, "ollama": {}},
                "paths": {}, "ui": {}}
        dialog = SettingsDialog(SimpleNamespace(data=data))
        self.addCleanup(dialog.deleteLater)

        self.assertEqual(dialog.claude_model_input.text(), "claude-sonnet-5-5")
        self.assertEqual(dialog.ollama_model_input.text(), "qwen3.8:27b")
        # No section at all: a config from before the backend existed.
        self.assertEqual(dialog.openai_url_input.text(), "https://api.openai.com/v1")
        self.assertEqual(dialog.openai_model_input.text(), "")

    def test_every_backend_can_be_chosen(self):
        combo = self.dialog("claude").ai_mode_combo

        self.assertEqual([combo.itemText(i) for i in range(combo.count())], list(BACKENDS))

    def test_openai_is_tested_with_the_model_and_the_address_typed_in_the_dialog(self):
        dialog = self.dialog("openai")
        dialog.openai_url_input.setText(" http://localhost:1234/v1 ")
        dialog.openai_model_input.setText("qwen/qwen3-4b")
        check_openai = mock.Mock(return_value=(True, "✅ localhost:1234 answers."))
        check_claude, check_ollama = mock.Mock(), mock.Mock()

        info, _warning = self.press(dialog, check_openai=check_openai, check_claude=check_claude,
                                    check_ollama=check_ollama)

        check_openai.assert_called_once_with("qwen/qwen3-4b", "http://localhost:1234/v1")
        check_claude.assert_not_called()
        check_ollama.assert_not_called()
        info.assert_called_once()

    def save(self, dialog):
        """Saves without touching the real config.yaml; returns what would be written."""
        with mock.patch.object(settings_module, "open", mock.mock_open(), create=True), \
                mock.patch.object(settings_module.yaml, "dump") as dump, \
                mock.patch.object(QMessageBox, "information"), \
                mock.patch.object(QMessageBox, "critical") as critical:
            dialog.save_settings()
        critical.assert_not_called()
        return dump.call_args.args[0]

    def test_saving_openai_writes_its_section_into_a_config_that_had_none(self):
        dialog = self.dialog("claude")
        del dialog.cfg.data["ai"]["openai"]
        fresh = SettingsDialog(dialog.cfg)
        self.addCleanup(fresh.deleteLater)
        fresh.ai_mode_combo.setCurrentText("openai")
        fresh.openai_url_input.setText("http://localhost:1234/v1")
        fresh.openai_model_input.setText(" qwen/qwen3-4b ")

        written = self.save(fresh)

        self.assertEqual(written["ai"]["mode"], "openai")
        self.assertEqual(written["ai"]["openai"]["base_url"], "http://localhost:1234/v1")
        self.assertEqual(written["ai"]["openai"]["model"], "qwen/qwen3-4b")

    def test_an_emptied_address_is_saved_as_openai_s(self):
        dialog = self.dialog("openai")
        dialog.openai_url_input.setText("  ")

        written = self.save(dialog)

        self.assertEqual(written["ai"]["openai"]["base_url"], "https://api.openai.com/v1")


if __name__ == "__main__":
    unittest.main()
