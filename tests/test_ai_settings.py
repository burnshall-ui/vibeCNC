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

from vibe_cnc.claude_client import AIClient
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


if __name__ == "__main__":
    unittest.main()
