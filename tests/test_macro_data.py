"""Macro library without a GUI stack (VC-19).

tools/macros.json replaces tools/macros.db, a checked-in SQLite file the app
wrote on every run. The library stays under version control on purpose — it is
curated content like the tool table — but as text, so a change is readable and
committed deliberately rather than appearing as a binary diff.
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from vibe_cnc import macro_data

PAYLOAD = {
    "macro_table": [
        {"nr": 9010, "name": "Antasten Z", "category": "Antasten",
         "call_type": "G65", "description": "Makro: G65 P9010"},
        {"nr": 1000, "name": "Nutprogramm", "category": "Drehen",
         "call_type": "M98", "description": "Unterprogramm: M98 P1000"},
        {"nr": "9004", "name": "senk", "category": "bohren"},
        {"nr": "abc", "name": "unparsable"},
    ]
}


class MacroRowTests(unittest.TestCase):
    def test_rows_are_ordered_by_macro_number(self):
        self.assertEqual([r[0] for r in macro_data.macro_rows(PAYLOAD)], [1000, 9004, 9010])

    def test_rows_and_details_cover_the_same_macros(self):
        rows = macro_data.macro_rows(PAYLOAD)
        details = macro_data.macros_by_number(PAYLOAD)

        self.assertEqual([r[0] for r in rows], sorted(details))

    def test_string_numbers_are_accepted_and_unparsable_ones_dropped(self):
        self.assertEqual(sorted(macro_data.macros_by_number(PAYLOAD)), [1000, 9004, 9010])

    def test_missing_fields_get_usable_defaults(self):
        macro = macro_data.macros_by_number(PAYLOAD)[9004]

        self.assertEqual(macro["call_type"], "M98")
        self.assertEqual(macro["description"], "")

    def test_unreadable_file_yields_an_empty_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            broken = os.path.join(tmp, "macros.json")
            with open(broken, "w", encoding="utf-8") as handle:
                handle.write("{invalid-json")

            with patch.object(macro_data, "MACROS_JSON", broken):
                self.assertEqual(macro_data.load_macros_json(), {"macro_table": []})

    def test_missing_file_yields_an_empty_table(self):
        with patch.object(macro_data, "MACROS_JSON", "/nonexistent/macros.json"):
            self.assertEqual(macro_data.load_macros_json(), {"macro_table": []})


class MacroRoundTripTests(unittest.TestCase):
    def test_saved_library_reads_back_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tools", "macros.json")

            with patch.object(macro_data, "MACROS_JSON", path):
                macro_data.save_macros_json(PAYLOAD)
                self.assertEqual(macro_data.load_macros_json(), PAYLOAD)

    def test_saved_library_stays_readable_utf8(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "macros.json")

            with patch.object(macro_data, "MACROS_JSON", path):
                macro_data.save_macros_json(
                    {"macro_table": [{"nr": 9002, "name": "Ansenken 90°"}]})

            with open(path, "r", encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("Ansenken 90°", text)      # not escaped
            self.assertTrue(text.endswith("\n"))     # newline at end of file
            json.loads(text)


class ShippedLibraryTests(unittest.TestCase):
    """The library that travels with the repository."""

    def test_the_checked_in_macros_load(self):
        payload = macro_data.load_macros_json()

        numbers = sorted(macro_data.macros_by_number(payload))
        self.assertIn(9004, numbers, "the hand-added macro 9004 went missing")
        self.assertGreaterEqual(len(numbers), 6)

    def test_every_macro_has_a_usable_call_type(self):
        for macro in macro_data.macros_by_number(macro_data.load_macros_json()).values():
            self.assertIn(macro["call_type"], macro_data.CALL_TYPES)
