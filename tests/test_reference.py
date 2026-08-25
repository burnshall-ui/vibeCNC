"""Reference program check (VC-14).

tests/fixtures/reference.nc walks through every branch of the parser. The
expected counts and lengths below are worked out by hand from the program text,
not recorded from a parser run — so if the parser starts producing something
else, this fails rather than agreeing with itself.
"""
import math
import os
import unittest

from vibe_cnc.gcode_parser import GCodeParser

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "reference.nc")
CHUCK_Z = -45.0


def segment_length(segment):
    """Travel of one segment in the radius plane (X is a diameter)."""
    (x1, z1), (x2, z2), _line = segment
    return math.hypot((x2 - x1) / 2.0, z2 - z1)


class ReferenceProgramTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(FIXTURE, "r", encoding="utf-8") as handle:
            cls.source = handle.read()

    def setUp(self):
        self.parser = GCodeParser(chuck_z=CHUCK_Z)
        # Pin the nose radius so the fixture does not depend on tools.json.
        self.parser.tool_items = {1: {"insert_radius_mm": 0.8}}
        self.paths = self.parser.parse(self.source)

    def test_segment_counts(self):
        self.assertEqual(len(self.paths["rapid"]), 2)
        self.assertEqual(len(self.paths["cut"]), 7)
        self.assertEqual(len(self.paths["arc"]), 3)
        self.assertEqual(len(self.paths["tool_changes"]), 1)

    def test_cut_path_length(self):
        # 2.0 + 16.0 + 10.0 + hypot(5,5) + 15.0 + 8.0 + hypot(7,17)
        expected = 2.0 + 16.0 + 10.0 + math.hypot(5.0, 5.0) + 15.0 + 8.0 + math.hypot(7.0, 17.0)
        self.assertAlmostEqual(expected, 76.455844122, places=6)

        total = sum(segment_length(s) for s in self.paths["cut"])
        self.assertAlmostEqual(total, expected, places=9)

    def test_rapid_path_length(self):
        # hypot(26,2) for the approach, hypot(2,5) for the U4./W5. reposition
        expected = math.hypot(26.0, 2.0) + math.hypot(2.0, 5.0)
        self.assertAlmostEqual(expected, 31.461974428, places=6)

        total = sum(segment_length(s) for s in self.paths["rapid"])
        self.assertAlmostEqual(total, expected, places=9)

    def test_every_arc_is_r5_and_closes_on_its_endpoints(self):
        self.assertEqual([round(a["radius"], 9) for a in self.paths["arc"]], [5.0, 5.0, 5.0])

        for arc in self.paths["arc"]:
            cr, cz = arc["center"][0] / 2.0, arc["center"][1]
            for (x, z) in (arc["start"], arc["end"]):
                self.assertAlmostEqual(math.hypot(x / 2.0 - cr, z - cz), arc["radius"], places=9)

    def test_arc_directions(self):
        self.assertEqual([a["cw"] for a in self.paths["arc"]], [True, False, True])

    def test_only_the_two_deliberate_overtravel_blocks_collide(self):
        self.assertEqual(len(self.paths["collisions"]), 2)
        for segment in self.paths["collisions"]:
            (_x1, z1), (_x2, z2), _line = segment
            self.assertTrue(min(z1, z2) < CHUCK_Z)

    def test_compensation_only_while_g42_is_active(self):
        self.assertEqual(len(self.paths["comp_cut"]), 1)
        self.assertEqual(len(self.paths["comp_arc"]), 1)

    def test_the_compensated_arc_is_an_arc_a_circle_could_make(self):
        # N200 is a clockwise R5 arc under G42, so the insert centre runs on
        # the inside of it: 5.0 - 0.8. Centre, radius and endpoints have to
        # agree afterwards -- they did not while only the radius was offset.
        arc = self.paths["comp_arc"][0]
        self.assertAlmostEqual(arc["radius"], 4.2, places=9)

        cr, cz = arc["center"][0] / 2.0, arc["center"][1]
        for (x, z) in (arc["start"], arc["end"]):
            self.assertAlmostEqual(math.hypot(x / 2.0 - cr, z - cz),
                                   arc["radius"], places=9)

    def test_modal_state_survives_cycles_dwell_and_reference_run(self):
        self.assertEqual(self.parser.mode, "G02")   # G28 must not clobber it
        self.assertEqual(self.parser.cycle, "G71")
        self.assertEqual(self.parser.comp, "G40")
        self.assertEqual(self.parser.tool, 1)
        self.assertEqual(self.parser.s, 180)        # from G96 S180
        self.assertEqual(self.parser.s_max, 2500)   # from G50 S2500

    def test_final_position(self):
        self.assertAlmostEqual(self.parser.x, 30.0)
        self.assertAlmostEqual(self.parser.z, -65.0)

    def test_reference_program_produces_no_warnings(self):
        self.assertEqual(self.parser.warnings, [])
