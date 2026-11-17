import math
import os
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

    def test_contour_between_p_and_q_reaches_the_paths(self):
        parser = GCodeParser(chuck_z=-100.0)

        paths = parser.parse(self.ROUGHING)

        # VC-01 asked only that the contour stop vanishing, and counted the
        # four blocks as moves. VC-13 expands the cycle instead, so they are a
        # shape now -- what has to arrive is the run along the roughed contour
        # at the end of it, U0.3 and W0.1 clear of the finished shape.
        self.assertTrue(paths["cut"])
        self.assertEqual(paths["cut"][-1][1], (50.3, -29.9))

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
                for nose_direction in (None, 2):
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

    def test_the_od_tip_shifts_the_path_by_a_radius_in_both_axes(self):
        # Tip 2 is the ordinary OD turning tool here: the programmed point sits
        # one radius below and one radius in front of the centre of the insert.
        # Along a diameter that puts the axis back on the programmed X -- which
        # is why cylindrical turning works without compensation at all -- and
        # moves Z by the nose radius.
        _parser, plain = self._parse("G01 X40. Z0. F0.2\nG42\nG01 Z-10.")
        _parser, tipped = self._parse("G01 X40. Z0. F0.2\nG42\nG01 Z-10.",
                                      nose_direction=2)

        (px, pz), _end, _line = plain["comp_cut"][-1]
        (tx, tz), _end, _line = tipped["comp_cut"][-1]
        self.assertAlmostEqual(tx, px - 2 * self.NOSE, delta=1e-9)   # diameter
        self.assertAlmostEqual(tz, pz - self.NOSE, delta=1e-9)

    def test_the_od_tip_moves_the_compensated_arc_with_its_centre(self):
        _parser, plain = self._parse(self.CORNER.format(comp="G42"))
        _parser, tipped = self._parse(self.CORNER.format(comp="G42"), nose_direction=2)

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


class GCodeParserThreadCycleTests(unittest.TestCase):
    """VC-15: G92 really cuts, so it has to draw -- one pass per block."""

    THREAD = "\n".join([
        "G00 X22. Z2.",
        "G92 X19.4 Z-20. F1.5",
        "X19.1",
        "X18.9",
    ])

    def _parse(self, source=None, chuck_z=-100.0):
        parser = GCodeParser(chuck_z=chuck_z)
        return parser, parser.parse(self.THREAD if source is None else source)

    def test_every_block_cuts_one_pass_at_its_own_diameter(self):
        _parser, paths = self._parse()

        self.assertEqual([segment[0][0] for segment in paths["thread"]],
                         [19.4, 19.1, 18.9])

    def test_the_end_of_the_thread_stays_modal_across_repeats(self):
        # The repeat blocks carry no Z. Reading it off the current position
        # instead of off the opening block made every repeat zero-length.
        _parser, paths = self._parse()

        for segment in paths["thread"]:
            self.assertEqual((segment[0][1], segment[1][1]), (2.0, -20.0))

    def test_the_tool_ends_each_pass_where_it_started(self):
        parser, _paths = self._parse()

        self.assertAlmostEqual(parser.x, 22.0)
        self.assertAlmostEqual(parser.z, 2.0)

    def test_infeed_retract_and_return_are_rapids(self):
        _parser, paths = self._parse()

        # The approach, plus three moves around each of the three passes.
        self.assertEqual(len(paths["rapid"]), 1 + 3 * 3)

    def test_the_passes_are_thread_passes_and_not_ordinary_cuts(self):
        # Each pass is a straight line along Z, which in the colour of a cut
        # reads as turning. They are their own kind, as a G70's run is.
        _parser, paths = self._parse()

        self.assertEqual(len(paths["thread"]), 3)
        self.assertEqual(paths["cut"], [])

    def test_a_motion_code_cancels_the_cycle(self):
        parser, paths = self._parse(self.THREAD + "\nG01 X30. Z-5. F0.2")

        self.assertEqual(parser.mode, "G01")
        self.assertEqual(paths["cut"][-1], [(22.0, 2.0), (30.0, -5.0), 5])

    def test_a_block_without_an_axis_word_does_not_repeat_it(self):
        _parser, paths = self._parse("G00 X22. Z2.\nG92 X19.4 Z-20. F1.5\nF1.2")

        self.assertEqual(len(paths["thread"]), 1)

    def test_incremental_words_reach_the_cycle_too(self):
        _parser, paths = self._parse("G00 X22. Z2.\nG92 U-2.6 W-22. F1.5")

        self.assertEqual(paths["thread"], [[(19.4, 2.0), (19.4, -20.0), 2]])

    def test_a_taper_moves_the_entry_diameter_not_the_target(self):
        # R is the difference in radius over the taper, so it widens the entry
        # by twice itself in diameter and leaves the programmed end alone.
        _parser, paths = self._parse("G00 X22. Z2.\nG92 X19.4 Z-20. R-1. F1.5")

        self.assertEqual(paths["thread"], [[(17.4, 2.0), (19.4, -20.0), 2]])

    def test_the_taper_stays_modal_as_well(self):
        _parser, paths = self._parse(
            "G00 X22. Z2.\nG92 X19.4 Z-20. R-1. F1.5\nX19.1")

        self.assertEqual([segment[0][0] for segment in paths["thread"]], [17.4, 17.1])

    def test_a_thread_that_runs_into_the_chuck_is_flagged(self):
        # The whole point of drawing the cycle: every leg is a real move and
        # gets checked like one. Drawing nothing checked nothing.
        #
        # Three per pass, not one: the thread ends at Z-20, so the retract in X
        # and the return in Z both happen behind the face at Z-15 as well.
        _parser, paths = self._parse(chuck_z=-15.0)

        self.assertEqual(len(paths["collisions"]), 9)
        for segment in paths["thread"]:
            self.assertIn(segment, paths["collisions"])

    def test_a_cycle_with_nowhere_to_go_is_reported(self):
        parser, paths = self._parse("G00 X22. Z2.\nG92 X22. Z2. F1.5")

        self.assertEqual(paths["thread"], [])
        self.assertEqual([w["code"] for w in parser.warnings], ["CYCLE_NO_PASS"])


