import unittest

from vibe_cnc.gcode_parser import GCodeParser


class GCodeParserCommentTests(unittest.TestCase):
    def test_semicolon_comment_lines_are_ignored(self):
        parser = GCodeParser()

        paths = parser.parse("   ; G01 X10 Z-2")

        self.assertEqual(paths["rapid"], [])
        self.assertEqual(paths["cut"], [])
        self.assertEqual(paths["tool_changes"], [])

    def test_inline_semicolon_comments_do_not_change_mode_or_axes(self):
        parser = GCodeParser()

        paths = parser.parse("G00 X20 ; G01 X40 Z-2")

        self.assertEqual(paths["rapid"], [[(0.0, 0.0), (20.0, 0.0), 1]])
        self.assertEqual(paths["cut"], [])
