"""The material a program leaves, for the playback.

GUI-free: what the operator sees of the part is worked out here, so it runs on
a bare interpreter with the parser and the lint engine.

Every expected value below is worked out by hand from the program text, not
recorded from a run. A slice is 0.025 mm long on these blanks, and `at` reads
the slice a Z falls in, so a value on a slope can be off by that much times
the slope: the tolerance is SLICE unless a test says otherwise.
"""
import math
import os
import unittest

from vibe_cnc.arc_geometry import sample_arc
from vibe_cnc.gcode_parser import GCodeParser
from vibe_cnc.stock_model import (MAX_CELLS, NO_BODY, Body, StockModel,
                                  StockPlayback, body_for)

HERE = os.path.dirname(os.path.abspath(__file__))
WELLE = os.path.join(HERE, os.pardir, "programs", "O0002_WELLE.nc")

OD = Body('outer')
BORE = Body('inner')
SLICE = 0.06

# The two tools the sample programs use, as tools.json has them, so the tests
# do not move when the shop edits its table.
TOOLS = {
    1: {"t": 1, "type": "turn_rough", "insert_radius_mm": 0.8, "nose_direction": 2},
    5: {"t": 5, "type": "drill", "d_mm": 8.2},
    7: {"t": 7, "type": "groove"},
    11: {"t": 11, "type": "turn_finish", "insert_radius_mm": 0.4, "nose_direction": 3},
}


def blank(diameter=40.0, length=50.0, z_front=0.0):
    return StockModel({'diameter': diameter, 'length': length, 'z_front': z_front})


def played(code, count=None, tools=TOOLS):
    paths = GCodeParser().parse(code)
    return StockPlayback(paths, tools).at(count)


class BodyTests(unittest.TestCase):
    def test_the_od_tool_reaches_out_and_back_from_its_tip(self):
        self.assertEqual(body_for({"type": "turn_rough", "nose_direction": 2}),
                         Body('outer', towards=1))

    def test_the_boring_bar_reaches_in_and_back(self):
        self.assertEqual(body_for({"type": "boring", "nose_direction": 3}),
                         Body('inner', towards=1))

    def test_a_tool_that_cuts_away_from_the_chuck_reaches_the_other_way(self):
        self.assertEqual(body_for({"type": "other", "nose_direction": 1}),
                         Body('outer', towards=-1))
        self.assertEqual(body_for({"type": "other", "nose_direction": 4}),
                         Body('inner', towards=-1))

    def test_a_tip_square_to_an_axis_or_unset_says_nothing_about_the_body(self):
        for direction in (0, 5, 6, 7, 8, 9, None, "x"):
            with self.subTest(direction=direction):
                self.assertEqual(body_for({"type": "turn_rough",
                                           "nose_direction": direction}), NO_BODY)

    def test_a_drill_is_its_diameter(self):
        self.assertEqual(body_for({"type": "drill", "d_mm": 8.2}),
                         Body('drill', radius=4.1))

    def test_a_drill_without_a_usable_diameter_cuts_nothing(self):
        for value in (None, "", 0, -3, "nan"):
            with self.subTest(d_mm=value):
                self.assertEqual(body_for({"type": "drill", "d_mm": value}), NO_BODY)

    def test_tools_whose_cut_is_a_width_or_a_profile_cut_nothing(self):
        for kind in ("groove", "parting", "thread_form"):
            with self.subTest(type=kind):
                self.assertEqual(body_for({"type": kind, "nose_direction": 2}), NO_BODY)

    def test_no_tool_cuts_nothing(self):
        self.assertEqual(body_for(None), NO_BODY)
        self.assertEqual(body_for({}), NO_BODY)


class BlankTests(unittest.TestCase):
    def test_an_uncut_blank_is_solid_from_the_centre_to_its_diameter(self):
        model = blank()
        self.assertEqual(model.at(-25.0), (0.0, 40.0))

    def test_outside_the_blank_there_is_nothing(self):
        model = blank(z_front=0.5)
        self.assertIsNone(model.at(0.6))
        self.assertIsNone(model.at(-49.6))
        self.assertEqual(model.at(0.4), (0.0, 40.0))

    def test_the_slices_cover_the_blank_exactly(self):
        model = blank(length=50.0, z_front=0.5)
        self.assertAlmostEqual(model.z[0] - model.cell / 2, -49.5)
        self.assertAlmostEqual(model.z[-1] + model.cell / 2, 0.5)

    def test_a_long_bar_gets_coarser_slices_not_more_of_them(self):
        model = blank(length=1000.0)
        self.assertEqual(len(model.z), MAX_CELLS)
        self.assertAlmostEqual(model.cell, 0.5)

    def test_an_uncut_blank_is_one_rectangle(self):
        self.assertEqual(blank().outline(),
                         [[(40.0, -50.0), (40.0, 0.0), (0.0, 0.0), (0.0, -50.0)]])


