"""The operator panel of the main window: machine keys on top, softkeys below.

vibe_cnc.py is a script next to a package of the same name, so it is loaded by
path. The window's QSettings are pointed at a temporary directory first: the
window restores the operator's own settings on start, and a test must neither
read nor overwrite them.
"""
import importlib.util
import os
import re
import tempfile
import unittest
from unittest import mock

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from vibe_cnc import ai_client

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
        # No test here may reach a server. The window reads the operator's own
        # config.yaml, whose backend may be a paid one; a test that stubbed the
        # wrong method once sent a request to it.
        def offline(*_args, **_kwargs):
            raise AssertionError("a test reached for the network")
        cls.offline = [mock.patch.object(ai_client.requests, "post", side_effect=offline),
                       mock.patch.object(ai_client.requests, "get", side_effect=offline),
                       mock.patch.object(ai_client, "_anthropic", side_effect=offline)]
        for patch in cls.offline:
            patch.start()

    @classmethod
    def tearDownClass(cls):
        for patch in cls.offline:
            patch.stop()
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
        # The box is on the ASSIST tab under the simulation; typing into it
        # means that tab is open.
        win.dock.show_page(win.ASSIST_PAGE)
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

    def test_enter_leaves_the_text_in_the_box(self):
        # The text is for AI GENERATE. Checking the program must not eat it
        # or show it in the chat as if it had been sent.
        win = self.assist()
        win.input.setText("Write G71 for T2, depth 5.0")

        QTest.keyClick(win.input, Qt.Key.Key_Return)

        self.assertEqual(win.input.text(), "Write G71 for T2, depth 5.0")
        self.assertNotIn("Write G71", win.chat.toPlainText())
        self.assertIn("Lint", win.chat.toPlainText())

    def test_the_analyze_button_leaves_the_text_in_the_box_too(self):
        win = self.assist()
        win.input.setText("Write G71 for T2, depth 5.0")

        win.btnAna.click()

        self.assertEqual(win.input.text(), "Write G71 for T2, depth 5.0")
        self.assertIn("Lint", win.chat.toPlainText())

    def test_generating_still_takes_the_text_and_shows_it(self):
        win = self.assist()
        win.input.setText("Write G71 for T2, depth 5.0")

        QTest.keyClick(win.input, Qt.Key.Key_Return, self.CTRL)

        self.assertEqual(win.input.text(), "")
        self.assertIn("Write G71 for T2, depth 5.0", win.chat.toPlainText())
        self.assertIn("G00 X36", win.chat.toPlainText())

    def test_ctrl_enter_does_nothing_from_the_editor(self):
        # From there it would run a prompt the user never looked at.
        win = self.assist()
        win.editor.setFocus()
        QTest.qWait(50)

        QTest.keyClick(win.editor, Qt.Key.Key_Return, self.CTRL)

        self.assertNotIn("G00 X36", win.chat.toPlainText())

    # --- what is sent along: a review and a generation are told different things ---

    def wait_for_chat(self, win, text, seconds=5.0):
        """The AI call runs on a worker thread; its answer arrives as a queued signal."""
        for _ in range(int(seconds * 20)):
            if text in win.chat.toPlainText():
                return True
            QTest.qWait(50)
        return False

    def test_a_review_is_sent_with_the_review_instructions(self):
        win = self.window()
        win.cfg.data["ai"].update({"offline": False, "mode": "ollama"})
        calls = []
        win.ai.chat_ollama = lambda messages, system=None: calls.append((messages, system)) or (True, "REVIEW-REPLY")

        win.action_analyze()

        self.assertTrue(self.wait_for_chat(win, "REVIEW-REPLY"))
        self.assertEqual(calls[0][1], win.ai.review_prompt())

    def test_a_review_in_anthropic_mode_goes_to_claude(self):
        # The window chose the backend itself and sent every mode but
        # "claude" to Ollama -- "anthropic", which the template offered, too.
        win = self.window()
        win.cfg.data["ai"].update({"offline": False, "mode": "anthropic"})
        calls = []
        win.ai.chat_claude = lambda messages, system=None: calls.append("claude") or (True, "CLAUDE-REPLY")
        win.ai.chat_ollama = lambda messages, system=None: calls.append("ollama") or (True, "OLLAMA-REPLY")

        win.action_analyze()

        self.assertTrue(self.wait_for_chat(win, "CLAUDE-REPLY"))
        self.assertEqual(calls, ["claude"])

    def test_a_review_in_openai_mode_goes_to_the_openai_compatible_server(self):
        win = self.window()
        win.cfg.data["ai"].update({"offline": False, "mode": "openai"})
        calls = []
        win.ai.chat_openai = lambda messages, system=None: calls.append("openai") or (True, "OPENAI-REPLY")
        win.ai.chat_claude = lambda messages, system=None: calls.append("claude") or (True, "CLAUDE-REPLY")
        win.ai.chat_ollama = lambda messages, system=None: calls.append("ollama") or (True, "OLLAMA-REPLY")

        win.action_analyze()

        self.assertTrue(self.wait_for_chat(win, "OPENAI-REPLY"))
        self.assertEqual(calls, ["openai"])

    def test_a_generation_is_sent_with_the_generation_instructions_not_the_review_ones(self):
        # It used to go out with the review prompt, so asking for G-code was
        # answered with a list of rule violations.
        win = self.window()
        win.cfg.data["ai"].update({"offline": False, "mode": "ollama"})
        win.input.setText("Write a G71 for T1")
        calls = []
        win.ai.chat = lambda messages, system=None: calls.append((messages, system)) or (True, "GENERATE-REPLY")

        win.action_generate()

        self.assertTrue(self.wait_for_chat(win, "GENERATE-REPLY"))
        messages, system = calls[0]
        self.assertIn("Write a G71 for T1", messages[0]["content"])
        self.assertEqual(system, win.ai.generate_prompt())
        self.assertNotEqual(system, win.ai.review_prompt())

    def test_the_example_in_a_generation_teaches_no_d_word_in_a_cycle(self):
        # It showed "G71 P10 Q20 U0.4 W0.1 D500 F0.25" and called D500 the
        # spindle speed: every generation was taught a word the 0i-TF's
        # two-block G71 does not take, with a wrong meaning.
        win = self.window()
        win.cfg.data["ai"].update({"offline": False, "mode": "ollama"})
        win.input.setText("Write a G71 for T1")
        calls = []
        win.ai.chat = (lambda messages, system=None:
                       calls.append(messages[0]["content"]) or (True, "GENERATE-REPLY"))

        win.action_generate()

        self.assertTrue(self.wait_for_chat(win, "GENERATE-REPLY"))
        cycle_lines = [ln for ln in calls[0].splitlines() if re.match(r"\s*G7[0-6]\b", ln)]
        self.assertTrue(cycle_lines)
        for line in cycle_lines:
            self.assertNotRegex(line, r"\bD\d", line)
        self.assertNotIn("RPM", calls[0])

    # --- the verifier loop, as the chat shows it ---

    G76_POINT = "G00 X24. Z5.\nG76 P020060 Q0.4 R0.05\nG76 X16.93 Z-30. P1534 Q400 F2.5"
    G76_RIGHT = "G00 X24. Z5.\nG76 P020060 Q100 R0.05\nG76 X16.93 Z-30. P1534 Q400 F2.5"

    PIECE_CLEAN = "Lint clean as a piece of a program"

    def generate(self, *answers, snippet=True):
        """Asks for a thread, the model answering from a script; returns the
        window and the conversations it was sent. A piece of a program, as
        the switch in the assist box says, unless snippet=False."""
        win = self.window()
        win.cfg.data["ai"].update({"offline": False, "mode": "ollama"})
        win.input.setText("Cut an M20x2.5 thread")
        win.chkSnippet.setChecked(snippet)
        script, sent = list(answers), []
        win.ai.chat = lambda messages, system=None: sent.append(messages) or script.pop(0)
        win.action_generate()
        return win, sent

    def test_a_clean_answer_is_shown_with_its_code_and_said_to_be_lint_clean(self):
        win, sent = self.generate((True, f"```\n{self.G76_RIGHT}\n```\nM20x2.5, **30 long**."))

        self.assertTrue(self.wait_for_chat(win, self.PIECE_CLEAN))
        self.assertIn("program end not checked", win.chat.toPlainText())
        shown = win.chat.toPlainText()
        self.assertIn("G76 P020060 Q100 R0.05", shown)
        self.assertNotIn("```", shown)
        self.assertIn("30 long", shown)
        self.assertNotIn("**", shown)
        self.assertEqual(len(sent), 1)

    def test_nothing_is_put_into_the_editor(self):
        win, _sent = self.generate((True, f"```\n{self.G76_RIGHT}\n```"))

        self.assertTrue(self.wait_for_chat(win, "Lint clean"))
        self.assertEqual(win.editor.toPlainText(), PROGRAM)

    def test_a_corrected_draft_shows_the_change_from_the_draft(self):
        win, sent = self.generate((True, f"```\n{self.G76_POINT}\n```"),
                                  (True, f"```\n{self.G76_RIGHT}\n```\nQ written as Q100."))

        self.assertTrue(self.wait_for_chat(win, self.PIECE_CLEAN))
        shown = win.chat.toPlainText()
        self.assertIn("1 correction(s) asked for", shown)
        self.assertIn("-G76 P020060 Q0.4 R0.05", shown)
        self.assertIn("+G76 P020060 Q100 R0.05", shown)
        self.assertEqual(len(sent), 2)

    def test_findings_that_remain_are_counted_and_listed(self):
        repeat = (True, f"```\n{self.G76_POINT}\n```")
        win, _sent = self.generate(repeat, repeat)

        self.assertTrue(self.wait_for_chat(win, "1 finding remains"))
        shown = win.chat.toPlainText()
        self.assertIn("Line 2: ERROR", shown)
        self.assertIn("Q0.4", shown)
        self.assertIn("Fix: Write Q400.", shown)
        self.assertIn("came back with the findings of an earlier one", shown)

    def test_a_whole_program_is_asked_for_unless_the_switch_says_a_piece(self):
        # The switch is off at the start. The same thread is then judged as a
        # program: no header, no spindle, no M30 -- not clean.
        self.assertFalse(self.window().chkSnippet.isChecked())
        repeat = (True, f"```\n{self.G76_RIGHT}\n```")
        win, _sent = self.generate(repeat, repeat, snippet=False)

        self.assertTrue(self.wait_for_chat(win, "findings remain"))
        shown = win.chat.toPlainText()
        self.assertIn("No program end", shown)
        self.assertNotIn("Lint clean", shown)

    def test_an_answer_without_a_code_block_is_shown_and_not_checked(self):
        win, _sent = self.generate((True, "G76 P020060 Q100 R0.05 should do it."))

        self.assertTrue(self.wait_for_chat(win, "not checked: the answer has no code block"))
        self.assertIn("should do it.", win.chat.toPlainText())

    def test_a_backend_error_is_shown_as_one(self):
        win, _sent = self.generate((False, "❌ No credit left"))

        self.assertTrue(self.wait_for_chat(win, "❌ No credit left"))
        self.assertNotIn("Lint clean", win.chat.toPlainText())

    def test_the_tooltips_name_the_key_as_this_platform_writes_it(self):
        win = self.window()
        key = QKeySequence("Ctrl+Return").toString(QKeySequence.SequenceFormat.NativeText)

        self.assertIn(key, win.input.toolTip())
        self.assertIn(key, win.btnGen.toolTip())
        self.assertIn("Enter", win.input.toolTip())



