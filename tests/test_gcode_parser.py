import math
import unittest

from vibe_cnc.arc_geometry import arc_sweep
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
        # I5. K0. is the centre of *this* arc: clockwise from (Ø30, Z0) up to
        # (Ø40, Z-5) curves around a centre at (Ø40, Z0), the fillet at the
        # foot of a shoulder. I0. K-5. is the other candidate circle, which
        # G02 would traverse the long way round -- see the R-sign tests below.
        ik = GCodeParser().parse("G00 X30. Z0.\nG02 X40. Z-5. I5. K0.")
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
        # Asserted on the swept angle rather than on a centre coordinate: the
        # sign of R is defined as "180 degrees or less" against "more than
        # that", and the centre only follows from it.
        minor = GCodeParser().parse("G00 X30. Z0.\nG02 X40. Z-5. R5.")["arc"][0]
        major = GCodeParser().parse("G00 X30. Z0.\nG02 X40. Z-5. R-5.")["arc"][0]

        self.assertAlmostEqual(arc_sweep(minor), 90.0, delta=1e-6)
        self.assertAlmostEqual(arc_sweep(major), 270.0, delta=1e-6)
        self.assertAlmostEqual(major["center"][0], 30.0, delta=1e-6)
        self.assertAlmostEqual(major["center"][1], -5.0, delta=1e-6)

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


class GCodeParserFullCircleTests(unittest.TestCase):
    """VC-20: a block with I/K and no X/Z is a full circle."""

    def test_full_circle_produces_an_arc(self):
        parser = GCodeParser(chuck_z=-1000.0)

        paths = parser.parse("G00 X50. Z-10.\nG02 I0. K-10.")

        self.assertEqual(len(paths["arc"]), 1)
        arc = paths["arc"][0]
        self.assertEqual(arc["start"], arc["end"])
        self.assertAlmostEqual(arc["radius"], 10.0)
        self.assertEqual(arc["center"], (50.0, -20.0))

    def test_full_circle_leaves_the_position_alone(self):
        parser = GCodeParser(chuck_z=-1000.0)

        parser.parse("G00 X50. Z-10.\nG02 I0. K-10.")

        self.assertAlmostEqual(parser.x, 50.0)
        self.assertAlmostEqual(parser.z, -10.0)

    def test_counter_clockwise_full_circle_too(self):
        paths = GCodeParser(chuck_z=-1000.0).parse("G00 X50. Z-10.\nG03 I0. K-10.")

        self.assertEqual(len(paths["arc"]), 1)
        self.assertFalse(paths["arc"][0]["cw"])

    def test_a_repeated_position_without_ik_stays_nothing(self):
        # G01 to where the tool already is is not a move and not an arc.
        parser = GCodeParser(chuck_z=-1000.0)

        paths = parser.parse("G01 X50. Z-10. F0.2\nG01 X50. Z-10.")

        self.assertEqual(len(paths["cut"]), 1)
        self.assertEqual(paths["arc"], [])

    def test_r_cannot_describe_a_full_circle_and_says_so(self):
        # Previously unreachable: the warning sat behind a movement check that
        # a zero-length block could never pass.
        parser = GCodeParser(chuck_z=-1000.0)

        paths = parser.parse("G00 X50. Z-10.\nG02 R5.")

        self.assertEqual(paths["arc"], [])
        self.assertEqual([w["code"] for w in parser.warnings], ["ARC_R_ZERO_CHORD"])


