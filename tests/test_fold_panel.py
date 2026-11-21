"""The tabbed panel under the simulation, which folds down to its tabs."""
import unittest

from PyQt6.QtWidgets import QApplication, QLabel

from vibe_cnc.fold_panel import FOLD, NO_MAXIMUM, UNFOLD, FoldPanel


class FoldPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def panel(self):
        panel = FoldPanel()
        self.addCleanup(panel.deleteLater)
        for title in ("TOOLS", "MACROS", "ASSIST"):
            panel.add_page(title, QLabel(title))
        panel.show_page(0)
        return panel

    @staticmethod
    def checked(panel):
        return [button.text() for button in panel.tabs if button.isChecked()]

    def test_a_click_on_another_tab_opens_its_page(self):
        panel = self.panel()

        panel.tabs[2].click()

        self.assertEqual(panel.current_index(), 2)
        self.assertFalse(panel.is_folded())
        self.assertEqual(self.checked(panel), ["ASSIST"])

    def test_a_click_on_the_open_tab_folds_the_panel(self):
        panel = self.panel()

        panel.tabs[0].click()

        self.assertTrue(panel.is_folded())
        self.assertTrue(panel.stack.isHidden())
        self.assertEqual(self.checked(panel), [])
        self.assertEqual(panel.maximumHeight(), panel.bar.sizeHint().height())

    def test_a_click_on_a_tab_of_the_folded_panel_opens_it_again(self):
        panel = self.panel()
        panel.tabs[0].click()

        panel.tabs[0].click()

        self.assertFalse(panel.is_folded())
        self.assertFalse(panel.stack.isHidden())
        self.assertEqual(panel.maximumHeight(), NO_MAXIMUM)

    def test_showing_a_page_opens_a_folded_panel_on_it(self):
        panel = self.panel()
        panel.set_folded(True)

        panel.show_page(1)

        self.assertFalse(panel.is_folded())
        self.assertEqual(panel.current_index(), 1)

    def test_the_fold_button_says_which_way_it_goes(self):
        panel = self.panel()
        self.assertEqual(panel.toggle.text(), FOLD)

        panel.toggle.click()
        self.assertTrue(panel.is_folded())
        self.assertEqual(panel.toggle.text(), UNFOLD)

        panel.toggle.click()
        self.assertFalse(panel.is_folded())

    def test_it_tells_when_it_folds_and_opens_and_not_otherwise(self):
        panel = self.panel()
        seen = []
        panel.folded_changed.connect(seen.append)

        panel.set_folded(True)
        panel.set_folded(True)
        panel.show_page(2)
        panel.show_page(1)

        self.assertEqual(seen, [True, False])


if __name__ == "__main__":
    unittest.main()