class LayoutTests(PanelCase):
    """The program and the simulation side by side, the rest folding away below."""

    def test_the_program_and_the_simulation_stand_side_by_side(self):
        win = self.window()

        self.assertIs(win.split.widget(0), win.title_center)
        self.assertIs(win.split.widget(1), win.vsplit)
        self.assertIs(win.vsplit.widget(1), win.dock)
        self.assertTrue(win.vsplit.widget(0).isAncestorOf(win.plotter))

    def test_tools_macros_and_the_assist_share_the_panel_below(self):
        win = self.window()

        self.assertEqual([tab.text() for tab in win.dock.tabs], ["TOOLS", "MACROS", "ASSIST"])
        self.assertTrue(win.dock.isAncestorOf(win.table))
        self.assertTrue(win.dock.isAncestorOf(win.macroTable))
        self.assertTrue(win.dock.isAncestorOf(win.chat))

    def test_an_answer_opens_the_assist(self):
        win = self.window()
        win.dock.set_folded(True)

        win._append_assistant_message("Lint", "No issues found")

        self.assertFalse(win.dock.is_folded())
        self.assertEqual(win.dock.current_index(), win.ASSIST_PAGE)

    def test_a_message_from_the_simulation_leaves_it_folded(self):
        win = self.window()
        win.dock.set_folded(True)

        win._append_system_message("Simulation started")

        self.assertTrue(win.dock.is_folded())

    def test_ctrl_j_folds_the_panel(self):
        win = self.window()

        (action,) = [a for a in win.actions() if a.shortcut() == QKeySequence("Ctrl+J")]
        action.trigger()
        self.assertTrue(win.dock.is_folded())
        action.trigger()
        self.assertFalse(win.dock.is_folded())

    def test_the_panel_comes_back_as_it_was_left(self):
        win = self.window()
        win.dock.show_page(1)
        win.dock.set_folded(True)
        win._save_state()

        again = self.window()

        self.assertTrue(again.dock.is_folded())
        self.assertEqual(again.dock.current_index(), 1)


if __name__ == "__main__":
    unittest.main()