class GCodeParserArcSideTests(unittest.TestCase):
    """VC-23: an R word names two possible circles, and which one is meant
    follows from the direction of travel."""

    @staticmethod
    def _side_of_centre(arc):
        """Cross product of chord and centre offset, as the control draws it.

        Positive means the centre lies left of the direction of travel, which
        is where the centre of curvature of a counter-clockwise arc belongs.
        Z is the abscissa of that picture and X the ordinate, so the cross
        product is taken in that order rather than in the parser's.
        """
        (x1, z1), (x2, z2) = arc["start"], arc["end"]
        cx, cz = arc["center"]
        dz, dr = z2 - z1, (x2 - x1) / 2.0
        mz, mr = cz - z1, (cx - x1) / 2.0
        return dz * mr - dr * mz

    def test_a_hemisphere_on_the_face_is_not_drawn_as_a_hollow(self):
        # The everyday way to write a spherical cap: out of the centre of the
        # face, counter-clockwise, R equal to half the diameter. Its centre is
        # on the axis. The mirrored side put it at (Ø20, Z0) and turned the
        # cap into the hollow that is left when you cut it away.
        arc = GCodeParser().parse("G03 X20. Z-10. R10.")["arc"][0]

        self.assertAlmostEqual(arc["center"][0], 0.0, delta=1e-6)
        self.assertAlmostEqual(arc["center"][1], -10.0, delta=1e-6)

    def test_r_and_ik_agree_on_the_hemisphere_too(self):
        r = GCodeParser().parse("G03 X20. Z-10. R10.")["arc"][0]
        ik = GCodeParser().parse("G03 X20. Z-10. I0. K-10.")["arc"][0]

        self.assertAlmostEqual(r["center"][0], ik["center"][0], delta=1e-6)
        self.assertAlmostEqual(r["center"][1], ik["center"][1], delta=1e-6)

    def test_the_centre_of_curvature_follows_the_direction_of_travel(self):
        ccw = GCodeParser().parse("G00 X30. Z0.\nG03 X40. Z-5. R5.")["arc"][0]
        cw = GCodeParser().parse("G00 X30. Z0.\nG02 X40. Z-5. R5.")["arc"][0]

        self.assertGreater(self._side_of_centre(ccw), 0.0)   # left of travel
        self.assertLess(self._side_of_centre(cw), 0.0)        # right of it


