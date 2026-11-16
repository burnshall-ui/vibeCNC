"""The operator panel of the main window: machine keys on top, softkeys below.

vibe_cnc.py is a script next to a package of the same name, so it is loaded by
path. The window's QSettings are pointed at a temporary directory first: the
window restores the operator's own settings on start, and a test must neither
read nor overwrite them.
"""
import importlib.util
import os
import tempfile
import unittest

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROGRAM = "G00 X10. Z2.\nG01 Z-5. F0.2\nG00 X50.\nM30"


def load_main_module():
    spec = importlib.util.spec_from_file_location(
        "vibe_cnc_main", os.path.join(ROOT, "vibe_cnc.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PanelCase(unittest.TestCase):
    """What every test here needs: a window, a way to run it, its log."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        # An INI file can be redirected; the native store (a plist on macOS) cannot.
        cls.settings_dir = tempfile.TemporaryDirectory()
        cls.previous_format = QSettings.defaultFormat()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                          cls.settings_dir.name)
        cls.module = load_main_module()

    @classmethod
    def tearDownClass(cls):
        QSettings.setDefaultFormat(cls.previous_format)
        cls.settings_dir.cleanup()

    def window(self):
        win = self.module.Main()
        self.addCleanup(win.deleteLater)
        win.resize(1100, 830)
        win.editor.setPlainText(PROGRAM)
        win.show()
        QTest.qWait(50)
        win.plotter.set_gcode(PROGRAM)
        return win

    @staticmethod
    def start(win):
        """Starts the simulation but ticks it by hand, not on the timer."""
        win._sim_start()
        win.sim_timer.stop()

    def log(self, win):
        return [line.strip() for line in win.chat.toPlainText().split("\n") if line.strip()]



class OperatorPanelTests(PanelCase):
    # --- run state ---

    def test_the_display_starts_at_stop(self):
        self.assertEqual(self.window().lblRunState.text(), "● STOP")

    def test_it_shows_run_and_the_line_while_the_program_runs(self):
        win = self.window()
        self.start(win)

        win._sim_step()

        self.assertEqual(win.lblRunState.text(), "● RUN  line 1")

    def test_feed_hold_shows_hold_and_keeps_the_line(self):
        win = self.window()
        self.start(win)
        win._sim_step()
        win._sim_step()

        win.action_feed_hold()

        self.assertEqual(win.lblRunState.text(), "● HOLD  line 2")

    def test_cycle_start_after_a_hold_runs_again(self):
        win = self.window()
        self.start(win)
        win._sim_step()
        win.action_feed_hold()

        win._sim_start()
        win.sim_timer.stop()

        self.assertEqual(win.lblRunState.text(), "● RUN  line 1")

    # --- reset ---

    def test_reset_stops_the_program_and_clears_the_display(self):
        win = self.window()
        self.start(win)
        win._sim_step()

        win.btnReset.click()

        self.assertEqual(win.sim_state, "STOPPED")
        self.assertEqual(win.lblRunState.text(), "● STOP")

    def test_reset_is_logged(self):
        win = self.window()
        self.start(win)

        win.action_reset()

        self.assertIn("CNC: RESET", self.log(win))

    def test_reset_with_nothing_running_does_nothing_and_logs_nothing(self):
        win = self.window()

        win.action_reset()

        self.assertNotIn("CNC: RESET", self.log(win))

    def test_the_program_running_to_its_end_returns_to_stop(self):
        win = self.window()
        self.start(win)

        for _ in range(60):
            win._sim_step()

        self.assertEqual(win.lblRunState.text(), "● STOP")

    # --- the two toggles show their state as a lamp ---

    def test_the_toggles_show_a_lit_lamp_when_on(self):
        win = self.window()
        self.assertEqual(win.btnOptStop.text(), "○ OPT STOP")
        self.assertEqual(win.btnSingleBlock.text(), "○ SINGLE BLOCK")

        win.btnOptStop.setChecked(True)
        win.btnSingleBlock.setChecked(True)

        self.assertEqual(win.btnOptStop.text(), "● OPT STOP")
        self.assertEqual(win.btnSingleBlock.text(), "● SINGLE BLOCK")

        win.btnOptStop.setChecked(False)
        self.assertEqual(win.btnOptStop.text(), "○ OPT STOP")

    # --- layout ---

    def test_the_two_rows_share_their_columns(self):
        win = self.window()
        machine = [win.btnCycleStart, win.btnFeedHold, win.btnReset,
                   win.btnOptStop, win.btnSingleBlock, win.lblRunState]
        program = [win.btnOpen, win.btnSave, win.btnSim,
                   win.btnAna, win.btnGen, win.btnSettings]

        for above, below in zip(machine, program):
            self.assertEqual(above.geometry().x(), below.geometry().x())
            self.assertEqual(above.geometry().width(), below.geometry().width())

    def test_the_machine_row_sits_above_the_program_row(self):
        win = self.window()

        self.assertLess(win.btnCycleStart.geometry().y(), win.btnOpen.geometry().y())

    def test_every_key_is_the_same_height(self):
        win = self.window()
        heights = {w.geometry().height() for w in win._panel_buttons}

        self.assertEqual(len(heights), 1, heights)

    # --- the labels ---

    def test_no_key_carries_an_emoji(self):
        # Emoji start at U+2600. The lamps and the menu arrow sit below it.
        win = self.window()

        for button in win._panel_buttons:
            for char in button.text():
                self.assertLess(ord(char), 0x2600, f"{button.text()!r} has {char!r}")

    def test_the_open_menu_has_no_emoji_either(self):
        win = self.window()

        for action in win.recent_files_menu.actions():
            for char in action.text():
                self.assertLess(ord(char), 0x2600, action.text())

    def test_every_key_has_a_tooltip(self):
        win = self.window()

        for widget in win._panel_buttons + [win.lblRunState]:
            self.assertTrue(widget.toolTip(), widget.text())

    def test_the_key_that_sends_to_camotics_says_so(self):
        win = self.window()

        self.assertIn("CAMOTICS", win.btnSim.text())


class AssistBoxKeyTests(PanelCase):
    """Enter checks the program; generating code takes Cmd/Ctrl+Enter.

    Generating calls the AI on whatever is typed, so it must be deliberate.
    """
    CTRL = Qt.KeyboardModifier.ControlModifier  # Cmd on a Mac

    def assist(self):
        win = self.window()
        # Offline, so generating answers with a stub and nothing reaches a
        # backend. Only this window's copy of the settings is touched.
        win.cfg.data['ai']['offline'] = True
        win.activateWindow()
        win.input.setFocus()
        QTest.qWait(50)
        return win

    def test_ctrl_enter_in_the_assist_box_generates(self):
        win = self.assist()

        QTest.keyClick(win.input, Qt.Key.Key_Return, self.CTRL)

        text = win.chat.toPlainText()
        self.assertIn("G00 X36", text)            # the offline generator's stub
        self.assertNotIn("Lint", text)            # and the program was not checked

    def test_the_keypad_enter_generates_too(self):
        win = self.assist()

        QTest.keyClick(win.input, Qt.Key.Key_Enter, self.CTRL)

        self.assertIn("G00 X36", win.chat.toPlainText())

    def test_plain_enter_still_checks_the_program_and_does_not_generate(self):
        win = self.assist()

        QTest.keyClick(win.input, Qt.Key.Key_Return)

        text = win.chat.toPlainText()
        self.assertIn("Lint", text)
        self.assertNotIn("G00 X36", text)

    def test_ctrl_enter_does_nothing_from_the_editor(self):
        # From there it would run a prompt the user never looked at.
        win = self.assist()
        win.editor.setFocus()
        QTest.qWait(50)

        QTest.keyClick(win.editor, Qt.Key.Key_Return, self.CTRL)

        self.assertNotIn("G00 X36", win.chat.toPlainText())

    def test_the_tooltips_name_the_key_as_this_platform_writes_it(self):
        win = self.window()
        key = QKeySequence("Ctrl+Return").toString(QKeySequence.SequenceFormat.NativeText)

        self.assertIn(key, win.input.toolTip())
        self.assertIn(key, win.btnGen.toolTip())
        self.assertIn("Enter", win.input.toolTip())


if __name__ == "__main__":
    unittest.main()
