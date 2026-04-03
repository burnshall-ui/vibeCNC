import unittest
from unittest.mock import mock_open, patch

from PyQt6.QtCore import QCoreApplication

from vibe_cnc import tool_model


class ToolModelJsonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_load_tool_data_skips_invalid_tool_numbers(self):
        payload = {
            "tool_table": [
                {"t": "abc", "name": "invalid-string"},
                {"t": None, "name": "invalid-none"},
                {"t": "7", "name": "valid"},
            ]
        }

        with patch.object(tool_model, "load_tools_json", return_value=payload), patch.object(
            tool_model.ToolModel, "_load_tools", return_value=[]
        ):
            model = tool_model.ToolModel()

        self.assertEqual(model.tool_data, {7: payload["tool_table"][2]})

    def test_load_tools_json_returns_empty_table_for_invalid_json(self):
        with patch("builtins.open", mock_open(read_data="{invalid-json")):
            self.assertEqual(tool_model.load_tools_json(), {"tool_table": []})
