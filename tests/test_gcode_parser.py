import unittest

from vibe_cnc.gcode_parser import GCodeParser


def all_segments(paths):
    """Every path list flattened — used to assert that nothing was drawn."""
    return (paths["rapid"] + paths["cut"] + paths["arc"]
            + paths["comp_cut"] + paths["comp_arc"] + paths["collisions"])


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


class GCodeParserCycleTests(unittest.TestCase):
    """VC-01: G71/G72 are cycle calls, not motion modes."""

    # The contour blocks carry no G word on purpose: they rely on the modal
    # G01 from before the cycle. That is exactly what a sticky G71 swallowed.
    ROUGHING = "\n".join([
        "G00 X52. Z2.",
        "G01 Z0. F0.2",
        "G71 U1.0 R0.5",
        "G71 P100 Q200 U0.3 W0.1 F0.25",
        "N100 X20. F0.15",
        "Z-10.",
        "X50.",
        "N200 Z-30.",
    ])

    def test_contour_between_p_and_q_is_drawn(self):
        parser = GCodeParser(chuck_z=-100.0)

        paths = parser.parse(self.ROUGHING)

        # One approach cut plus the four contour blocks. Counting matters: a
        # sticky G71 leaves the approach cut in place and drops only the four.
        self.assertEqual(len(paths["cut"]), 5)
        self.assertEqual(paths["cut"][-1][1], (50.0, -30.0))

    def test_mode_never_becomes_a_cycle(self):
        parser = GCodeParser(chuck_z=-100.0)

        parser.parse(self.ROUGHING)

        self.assertNotEqual(parser.mode, "G71")
        self.assertIn(parser.mode, ("G00", "G01", "G02", "G03"))
        self.assertEqual(parser.cycle, "G71")

    def test_cycle_parameters_are_not_a_movement(self):
        parser = GCodeParser(chuck_z=-100.0)

        paths = parser.parse("G00 X52. Z0.\nG71 U1.0 R0.5\nG71 P100 Q200 U0.3 W0.1 F0.25")

        # Only the G00 approach may appear; the U/W of the cycle are allowances.
        self.assertEqual(paths["rapid"], [[(0.0, 0.0), (52.0, 0.0), 1]])
        self.assertEqual(paths["cut"], [])
        self.assertEqual(parser.f, 0.25)

    def test_g72_is_also_a_cycle(self):
        parser = GCodeParser(chuck_z=-100.0)

        parser.parse("G01 X20. Z0.\nG72 W1.0 R0.5\nG72 P100 Q200 U0.3 W0.1 F0.25")

        self.assertEqual(parser.mode, "G01")
        self.assertEqual(parser.cycle, "G72")


