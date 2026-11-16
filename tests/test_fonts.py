import unittest

from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import QApplication

from vibe_cnc import fonts


class MonoFontTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_only_installed_families_are_asked_for(self):
        installed = set(QFontDatabase.families())

        for name in fonts.mono_families():
            self.assertIn(name, installed)

    def test_the_order_of_preference_is_kept(self):
        found = fonts.mono_families()

        self.assertEqual(list(found),
                         [name for name in fonts.CANDIDATES if name in found])

    def test_the_css_list_names_the_same_families_and_ends_generic(self):
        css = fonts.mono_css()

        self.assertTrue(css.endswith("monospace"))
        for name in fonts.mono_families():
            self.assertIn(name, css)

    def test_a_family_with_a_space_is_quoted_in_css(self):
        # 'SF Mono' unquoted would be read as two words.
        for name in fonts.mono_families():
            if " " in name:
                self.assertIn(f"'{name}'", fonts.mono_css())

    def test_a_machine_with_none_of_them_still_gets_a_usable_css_value(self):
        original = fonts.mono_families
        fonts.mono_families = lambda: ()
        try:
            self.assertEqual(fonts.mono_css(), "monospace")
        finally:
            fonts.mono_families = original


if __name__ == "__main__":
    unittest.main()