class TurningTests(unittest.TestCase):
    def test_a_pass_leaves_its_diameter_from_its_end_to_the_face(self):
        model = blank()
        model.cut((30.0, 2.0), (30.0, -20.0), OD)

        self.assertEqual(model.at(-10.0), (0.0, 30.0))
        self.assertEqual(model.at(-19.9), (0.0, 30.0))
        self.assertEqual(model.at(-20.1), (0.0, 40.0))

    def test_the_outline_steps_down_at_the_shoulder(self):
        model = blank()
        model.cut((30.0, 2.0), (30.0, -20.0), OD)

        (outline,) = model.outline()
        top = outline[:4]
        self.assertEqual([x for x, _z in top], [40.0, 40.0, 30.0, 30.0])
        self.assertAlmostEqual(top[1][1], -20.0, delta=model.cell)
        self.assertEqual(top[1][1], top[2][1])
        self.assertEqual(top[3][1], 0.0)

    def test_a_face_cut_takes_the_allowance_in_front_of_it(self):
        # (STOCK .. Z0.5) leaves half a millimetre in front of Z0. The tip runs
        # down the face at Z0; the body behind it takes everything in front.
        model = blank(z_front=0.5)
        model.cut((42.0, 0.0), (-1.6, 0.0), OD)

        self.assertIsNone(model.at(0.25))
        self.assertEqual(model.at(-0.1), (0.0, 40.0))

    def test_a_chamfer_towards_the_chuck_follows_the_tip(self):
        # X16 Z0 to X20 Z-2: the diameter grows by 2 for every millimetre of Z,
        # so at Z-1 it is 18.
        model = blank()
        model.cut((16.0, 0.0), (20.0, -2.0), OD)

        self.assertAlmostEqual(model.at(-1.0)[1], 18.0, delta=SLICE * 2)

    def test_a_move_down_towards_the_chuck_takes_the_square_body_with_it(self):
        # X20 Z0 to X16 Z-4. The tip passes X18 at Z-2, but the body is a square
        # shoulder behind the tip, so where the tip ends at X16 it has taken
        # everything behind it down to X16 too. A real insert clears 5 degrees
        # or more and would gouge less; this errs towards showing the gouge.
        model = blank()
        model.cut((20.0, 0.0), (16.0, -4.0), OD)

        self.assertAlmostEqual(model.at(-2.0)[1], 16.0)

    def test_a_trailing_edge_angle_keeps_a_shallow_slope(self):
        # Same move, an insert that clears 45 degrees behind its tip. The slope
        # falls 0.5 of radius per millimetre, less than tan 45, so the edge
        # follows the tip: radius 10 + Z/2, diameter 18 at Z-2.
        model = blank()
        model.cut((20.0, 0.0), (16.0, -4.0), Body('outer', trailing_deg=45.0))

        self.assertAlmostEqual(model.at(-2.0)[1], 18.0, delta=SLICE)

    def test_a_body_that_reaches_the_other_way_cuts_towards_the_face(self):
        model = blank()
        model.cut((30.0, -50.5), (30.0, -30.0), Body('outer', towards=-1))

        self.assertEqual(model.at(-40.0), (0.0, 30.0))
        self.assertEqual(model.at(-29.9), (0.0, 40.0))

    def test_cutting_past_the_centre_leaves_nothing(self):
        model = blank()
        model.cut((42.0, -30.0), (-1.0, -30.0), OD)

        self.assertIsNone(model.at(-20.0))
        self.assertEqual(model.at(-30.1), (0.0, 40.0))
        self.assertEqual(len(model.outline()), 1)

    def test_everything_cut_away_has_no_outline(self):
        model = blank()
        model.cut((42.0, -51.0), (-1.0, -51.0), OD)

        self.assertEqual(model.outline(), [])