class GCodeParserRoughingExpansionTests(unittest.TestCase):
    """VC-13: G71/G72 expand into the passes they really cut."""

    # Ø52 stock, contour Ø20 to Z-10, a taper out to Ø30 and on to Z-30.
    ROUGHING = "\n".join([
        "G00 X52. Z2.",
        "G71 U1.5 R0.5",
        "G71 P100 Q200 U0.4 W0.1 F0.25",
        "N100 G01 X20. F0.15",
        "N110 Z-10.",
        "N120 X30. Z-15.",
        "N200 Z-30.",
    ])
    CYCLE_LINE = 3          # the block carrying P and Q

    def _parse(self, source=None):
        parser = GCodeParser(chuck_z=-100.0)
        return parser, parser.parse(self.ROUGHING if source is None else source)

    def _layers(self, paths):
        """Diameter of each roughing pass, in the order they are cut."""
        return [segment[0][0] for segment in paths["cut"]
                if segment[0][0] == segment[1][0]]

    def test_layers_step_down_by_twice_the_programmed_depth(self):
        # U1.5 on the first block is a depth in radius, so the diameter drops
        # by 3.0 a pass, starting one step below the Ø52 the tool sits at.
        _parser, paths = self._parse()

        self.assertEqual(self._layers(paths)[:4], [49.0, 46.0, 43.0, 40.0])

    def test_no_pass_cuts_into_the_finishing_allowance(self):
        _parser, paths = self._parse()

        for segment in paths["cut"]:
            for (x, _z) in segment[:2]:
                self.assertGreaterEqual(x, 20.4 - 1e-9)

    def test_a_pass_clear_of_the_contour_runs_its_whole_length(self):
        # Ø49 is wider than anything the contour reaches, so nothing stops it
        # before the far end -- Z-30 plus the W0.1 allowance.
        _parser, paths = self._parse()

        first = paths["cut"][0]
        self.assertEqual(first[0], (49.0, 2.0))
        self.assertAlmostEqual(first[1][1], -29.9, places=9)

    def test_a_pass_inside_the_contour_stops_on_it(self):
        # Ø28 meets the taper between (Ø20.4, Z-9.9) and (Ø30.4, Z-14.9):
        # 7.6 of the 10 mm rise, so 3.8 of the 5 mm run.
        _parser, paths = self._parse()

        stopped = [s for s in paths["cut"] if s[0][0] == 28.0][0]
        self.assertAlmostEqual(stopped[1][1], -13.7, places=9)

    def test_the_contour_blocks_stop_being_moves_of_their_own(self):
        # Between P and Q they describe a shape. Fanuc jumps past them once the
        # cycle is done, so every segment here belongs to the cycle block.
        _parser, paths = self._parse()

        lines = {segment[2] for segment in paths["cut"]}
        self.assertEqual(lines, {self.CYCLE_LINE})

    def test_the_cycle_ends_where_it_started(self):
        parser, _paths = self._parse()

        self.assertAlmostEqual(parser.x, 52.0)
        self.assertAlmostEqual(parser.z, 2.0)

    def test_the_cycle_finishes_along_the_roughed_contour(self):
        # Last three cuts: down the Ø20.4 face, out along the taper, then on
        # to the far end -- the finished shape with the allowance still on it.
        _parser, paths = self._parse()

        self.assertEqual(paths["cut"][-3:], [
            [(20.4, 2.1), (20.4, -9.9), self.CYCLE_LINE],
            [(20.4, -9.9), (30.4, -14.9), self.CYCLE_LINE],
            [(30.4, -14.9), (30.4, -29.9), self.CYCLE_LINE],
        ])

    def test_without_an_allowance_the_cycle_runs_onto_the_contour(self):
        _parser, paths = self._parse(
            self.ROUGHING.replace("U0.4 W0.1", "U0. W0."))

        self.assertEqual(paths["cut"][-1], [(30.0, -15.0), (30.0, -30.0),
                                            self.CYCLE_LINE])

    def test_an_arc_in_the_contour_stops_the_pass_on_the_curve(self):
        # Quarter circle from (Ø20, Z-10) to (Ø30, Z-15), centre (Ø20, Z-15).
        # At Ø28 the arc is sqrt(5^2 - 4^2) = 3 short of the centre in Z, so
        # the pass stops at Z-12, not at the Z-13 of the chord.
        source = "\n".join([
            "G00 X52. Z2.",
            "G71 U1.5 R0.5",
            "G71 P100 Q200 U0. W0. F0.25",
            "N100 G01 X20. F0.15",
            "N110 Z-10.",
            "N200 G03 X30. Z-15. R5.",
        ])
        _parser, paths = self._parse(source)

        stopped = [s for s in paths["cut"] if s[0][0] == 28.0][0]
        self.assertAlmostEqual(stopped[1][1], -12.0, places=2)

    def test_g72_steps_along_z_and_cuts_along_x(self):
        # Facing cycle: W1.0 is the depth, so the layers are 1 mm apart in Z
        # and each pass runs in X.
        # Like G71, the contour is written in the direction the cycle cuts --
        # for facing that is outside in, so the first block feeds to depth in Z
        # and the next one runs to the smallest diameter.
        source = "\n".join([
            "G00 X52. Z2.",
            "G72 W1.0 R0.5",
            "G72 P100 Q200 U0. W0. F0.25",
            "N100 G01 Z-4. F0.15",
            "N200 X20.",
        ])
        _parser, paths = self._parse(source)

        levels = [s[0][1] for s in paths["cut"] if s[0][1] == s[1][1]]
        self.assertEqual(levels[:3], [1.0, 0.0, -1.0])
        for segment in paths["cut"][:3]:
            self.assertEqual(segment[0][0], 52.0)      # each pass runs in X
            self.assertEqual(segment[1][0], 20.0)

    def test_a_cycle_without_a_depth_of_cut_is_reported(self):
        parser, paths = self._parse(
            self.ROUGHING.replace("G71 U1.5 R0.5\n", ""))

        self.assertEqual([w["code"] for w in parser.warnings], ["CYCLE_NO_DEPTH"])
        # ...and the contour is still drawn, rather than vanishing with it.
        self.assertEqual(len(paths["cut"]), 4)

    def test_block_numbers_that_name_nothing_are_reported(self):
        parser, paths = self._parse(
            self.ROUGHING.replace("P100 Q200", "P900 Q950"))

        self.assertEqual([w["code"] for w in parser.warnings],
                         ["CYCLE_BLOCKS_MISSING"])
        self.assertEqual(len(paths["cut"]), 4)

    def test_the_cycle_is_checked_against_the_chuck(self):
        parser = GCodeParser(chuck_z=-20.0)

        paths = parser.parse(self.ROUGHING)

        self.assertTrue(paths["collisions"])
        for segment in paths["collisions"]:
            self.assertLess(min(segment[0][1], segment[1][1]), -20.0)


class GCodeParserStockTests(unittest.TestCase):
    """The blank is stated in a comment, never inferred from the toolpath."""

    def stock(self, code):
        return GCodeParser().parse(code)["stock"]

    def test_a_stock_comment_gives_diameter_and_length(self):
        self.assertEqual(self.stock("(STOCK D40 L50)\nG00 X10. Z2."),
                         {'diameter': 40.0, 'length': 50.0, 'z_front': 0.0})

    def test_z_moves_the_front_face(self):
        self.assertEqual(self.stock("(STOCK D40 L50 Z0.5)")['z_front'], 0.5)

    def test_the_words_may_come_in_any_order_and_any_case(self):
        stock = self.stock("(stock l50 d40.5)")

        self.assertEqual((stock['diameter'], stock['length']), (40.5, 50.0))

    def test_no_comment_means_no_stock(self):
        self.assertIsNone(self.stock("G00 X10. Z2."))

    def test_a_toolpath_does_not_conjure_a_stock(self):
        # Cutting at diameter 40 says nothing about the blank it came from.
        self.assertIsNone(self.stock("G00 X40. Z2.\nG01 Z-30. F0.2"))

    def test_a_comment_that_merely_mentions_stock_is_not_a_definition(self):
        self.assertIsNone(self.stock("(Material C45, STOCK D40 L50, REV A)"))

    def test_a_missing_or_non_positive_size_is_no_stock(self):
        for body in ("(STOCK)", "(STOCK D40)", "(STOCK L50)",
                     "(STOCK D0 L50)", "(STOCK D40 L-5)"):
            with self.subTest(body):
                self.assertIsNone(self.stock(body))

    def test_the_first_valid_definition_wins(self):
        stock = self.stock("(STOCK D40)\n(STOCK D30 L20)\n(STOCK D99 L99)")

        self.assertEqual((stock['diameter'], stock['length']), (30.0, 20.0))

    def test_the_comment_moves_nothing(self):
        self.assertEqual(all_segments(GCodeParser().parse("(STOCK D40 L50 Z0.5)")), [])


