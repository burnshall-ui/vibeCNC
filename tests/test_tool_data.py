"""Tool library without a GUI stack (VC-06, VC-17).

This file runs on a bare interpreter. That it imports at all is the VC-17 test:
load_tools_json used to live in tool_model, which imports PyQt6, so every
GUI-free caller silently fell back to an empty tool table.
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from vibe_cnc import tool_data
from vibe_cnc.gcode_parser import GCodeParser

PAYLOAD = {
    "units": "metric",
    "tool_table": [
        {"t": 7, "name": "Groove ORing"},
        {"t": 1, "name": "CNMG Außen", "insert_radius_mm": 0.8},
        {"t": 5, "name": "Bohrer 8.2", "d_mm": 8.2},
        {"t": "abc", "name": "unparsable"},
        {"t": None, "name": "missing"},
    ],
}


class ToolRowTests(unittest.TestCase):
    def test_rows_are_ordered_by_tool_number(self):
        self.assertEqual([r[0] for r in tool_data.tool_rows(PAYLOAD)], [1, 5, 7])

    def test_rows_and_details_cover_the_same_tools(self):
        # The drift this fixes: rows came from tools.db ordered by t, details
        # from tools.json in file order, so row N and detail N disagreed.
        rows = tool_data.tool_rows(PAYLOAD)
        details = tool_data.tools_by_number(PAYLOAD)

        self.assertEqual([r[0] for r in rows], sorted(details))

    def test_missing_diameter_renders_as_a_dash(self):
        rows = {r[0]: r[1] for r in tool_data.tool_rows(PAYLOAD)}

        self.assertEqual(rows[1], "-")
        self.assertEqual(rows[5], 8.2)

    def test_unparsable_tool_numbers_are_dropped(self):
        self.assertEqual(sorted(tool_data.tools_by_number(PAYLOAD)), [1, 5, 7])

    def test_string_tool_numbers_are_accepted(self):
        payload = {"tool_table": [{"t": "7", "name": "valid"}]}

        self.assertEqual(sorted(tool_data.tools_by_number(payload)), [7])

    def test_unreadable_file_yields_an_empty_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            broken = os.path.join(tmp, "tools.json")
            with open(broken, "w", encoding="utf-8") as handle:
                handle.write("{invalid-json")

            with patch.object(tool_data, "TOOLS_JSON", broken):
                self.assertEqual(tool_data.load_tools_json(), {"tool_table": []})

    def test_missing_file_yields_an_empty_table(self):
        with patch.object(tool_data, "TOOLS_JSON", "/nonexistent/tools.json"):
            self.assertEqual(tool_data.load_tools_json(), {"tool_table": []})


class ToolRoundTripTests(unittest.TestCase):
    def test_saved_library_reads_back_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tools", "tools.json")

            with patch.object(tool_data, "TOOLS_JSON", path):
                tool_data.save_tools_json(PAYLOAD)
                self.assertEqual(tool_data.load_tools_json(), PAYLOAD)

    def test_saved_library_stays_readable_utf8_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tools.json")

            with patch.object(tool_data, "TOOLS_JSON", path):
                tool_data.save_tools_json({"tool_table": [{"t": 1, "name": "Außen"}]})

            with open(path, "r", encoding="utf-8") as handle:
                self.assertIn("Außen", handle.read())   # not escaped to ß etc.
            with open(path, "r", encoding="utf-8") as handle:
                json.load(handle)


class ParserReadsToolDataWithoutQtTests(unittest.TestCase):
    """VC-17: the nose radius has to arrive even with no GUI stack installed."""

    def test_nose_radius_is_picked_up_from_the_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tools.json")
            with patch.object(tool_data, "TOOLS_JSON", path):
                tool_data.save_tools_json(PAYLOAD)
                parser = GCodeParser(chuck_z=-100.0)
                paths = parser.parse("T0101\nG42\nG01 X20. Z-5. F0.2")

        self.assertAlmostEqual(parser.tnr, 0.8)
        self.assertEqual(len(paths["comp_cut"]), 1)
