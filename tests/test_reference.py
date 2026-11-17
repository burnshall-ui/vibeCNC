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

# The G71 block that carries P and Q. Everything the cycle generates is filed
# under it, so it is also how the cycle's paths are told from the rest.
CYCLE_LINE = 22

# The G70 block, which runs the same contour once more as it was programmed.
FINISH_LINE = 29

# The G76 block that cuts the thread; the one above it only sets it up.
THREAD_LINE = 35

# The G75 block that cuts the groove; the one above it sets the return.
GROOVE_LINE = 41

# The G74 block that cuts the face groove; the one above it sets the return.
FACE_GROOVE_LINE = 47

# The G73 block that cuts; the one above it sets the relief and the passes.
PATTERN_LINE = 53


def segment_length(segment):
    """Travel of one segment in the radius plane (X is a diameter)."""
    (x1, z1), (x2, z2), _line = segment
    return math.hypot((x2 - x1) / 2.0, z2 - z1)


def outside_the_cycle(segments):
    """The moves the program makes itself, without those the cycles generate."""
    return [s for s in segments
            if s[2] not in (CYCLE_LINE, FINISH_LINE, THREAD_LINE, GROOVE_LINE,
                            FACE_GROOVE_LINE, PATTERN_LINE)]


def inside_the_cycle(segments):
    return [s for s in segments if s[2] == CYCLE_LINE]


def in_the_finishing_cycle(segments):
    return [s for s in segments if s[2] == FINISH_LINE]


def in_the_threading_cycle(segments):
    return [s for s in segments if s[2] == THREAD_LINE]


def in_the_grooving_cycle(segments):
    return [s for s in segments if s[2] == GROOVE_LINE]


def in_the_face_grooving_cycle(segments):
    return [s for s in segments if s[2] == FACE_GROOVE_LINE]