class GCodeParserOrderTests(unittest.TestCase):
    """A playback needs the moves in the order they happened.

    The path lists keep the order within a kind and lose it across kinds, and a
    cycle files dozens of moves under one line.
    """

    REFERENCE = os.path.join(os.path.dirname(__file__), "fixtures", "reference.nc")

    def test_moves_are_listed_in_program_order_across_kinds(self):
        paths = GCodeParser().parse("\n".join([
            "G00 X30. Z0.",
            "G01 X30. Z-5. F0.2",
            "G02 X40. Z-10. R5.",
            "G00 X50.",
        ]))

        self.assertEqual(paths["order"], [("rapid", 0, 1), ("cut", 0, 2),
                                          ("arc", 0, 3), ("rapid", 1, 4)])

    def test_every_recorded_move_is_listed_once_under_its_own_line(self):
        with open(self.REFERENCE, "r", encoding="utf-8") as handle:
            paths = GCodeParser(chuck_z=-45.0).parse(handle.read())

        for kind in ("rapid", "cut", "arc"):
            listed = [(index, line) for k, index, line in paths["order"] if k == kind]
            own = [(index, item["line"] if kind == "arc" else item[2])
                   for index, item in enumerate(paths[kind])]
            self.assertEqual(listed, own, kind)

    def test_a_cycle_files_its_moves_under_the_cycle_line_in_order(self):
        paths = GCodeParser(chuck_z=-100.0).parse(GCodeParserCycleTests.ROUGHING)

        in_cycle = [kind for kind, _index, line in paths["order"] if line == 4]

        self.assertGreater(len(in_cycle), 2)
        # A pass starts with a rapid onto the layer, then cuts.
        self.assertEqual(in_cycle[:2], ["rapid", "cut"])

    def test_the_contour_blocks_of_a_cycle_are_listed_as_skipped(self):
        paths = GCodeParser(chuck_z=-100.0).parse(GCodeParserCycleTests.ROUGHING)

        self.assertEqual(paths["skipped"], frozenset({5, 6, 7, 8}))

    def test_a_program_without_a_cycle_skips_nothing(self):
        self.assertEqual(GCodeParser().parse("G00 X10. Z2.")["skipped"], frozenset())

    def test_a_cycle_that_cannot_be_expanded_skips_nothing(self):
        # Its contour blocks are then all there is to draw.
        paths = GCodeParser().parse("G00 X52. Z2.\nG71 U1.0 R0.5\n"
                                    "G71 P100 Q200 U0.3 W0.1 F0.25")

        self.assertEqual(paths["skipped"], frozenset())


class GCodeParserFinishingCycleTests(unittest.TestCase):
    """G70 runs the roughed contour once more, exactly as programmed."""

    # ROUGHING is lines 1-8; the cycle is on line 4 and G70 is line 9. The tool
    # is back at X52 Z0 when G70 is called -- where the G71 started.
    FINISHING = GCodeParserCycleTests.ROUGHING + "\nG70 P100 Q200"
    CALL = 9

    def parse(self, code=None, chuck_z=-100.0):
        parser = GCodeParser(chuck_z=chuck_z)
        paths = parser.parse(self.FINISHING if code is None else code)
        return parser, paths

    @staticmethod
    def moves_of(paths, line):
        """(kind, start, end) of every move filed under `line`, in order."""
        return [(kind, paths[kind][index][0], paths[kind][index][1])
                for kind, index, filed in paths["order"] if filed == line]

    def test_it_goes_to_the_contour_follows_it_and_comes_back(self):
        _parser, paths = self.parse()

        self.assertEqual(self.moves_of(paths, self.CALL), [
            ("rapid", (52.0, 0.0), (20.0, 0.0)),
            ("finish", (20.0, 0.0), (20.0, -10.0)),
            ("finish", (20.0, -10.0), (50.0, -10.0)),
            ("finish", (50.0, -10.0), (50.0, -30.0)),
            ("rapid", (50.0, -30.0), (52.0, 0.0)),
        ])

    def test_the_contour_is_run_without_the_roughing_allowance(self):
        # The G71 above has U0.3 W0.1. What it left standing is what G70 cuts,
        # so G70 itself must not offset the contour by it.
        _parser, paths = self.parse()

        run = [m for m in self.moves_of(paths, self.CALL) if m[0] == "finish"]

        self.assertEqual(run[0][2], (20.0, -10.0))
        self.assertEqual(run[-1][2], (50.0, -30.0))

    def test_the_tool_ends_where_the_cycle_was_called(self):
        parser, _paths = self.parse()

        self.assertEqual((parser.x, parser.z), (52.0, 0.0))

    def test_the_block_after_it_is_measured_from_the_start_point(self):
        _parser, paths = self.parse(self.FINISHING + "\nG00 X100.")

        last = paths["rapid"][-1]

        self.assertEqual((last[0], last[1], last[2]), ((52.0, 0.0), (100.0, 0.0), 10))

    def test_it_is_a_cycle_call_and_not_a_motion_mode(self):
        parser, _paths = self.parse(self.FINISHING + "\nX60.")

        self.assertEqual(parser.cycle, "G70")
        # X60. after the cycle is an ordinary rapid or cut again, not a G70 move.
        self.assertEqual(parser.mode, "G01")

    def test_it_skips_no_lines_of_its_own(self):
        # G71 already stepped past the contour blocks; G70 adds none.
        _parser, paths = self.parse()

        self.assertEqual(paths["skipped"], frozenset({5, 6, 7, 8}))

    def test_where_no_roughing_cycle_ran_the_contour_blocks_are_ordinary_blocks(self):
        # Without a G71 nothing steps past them, so they run as they would on
        # the control -- and G70 runs them once more.
        code = "\n".join([
            "G00 X52. Z0.",
            "G70 P10 Q20",
            "N10 G01 X20. F0.15",
            "N20 Z-10.",
        ])

        _parser, paths = self.parse(code)

        self.assertEqual(paths["skipped"], frozenset())
        self.assertTrue([m for m in self.moves_of(paths, 3) if m[0] == "cut"])
        self.assertTrue([m for m in self.moves_of(paths, 2) if m[0] == "finish"])

    def test_an_arc_in_the_contour_is_followed_along_the_curve(self):
        code = "\n".join([
            "G00 X40. Z2.",
            "G70 P10 Q30",
            "N10 G00 X30. Z0.",
            "N20 G02 X40. Z-5. R5.",
            "N30 G01 Z-10.",
        ])

        _parser, paths = self.parse(code)
        cuts = [m for m in self.moves_of(paths, 2) if m[0] == "finish"]

        # Chorded it would be the arc's single straight line plus the Z move.
        self.assertGreater(len(cuts), 3)
        # And the run ends where the contour does.
        self.assertEqual(cuts[-1][2], (40.0, -10.0))

    def test_it_is_checked_against_the_chuck(self):
        _parser, paths = self.parse(chuck_z=-20.0)

        self.assertIn(self.CALL, [segment[2] for segment in paths["collisions"]])

    def test_blocks_that_are_not_in_the_program_draw_nothing_and_warn(self):
        parser, paths = self.parse("G00 X52. Z2.\nG70 P100 Q200")

        self.assertEqual([w["code"] for w in parser.warnings], ["CYCLE_BLOCKS_MISSING"])
        self.assertEqual(paths["finish"], [])
        self.assertEqual((parser.x, parser.z), (52.0, 2.0))

    def test_without_p_and_q_there_is_nothing_to_run(self):
        parser, paths = self.parse("G00 X52. Z2.\nG70")

        self.assertEqual(paths["finish"], [])
        self.assertEqual(len(paths["rapid"]), 1)
        self.assertEqual(parser.cycle, "G70")

    def test_calling_it_twice_runs_the_contour_twice_from_the_same_start(self):
        _parser, paths = self.parse(self.FINISHING + "\nG70 P100 Q200")

        first, second = self.moves_of(paths, 9), self.moves_of(paths, 10)

        self.assertEqual(len(first), 5)
        # The cycle returned to its start, so the second call begins there too
        # and makes exactly the same moves.
        self.assertEqual(second, first)

    def test_its_moves_are_filed_in_order_under_the_call(self):
        _parser, paths = self.parse()

        kinds = [kind for kind, _index, line in paths["order"] if line == self.CALL]

        self.assertEqual(kinds, ["rapid", "finish", "finish", "finish", "rapid"])