class GCodeParserCompensationTests(unittest.TestCase):
    """VC-07/VC-08: tool nose radius compensation.

    Contours are written the way they are turned: the fillet at the foot of a
    shoulder runs clockwise, the rounded corner at the front of the part runs
    counter-clockwise. Which way the compensated radius goes has to follow
    from that, not from the G-code alone.
    """

    NOSE = 0.8
    FILLET = "G00 X30. Z0.\n{comp}\nG02 X40. Z-5. R5. F0.2"      # concave, R5
    CORNER = "G00 X36. Z0.\n{comp}\nG03 X40. Z-2. R2. F0.2"      # convex, R2

    def _parse(self, source, nose_direction=None, radius=NOSE):
        parser = GCodeParser(chuck_z=-1000.0)
        tool = {"insert_radius_mm": radius}
        if nose_direction is not None:
            tool["nose_direction"] = nose_direction
        parser.tool_items = {1: tool}
        return parser, parser.parse("T0101\n" + source)

    def test_the_four_offset_directions(self):
        # Outside corner with the tool on the outside of it: the centre of the
        # insert runs on the larger radius. Inside fillet with the same tool:
        # the smaller one. Swapping the compensation side swaps both.
        cases = [
            (self.CORNER, "G42", 2.0 + self.NOSE),
            (self.CORNER, "G41", 2.0 - self.NOSE),
            (self.FILLET, "G42", 5.0 - self.NOSE),
            (self.FILLET, "G41", 5.0 + self.NOSE),
        ]
        for contour, comp, expected in cases:
            with self.subTest(comp=comp, contour=contour.splitlines()[2]):
                _parser, paths = self._parse(contour.format(comp=comp))

                self.assertEqual(len(paths["comp_arc"]), 1)
                self.assertAlmostEqual(paths["comp_arc"][0]["radius"], expected, delta=1e-9)

    def test_every_compensated_arc_closes_on_its_own_endpoints(self):
        # The old code kept the programmed endpoints and changed the radius
        # underneath them, leaving an arc that could not pass through them.
        for contour in (self.CORNER, self.FILLET):
            for comp in ("G41", "G42"):
                for nose_direction in (None, 3):
                    with self.subTest(comp=comp, nose=nose_direction):
                        _parser, paths = self._parse(contour.format(comp=comp),
                                                     nose_direction=nose_direction)
                        arc = paths["comp_arc"][0]
                        cr, cz = arc["center"][0] / 2.0, arc["center"][1]

                        for (x, z) in (arc["start"], arc["end"]):
                            self.assertAlmostEqual(math.hypot(x / 2.0 - cr, z - cz),
                                                   arc["radius"], delta=1e-6)

    def test_the_compensated_path_stays_joined_across_a_tangent_corner(self):
        # Facing outwards, then the rounded corner onto the diameter. The two
        # contour elements meet tangentially, so their compensated paths have
        # to meet as well -- they did not while the arc offset ignored the
        # direction of travel.
        _parser, paths = self._parse("G42\nG01 X36. Z0. F0.2\nG03 X40. Z-2. R2.")

        end = paths["comp_cut"][-1][1]
        start = paths["comp_arc"][0]["start"]
        self.assertAlmostEqual(end[0], start[0], delta=1e-6)
        self.assertAlmostEqual(end[1], start[1], delta=1e-6)

    def test_turning_towards_the_chuck_offsets_away_from_the_axis(self):
        # G42 along a diameter is ordinary OD turning on a rear tool post: the
        # insert centre sits one nose radius outboard of the turned surface,
        # which is +0.8 mm in radius and so +1.6 mm in diameter.
        _parser, paths = self._parse("G01 X40. Z0. F0.2\nG42\nG01 Z-10.")

        (x1, z1), (x2, z2), _line = paths["comp_cut"][-1]
        self.assertAlmostEqual(x1, 41.6, delta=1e-9)
        self.assertAlmostEqual(x2, 41.6, delta=1e-9)
        self.assertEqual((z1, z2), (0.0, -10.0))

    def test_g41_along_the_same_move_offsets_towards_the_axis(self):
        _parser, paths = self._parse("G01 X40. Z0. F0.2\nG41\nG01 Z-10.")

        self.assertAlmostEqual(paths["comp_cut"][-1][0][0], 38.4, delta=1e-9)

    def test_tip_three_shifts_the_path_by_a_radius_in_both_axes(self):
        # Tip 3 is the ordinary OD turning tool: the programmed point sits one
        # radius below and one radius in front of the centre of the insert.
        # Along a diameter that puts the axis back on the programmed X -- which
        # is why cylindrical turning works without compensation at all -- and
        # moves Z by the nose radius.
        _parser, plain = self._parse("G01 X40. Z0. F0.2\nG42\nG01 Z-10.")
        _parser, tipped = self._parse("G01 X40. Z0. F0.2\nG42\nG01 Z-10.",
                                      nose_direction=3)

        (px, pz), _end, _line = plain["comp_cut"][-1]
        (tx, tz), _end, _line = tipped["comp_cut"][-1]
        self.assertAlmostEqual(tx, px - 2 * self.NOSE, delta=1e-9)   # diameter
        self.assertAlmostEqual(tz, pz - self.NOSE, delta=1e-9)

    def test_tip_three_moves_the_compensated_arc_with_its_centre(self):
        _parser, plain = self._parse(self.CORNER.format(comp="G42"))
        _parser, tipped = self._parse(self.CORNER.format(comp="G42"), nose_direction=3)

        self.assertAlmostEqual(tipped["comp_arc"][0]["radius"],
                               plain["comp_arc"][0]["radius"], delta=1e-9)
        self.assertAlmostEqual(tipped["comp_arc"][0]["center"][0],
                               plain["comp_arc"][0]["center"][0] - 2 * self.NOSE, delta=1e-9)
        self.assertAlmostEqual(tipped["comp_arc"][0]["center"][1],
                               plain["comp_arc"][0]["center"][1] - self.NOSE, delta=1e-9)

    def test_a_missing_or_unusable_tip_number_reads_as_zero(self):
        _parser, assumed = self._parse(self.CORNER.format(comp="G42"))
        for value in (None, 0, 12, "drei"):
            with self.subTest(value=value):
                parser = GCodeParser(chuck_z=-1000.0)
                parser.tool_items = {1: {"insert_radius_mm": self.NOSE,
                                         "nose_direction": value}}
                paths = parser.parse("T0101\n" + self.CORNER.format(comp="G42"))

                self.assertEqual(parser.nose_direction, 0)
                self.assertEqual(paths["comp_arc"][0]["center"],
                                 assumed["comp_arc"][0]["center"])

    def test_an_arc_tighter_than_the_insert_is_reported_not_drawn(self):
        # A hollow of R0.5 cannot be cut with a 0.8 mm nose. Saying so is more
        # use to the operator than a compensated path that quietly disappears.
        parser, paths = self._parse("G00 X30. Z0.\nG42\nG02 X31. Z-0.5 I0.5 K0. F0.2")

        self.assertEqual(paths["comp_arc"], [])
        self.assertEqual([w["code"] for w in parser.warnings], ["ARC_COMP_TOO_TIGHT"])

    def test_no_compensation_without_a_nose_radius(self):
        _parser, paths = self._parse(self.CORNER.format(comp="G42"), radius=0.0)

        self.assertEqual(paths["comp_arc"], [])
        self.assertEqual(paths["comp_cut"], [])
