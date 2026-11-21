"""What a tool looks like, from the ISO codes in tools.json. GUI-free."""
import math
import unittest

from vibe_cnc.stock_model import Body, body_for
from vibe_cnc.tool_shape import (TurningTool, drill_outline, holder_code, insert_code,
                                 turning_outline, turning_tool)

# T01 and T11 as tools.json has them.
OD = {"t": 1, "name": "CNMG1204P-S External", "type": "turn_rough",
      "insert_radius_mm": 0.8, "holder": "PCLNR2525", "nose_direction": 2}
BORING = {"t": 11, "name": "DCMT11T308 Internal", "type": "turn_finish",
          "insert_radius_mm": 0.4, "holder": "SDJCR2020", "nose_direction": 3}


class CodeTests(unittest.TestCase):
    def test_a_holder_code_gives_clamping_shape_style_clearance_hand_and_shank(self):
        self.assertEqual(holder_code("PCLNR2525"), ("P", "C", "L", "N", "R", 25.0))
        self.assertEqual(holder_code("sdjcr2020"), ("S", "D", "J", "C", "R", 20.0))

    def test_a_holder_without_its_size_still_reads(self):
        self.assertEqual(holder_code("PCLNR"), ("P", "C", "L", "N", "R", None))

    def test_no_holder_code_is_none(self):
        for text in (None, "", "Groove ORing", "2525"):
            with self.subTest(text=text):
                self.assertIsNone(holder_code(text))

    def test_an_insert_code_gives_its_shape_and_edge_length(self):
        self.assertEqual(insert_code("CNMG1204P-S External"), ("C", 12.0))
        self.assertEqual(insert_code("DCMT11T308 Internal"), ("D", 11.0))

    def test_a_name_without_an_insert_code_is_none(self):
        for text in ("Drill 8.2", "Thread Former M10x1", None):
            with self.subTest(text=text):
                self.assertIsNone(insert_code(text))


class TurningToolTests(unittest.TestCase):
    def test_the_od_tool_is_an_80_degree_rhombus_in_a_95_degree_holder(self):
        tool = turning_tool(OD)

        self.assertEqual(tool, TurningTool(corner_deg=80.0, approach_deg=95.0, edge_mm=12.0,
                                           nose_mm=0.8, shank_mm=25.0, out=1, back=1))
        # 180 - 95 - 80: the edge behind the tip rises 5 degrees.
        self.assertEqual(tool.trailing_deg, 5.0)

    def test_the_boring_tool_is_a_55_degree_rhombus_turned_inwards(self):
        tool = turning_tool(BORING)

        self.assertEqual((tool.corner_deg, tool.approach_deg, tool.out, tool.back),
                         (55.0, 93.0, -1, 1))
        self.assertEqual(tool.trailing_deg, 32.0)

    def test_a_style_whose_angle_is_not_listed_gives_no_shape(self):
        # K is a facing style: its angle is measured against another feed.
        self.assertIsNone(turning_tool(dict(OD, holder="PCKNR2525")))

    def test_without_a_holder_or_a_tip_number_there_is_no_shape(self):
        self.assertIsNone(turning_tool(dict(OD, holder=None)))
        self.assertIsNone(turning_tool(dict(OD, nose_direction=0)))
        self.assertIsNone(turning_tool(None))

    def test_an_insert_code_of_another_shape_does_not_size_the_insert(self):
        tool = turning_tool(dict(OD, name="DNMG1504 External"))
        self.assertEqual(tool.edge_mm, 12.0)    # the default, not 15

    def test_the_stock_model_takes_the_trailing_angle(self):
        self.assertEqual(body_for(OD), Body('outer', towards=1, trailing_deg=5.0))
        self.assertEqual(body_for(BORING), Body('inner', towards=1, trailing_deg=32.0))


class OutlineTests(unittest.TestCase):
    def test_the_nose_touches_both_axes_through_the_programmed_point(self):
        insert = turning_outline(turning_tool(OD))['insert']

        # The programmed point is the imaginary nose: the insert reaches it in
        # Z and in X, within what twelve chords of a 0.8 radius leave off.
        self.assertAlmostEqual(min(dz for dz, _dr in insert), 0.0, delta=0.005)
        self.assertAlmostEqual(min(dr for _dz, dr in insert), 0.0, delta=0.005)

    def test_the_od_tool_lies_above_and_behind_its_tip(self):
        outline = turning_outline(turning_tool(OD))

        for name in ('insert', 'holder'):
            with self.subTest(part=name):
                self.assertGreaterEqual(min(dz for dz, _dr in outline[name]), -1e-9)
                self.assertGreaterEqual(min(dr for _dz, dr in outline[name]), -1e-9)

    def test_the_boring_tool_lies_below_its_tip_and_its_bar_runs_along_z(self):
        outline = turning_outline(turning_tool(BORING))

        self.assertLessEqual(max(dr for _dz, dr in outline['insert']), 1e-9)
        holder = outline['holder']
        self.assertGreater(max(dz for dz, _dr in holder) - min(dz for dz, _dr in holder),
                           max(dr for _dz, dr in holder) - min(dr for _dz, dr in holder))

    def test_the_trailing_edge_rises_at_the_trailing_angle(self):
        insert = turning_outline(turning_tool(OD))['insert']
        # The last point of the nose and the far end of the trailing edge.
        nose_end, trail_end = insert[12], insert[13]
        rise = math.degrees(math.atan2(trail_end[1] - nose_end[1], trail_end[0] - nose_end[0]))
        self.assertAlmostEqual(rise, 5.0, places=6)


class DrillTests(unittest.TestCase):
    def test_a_drill_comes_to_its_point_at_the_programmed_point(self):
        outline = drill_outline(8.2)

        self.assertEqual(outline[0], (0.0, 0.0))
        # Full diameter tan(59) back: 4.1 / 1.664 = 2.464 mm.
        self.assertAlmostEqual(outline[1][0], 4.1 / math.tan(math.radians(59)))
        self.assertEqual(outline[1][1], 4.1)
        self.assertEqual(min(dr for _dz, dr in outline), -4.1)


if __name__ == "__main__":
    unittest.main()