class GCodeParserRadiusWordTests(unittest.TestCase):
    """VC-03: R arcs must produce the same geometry as the I/K form."""

    def test_r_arc_matches_equivalent_ik_arc(self):
        ik = GCodeParser().parse("G00 X30. Z0.\nG02 X40. Z-5. I0. K-5.")
        r = GCodeParser().parse("G00 X30. Z0.\nG02 X40. Z-5. R5.")

        self.assertEqual(len(r["arc"]), 1)
        self.assertAlmostEqual(r["arc"][0]["center"][0], ik["arc"][0]["center"][0], delta=1e-6)
        self.assertAlmostEqual(r["arc"][0]["center"][1], ik["arc"][0]["center"][1], delta=1e-6)
        self.assertAlmostEqual(r["arc"][0]["radius"], ik["arc"][0]["radius"], delta=1e-6)

    def test_endpoints_lie_on_the_computed_circle(self):
        import math

        paths = GCodeParser().parse("G00 X30. Z0.\nG02 X40. Z-5. R5.")
        arc = paths["arc"][0]
        cr, cz = arc["center"][0] / 2.0, arc["center"][1]

        for (x, z) in (arc["start"], arc["end"]):
            self.assertAlmostEqual(math.hypot(x / 2.0 - cr, z - cz), arc["radius"], delta=1e-6)

    def test_negative_r_selects_the_major_arc(self):
        minor = GCodeParser().parse("G00 X30. Z0.\nG02 X40. Z-5. R5.")["arc"][0]
        major = GCodeParser().parse("G00 X30. Z0.\nG02 X40. Z-5. R-5.")["arc"][0]

        self.assertNotAlmostEqual(minor["center"][0], major["center"][0], delta=1e-6)
        self.assertAlmostEqual(major["center"][0], 40.0, delta=1e-6)
        self.assertAlmostEqual(major["center"][1], 0.0, delta=1e-6)

    def test_ik_wins_over_r(self):
        both = GCodeParser().parse("G00 X30. Z0.\nG02 X40. Z-5. I0. K-5. R99.")["arc"][0]

        self.assertAlmostEqual(both["radius"], 5.0, delta=1e-6)

    def test_impossible_radius_is_reported_not_silently_drawn(self):
        parser = GCodeParser()

        paths = parser.parse("G00 X30. Z0.\nG02 X40. Z-5. R1.")

        self.assertEqual(paths["arc"], [])
        self.assertEqual([w["code"] for w in parser.warnings], ["ARC_R_TOO_SMALL"])

    def test_arc_without_centre_is_reported(self):
        parser = GCodeParser()

        paths = parser.parse("G00 X30. Z0.\nG02 X40. Z-5.")

        self.assertEqual(paths["arc"], [])
        self.assertEqual([w["code"] for w in parser.warnings], ["ARC_NO_CENTER"])


class GCodeParserIncrementalWordTests(unittest.TestCase):
    """VC-04: U and W are the incremental words, U in diameter."""

    def test_w_moves_along_z(self):
        parser = GCodeParser(chuck_z=-100.0)

        parser.parse("G00 X50. Z0.\nG01 W-20.")

        self.assertAlmostEqual(parser.x, 50.0)
        self.assertAlmostEqual(parser.z, -20.0)

    def test_u_is_a_diameter_increment(self):
        parser = GCodeParser(chuck_z=-100.0)

        parser.parse("G00 X50. Z0.\nG01 U-4. W-10.")

        self.assertAlmostEqual(parser.x, 46.0)
        self.assertAlmostEqual(parser.z, -10.0)

    def test_absolute_and_incremental_mix_in_one_block(self):
        parser = GCodeParser(chuck_z=-100.0)

        paths = parser.parse("G00 X50. Z0.\nG01 X30. W-5.")

        self.assertEqual(paths["cut"], [[(50.0, 0.0), (30.0, -5.0), 2]])


class GCodeParserNonMotionTests(unittest.TestCase):
    """VC-05: X/Z/U/W are parameters, not targets, on these codes."""

    def test_dwell_does_not_move_the_tool(self):
        parser = GCodeParser()

        paths = parser.parse("G00 X50. Z0.\nG04 X1.0")

        self.assertEqual(paths["cut"], [])
        self.assertEqual(len(paths["rapid"]), 1)
        self.assertAlmostEqual(parser.x, 50.0)

    def test_g50_s_is_a_speed_limit_not_a_speed(self):
        parser = GCodeParser()

        parser.parse("G50 S3000")

        self.assertEqual(parser.s, 0)
        self.assertEqual(parser.s_max, 3000)

    def test_g50_offset_does_not_move_the_tool(self):
        parser = GCodeParser()

        paths = parser.parse("G50 X100. Z50.")

        self.assertEqual(all_segments(paths), [])

    def test_g28_reference_run_leaves_mode_alone(self):
        parser = GCodeParser()

        paths = parser.parse("G01 X20. Z-5.\nG28 U0 W0")

        self.assertEqual(parser.mode, "G01")
        self.assertEqual(len(paths["cut"]), 1)