class BoreTests(unittest.TestCase):
    def test_a_drill_bores_its_diameter_with_a_118_degree_point(self):
        model = blank()
        model.cut((0.0, 2.0), (0.0, -20.0), Body('drill', radius=4.1))

        self.assertEqual(model.at(-10.0), (8.2, 40.0))
        self.assertEqual(model.at(-20.1), (0.0, 40.0))
        # One millimetre back from the point the cone is tan(59) wide in
        # radius: 2 * 1.6643 = 3.329 in diameter.
        self.assertAlmostEqual(model.at(-19.0)[0], 2 * math.tan(math.radians(59)),
                               delta=SLICE * 2)

    def test_a_drill_off_the_centre_line_cuts_nothing(self):
        model = blank()
        model.cut((10.0, 2.0), (10.0, -20.0), Body('drill', radius=4.1))

        self.assertEqual(model.at(-10.0), (0.0, 40.0))

    def test_a_boring_bar_opens_the_bore_from_its_end_to_the_face(self):
        model = blank()
        model.cut((0.0, 2.0), (0.0, -30.0), Body('drill', radius=4.1))
        model.cut((20.0, 2.0), (20.0, -15.0), BORE)

        self.assertEqual(model.at(-10.0), (20.0, 40.0))
        self.assertEqual(model.at(-20.0), (8.2, 40.0))

    def test_the_outline_returns_along_the_bore(self):
        model = blank()
        model.cut((0.0, 2.0), (0.0, -30.0), Body('drill', radius=4.1))

        (outline,) = model.outline()
        self.assertEqual(outline[-1], (0.0, -50.0))
        self.assertIn((8.2, 0.0), outline)


class PlaybackTests(unittest.TestCase):
    PROGRAM = ("(STOCK D40 L50)\n"
               "T0101\n"
               "G00 X36. Z2.\n"
               "G01 Z-20. F0.2\n"
               "G00 X38.\n"
               "Z2.\n"
               "X32.\n"
               "G01 Z-10.\n")

    def test_no_stock_comment_means_no_model(self):
        self.assertIsNone(played("T0101\nG00 X36. Z2.\nG01 Z-20. F0.2"))

    def test_it_plays_the_moves_in_the_order_the_control_runs_them(self):
        paths = GCodeParser().parse(self.PROGRAM)
        playback = StockPlayback(paths, TOOLS)
        # Moves: rapid, cut X36, rapid, rapid, rapid, cut X32.
        self.assertEqual(len(playback), 6)

        self.assertEqual(playback.at(1).at(-5.0), (0.0, 40.0))
        self.assertEqual(playback.at(2).at(-5.0), (0.0, 36.0))
        self.assertEqual(playback.at(6).at(-5.0), (0.0, 32.0))
        self.assertEqual(playback.at(6).at(-15.0), (0.0, 36.0))

    def test_going_back_starts_again_from_the_blank(self):
        playback = StockPlayback(GCodeParser().parse(self.PROGRAM), TOOLS)
        playback.at(6)

        self.assertEqual(playback.at(2).at(-5.0), (0.0, 36.0))
        self.assertEqual(playback.at(0).at(-5.0), (0.0, 40.0))

    def test_rapids_cut_nothing(self):
        model = played("(STOCK D40 L50)\nT0101\nG00 X30. Z2.\nG00 Z-20.")
        self.assertEqual(model.at(-10.0), (0.0, 40.0))

    def test_without_a_tool_nothing_is_cut(self):
        model = played("(STOCK D40 L50)\nG00 X30. Z2.\nG01 Z-20. F0.2")
        self.assertEqual(model.at(-10.0), (0.0, 40.0))

    def test_a_groove_tool_cuts_nothing_without_a_width(self):
        model = played("(STOCK D40 L50)\nT0707\nG00 X42. Z-10.\nG01 X30. F0.05")
        self.assertEqual(model.at(-10.0), (0.0, 40.0))

    def test_thread_passes_leave_the_crest(self):
        # G92 cuts down to X17.4 in passes; the thread is drawn as passes, the
        # part keeps its crest diameter of 20.
        model = played("(STOCK D20 L40)\nT0101\nG00 X24. Z4.\n"
                       "G92 X19.2 Z-30. F2.\nX18.6\nX18.\nX17.4\nG00 X100.")
        self.assertEqual(model.at(-15.0), (0.0, 20.0))

    def test_an_arc_is_followed_along_its_chords(self):
        # G02 from X20 Z-18 to X24 Z-20, R2: a fillet with its centre at X24
        # Z-18. Its middle sits 2 / sqrt(2) from the centre: radius
        # 12 - 1.4142 = 10.586, Z -19.414.
        code = ("(STOCK D40 L50)\nT0101\nG00 X20. Z2.\nG01 Z-18. F0.2\n"
                "G02 X24. Z-20. R2.\nG01 X42.")
        model = played(code)
        self.assertAlmostEqual(model.at(-19.414)[1], 2 * (12 - math.sqrt(2)),
                               delta=SLICE)