class GCodeParserMultiPassThreadTests(unittest.TestCase):
    """G76: two blocks, and the control works the passes out itself.

    The depths are worked out here from the rule, not read back from a run:
    the depth after n passes is first * sqrt(n), no pass adds less than dmin,
    the roughing stops short of the full height by the allowance, and the
    finishing passes cut the full height.
    """

    # M20x2, external: crest Ø20, root Ø17.4, so a thread height of 1.3 (radius).
    # First cut 0.4, never less than 0.1 a pass, 0.05 left for the finishing.
    SETUP = "G00 X24. Z2.\nG76 P010060 Q100 R0.05\n"
    THREAD = "G76 X17.4 Z-30. P1300 Q400 F2."
    SECOND_BLOCK = 3   # the block that cuts

    def parse(self, code=None, chuck_z=-100.0):
        parser = GCodeParser(chuck_z=chuck_z)
        paths = parser.parse(self.SETUP + self.THREAD if code is None else code)
        return parser, paths

    @staticmethod
    def passes(paths, line=3):
        """The cut of every pass, in order."""
        return [s for s in paths["thread"] if s[2] == line]

    @staticmethod
    def depth_of(cut, crest=20.0):
        """How deep a pass goes, measured from the crest, as a radius."""
        return (crest - cut[1][0]) / 2.0

    def test_the_passes_get_shallower_and_then_settle_at_the_minimum(self):
        _parser, paths = self.parse()

        depths = [self.depth_of(c) for c in self.passes(paths)]

        expected = [
            0.4,                          # first * sqrt(1)
            0.4 * math.sqrt(2),           # 0.5657
            0.4 * math.sqrt(3),           # 0.6928
            0.4 * math.sqrt(4),           # 0.8
            # The next step would be 0.4*(sqrt(5)-2) = 0.094, under the 0.1
            # minimum, so from here it is 0.1 a pass.
            0.9, 1.0, 1.1, 1.2,
            1.25,                         # height 1.3 less the 0.05 allowance
            1.3,                          # the finishing pass, full height
        ]
        self.assertEqual(len(depths), len(expected))
        for got, want in zip(depths, expected):
            self.assertAlmostEqual(got, want, places=9)

    def test_the_finishing_passes_are_cut_at_the_full_height(self):
        _parser, paths = self.parse("G00 X24. Z2.\nG76 P030060 Q100 R0.05\n" + self.THREAD)

        last_three = [c[1][0] for c in self.passes(paths)[-3:]]

        for got in last_three:
            self.assertAlmostEqual(got, 17.4, places=9)       # the root diameter

    def test_the_roughing_leaves_the_allowance(self):
        _parser, paths = self.parse("G00 X24. Z2.\nG76 P010060 Q100 R0.2\n" + self.THREAD)

        depths = [self.depth_of(c) for c in self.passes(paths)]

        self.assertAlmostEqual(depths[-2], 1.3 - 0.2, places=9)   # last roughing pass
        self.assertAlmostEqual(depths[-1], 1.3, places=9)         # finishing

    def test_each_pass_is_four_moves_and_only_the_second_cuts(self):
        # No allowance here, so Q1300 -- the whole height -- is one cut.
        _parser, paths = self.parse("G00 X24. Z2.\nG76 P010060 Q100 R0.\n"
                                    "G76 X17.4 Z-30. P1300 Q1300 F2.")

        first_pass = [(kind, paths[kind][i][0], paths[kind][i][1])
                      for kind, i, line in paths["order"] if line == 3][:4]

        # X = 20 - 2*1.3 = 17.4.
        self.assertEqual(first_pass, [
            ("rapid", (24.0, 2.0), (17.4, 2.0)),       # feed in
            ("thread", (17.4, 2.0), (17.4, -30.0)),       # the thread
            ("rapid", (17.4, -30.0), (24.0, -30.0)),   # retract
            ("rapid", (24.0, -30.0), (24.0, 2.0)),     # back to the start
        ])

    def test_the_passes_are_thread_passes_and_not_ordinary_cuts(self):
        # Every pass is a straight line along Z, which in the colour of a cut
        # reads as turning. They are their own kind, as a G70's run is.
        _parser, paths = self.parse()

        self.assertEqual(len(paths["thread"]), 10)
        self.assertEqual(paths["cut"], [])

    def test_the_tool_ends_where_the_cycle_began(self):
        parser, _paths = self.parse()

        self.assertEqual((parser.x, parser.z), (24.0, 2.0))

    def test_the_first_block_moves_nothing_and_skips_nothing(self):
        _parser, paths = self.parse()

        self.assertEqual([m for m in paths["order"] if m[2] == 2], [])
        self.assertEqual(paths["skipped"], frozenset())

    def test_all_the_moves_are_filed_under_the_cutting_block_in_order(self):
        _parser, paths = self.parse()

        kinds = [kind for kind, _i, line in paths["order"] if line == self.SECOND_BLOCK]

        self.assertEqual(kinds, ["rapid", "thread", "rapid", "rapid"] * 10)

    def test_a_value_without_a_decimal_point_is_in_thousandths_of_a_millimetre(self):
        # Q350 is 0.35 mm and P1226 is 1.226 mm; with a point they are millimetres.
        bare = self.parse("G00 X24. Z2.\nG76 P010060 Q100 R0.05\nG76 X17.4 Z-30. P1226 Q350 F2.")[1]
        pointed = self.parse("G00 X24. Z2.\nG76 P010060 Q0.1 R0.05\nG76 X17.4 Z-30. P1.226 Q0.35 F2.")[1]

        self.assertEqual(self.passes(bare), self.passes(pointed))
        self.assertAlmostEqual(self.depth_of(self.passes(bare)[0], crest=17.4 + 2 * 1.226), 0.35, places=9)

    def test_a_point_at_the_end_still_means_millimetres(self):
        # Q1. is 1 mm, as Q1000 is. Read as Q1 -- a thousandth -- it asked for
        # the pass limit instead of a two-pass thread.
        trailing = self.parse("G00 X24. Z2.\nG76 P010060 Q100 R0.\nG76 X17.4 Z-30. P1300 Q1. F2.")
        bare = self.parse("G00 X24. Z2.\nG76 P010060 Q100 R0.\nG76 X17.4 Z-30. P1300 Q1000 F2.")

        self.assertEqual(trailing[0].warnings, [])
        self.assertEqual(self.passes(trailing[1]), self.passes(bare[1]))

    def test_the_first_block_stays_in_force_for_the_next_thread(self):
        two_threads = (self.SETUP + self.THREAD + "\n"
                       "G76 X17.4 Z-30. P1300 Q400 F2.")

        _parser, paths = self.parse(two_threads)

        self.assertEqual([self.depth_of(c) for c in self.passes(paths, 4)],
                         [self.depth_of(c) for c in self.passes(paths, 3)])

    def test_without_a_first_block_the_passes_follow_the_rule_alone(self):
        # No minimum, no allowance, one finishing pass: depth first*sqrt(n) up
        # to 1.3 -- n = 11, since 0.4*sqrt(10) = 1.265 is still short of it --
        # and then the finishing pass.
        _parser, paths = self.parse("G00 X24. Z2.\n" + self.THREAD)

        depths = [self.depth_of(c) for c in self.passes(paths, 2)]

        self.assertEqual(len(depths), 12)
        self.assertAlmostEqual(depths[9], 0.4 * math.sqrt(10), places=9)
        self.assertAlmostEqual(depths[-2], 1.3, places=9)
        self.assertAlmostEqual(depths[-1], 1.3, places=9)

    def test_the_run_out_leaves_the_thread_at_45_degrees(self):
        # r = 10 is one lead, 2 mm: the cut stops 2 mm short of the end and the
        # last stretch climbs 2 mm of radius while it moves 2 mm along Z.
        _parser, paths = self.parse("G00 X24. Z2.\nG76 P011060 Q100 R0.\n"
                                    "G76 X17.4 Z-30. P1300 Q1300 F2.")

        first_pass = [paths["thread"][0], paths["thread"][1]]

        self.assertEqual(first_pass[0][:2], [(17.4, 2.0), (17.4, -28.0)])
        self.assertEqual(first_pass[1][:2], [(17.4, -28.0), (21.4, -30.0)])

    def test_the_run_out_never_climbs_past_the_start(self):
        # A huge run-out is cut down to the thread's own length, and the climb
        # stops at the start diameter rather than going beyond it.
        _parser, paths = self.parse("G00 X24. Z2.\nG76 P019960 Q100 R0.\n"
                                    "G76 X17.4 Z-30. P1300 Q1300 F5.")

        for cut in paths["thread"]:
            self.assertLessEqual(max(cut[0][0], cut[1][0]), 24.0 + 1e-9)

    def test_an_internal_thread_feeds_outwards(self):
        # Start Ø12, root Ø16: a bore. The crest is a full height inside the
        # root, 16 - 2*0.6 = 14.8, and each pass goes deeper = larger.
        _parser, paths = self.parse("G00 X12. Z2.\nG76 P010060 Q100 R0.\n"
                                    "G76 X16. Z-20. P600 Q300 F1.")

        ends = [c[1][0] for c in self.passes(paths)]

        expected = [
            14.8 + 2 * 0.3,                       # first * sqrt(1)
            14.8 + 2 * 0.3 * math.sqrt(2),
            14.8 + 2 * (0.3 * math.sqrt(2) + 0.1),  # 0.3*(sqrt(3)-sqrt(2)) = 0.095 < 0.1
            16.0,                                 # 0.6 reached, capped
            16.0,                                 # finishing
        ]
        self.assertEqual(len(ends), len(expected))
        for got, want in zip(ends, expected):
            self.assertAlmostEqual(got, want, places=9)

    def test_a_taper_makes_the_start_of_the_thread_wider(self):
        # R is the difference in radius from start to end, as in G92.
        _parser, paths = self.parse("G00 X24. Z2.\nG76 P010060 Q100 R0.\n"
                                    "G76 X17.4 Z-30. R1.5 P1300 Q1300 F2.")

        start, end = paths["thread"][0][0], paths["thread"][0][1]

        self.assertAlmostEqual(start[0], 17.4 + 2 * 1.5, places=9)
        self.assertEqual(start[1], 2.0)
        self.assertEqual(end, (17.4, -30.0))

    def test_u_and_w_are_measured_from_the_start(self):
        absolute = self.parse(self.SETUP + self.THREAD)[1]
        relative = self.parse(self.SETUP + "G76 U-6.6 W-32. P1300 Q400 F2.")[1]

        for got, want in zip(self.passes(relative), self.passes(absolute)):
            self.assertAlmostEqual(got[1][0], want[1][0], places=9)
            self.assertEqual(got[1][1], want[1][1])

    def test_it_is_checked_against_the_chuck(self):
        _parser, paths = self.parse(chuck_z=-20.0)

        self.assertIn(self.SECOND_BLOCK, [s[2] for s in paths["collisions"]])

    def test_without_a_thread_height_nothing_is_cut(self):
        parser, paths = self.parse(self.SETUP + "G76 X17.4 Z-30. Q400 F2.")

        self.assertEqual([w["code"] for w in parser.warnings], ["CYCLE_NO_DEPTH"])
        self.assertEqual(paths["thread"], [])

    def test_without_a_first_depth_nothing_is_cut(self):
        parser, paths = self.parse(self.SETUP + "G76 X17.4 Z-30. P1300 F2.")

        self.assertEqual([w["code"] for w in parser.warnings], ["CYCLE_NO_DEPTH"])
        self.assertEqual(paths["thread"], [])

    def test_a_thread_that_ends_where_it_starts_is_a_warning(self):
        parser, paths = self.parse(self.SETUP + "G76 X24. Z-30. P1300 Q400 F2.")

        self.assertEqual([w["code"] for w in parser.warnings], ["CYCLE_NO_PASS"])
        self.assertEqual(paths["thread"], [])

    def test_a_first_cut_of_one_micron_stops_at_the_limit_and_says_so(self):
        parser, paths = self.parse("G00 X24. Z2.\nG76 P010060 Q0 R0.\n"
                                   "G76 X17.4 Z-30. P1300 Q1 F2.")

        self.assertEqual([w["code"] for w in parser.warnings], ["CYCLE_TOO_MANY_PASSES"])
        self.assertEqual(len(paths["thread"]), 200)

    def test_a_minimum_cut_keeps_the_pass_count_down(self):
        # The same one-micron first cut as above, but the first block sets 0.1
        # as the least a pass may add: roughing to 1.25 is then 13 passes of at
        # most 0.1, and one finishing pass makes 14 -- no limit is reached.
        parser, paths = self.parse(self.SETUP + "G76 X17.4 Z-30. P1300 Q1 F2.")

        self.assertEqual(parser.warnings, [])
        self.assertEqual(len(self.passes(paths)), 14)

    def test_it_is_a_cycle_call_and_not_a_motion_mode(self):
        parser, paths = self.parse(self.SETUP + self.THREAD + "\nG00 X30.")

        self.assertEqual(parser.cycle, "G76")
        # The block after it is an ordinary rapid again, from the start point.
        self.assertEqual(paths["rapid"][-1][:2], [(24.0, 2.0), (30.0, 2.0)])


