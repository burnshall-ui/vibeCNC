"""Autocomplete reads its G- and M-codes from specs/fanuc_0i_tf/dialect.toml.

Needs PyQt6, so it runs in the dependency-installing CI job.
"""
import unittest

from PyQt6.QtWidgets import QApplication

from vibe_cnc.gcode_completer import GCodeCompleter
from vibe_cnc.specs import load_specs


class CompleterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def suggest(self, text):
        return dict(zip(*GCodeCompleter().get_context_suggestions(text)))

    def test_g9_offers_the_cycles_of_system_a(self):
        offered = self.suggest("G9")

        self.assertIn("cycle", offered["G90"])
        self.assertIn("cycle", offered["G92"])
        self.assertIn("cycle", offered["G94"])
        # Absolute/incremental and the feed modes of another G-code system.
        self.assertNotIn("G91", offered)
        self.assertNotIn("G95", offered)
        self.assertEqual(offered["G99"], "Feed per revolution")

    def test_the_texts_are_the_dialect_files(self):
        dialect = load_specs().dialect
        self.assertEqual(self.suggest("G7")["G76"], dialect.g_codes["G76"])
        self.assertEqual(self.suggest("M3")["M30"], dialect.m_codes["M30"])

    def test_a_machine_builders_m_code_says_so(self):
        offered = self.suggest("M0")

        self.assertIn("machine-specific", offered["M06"])
        self.assertNotIn("machine-specific", offered["M03"])

    def test_every_code_the_completer_names_itself_is_in_the_dialect(self):
        # The start list and the fallback name codes by hand; a code missing
        # from the file would fail with a KeyError, or show without a text.
        for text in ("", "?"):
            with self.subTest(text=text):
                for code, description in self.suggest(text).items():
                    if code[:1] in "GM" and code[1:].isdigit():
                        self.assertTrue(description, code)


if __name__ == "__main__":
    unittest.main()
