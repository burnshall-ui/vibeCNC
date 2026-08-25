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

        self.assertEqual([segment[0][0] for segment in paths["cut"]],
                         [19.4, 19.1, 18.9])

    def test_the_end_of_the_thread_stays_modal_across_repeats(self):
        # The repeat blocks carry no Z. Reading it off the current position
        # instead of off the opening block made every repeat zero-length.
        _parser, paths = self._parse()

        for segment in paths["cut"]:
            self.assertEqual((segment[0][1], segment[1][1]), (2.0, -20.0))

    def test_the_tool_ends_each_pass_where_it_started(self):
        parser, _paths = self._parse()

        self.assertAlmostEqual(parser.x, 22.0)
        self.assertAlmostEqual(parser.z, 2.0)

    def test_infeed_retract_and_return_are_rapids(self):
        _parser, paths = self._parse()

        # The approach, plus three moves around each of the three passes.
        self.assertEqual(len(paths["rapid"]), 1 + 3 * 3)

    def test_a_motion_code_cancels_the_cycle(self):
        parser, paths = self._parse(self.THREAD + "\nG01 X30. Z-5. F0.2")

        self.assertEqual(parser.mode, "G01")
        self.assertEqual(paths["cut"][-1], [(22.0, 2.0), (30.0, -5.0), 5])

    def test_a_block_without_an_axis_word_does_not_repeat_it(self):
        _parser, paths = self._parse("G00 X22. Z2.\nG92 X19.4 Z-20. F1.5\nF1.2")

        self.assertEqual(len(paths["cut"]), 1)

    def test_incremental_words_reach_the_cycle_too(self):
        _parser, paths = self._parse("G00 X22. Z2.\nG92 U-2.6 W-22. F1.5")

        self.assertEqual(paths["cut"], [[(19.4, 2.0), (19.4, -20.0), 2]])

    def test_a_taper_moves_the_entry_diameter_not_the_target(self):
        # R is the difference in radius over the taper, so it widens the entry
        # by twice itself in diameter and leaves the programmed end alone.
        _parser, paths = self._parse("G00 X22. Z2.\nG92 X19.4 Z-20. R-1. F1.5")

        self.assertEqual(paths["cut"], [[(17.4, 2.0), (19.4, -20.0), 2]])

    def test_the_taper_stays_modal_as_well(self):
        _parser, paths = self._parse(
            "G00 X22. Z2.\nG92 X19.4 Z-20. R-1. F1.5\nX19.1")

        self.assertEqual([segment[0][0] for segment in paths["cut"]], [17.4, 17.1])

    def test_a_thread_that_runs_into_the_chuck_is_flagged(self):
        # The whole point of drawing the cycle: every leg is a real move and
        # gets checked like one. Drawing nothing checked nothing.
        #
        # Three per pass, not one: the thread ends at Z-20, so the retract in X
        # and the return in Z both happen behind the face at Z-15 as well.
        _parser, paths = self._parse(chuck_z=-15.0)

        self.assertEqual(len(paths["collisions"]), 9)
        for segment in paths["cut"]:
            self.assertIn(segment, paths["collisions"])

    def test_a_cycle_with_nowhere_to_go_is_reported(self):
        parser, paths = self._parse("G00 X22. Z2.\nG92 X22. Z2. F1.5")

        self.assertEqual(paths["cut"], [])
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
