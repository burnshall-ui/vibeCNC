"""Qt table model over the macro library (VC-18, VC-19).

VC-18 is the signal: the editor emitted layoutChanged after adding or deleting
a macro, which tells a view the order changed, not the row count. Views kept
their old count and read past the end of the list.
"""
import os
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

from vibe_cnc import macro_data, macro_model

PAYLOAD = {
    "macro_table": [
        {"nr": 9010, "name": "Antasten Z", "category": "Antasten",
         "call_type": "G65", "description": "Makro: G65 P9010"},
        {"nr": 1000, "name": "Nutprogramm", "category": "Drehen",
         "call_type": "M98", "description": "Unterprogramm: M98 P1000"},
        {"nr": 9004, "name": "senk", "category": "bohren",
         "call_type": "M98", "description": "g0 z5"},
    ]
}


class MacroModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._patch = patch.object(macro_data, "MACROS_JSON",
                                   os.path.join(self._tmp.name, "macros.json"))
        self._patch.start()
        self.addCleanup(self._patch.stop)
        macro_data.save_macros_json(PAYLOAD)
        self.model = macro_model.MacroModel()

    def _dialog(self, macro_nr=None):
        from vibe_cnc.dialogs.macro_editor import MacroEditorDialog
        dialog = MacroEditorDialog(self.model, macro_nr)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_rows_are_ordered_by_number(self):
        self.assertEqual([row[0] for row in self.model.rows], [1000, 9004, 9010])

    def test_details_come_from_the_same_file_as_the_rows(self):
        self.assertEqual([row[0] for row in self.model.rows], sorted(self.model.macro_data))

    def test_get_macro_returns_the_full_record(self):
        self.assertEqual(self.model.get_macro(9004)["description"], "g0 z5")
        self.assertIsNone(self.model.get_macro(1234))

    def test_reload_resets_the_model_rather_than_relayouting(self):
        resets = []
        self.model.modelReset.connect(lambda: resets.append(True))

        self.model.reload()

        self.assertEqual(len(resets), 1)

    def test_new_macro_shows_up_in_the_table_immediately(self):
        dialog = self._dialog()
        dialog.nr_input.setValue(9500)
        dialog.name_input.setText("Neues Makro")
        dialog.category_input.setText("Bohren")

        dialog.save_macro()

        self.assertEqual([row[0] for row in self.model.rows], [1000, 9004, 9010, 9500])
        self.assertEqual(self.model.rowCount(), 4)
        self.assertEqual(self.model.get_macro(9500)["name"], "Neues Makro")

    def test_edited_macro_shows_up_immediately(self):
        dialog = self._dialog(macro_nr=9004)
        dialog.name_input.setText("Umbenannt")

        dialog.save_macro()

        self.assertEqual(self.model.get_macro(9004)["name"], "Umbenannt")

    def test_deleting_a_macro_shrinks_the_table(self):
        payload = macro_data.load_macros_json()
        payload["macro_table"] = [m for m in payload["macro_table"] if m["nr"] != 9004]
        macro_data.save_macros_json(payload)

        self.model.reload()

        self.assertEqual([row[0] for row in self.model.rows], [1000, 9010])
        self.assertIsNone(self.model.get_macro(9004))

    def test_a_taken_number_is_refused_not_overwritten(self):
        before = self.model.get_macro(9004)
        dialog = self._dialog()
        dialog.nr_input.setValue(9004)
        dialog.name_input.setText("VERSEHENTLICH")

        with patch("vibe_cnc.dialogs.macro_editor.QMessageBox.warning") as warned:
            dialog.save_macro()

        self.assertTrue(warned.called)
        self.assertEqual(self.model.get_macro(9004), before)
        self.assertEqual(self.model.rowCount(), 3)

    def test_renumbering_onto_a_free_number_works(self):
        dialog = self._dialog(macro_nr=9004)
        dialog.nr_input.setValue(9005)
        dialog.name_input.setText("senk")

        dialog.save_macro()

        self.assertEqual([row[0] for row in self.model.rows], [1000, 9005, 9010])
        self.assertIsNone(self.model.get_macro(9004))