class GCodeParserGroovingTests(unittest.TestCase):
    """G75: peck in X to the groove bottom, step along Z, peck again.

    Every expected position is worked out here from the program text. P and Q
    without a decimal point are thousandths of a millimetre, P is a radius,
    and the return e from the first block is drawn as a radius as well.
    """

    # From Ø32 at Z-10 down to Ø26: 3.0 of radius, 1.0 a peck, 0.5 back
    # after each peck -- 1.0 on the diameter.
    SINGLE = "G00 X32. Z-10.\nG75 R0.5\nG75 X26. P1000 F0.05"
    CUTTING_BLOCK = 3

    def parse(self, code=None, chuck_z=-100.0):
        parser = GCodeParser(chuck_z=chuck_z)
        paths = parser.parse(self.SINGLE if code is None else code)
        return parser, paths

    @staticmethod
    def moves(paths, line=3):
        """Every move of the cycle, in order, as (kind, start, end)."""
        return [(kind, paths[kind][i][0], paths[kind][i][1])
                for kind, i, at in paths["order"] if at == line]

    def test_a_single_groove_pecks_to_the_bottom_and_goes_back_out(self):
        _parser, paths = self.parse()

        self.assertEqual(self.moves(paths), [
            ("cut", (32.0, -10.0), (30.0, -10.0)),     # first peck, 1.0 deep
            ("rapid", (30.0, -10.0), (31.0, -10.0)),   # back 0.5
            ("cut", (31.0, -10.0), (28.0, -10.0)),     # through the 0.5, then 1.0
            ("rapid", (28.0, -10.0), (29.0, -10.0)),
            ("cut", (29.0, -10.0), (26.0, -10.0)),     # the bottom
            ("rapid", (26.0, -10.0), (32.0, -10.0)),   # out to the start
        ])

    def test_a_wide_groove_steps_along_z_and_the_last_step_is_shorter(self):
        # Z-20 to Z-27 in steps of 3.0: plunges at -20, -23, -26, and the last
        # at -27, one millimetre on. P1.5 reaches Ø26 in two pecks.
        _parser, paths = self.parse("G00 X32. Z-20.\nG75 R0.5\nG75 X26. Z-27. P1500 Q3000 F0.05")

        bottoms = [end[1] for kind, _start, end in self.moves(paths)
                   if kind == "cut" and end[0] == 26.0]
        steps = [(start, end) for kind, start, end in self.moves(paths)
                 if kind == "rapid" and start[0] == end[0] == 32.0]

        self.assertEqual(bottoms, [-20.0, -23.0, -26.0, -27.0])
        self.assertEqual(steps, [
            ((32.0, -20.0), (32.0, -23.0)),
            ((32.0, -23.0), (32.0, -26.0)),
            ((32.0, -26.0), (32.0, -27.0)),
            ((32.0, -27.0), (32.0, -20.0)),   # and back to the start
        ])

    def test_the_tool_ends_where_the_cycle_began(self):
        parser, _paths = self.parse("G00 X32. Z-20.\nG75 R0.5\nG75 X26. Z-27. P1500 Q3000 F0.05")

        self.assertEqual((parser.x, parser.z), (32.0, -20.0))

    def test_the_pecks_are_cuts_and_everything_else_is_rapid(self):
        _parser, paths = self.parse()

        self.assertEqual([kind for kind, _s, _e in self.moves(paths)],
                         ["cut", "rapid", "cut", "rapid", "cut", "rapid"])
        self.assertEqual(paths["thread"], [])
        self.assertEqual(paths["finish"], [])

    def test_the_return_never_goes_past_the_start(self):
        # A 5 mm return after a 1 mm peck would leave the work; it stops at Ø32.
        _parser, paths = self.parse("G00 X32. Z-10.\nG75 R5.\nG75 X26. P1000 F0.05")

        for _kind, start, end in self.moves(paths):
            self.assertLessEqual(max(start[0], end[0]), 32.0)
        self.assertEqual(self.moves(paths)[1], ("rapid", (30.0, -10.0), (32.0, -10.0)))

    def test_without_a_first_block_the_pecks_follow_on_without_a_return(self):
        # The return then comes from a machine parameter that is not known
        # here, so none is drawn: the pecks run one into the next.
        _parser, paths = self.parse("G00 X32. Z-10.\nG75 X26. P1000 F0.05", )

        self.assertEqual(self.moves(paths, 2), [
            ("cut", (32.0, -10.0), (30.0, -10.0)),
            ("cut", (30.0, -10.0), (28.0, -10.0)),
            ("cut", (28.0, -10.0), (26.0, -10.0)),
            ("rapid", (26.0, -10.0), (32.0, -10.0)),
        ])

    def test_the_first_block_stays_in_force_for_the_next_groove(self):
        _parser, paths = self.parse(self.SINGLE + "\nG75 X26. P1000 F0.05")

        self.assertEqual([m[1:] for m in self.moves(paths, 4)],
                         [m[1:] for m in self.moves(paths, 3)])

    def test_a_value_without_a_decimal_point_is_in_thousandths_of_a_millimetre(self):
        bare = self.parse("G00 X32. Z-20.\nG75 R500\nG75 X26. Z-27. P1500 Q3000 F0.05")[1]
        pointed = self.parse("G00 X32. Z-20.\nG75 R0.5\nG75 X26. Z-27. P1.5 Q3. F0.05")[1]

        self.assertEqual(self.moves(bare), self.moves(pointed))

    def test_an_internal_groove_pecks_outwards(self):
        # A bore at Ø20, groove bottom Ø24: two pecks of 1.0, a return of 0.5
        # between them that goes back towards the centre line.
        _parser, paths = self.parse("G00 X20. Z-5.\nG75 R0.5\nG75 X24. P1000 F0.05")

        self.assertEqual(self.moves(paths), [
            ("cut", (20.0, -5.0), (22.0, -5.0)),
            ("rapid", (22.0, -5.0), (21.0, -5.0)),
            ("cut", (21.0, -5.0), (24.0, -5.0)),
            ("rapid", (24.0, -5.0), (20.0, -5.0)),
        ])

    def test_u_and_w_are_measured_from_the_start(self):
        absolute = self.parse("G00 X32. Z-20.\nG75 R0.5\nG75 X26. Z-27. P1500 Q3000 F0.05")[1]
        relative = self.parse("G00 X32. Z-20.\nG75 R0.5\nG75 U-6. W-7. P1500 Q3000 F0.05")[1]

        self.assertEqual(self.moves(relative), self.moves(absolute))

    def test_the_relief_at_the_bottom_is_not_drawn(self):
        # Its direction is not settled by the sources this was built from, so
        # it changes nothing -- and the README says so.
        without = self.parse()[1]
        with_relief = self.parse("G00 X32. Z-10.\nG75 R0.5\nG75 X26. P1000 R0.2 F0.05")[1]

        self.assertEqual(self.moves(with_relief), self.moves(without))

    def test_it_is_checked_against_the_chuck(self):
        _parser, paths = self.parse("G00 X32. Z-20.\nG75 R0.5\nG75 X26. Z-27. P1500 Q3000 F0.05",
                                    chuck_z=-25.0)

        self.assertIn(self.CUTTING_BLOCK, [s[2] for s in paths["collisions"]])

    def test_without_a_peck_depth_nothing_is_cut(self):
        parser, paths = self.parse("G00 X32. Z-10.\nG75 R0.5\nG75 X26. F0.05")

        self.assertEqual([w["code"] for w in parser.warnings], ["CYCLE_NO_DEPTH"])
        self.assertEqual(paths["cut"], [])

    def test_a_z_move_without_a_step_cuts_nothing(self):
        parser, paths = self.parse("G00 X32. Z-20.\nG75 R0.5\nG75 X26. Z-27. P1500 F0.05")

        self.assertEqual([w["code"] for w in parser.warnings], ["CYCLE_NO_PASS"])
        self.assertEqual(paths["cut"], [])

    def test_a_groove_without_depth_is_a_warning(self):
        parser, paths = self.parse("G00 X32. Z-20.\nG75 R0.5\nG75 X32. Z-27. P1500 Q3000 F0.05")

        self.assertEqual([w["code"] for w in parser.warnings], ["CYCLE_NO_PASS"])
        self.assertEqual(paths["cut"], [])

    def test_a_peck_of_one_micron_stops_at_the_limit_and_says_so(self):
        # 3 mm of radius in pecks of 0.001 is 3000 pecks. The first 1000 are
        # drawn, which reach Ø32 - 2*1.0 = Ø30 -- and not the bottom.
        parser, paths = self.parse("G00 X32. Z-10.\nG75 X26. P1 F0.05")

        self.assertEqual([w["code"] for w in parser.warnings], ["CYCLE_TOO_MANY_PASSES"])
        self.assertEqual(len(paths["cut"]), 1000)
        self.assertAlmostEqual(paths["cut"][-1][1][0], 30.0, places=9)
        self.assertEqual(paths["rapid"][-1][1], (32.0, -10.0))

    def test_the_first_block_moves_nothing(self):
        _parser, paths = self.parse()

        self.assertEqual(self.moves(paths, 2), [])

    def test_it_is_a_cycle_call_and_not_a_motion_mode(self):
        parser, paths = self.parse(self.SINGLE + "\nG00 X40.")

        self.assertEqual(parser.cycle, "G75")
        self.assertEqual(paths["rapid"][-1][:2], [(32.0, -10.0), (40.0, -10.0)])