class SampleProgramTests(unittest.TestCase):
    """O0002_WELLE.nc: a D8.2 hole 20 deep, then G71 and G70 to the contour
    X16 Z2 - X20 Z-2 - Z-18 - R2 to X24 Z-20 - X30 - Z-35 - X38 - X40 Z-36."""

    @classmethod
    def setUpClass(cls):
        with open(WELLE, encoding="utf-8") as handle:
            cls.paths = GCodeParser().parse(handle.read())
        cls.model = StockPlayback(cls.paths, TOOLS).at()

    def test_the_hole_is_the_drill(self):
        self.assertEqual(self.model.at(-10.0)[0], 8.2)
        self.assertEqual(self.model.at(-25.0)[0], 0.0)

    def test_the_front_chamfer(self):
        # X16 at Z2 to X20 at Z-2: diameter 18 - Z, 19 at Z-1.
        self.assertAlmostEqual(self.model.at(-1.0)[1], 19.0, delta=SLICE)

    def test_the_diameters_of_the_finished_contour(self):
        self.assertEqual(self.model.at(-10.0)[1], 20.0)
        self.assertEqual(self.model.at(-27.5)[1], 30.0)
        self.assertEqual(self.model.at(-45.0)[1], 40.0)

    def test_the_fillet_and_the_back_chamfer(self):
        # Fillet: centre X24 Z-18, R2. At Z-19 the radius is
        # 12 - sqrt(4 - 1) = 10.268.
        self.assertAlmostEqual(self.model.at(-19.0)[1], 2 * (12 - math.sqrt(3)),
                               delta=SLICE)
        # X38 Z-35 to X40 Z-36: diameter 38 + 2 * (-35 - Z), 39 at Z-35.5.
        self.assertAlmostEqual(self.model.at(-35.5)[1], 39.0, delta=SLICE * 2)

    def test_halfway_through_roughing_the_part_is_half_roughed(self):
        # Up to and including the pass at X27, the fifth: X27 from the face to
        # where the passes stop short of the shoulder at Z-20, and X33 behind.
        order = self.paths['order']
        cuts_of_g71 = [n for n, (kind, _i, line) in enumerate(order)
                       if kind == 'cut' and line == 21]
        model = StockPlayback(self.paths, TOOLS).at(cuts_of_g71[4] + 1)

        self.assertEqual(model.at(-10.0)[1], 27.0)
        self.assertEqual(model.at(-27.5)[1], 33.0)
        self.assertEqual(model.at(-45.0)[1], 40.0)


class SampleArcTests(unittest.TestCase):
    def test_the_points_run_the_way_the_tool_does(self):
        for code, start, end in (("G00 X20. Z-18.\nG02 X24. Z-20. R2.", (20, -18), (24, -20)),
                                 ("G00 X24. Z-20.\nG03 X20. Z-18. R2.", (24, -20), (20, -18))):
            with self.subTest(code=code):
                arc = GCodeParser().parse(code)['arc'][0]
                points = sample_arc(arc, 8)
                self.assertEqual(len(points), 9)
                self.assertAlmostEqual(points[0][0], start[0])
                self.assertAlmostEqual(points[0][1], start[1])
                self.assertAlmostEqual(points[-1][0], end[0])
                self.assertAlmostEqual(points[-1][1], end[1])
                # The short way round: the middle lies 2 / sqrt(2) from the
                # centre at X24 Z-18, towards the chuck and the centre line.
                self.assertAlmostEqual(points[4][0], 2 * (12 - math.sqrt(2)))
                self.assertAlmostEqual(points[4][1], -18 - math.sqrt(2))


if __name__ == "__main__":
    unittest.main()