def in_the_pattern_cycle(segments):
    return [s for s in segments if s[2] == PATTERN_LINE]


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
        # Counted without the cycles: three rapids of its own, four cuts, three
        # arcs. What G71 generates is checked on its own further down.
        self.assertEqual(len(outside_the_cycle(self.paths["rapid"])), 3)
        self.assertEqual(len(outside_the_cycle(self.paths["cut"])), 4)
        self.assertEqual(len(self.paths["arc"]), 3)
        self.assertEqual(len(self.paths["tool_changes"]), 1)

    def test_cut_path_length(self):
        # 2.0 down to Z0, 15.0 along Ø50, 8.0 on the W-8., then hypot(7,17).
        expected = 2.0 + 15.0 + 8.0 + math.hypot(7.0, 17.0)
        self.assertAlmostEqual(expected, 43.384776311, places=6)

        total = sum(segment_length(s) for s in outside_the_cycle(self.paths["cut"]))
        self.assertAlmostEqual(total, expected, places=9)

    def test_rapid_path_length(self):
        # hypot(26,2) for the approach, hypot(11,15) onto the end of the
        # contour, hypot(2,5) for the U4./W5. reposition.
        expected = math.hypot(26.0, 2.0) + math.hypot(11.0, 15.0) + math.hypot(2.0, 5.0)
        self.assertAlmostEqual(expected, 50.063049666, places=6)

        total = sum(segment_length(s) for s in outside_the_cycle(self.paths["rapid"]))
        self.assertAlmostEqual(total, expected, places=9)

    def test_the_roughing_cycle_cuts_the_layers_it_should(self):
        # Ø52 down to the Ø20.4 the allowance leaves, 3.0 a pass: ten layers,
        # and then one run along the roughed contour.
        cuts = inside_the_cycle(self.paths["cut"])

        # The layers come first, then the two segments of the contour run --
        # which is also parallel to the axis for its first half, so it has to
        # be told apart by position rather than by shape.
        self.assertEqual([s[0][0] for s in cuts[:-2]],
                         [49.0, 46.0, 43.0, 40.0, 37.0,
                          34.0, 31.0, 28.0, 25.0, 22.0])
        self.assertEqual(cuts[-2:], [
            [(20.4, 0.1), (20.4, -9.9), CYCLE_LINE],
            [(20.4, -9.9), (30.4, -14.9), CYCLE_LINE],
        ])

    def test_no_pass_touches_the_finished_contour(self):
        # U0.4 and W0.1 are what the finishing cut is there for.
        for segment in inside_the_cycle(self.paths["cut"]):
            for (x, z) in segment[:2]:
                self.assertGreaterEqual(x, 20.4 - 1e-9)
                self.assertLessEqual(z, 0.1 + 1e-9)

    def test_the_cycle_hands_the_tool_back_where_it_took_it(self):
        # The block after the cycle is a G00 to the end of the contour, and it
        # is measured from Ø52/Z0 in test_rapid_path_length above.
        cycle_rapids = inside_the_cycle(self.paths["rapid"])

        self.assertEqual(cycle_rapids[-1][1], (52.0, 0.0))

    def test_the_line_numbers_name_the_blocks_they_say_they_do(self):
        # CYCLE_LINE and FINISH_LINE are worked out by hand; this ties them to
        # the file, so a line added above them cannot quietly move the cycles.
        lines = self.source.splitlines()

        self.assertTrue(lines[CYCLE_LINE - 1].startswith("N080 G71 P090 Q110"))
        self.assertTrue(lines[FINISH_LINE - 1].startswith("N115 G70 P090 Q110"))
        self.assertTrue(lines[THREAD_LINE - 1].startswith("N118 G76 X28. Z-20."))
        self.assertTrue(lines[GROOVE_LINE - 1].startswith("N1192 G75 X46. Z-6."))
        self.assertTrue(lines[FACE_GROOVE_LINE - 1].startswith("N1194 G74 X46. Z-3."))
        self.assertTrue(lines[PATTERN_LINE - 1].startswith("N1196 G73 P090 Q110"))

    def test_the_finishing_cycle_runs_the_contour_exactly_as_programmed(self):
        # N090 X20. from Ø52/Z0 lands on Ø20 at Z0; N100 Z-10. goes to Z-10;
        # N110 X30. Z-15. ends at Ø30/Z-15. No U0.4/W0.1: that allowance is
        # what this cut exists to take off.
        cuts = self.paths["finish"]

        self.assertEqual(cuts, [
            [(20.0, 0.0), (20.0, -10.0), FINISH_LINE],
            [(20.0, -10.0), (30.0, -15.0), FINISH_LINE],
        ])

    def test_the_finishing_run_is_its_own_kind_of_path_not_a_cut(self):
        # In the roughing's colour it would add nothing: it lies a fraction of a
        # millimetre from the roughed contour.
        self.assertTrue(self.paths["finish"])
        self.assertEqual([s for s in self.paths["cut"] if s[2] == FINISH_LINE], [])

    def test_the_finishing_cycle_goes_to_the_contour_and_back_to_the_start(self):
        rapids = in_the_finishing_cycle(self.paths["rapid"])

        self.assertEqual(rapids, [
            [(52.0, 0.0), (20.0, 0.0), FINISH_LINE],     # onto the contour
            [(30.0, -15.0), (52.0, 0.0), FINISH_LINE],   # and back where it began
        ])

    def test_the_finishing_cycle_path_lengths(self):
        # Cuts: 10.0 along Z at Ø20, then hypot(5,5) -- X is a diameter, so the
        # 10 mm step to Ø30 is 5 mm of radius. Rapids: 16.0 of radius onto Ø20,
        # and hypot(11,15) back out to Ø52.
        cut = 10.0 + math.hypot(5.0, 5.0)
        rapid = 16.0 + math.hypot(11.0, 15.0)
        self.assertAlmostEqual(cut, 17.071067812, places=6)
        self.assertAlmostEqual(rapid, 34.601075238, places=6)

        self.assertAlmostEqual(
            sum(segment_length(s) for s in self.paths["finish"]),
            cut, places=9)
        self.assertAlmostEqual(
            sum(segment_length(s) for s in in_the_finishing_cycle(self.paths["rapid"])),
            rapid, places=9)

    def test_the_finishing_cycle_cuts_below_the_roughed_contour(self):
        # The roughing run stops U0.4 / W0.1 clear of the contour; the finishing
        # run is what reaches it.
        roughed = inside_the_cycle(self.paths["cut"])[-2]       # (20.4, 0.1)..(20.4, -9.9)
        finished = self.paths["finish"][0]  # (20.0, 0.0)..(20.0, -10.0)

        self.assertLess(finished[0][0], roughed[0][0])
        self.assertLess(finished[0][1], roughed[0][1])

    # --- the threading cycle ---
    #
    # N117 sets it up: one finishing pass, no run-out, a minimum cut of 0.1 and
    # no allowance. N118 is the thread: root Ø28 over 20 mm of Z, a height of
    # 1.0 (so the crest is Ø30), and a first cut of 0.5. The cycle starts from
    # Ø52/Z0, where the G70 above left the tool.
    #
    # With a first cut of 0.5 the depth after n passes is 0.5*sqrt(n): 0.5,
    # 0.7071, 0.8660 and 1.0 at n = 4, which is the full height. Each step is
    # more than the 0.1 minimum. The finishing pass repeats the full height.

    THREAD_DEPTHS = [0.5, 0.5 * math.sqrt(2), 0.5 * math.sqrt(3), 1.0, 1.0]
    THREAD_DIAMETERS = [30.0 - 2.0 * depth for depth in THREAD_DEPTHS]

    def test_the_thread_is_cut_in_five_passes_to_the_root_diameter(self):
        cuts = in_the_threading_cycle(self.paths["thread"])

        self.assertEqual(len(cuts), 5)
        for cut, diameter in zip(cuts, self.THREAD_DIAMETERS):
            self.assertAlmostEqual(cut[0][0], diameter, places=9)
            self.assertAlmostEqual(cut[1][0], diameter, places=9)
            self.assertEqual((cut[0][1], cut[1][1]), (0.0, -20.0))
        # Worked out by hand: Ø29.0, 28.5858, 28.2679, 28.0, 28.0.
        self.assertEqual([round(c[1][0], 4) for c in cuts],
                         [29.0, 28.5858, 28.2679, 28.0, 28.0])

    def test_the_thread_path_lengths(self):
        # Every pass cuts 20 mm along Z: 100 in all. Each rapid pair carries the
        # tool in and out by (52 - diameter)/2 of radius twice and then 20 mm
        # back along Z, so a pass adds (52 - diameter) + 20 of rapid travel.
        cut = 5 * 20.0
        rapid = sum((52.0 - d) + 20.0 for d in (29.0, 28.585786, 28.267949, 28.0, 28.0))
        self.assertAlmostEqual(rapid, 218.146264, places=5)

        self.assertAlmostEqual(
            sum(segment_length(s) for s in in_the_threading_cycle(self.paths["thread"])),
            cut, places=9)
        self.assertAlmostEqual(
            sum(segment_length(s) for s in in_the_threading_cycle(self.paths["rapid"])),
            rapid, places=5)

    def test_every_pass_feeds_in_cuts_retracts_and_returns(self):
        rapids = in_the_threading_cycle(self.paths["rapid"])

        self.assertEqual(len(rapids), 15)
        # The first pass: in to the diameter at Z0, out again at Z-20, back to Z0.
        self.assertEqual(rapids[0][:2], [(52.0, 0.0), (29.0, 0.0)])
        self.assertAlmostEqual(rapids[1][0][0], 29.0, places=9)
        self.assertEqual(rapids[1][1], (52.0, -20.0))
        self.assertEqual(rapids[2][:2], [(52.0, -20.0), (52.0, 0.0)])

    def test_the_thread_cycle_hands_the_tool_back_where_it_took_it(self):
        self.assertEqual(in_the_threading_cycle(self.paths["rapid"])[-1][1], (52.0, 0.0))

    def test_the_setup_block_of_the_thread_moves_nothing(self):
        self.assertEqual([s for s in self.paths["rapid"] + self.paths["cut"] if s[2] == THREAD_LINE - 1], [])

    # --- the grooving cycle ---
    #
    # N1191 sets a return of 0.5 after each peck. N1192 cuts from Ø52/Z0, where
    # the G76 above left the tool, down to Ø46: 3.0 of radius in pecks of 1.0.
    # One plunge is a cut of 1.0 to Ø50, back 0.5 to Ø51, a cut of 1.5 to Ø48,
    # back 0.5 to Ø49, a cut of 1.5 to Ø46, and out 3.0 to Ø52. Along Z it
    # plunges at Z0, -2.5 and -5.0, and at -6.0 after a last step of only 1.0.

    def test_the_groove_is_cut_in_four_plunges_to_the_bottom(self):
        cuts = in_the_grooving_cycle(self.paths["cut"])

        self.assertEqual(len(cuts), 12)
        self.assertEqual([c[1][1] for c in cuts if c[1][0] == 46.0], [0.0, -2.5, -5.0, -6.0])
        self.assertEqual(cuts[:3], [
            [(52.0, 0.0), (50.0, 0.0), GROOVE_LINE],
            [(51.0, 0.0), (48.0, 0.0), GROOVE_LINE],
            [(49.0, 0.0), (46.0, 0.0), GROOVE_LINE],
        ])

    def test_the_groove_path_lengths(self):
        # Cut: 1.0 + 1.5 + 1.5 a plunge, four plunges. Rapid: 0.5 + 0.5 back
        # after the pecks and 3.0 out, four times; 2.5 + 2.5 + 1.0 of steps
        # along Z; and 6.0 back to Z0 at the end.
        cut = 4 * (1.0 + 1.5 + 1.5)
        rapid = 4 * (0.5 + 0.5 + 3.0) + (2.5 + 2.5 + 1.0) + 6.0
        self.assertEqual((cut, rapid), (16.0, 28.0))

        self.assertAlmostEqual(
            sum(segment_length(s) for s in in_the_grooving_cycle(self.paths["cut"])),
            cut, places=9)
        self.assertAlmostEqual(
            sum(segment_length(s) for s in in_the_grooving_cycle(self.paths["rapid"])),
            rapid, places=9)

    def test_the_grooving_cycle_hands_the_tool_back_where_it_took_it(self):
        rapids = in_the_grooving_cycle(self.paths["rapid"])

        self.assertEqual(len(rapids), 16)   # 3 a plunge, 3 steps, 1 home
        self.assertEqual(rapids[-1][:2], [(52.0, -6.0), (52.0, 0.0)])

    def test_the_setup_block_of_the_groove_moves_nothing(self):
        self.assertEqual([s for s in self.paths["rapid"] + self.paths["cut"]
                          if s[2] == GROOVE_LINE - 1], [])

    # --- the face grooving cycle ---
    #
    # N1194 starts from Ø52/Z0, where the G75 left the tool. One plunge along
    # Z is a cut of 1.5 to Z-1.5, back 0.5 to Z-1.0, a cut of 2.0 to Z-3 and
    # out 3.0 to Z0. Across, it plunges at Ø52, 50, 48 and 46: 1.0 of radius
    # a step.

    def test_the_face_groove_is_cut_in_four_plunges_to_z_minus_3(self):
        cuts = in_the_face_grooving_cycle(self.paths["cut"])

        self.assertEqual(len(cuts), 8)
        self.assertEqual([c[1][0] for c in cuts if c[1][1] == -3.0], [52.0, 50.0, 48.0, 46.0])
        self.assertEqual(cuts[:2], [
            [(52.0, 0.0), (52.0, -1.5), FACE_GROOVE_LINE],
            [(52.0, -1.0), (52.0, -3.0), FACE_GROOVE_LINE],
        ])

    def test_the_face_groove_path_lengths(self):
        # Cut: 1.5 + 2.0 a plunge, four plunges. Rapid: 0.5 back and 3.0 out,
        # four times; three steps of 1.0 of radius; 3.0 back out to Ø52.
        cut = 4 * (1.5 + 2.0)
        rapid = 4 * (0.5 + 3.0) + 3 * 1.0 + 3.0
        self.assertEqual((cut, rapid), (14.0, 20.0))

        self.assertAlmostEqual(
            sum(segment_length(s) for s in in_the_face_grooving_cycle(self.paths["cut"])),
            cut, places=9)
        self.assertAlmostEqual(
            sum(segment_length(s) for s in in_the_face_grooving_cycle(self.paths["rapid"])),
            rapid, places=9)

    def test_the_face_grooving_cycle_hands_the_tool_back_where_it_took_it(self):
        rapids = in_the_face_grooving_cycle(self.paths["rapid"])

        self.assertEqual(len(rapids), 12)   # 2 a plunge, 3 steps, 1 home
        self.assertEqual(rapids[-1][:2], [(46.0, 0.0), (52.0, 0.0)])

    # --- the pattern repeating cycle ---
    #
    # N1196 runs the contour N090-N110 -- Ø20 from Z0 to Z-10, then up to Ø30
    # at Z-15 -- twice from Ø52/Z0. The relief is 2.0 of radius and 1.0 along
    # Z, the allowance 0.4 on the diameter and 0.1 along Z. Over two passes
    # the first carries all of the relief and the second none: shifted by
    # (2*2.0 + 0.4, 1.0 + 0.1) = (4.4, 1.1), then by (0.4, 0.1).

    def test_the_pattern_passes_are_the_contour_shifted_twice(self):
        cuts = in_the_pattern_cycle(self.paths["cut"])

        expected = [
            [(24.4, 1.1), (24.4, -8.9)], [(24.4, -8.9), (34.4, -13.9)],
            [(20.4, 0.1), (20.4, -9.9)], [(20.4, -9.9), (30.4, -14.9)],
        ]
        self.assertEqual(len(cuts), len(expected))
        for cut, (start, end) in zip(cuts, expected):
            for got, want in ((cut[0], start), (cut[1], end)):
                self.assertAlmostEqual(got[0], want[0], places=9)
                self.assertAlmostEqual(got[1], want[1], places=9)

    def test_the_pattern_cycle_path_lengths(self):
        # Cut: each pass is 10.0 along Z and hypot(5,5) up the taper.
        # Rapid: from Ø52/Z0 onto each shifted start and back from each
        # shifted end -- 13.8 and 1.1, 8.8 and 13.9 for the first pass, 15.8
        # and 0.1, 10.8 and 14.9 for the second, radius against Z.
        cut = 2 * (10.0 + math.hypot(5.0, 5.0))
        rapid = (math.hypot(13.8, 1.1) + math.hypot(8.8, 13.9)
                 + math.hypot(15.8, 0.1) + math.hypot(10.8, 14.9))
        self.assertAlmostEqual(cut, 34.142135624, places=6)
        self.assertAlmostEqual(rapid, 64.497976810, places=6)

        self.assertAlmostEqual(
            sum(segment_length(s) for s in in_the_pattern_cycle(self.paths["cut"])),
            cut, places=9)
        self.assertAlmostEqual(
            sum(segment_length(s) for s in in_the_pattern_cycle(self.paths["rapid"])),
            rapid, places=9)

    def test_the_pattern_cycle_hands_the_tool_back_where_it_took_it(self):
        rapids = in_the_pattern_cycle(self.paths["rapid"])

        self.assertEqual(len(rapids), 4)    # in and back out, twice
        self.assertEqual(rapids[-1][1], (52.0, 0.0))

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
        self.assertEqual(self.parser.cycle, "G73")  # the last one called
        self.assertEqual(self.parser.comp, "G40")
        self.assertEqual(self.parser.tool, 1)
        self.assertEqual(self.parser.s, 180)        # from G96 S180
        self.assertEqual(self.parser.s_max, 2500)   # from G50 S2500

    def test_final_position(self):
        self.assertAlmostEqual(self.parser.x, 30.0)
        self.assertAlmostEqual(self.parser.z, -65.0)

    def test_reference_program_produces_no_warnings(self):
        self.assertEqual(self.parser.warnings, [])