class GCodeParserFaceGroovingTests(unittest.TestCase):
    """G74: G75 turned through 90 degrees -- pecks along Z, steps in X.

    Q is the peck along Z, P the step in X as a radius, and the return e of
    the first block is a distance along Z. Positions are worked out by hand.
    """

    # A deep hole on the centre line: no X, so one plunge. From Z2 to Z-12 is
    # 14 mm in pecks of 5: to Z-3, Z-8 and the bottom at Z-12, with 0.5 back
    # after the first two.
    DRILL = "G00 X0. Z2.\nG74 R0.5\nG74 Z-12. Q5000 F0.1"

    def parse(self, code=None, chuck_z=-100.0):
        parser = GCodeParser(chuck_z=chuck_z)
        paths = parser.parse(self.DRILL if code is None else code)
        return parser, paths

    moves = staticmethod(GCodeParserGroovingTests.moves)

    def test_a_deep_hole_is_pecked_along_the_centre_line(self):
        _parser, paths = self.parse()

        self.assertEqual(self.moves(paths), [
            ("cut", (0.0, 2.0), (0.0, -3.0)),
            ("rapid", (0.0, -3.0), (0.0, -2.5)),
            ("cut", (0.0, -2.5), (0.0, -8.0)),
            ("rapid", (0.0, -8.0), (0.0, -7.5)),
            ("cut", (0.0, -7.5), (0.0, -12.0)),
            ("rapid", (0.0, -12.0), (0.0, 2.0)),
        ])

    def test_a_face_groove_steps_inwards_by_a_radius(self):
        # Ø52 to Ø46 is 3.0 of radius, P1.0 a step: plunges at Ø52, 50, 48
        # and 46, each pecked to Z-3 in 1.5 with 0.5 back between.
        _parser, paths = self.parse("G00 X52. Z0.\nG74 R0.5\nG74 X46. Z-3. P1000 Q1500 F0.1")

        bottoms = [end[0] for kind, _start, end in self.moves(paths)
                   if kind == "cut" and end[1] == -3.0]
        first_plunge = self.moves(paths)[:4]

        self.assertEqual(bottoms, [52.0, 50.0, 48.0, 46.0])
        self.assertEqual(first_plunge, [
            ("cut", (52.0, 0.0), (52.0, -1.5)),
            ("rapid", (52.0, -1.5), (52.0, -1.0)),
            ("cut", (52.0, -1.0), (52.0, -3.0)),
            ("rapid", (52.0, -3.0), (52.0, 0.0)),
        ])

    def test_the_last_step_in_x_is_shorter(self):
        # 3.5 of radius in steps of 1.5: Ø52, 49, 46 and then Ø45.
        _parser, paths = self.parse("G00 X52. Z0.\nG74 X45. Z-3. P1500 Q3000 F0.1")

        self.assertEqual([end[0] for kind, _start, end in self.moves(paths, 2) if kind == "cut"],
                         [52.0, 49.0, 46.0, 45.0])

    def test_the_tool_ends_where_the_cycle_began(self):
        parser, _paths = self.parse("G00 X52. Z0.\nG74 R0.5\nG74 X46. Z-3. P1000 Q1500 F0.1")

        self.assertEqual((parser.x, parser.z), (52.0, 0.0))

    def test_the_return_never_goes_back_past_the_start(self):
        _parser, paths = self.parse("G00 X0. Z2.\nG74 R5.\nG74 Z-12. Q5000 F0.1")

        self.assertEqual(self.moves(paths)[1], ("rapid", (0.0, -3.0), (0.0, 2.0)))

    def test_g74_and_g75_share_the_return(self):
        # One machine parameter holds it for both cycles, so a G75 first block
        # sets it for the G74 that follows.
        _parser, paths = self.parse("G00 X0. Z2.\nG75 R0.5\nG74 Z-12. Q5000 F0.1")

        self.assertEqual(self.moves(paths)[1], ("rapid", (0.0, -3.0), (0.0, -2.5)))

    def test_u_and_w_are_measured_from_the_start(self):
        absolute = self.parse("G00 X52. Z0.\nG74 R0.5\nG74 X46. Z-3. P1000 Q1500 F0.1")[1]
        relative = self.parse("G00 X52. Z0.\nG74 R0.5\nG74 U-6. W-3. P1000 Q1500 F0.1")[1]

        self.assertEqual(self.moves(relative), self.moves(absolute))

    def test_it_is_checked_against_the_chuck(self):
        _parser, paths = self.parse(chuck_z=-10.0)

        self.assertIn(3, [s[2] for s in paths["collisions"]])

    def test_without_a_peck_depth_nothing_is_cut(self):
        parser, paths = self.parse("G00 X0. Z2.\nG74 R0.5\nG74 Z-12. F0.1")

        self.assertEqual([w["code"] for w in parser.warnings], ["CYCLE_NO_DEPTH"])
        self.assertIn("(Q)", parser.warnings[0]["message"])
        self.assertEqual(paths["cut"], [])

    def test_an_x_move_without_a_step_cuts_nothing(self):
        parser, paths = self.parse("G00 X52. Z0.\nG74 X46. Z-3. Q1500 F0.1")

        self.assertEqual([w["code"] for w in parser.warnings], ["CYCLE_NO_PASS"])
        self.assertIn("an X move", parser.warnings[0]["message"])
        self.assertIn("(P)", parser.warnings[0]["message"])
        self.assertEqual(paths["cut"], [])

    def test_a_hole_without_depth_is_a_warning(self):
        parser, paths = self.parse("G00 X0. Z2.\nG74 X0. Z2. Q5000 F0.1")

        self.assertEqual([w["code"] for w in parser.warnings], ["CYCLE_NO_PASS"])
        self.assertEqual(paths["cut"], [])

    def test_it_is_a_cycle_call_and_not_a_motion_mode(self):
        parser, paths = self.parse(self.DRILL + "\nG00 Z50.")

        self.assertEqual(parser.cycle, "G74")
        self.assertEqual(paths["rapid"][-1][:2], [(0.0, 2.0), (0.0, 50.0)])


