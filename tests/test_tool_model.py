"""Qt table model over the tool library (VC-06).

The split brain this covers: rows came from tools/tools.db via SQLite while the
detail pane came from tools/tools.json, and only the JSON was ever written. An
edit appeared in the details and nowhere else, and the two files had already
drifted apart in the repository.
"""
import os
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

from vibe_cnc import tool_data, tool_model

PAYLOAD = {
    "tool_table": [
        {"t": 7, "name": "Groove ORing"},
        {"t": 1, "name": "CNMG Außen", "insert_radius_mm": 0.8},
        {"t": 5, "name": "Bohrer 8.2", "d_mm": 8.2},
        {"t": "abc", "name": "unparsable"},
    ]
}


class ToolModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "tools.json")
        self._patch = patch.object(tool_data, "TOOLS_JSON", self.path)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        tool_data.save_tools_json(PAYLOAD)

    def test_rows_and_details_come_from_the_same_file(self):
        model = tool_model.ToolModel()

        self.assertEqual([row[0] for row in model.rows], sorted(model.tool_data))

    def test_rows_are_ordered_by_tool_number(self):
        model = tool_model.ToolModel()

        self.assertEqual([row[0] for row in model.rows], [1, 5, 7])

    def test_unparsable_tool_numbers_never_reach_the_table(self):
        model = tool_model.ToolModel()

        self.assertEqual(model.rowCount(), 3)

    def test_reload_picks_up_a_tool_added_on_disk(self):
        model = tool_model.ToolModel()
        self.assertEqual(model.rowCount(), 3)

        payload = tool_data.load_tools_json()
        payload["tool_table"].append({"t": 3, "name": "Neues Werkzeug",
                                      "insert_radius_mm": 0.4})
        tool_data.save_tools_json(payload)
        model.reload()

        self.assertEqual([row[0] for row in model.rows], [1, 3, 5, 7])
        self.assertEqual(model.get_tool_info(3)["name"], "Neues Werkzeug")

    def test_reload_picks_up_a_deleted_tool(self):
        model = tool_model.ToolModel()

        payload = tool_data.load_tools_json()
        payload["tool_table"] = [t for t in payload["tool_table"] if t.get("t") != 5]
        tool_data.save_tools_json(payload)
        model.reload()

        self.assertEqual([row[0] for row in model.rows], [1, 7])
        self.assertEqual(model.get_tool_info(5), {})

    def test_reload_resets_the_model_rather_than_relayouting(self):
        # A changed row count is not something layoutChanged covers; views keep
        # their old row count and read past the end.
        model = tool_model.ToolModel()
        resets = []
        model.modelReset.connect(lambda: resets.append(True))

        model.reload()

        self.assertEqual(len(resets), 1)

    def test_round_trip_through_the_model_keeps_the_content(self):
        model = tool_model.ToolModel()
        before = model.get_tool_info(1)

        tool_data.save_tools_json(tool_data.load_tools_json())
        model.reload()

        self.assertEqual(model.get_tool_info(1), before)

    def test_tool_code_is_built_from_the_number(self):
        model = tool_model.ToolModel()

        self.assertEqual(model.get_tool_code(model.rows[0][0]), "T0101")


class ToolEditorIntegrationTests(unittest.TestCase):
    """VC-06 acceptance: an edit must reach the table without a restart."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._patch = patch.object(tool_data, "TOOLS_JSON",
                                   os.path.join(self._tmp.name, "tools.json"))
        self._patch.start()
        self.addCleanup(self._patch.stop)
        tool_data.save_tools_json(PAYLOAD)
        self.model = tool_model.ToolModel()

    def _dialog(self, tool_num=None):
        from vibe_cnc.dialogs.tool_editor import ToolEditorDialog
        dialog = ToolEditorDialog(self.model, tool_num)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_new_tool_shows_up_in_the_table_immediately(self):
        dialog = self._dialog()
        dialog.t_input.setValue(3)
        dialog.name_input.setText("Neues Werkzeug")
        dialog.insert_radius_input.setValue(0.4)

        dialog.save_tool()

        self.assertEqual([row[0] for row in self.model.rows], [1, 3, 5, 7])
        self.assertEqual(self.model.rowCount(), 4)
        self.assertEqual(self.model.get_tool_info(3)["insert_radius_mm"], 0.4)

    def test_edited_tool_shows_up_in_the_table_immediately(self):
        dialog = self._dialog(tool_num=1)
        dialog.name_input.setText("Umbenannt")

        dialog.save_tool()

        names = {row[0]: row[2] for row in self.model.rows}
        self.assertEqual(names[1], "Umbenannt")
        self.assertEqual(self.model.get_tool_info(1)["name"], "Umbenannt")

    def test_the_table_survives_a_deletion(self):
        payload = tool_data.load_tools_json()
        payload["tool_table"] = [x for x in payload["tool_table"] if x.get("t") != 5]
        tool_data.save_tools_json(payload)

        self.model.reload()

        self.assertEqual([row[0] for row in self.model.rows], [1, 7])

    def test_a_taken_tool_number_is_refused_not_overwritten(self):
        # The check that was meant to do this sat behind an unreachable branch,
        # so a "new" tool with an existing number replaced the old record and
        # took its insert radius with it.
        before = self.model.get_tool_info(1)
        dialog = self._dialog()
        dialog.t_input.setValue(1)
        dialog.name_input.setText("VERSEHENTLICH")

        with patch("vibe_cnc.dialogs.tool_editor.QMessageBox.warning") as warned:
            dialog.save_tool()

        self.assertTrue(warned.called)
        self.assertEqual(self.model.get_tool_info(1), before)
        self.assertEqual(self.model.rowCount(), 3)

    def test_renumbering_onto_a_free_number_still_works(self):
        dialog = self._dialog(tool_num=5)
        dialog.t_input.setValue(6)
        dialog.name_input.setText("Bohrer 8.2")

        dialog.save_tool()

        self.assertEqual([row[0] for row in self.model.rows], [1, 6, 7])
        self.assertEqual(self.model.get_tool_info(5), {})
