"""The editor's auto-uppercase.

Code is capitals, a comment is not, and the replacement must not cost the user
their undo history or their cursor -- the first version swapped in the whole
text with setPlainText and did all three wrong.

Typing is capitalised before it reaches the document. Changing the document
from inside textChanged instead leaves Qt's undo unable to undo anything, so
whatever else needs fixing -- a loaded file, a paste -- is fixed just after it.
"""
import unittest

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QTextCursor
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from vibe_cnc.gcode_highlighter import GCodeEditor

COLORS = {'CRT_GREEN': '#6CFF6C', 'CRT_GREEN_DIM': '#4ACF4A', 'FANUC_YELLOW': '#FFC800',
          'CYAN': '#56D6FF', 'BG_DARK': '#1A1A1A', 'WHITE': '#EDEDED'}


class AutoUppercaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def editor(self):
        widget = GCodeEditor(COLORS)
        self.addCleanup(widget.deleteLater)
        return widget

    def test_typed_code_becomes_upper_case(self):
        widget = self.editor()

        QTest.keyClicks(widget, "g01 x10. z-5.")

        self.assertEqual(widget.toPlainText(), "G01 X10. Z-5.")

    def test_a_comment_keeps_the_case_it_was_typed_in(self):
        widget = self.editor()

        QTest.keyClicks(widget, "g0 (rapid, 10 mm) x1")

        self.assertEqual(widget.toPlainText(), "G0 (rapid, 10 mm) X1")

    def test_the_cursor_stays_where_the_typing_is(self):
        widget = self.editor()

        QTest.keyClicks(widget, "g01 x10.")

        self.assertEqual(widget.textCursor().position(), len("G01 X10."))

    def test_a_loaded_program_is_upper_cased_except_for_its_comments(self):
        widget = self.editor()

        widget.setPlainText("(Material: C45, 40 mm)\ng00 x10. z2.\ng01 z-5. f0.2 ; feed in")
        QTest.qWait(20)  # the fix runs just after the signal, not inside it

        self.assertEqual(widget.toPlainText(),
                         "(Material: C45, 40 mm)\nG00 X10. Z2.\nG01 Z-5. F0.2 ; feed in")

    def test_the_undo_history_survives(self):
        widget = self.editor()

        QTest.keyClicks(widget, "g")

        self.assertTrue(widget.document().isUndoAvailable())

    def test_one_undo_takes_back_the_letter_and_its_capital_together(self):
        widget = self.editor()
        QTest.keyClicks(widget, "g")

        widget.undo()

        self.assertEqual(widget.toPlainText(), "")

    def test_a_word_typed_is_one_undo_step(self):
        widget = self.editor()
        QTest.keyClicks(widget, "g01")

        widget.undo()

        self.assertEqual(widget.toPlainText(), "")

    def test_a_comment_typed_is_undone_like_any_typing(self):
        widget = self.editor()
        QTest.keyClicks(widget, "(mm in a comment)")

        widget.undo()

        self.assertEqual(widget.toPlainText(), "")

    def test_a_shortcut_is_not_typed_as_a_letter(self):
        widget = self.editor()
        QTest.keyClicks(widget, "g01")

        QTest.keyClick(widget, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)

        # Ctrl+A selects all; it must not insert an "A".
        self.assertEqual(widget.toPlainText(), "G01")

    def test_text_put_in_by_other_means_is_fixed_just_after(self):
        widget = self.editor()

        widget.insertPlainText("g00 x10. (a mm note)")
        QTest.qWait(20)

        self.assertEqual(widget.toPlainText(), "G00 X10. (a mm note)")

    def test_a_fix_made_after_the_fact_leaves_later_typing_undoable(self):
        widget = self.editor()
        widget.setPlainText("g00")
        QTest.qWait(20)

        widget.moveCursor(QTextCursor.MoveOperation.End)
        QTest.keyClicks(widget, " x1")
        self.assertEqual(widget.toPlainText(), "G00 X1")
        widget.undo()

        # Qt may fold the capitalisation into the same step; what matters is
        # that undo is alive and the typing is gone.
        self.assertNotIn("X1", widget.toPlainText())

    def test_a_fix_made_after_the_fact_leaves_the_caret_where_it_was(self):
        # A caret standing exactly where a changed letter is gets pushed past it
        # by the replacement, although the user did nothing.
        widget = self.editor()
        widget.setPlainText("g00 x10.")
        for caret in (0, 1, 4, 8):
            with self.subTest(caret=caret):
                cursor = widget.textCursor()
                cursor.setPosition(caret)
                widget.setTextCursor(cursor)
                widget.setPlainText("g00 x10.")
                cursor = widget.textCursor()
                cursor.setPosition(caret)
                widget.setTextCursor(cursor)

                QTest.qWait(20)

                self.assertEqual(widget.toPlainText(), "G00 X10.")
                self.assertEqual(widget.textCursor().position(), caret)

    def test_a_selection_survives_the_fix_too(self):
        widget = self.editor()
        widget.setPlainText("g00 x10.")
        cursor = widget.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(3, QTextCursor.MoveMode.KeepAnchor)
        widget.setTextCursor(cursor)

        QTest.qWait(20)

        self.assertEqual(widget.textCursor().selectedText(), "G00")

    def test_other_listeners_hear_of_one_change_and_see_the_capitals(self):
        widget = self.editor()
        seen = []
        widget.textChanged.connect(lambda: seen.append(widget.toPlainText()))

        QTest.keyClicks(widget, "g")

        self.assertEqual(seen, ["G"])


if __name__ == "__main__":
    unittest.main()