class GCodeParserPatternCycleTests(unittest.TestCase):
    """G73: the contour between P and Q, cut again and again, closer each time.

    Worked out by hand from the rule drawn here: the first pass lies the
    relief (a radius, so twice that on X) outside the allowance contour, the
    last on it, the passes between evenly spaced.
    """

    # The contour is Ø20 from Z2 to Z-10, then up to Ø30 at Z-15. A relief of
    # 3.0 in X over three passes: 6.0, 3.0 and 0 on the diameter, plus the
    # 0.4 allowance -- the contour shifted by 6.4, 3.4 and 0.4 in X, and by
    # the 0.1 allowance in Z.
    SETUP = "G00 X40. Z2.\nG73 U3. W0. R3\n"
    CALL = "G73 P10 Q20 U0.4 W0.1 F0.2\n"
    CONTOUR = "N10 G00 X20.\nG01 Z-10.\nN20 X30. Z-15."
    CUTTING_BLOCK = 3

    def parse(self, code=None, chuck_z=-100.0):
        parser = GCodeParser(chuck_z=chuck_z)
        paths = parser.parse(self.SETUP + self.CALL + self.CONTOUR if code is None else code)
        return parser, paths

    moves = staticmethod(GCodeParserGroovingTests.moves)

    @staticmethod
    def pass_starts(paths, line=3):
        """Where each pass meets the contour: the end of every approach."""
        return [end for kind, start, end in GCodeParserGroovingTests.moves(paths, line)
                if kind == "rapid" and start == (40.0, 2.0)]

    def assertPoints(self, got, want):
        self.assertEqual(len(got), len(want))
        for (gx, gz), (wx, wz) in zip(got, want):
            self.assertAlmostEqual(gx, wx, places=9)
            self.assertAlmostEqual(gz, wz, places=9)

    def test_the_passes_close_in_evenly_on_the_allowance(self):
        _parser, paths = self.parse()

        self.assertPoints(self.pass_starts(paths), [(26.4, 2.1), (23.4, 2.1), (20.4, 2.1)])

    def test_every_pass_follows_the_whole_contour_and_comes_back(self):
        _parser, paths = self.parse()

        last_pass = self.moves(paths)[-4:]

        self.assertEqual([kind for kind, _s, _e in self.moves(paths)],
                         ["rapid", "cut", "cut", "rapid"] * 3)
        self.assertPoints([p for _kind, start, end in last_pass for p in (start, end)], [
            (40.0, 2.0), (20.4, 2.1),       # onto the allowance contour
            (20.4, 2.1), (20.4, -9.9),      # along Ø20.4
            (20.4, -9.9), (30.4, -14.9),    # up the taper
            (30.4, -14.9), (40.0, 2.0),     # straight back to the start
        ])

    def test_the_tool_ends_where_the_cycle_began(self):
        parser, _paths = self.parse()

        self.assertEqual((parser.x, parser.z), (40.0, 2.0))

    def test_the_contour_blocks_are_a_shape_and_not_moves(self):
        _parser, paths = self.parse()

        self.assertEqual(paths["skipped"], frozenset({4, 5, 6}))
        self.assertEqual([m for m in paths["order"] if m[2] in (4, 5, 6)], [])

    def test_a_relief_along_z_shifts_the_passes_along_z(self):
        # W2. over two passes: 2.0 and then 0 along Z, plus the 0.1.
        _parser, paths = self.parse("G00 X40. Z2.\nG73 U0. W2. R2\n" + self.CALL + self.CONTOUR)

        self.assertPoints(self.pass_starts(paths), [(20.4, 4.1), (20.4, 2.1)])

    def test_a_single_pass_lies_on_the_allowance(self):
        _parser, paths = self.parse("G00 X40. Z2.\nG73 U3. W0. R1\n" + self.CALL + self.CONTOUR)

        self.assertPoints(self.pass_starts(paths), [(20.4, 2.1)])

    def test_a_negative_relief_moves_the_passes_inwards(self):
        # A bore: relief and allowance both point towards the centre line.
        _parser, paths = self.parse("G00 X10. Z2.\nG73 U-2. W0. R2\n"
                                    "G73 P10 Q20 U-0.4 W0.1 F0.2\n"
                                    "N10 G00 X20.\nG01 Z-10.\nN20 X16. Z-15.")

        starts = [end for kind, start, end in self.moves(paths)
                  if kind == "rapid" and start == (10.0, 2.0)]

        self.assertPoints(starts, [(15.6, 2.1), (19.6, 2.1)])

    def test_a_g70_after_it_finishes_on_the_contour_itself(self):
        _parser, paths = self.parse(self.SETUP + self.CALL + self.CONTOUR + "\nG70 P10 Q20")

        self.assertEqual([s[:2] for s in paths["finish"]], [
            [(20.0, 2.0), (20.0, -10.0)],
            [(20.0, -10.0), (30.0, -15.0)],
        ])

    def test_the_first_block_stays_in_force_for_the_next_call(self):
        _parser, paths = self.parse(self.SETUP + self.CALL + self.CONTOUR + "\n" + self.CALL)

        self.assertPoints(self.pass_starts(paths, 7), self.pass_starts(paths, 3))

    def test_without_a_number_of_passes_the_contour_is_drawn_as_it_stands(self):
        parser, paths = self.parse("G00 X40. Z2.\n" + self.CALL + self.CONTOUR)

        # Line 2 is the call, lines 3-5 the contour, which now runs as blocks.
        self.assertEqual([w["code"] for w in parser.warnings], ["CYCLE_NO_DEPTH"])
        self.assertEqual(self.moves(paths, 2), [])
        self.assertEqual(self.moves(paths, 4), [("cut", (20.0, 2.0), (20.0, -10.0))])

    def test_block_numbers_that_name_nothing_are_a_warning(self):
        parser, _paths = self.parse(self.SETUP + "G73 P90 Q95 U0.4 W0.1 F0.2\n" + self.CONTOUR)

        self.assertEqual([w["code"] for w in parser.warnings], ["CYCLE_BLOCKS_MISSING"])

    def test_a_runaway_pass_count_stops_at_the_limit_and_says_so(self):
        parser, paths = self.parse("G00 X40. Z2.\nG73 U3. W0. R1000\n" + self.CALL + self.CONTOUR)

        self.assertEqual([w["code"] for w in parser.warnings], ["CYCLE_TOO_MANY_PASSES"])
        self.assertEqual(len(self.pass_starts(paths)), 200)

    def test_it_is_checked_against_the_chuck(self):
        _parser, paths = self.parse(chuck_z=-12.0)

        self.assertIn(self.CUTTING_BLOCK, [s[2] for s in paths["collisions"]])

    def test_it_is_a_cycle_call_and_not_a_motion_mode(self):
        parser, _paths = self.parse()

        # The contour blocks are skipped, so the mode is the one from before
        # the cycle -- as after G71.
        self.assertEqual(parser.cycle, "G73")
        self.assertEqual(parser.mode, "G00")
