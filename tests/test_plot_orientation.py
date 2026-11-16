"""Plot orientation (Z horizontal, X vertical).

Every lathe control draws Z along the horizontal with the chuck to the left and
X up the vertical, and a turned part is long in Z and small in X — which is
also the shape of the simulation panel. The plot used to be the other way
round, which squeezed the whole toolpath into a corner.

Transposing the axes reflects the plane, so the arc angles are mirrored as well
as swapped. These tests measure the patch matplotlib produces rather than
trusting that derivation.
"""
import math
import unittest

from PyQt6.QtWidgets import QApplication
import matplotlib.patches as patches
import matplotlib.lines as lines

from vibe_cnc.arc_geometry import arc_sweep
from vibe_cnc.gcode_parser import GCodeParser
from vibe_cnc.gcode_plotter import GCodePlotterWidget

COLORS = {'CRT_GREEN': '#6CFF6C', 'FANUC_YELLOW': '#FFC800', 'CYAN': '#56D6FF',
          'BG_DARK': '#1A1A1A', 'WHITE': '#EDEDED'}

ARCS = "G00 X30. Z0.\nG02 X40. Z-5. R5.\nG03 X50. Z-10. R5."


class PlotOrientationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def plotter(self, code, chuck_z=-1000.0, chuck_diameter=None):
        widget = GCodePlotterWidget(COLORS, chuck_z=chuck_z,
                                    chuck_diameter=chuck_diameter)
        self.addCleanup(widget.deleteLater)
        widget.set_gcode(code)
        widget._do_update()
        return widget

    def test_axes_are_labelled_z_across_and_x_up(self):
        widget = self.plotter(ARCS)

        self.assertIn("Z", widget.ax.get_xlabel())
        self.assertIn("X", widget.ax.get_ylabel())

    def test_a_straight_move_is_drawn_with_z_on_the_horizontal(self):
        widget = self.plotter("G00 X30. Z0.\nG01 X30. Z-40. F0.2")

        drawn = [ln for ln in widget.ax.lines if isinstance(ln, lines.Line2D)
                 and len(ln.get_xdata()) == 2]
        cut = [ln for ln in drawn if list(ln.get_ydata()) == [30.0, 30.0]]

        self.assertTrue(cut, "no segment drawn at a constant diameter of 30")
        self.assertEqual(list(cut[0].get_xdata()), [0.0, -40.0])

    def test_arcs_land_on_their_programmed_endpoints(self):
        widget = self.plotter(ARCS)
        machine = GCodeParser(chuck_z=-1000.0).parse(ARCS)["arc"]
        drawn = [p for p in widget.ax.patches if isinstance(p, patches.Arc)]

        self.assertEqual(len(drawn), len(machine))
        for arc, patch in zip(machine, drawn):
            verts = patch.get_patch_transform().transform(patch.get_path().vertices)
            ends = {self._round(verts[0]), self._round(verts[-1])}
            # Plot coordinates are (Z, X). matplotlib always sweeps
            # counter-clockwise, so a G03 comes out drawn end-to-start.
            wanted = {self._round((arc["start"][1], arc["start"][0])),
                      self._round((arc["end"][1], arc["end"][0]))}
            self.assertEqual(ends, wanted)

    def test_arcs_keep_the_span_they_have_in_machine_space(self):
        widget = self.plotter(ARCS)
        machine = GCodeParser(chuck_z=-1000.0).parse(ARCS)["arc"]
        drawn = [p for p in widget.ax.patches if isinstance(p, patches.Arc)]

        for arc, patch in zip(machine, drawn):
            self.assertAlmostEqual(self._span(patch), arc_sweep(arc), delta=0.5)

    def test_a_full_circle_still_covers_a_whole_turn(self):
        widget = self.plotter("G00 X50. Z-30.\nG02 I0. K-10.")
        drawn = [p for p in widget.ax.patches if isinstance(p, patches.Arc)]

        self.assertEqual(len(drawn), 1)
        self.assertAlmostEqual(self._span(drawn[0]), 360.0, delta=0.5)

    def test_the_chuck_stands_to_the_left_rather_than_lying_below(self):
        widget = self.plotter("G00 X30. Z0.\nG01 X30. Z-40. F0.2",
                              chuck_z=-100.0, chuck_diameter=200.0)
        hatched = [p for p in widget.ax.patches if p.get_hatch() == '//']

        self.assertEqual(len(hatched), 1)
        # get_extents() reports display pixels; the Rectangle's own accessors
        # are the data coordinates this is about.
        band = hatched[0]
        self.assertLessEqual(band.get_x() + band.get_width(), -100.0)  # left of the face
        self.assertAlmostEqual(band.get_y(), -200.0)                   # bounded by the jaws
        self.assertAlmostEqual(band.get_y() + band.get_height(), 200.0)
        self.assertGreater(band.get_height(), band.get_width())        # standing, not lying

    def test_the_view_fits_the_path_instead_of_the_chuck(self):
        # A short part with a distant chuck limit: forcing the chuck into view
        # is what squeezed the toolpath into a corner.
        widget = self.plotter("G00 X30. Z0.\nG01 X30. Z-40. F0.2", chuck_z=-500.0)

        self.assertGreater(widget.ax.get_xlim()[0], -100.0)

    @staticmethod
    def _round(point):
        return (round(float(point[0]), 4), round(float(point[1]), 4))

    @staticmethod
    def _span(patch):
        verts = patch.get_path().vertices
        angles = [math.degrees(math.atan2(y, x)) % 360 for x, y in verts]
        return sum((b - a) % 360 for a, b in zip(angles, angles[1:]))


class PlotViewTests(unittest.TestCase):
    """What the panel frames, and at what scale.

    X is a diameter, so a part drawn with equal units on both axes comes out
    twice as tall as it is. The panel has to be to scale, and has to stay so
    when it changes shape.
    """

    # The rapids run out to X100 Z50, the tool-change position; the cut is a
    # 22 mm stroke at diameter 30.
    PART = "G00 X100. Z50.\nG00 X30. Z2.\nG01 Z-20. F0.2\nG00 X100. Z50."

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def plotter(self, code, size=(900, 300), chuck_z=-1000.0):
        widget = GCodePlotterWidget(COLORS, chuck_z=chuck_z)
        self.addCleanup(widget.deleteLater)
        widget.resize(*size)
        # Shown, because an unshown widget never receives the resize event.
        widget.show()
        self.app.processEvents()
        widget.set_gcode(code)
        widget.canvas.draw()
        return widget

    @staticmethod
    def pixels_per_unit(widget):
        """Screen pixels per unit of Z, and per unit of X, as drawn."""
        box = widget.ax.get_window_extent()
        xlim, ylim = widget.ax.get_xlim(), widget.ax.get_ylim()
        return box.width / (xlim[1] - xlim[0]), box.height / (ylim[1] - ylim[0])

    @staticmethod
    def stock_patches(widget):
        return [p for p in widget.ax.patches if p.get_gid() == 'stock']

    def test_a_millimetre_of_radius_is_as_long_as_a_millimetre_of_z(self):
        per_z, per_x = self.pixels_per_unit(self.plotter(self.PART))

        # A unit of X is a diameter, so it is drawn half as long.
        self.assertAlmostEqual(per_x / per_z, 0.5, places=3)

    def test_the_scale_survives_the_panel_changing_shape(self):
        widget = self.plotter(self.PART, size=(900, 300))

        widget.resize(500, 450)
        self.app.processEvents()
        widget.canvas.draw()
        per_z, per_x = self.pixels_per_unit(widget)

        self.assertAlmostEqual(per_x / per_z, 0.5, places=3)

    def test_the_view_frames_the_cuts_and_not_the_tool_change_position(self):
        widget = self.plotter(self.PART)
        xlim, ylim = widget.ax.get_xlim(), widget.ax.get_ylim()

        # The cut is in view ...
        self.assertLessEqual(xlim[0], -20.0)
        self.assertGreaterEqual(xlim[1], 2.0)
        self.assertGreaterEqual(ylim[1], 30.0)
        # ... and the position the rapids run out to is not what sets the zoom.
        self.assertLess(xlim[1], 50.0)
        self.assertLess(ylim[1], 100.0)

    def test_a_small_edit_leaves_the_view_alone(self):
        widget = self.plotter(self.PART)
        before = (widget.ax.get_xlim(), widget.ax.get_ylim())

        widget.set_gcode(self.PART.replace("Z-20.", "Z-21."))
        widget.canvas.draw()

        self.assertEqual((widget.ax.get_xlim(), widget.ax.get_ylim()), before)

    def test_a_stock_comment_draws_the_blank(self):
        widget = self.plotter("(STOCK D40 L50)\n" + self.PART)

        (blank,) = self.stock_patches(widget)

        self.assertEqual((blank.get_x(), blank.get_y(),
                          blank.get_width(), blank.get_height()),
                         (-50.0, 0.0, 50.0, 40.0))

    def test_without_a_stock_comment_no_blank_is_drawn(self):
        self.assertEqual(self.stock_patches(self.plotter(self.PART)), [])

    def test_the_view_makes_room_for_the_stock(self):
        # A short cut on a long blank: the blank is what the part is made of.
        widget = self.plotter("(STOCK D40 L50)\nG00 X30. Z2.\nG01 Z-5. F0.2")

        self.assertLessEqual(widget.ax.get_xlim()[0], -50.0)
        self.assertGreaterEqual(widget.ax.get_ylim()[1], 40.0)

    def test_tool_changes_at_one_position_share_a_label(self):
        widget = self.plotter("G00 X100. Z50.\nT0505\nG00 X0. Z2.\n"
                              "G00 X100. Z50.\nT0101\nG01 X30. Z-5. F0.2")

        labels = [t.get_text().strip() for t in widget.ax.texts
                  if t.get_text().strip().startswith('T')]

        self.assertEqual(labels, ['T5, T1'])

    def test_the_cursor_marker_is_not_the_colour_of_a_collision(self):
        widget = self.plotter(self.PART)

        widget.highlight_line(3)

        self.assertEqual(widget.highlight_artist.get_color(), COLORS['WHITE'])

    def test_a_click_picks_the_nearest_point_as_it_is_drawn(self):
        widget = self.plotter("G00 X30. Z0.\nG01 X38. Z-5. F0.2")

        # From (30, -5) the end of line 1 is 5 mm away in Z. The end of line 2
        # is 8 away in diameter -- 4 mm of radius, so it is the nearer one on
        # screen even though its raw number is larger.
        self.assertEqual(widget._find_closest_line(30.0, -5.0), 2)


class PlotPlaybackTests(unittest.TestCase):
    """A cycle is one block that makes many moves; the playback shows them one by one."""

    # Line 4 is the cycle call. The blocks between P and Q (lines 5-8) only
    # describe the contour.
    CYCLE = "\n".join([
        "G00 X52. Z2.",
        "G01 Z0. F0.2",
        "G71 U1.0 R0.5",
        "G71 P100 Q200 U0.3 W0.1 F0.25",
        "N100 X20. F0.15",
        "Z-10.",
        "X50.",
        "N200 Z-30.",
        "G00 X100. Z50.",
    ])
    CALL = 4

    # The same, with the finishing cycle on line 9 and the retract after it.
    FINISHED = CYCLE.replace("G00 X100. Z50.", "G70 P100 Q200\nG00 X100. Z50.")
    FINISH_CALL = 9

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def plotter(self, chuck_z=-1000.0, code=None):
        widget = GCodePlotterWidget(COLORS, chuck_z=chuck_z)
        self.addCleanup(widget.deleteLater)
        widget.set_gcode(self.CYCLE if code is None else code)
        return widget

    def test_a_cycle_is_one_line_with_many_moves(self):
        widget = self.plotter()

        self.assertGreater(widget.steps_in_line(self.CALL), 1)
        self.assertEqual(widget.steps_in_line(1), 1)
        self.assertEqual(widget.steps_in_line(99), 0)

    def test_a_step_draws_that_many_moves_of_the_line_and_no_more(self):
        widget = self.plotter()
        total = widget.steps_in_line(self.CALL)

        widget.set_live_max_line(self.CALL - 1)
        before = len(widget.ax.lines)

        widget.set_live_step(self.CALL, 0)
        self.assertEqual(len(widget.ax.lines), before)
        widget.set_live_step(self.CALL, 1)
        self.assertEqual(len(widget.ax.lines), before + 1)
        widget.set_live_step(self.CALL, 2)
        self.assertEqual(len(widget.ax.lines), before + 2)

        # The last step is the whole line, which is what a line-by-line
        # playback shows for it.
        widget.set_live_step(self.CALL, total)
        self.assertEqual(len(widget.ax.lines), before + total)
        widget.set_live_max_line(self.CALL)
        self.assertEqual(len(widget.ax.lines), before + total)

    def test_step_end_is_where_the_move_ends(self):
        widget = self.plotter()
        total = widget.steps_in_line(self.CALL)

        self.assertEqual(widget.step_end(1, 1), (52.0, 2.0))
        # A cycle finishes where it started: at X52, Z0 after the G01.
        self.assertEqual(widget.step_end(self.CALL, total), (52.0, 0.0))
        self.assertIsNone(widget.step_end(self.CALL, 0))
        self.assertIsNone(widget.step_end(self.CALL, total + 1))

    def test_the_finishing_cycle_plays_back_move_by_move_too(self):
        widget = self.plotter(code=self.FINISHED)
        call = self.FINISH_CALL

        # Onto the contour, three cuts along it, and back to the start.
        self.assertEqual(widget.steps_in_line(call), 5)
        self.assertEqual(widget.step_end(call, 1), (20.0, 0.0))
        self.assertEqual(widget.step_end(call, 5), (52.0, 0.0))

        widget.set_live_max_line(call - 1)
        before = len(widget.ax.lines)
        widget.set_live_step(call, 2)
        self.assertEqual(len(widget.ax.lines), before + 2)
        widget.set_live_step(call, 5)
        self.assertEqual(len(widget.ax.lines), before + 5)

    def test_the_finishing_run_is_drawn_in_its_own_colour_over_the_roughing(self):
        # It lies a fraction of a millimetre from the roughed contour, so in the
        # roughing's green it would add nothing to the picture.
        widget = self.plotter(code=self.FINISHED)

        def finishing(w):
            # The G54 cross is cyan as well, but it is a single point.
            return [ln for ln in w.ax.lines
                    if ln.get_color() == COLORS['CYAN'] and len(ln.get_xdata()) == 2]

        run = finishing(widget)
        roughing = [ln for ln in widget.ax.lines if ln.get_color() == COLORS['CRT_GREEN']]

        self.assertEqual(len(run), 3)
        self.assertGreater(min(ln.get_zorder() for ln in run),
                           max(ln.get_zorder() for ln in roughing))

    def test_the_finishing_run_appears_with_its_moves(self):
        widget = self.plotter(code=self.FINISHED)
        call = self.FINISH_CALL

        def finishing():
            return [ln for ln in widget.ax.lines
                    if ln.get_color() == COLORS['CYAN'] and len(ln.get_xdata()) == 2]

        widget.set_live_step(call, 1)       # only the rapid onto the contour
        self.assertEqual(finishing(), [])
        widget.set_live_step(call, 2)       # the first cut
        self.assertEqual(len(finishing()), 1)
        widget.set_live_step(call, 5)
        self.assertEqual(len(finishing()), 3)

    def test_the_threading_cycle_plays_back_pass_by_pass_too(self):
        # No allowance and a first cut of the whole height: one roughing pass
        # and one finishing pass, four moves each.
        widget = self.plotter(code="G00 X24. Z2.\nG76 P010060 Q100 R0.\n"
                                   "G76 X17.4 Z-30. P1300 Q1300 F2.")
        call = 3

        self.assertEqual(widget.steps_in_line(call), 8)
        self.assertEqual(widget.step_end(call, 1), (17.4, 2.0))     # fed in
        self.assertEqual(widget.step_end(call, 2), (17.4, -30.0))   # cut
        self.assertEqual(widget.step_end(call, 8), (24.0, 2.0))     # back at the start

        widget.set_live_max_line(call - 1)
        before = len(widget.ax.lines)
        widget.set_live_step(call, 2)
        self.assertEqual(len(widget.ax.lines), before + 2)
        widget.set_live_step(call, 8)
        self.assertEqual(len(widget.ax.lines), before + 8)

    # Every pass of a thread is a straight line along Z, which in the colour of
    # a cut reads as turning, so the passes have a colour of their own.
    G76_THREAD = "G00 X24. Z2.\nG76 P010060 Q100 R0.\nG76 X17.4 Z-30. P1300 Q1300 F2."
    G92_THREAD = "G00 X22. Z2.\nG92 X19.4 Z-20. F1.5\nX19.1"

    @staticmethod
    def lines_in(widget, colour):
        """The two-point lines of one colour; markers and crosses are single points."""
        return [ln for ln in widget.ax.lines
                if ln.get_color() == colour and len(ln.get_xdata()) == 2]

    def test_g76_passes_are_drawn_in_their_own_colour_and_not_as_cuts(self):
        widget = self.plotter(code=self.G76_THREAD)

        # One roughing and one finishing pass: two cutting legs.
        self.assertEqual(len(self.lines_in(widget, COLORS['FANUC_YELLOW'])), 2)
        self.assertEqual(self.lines_in(widget, COLORS['CRT_GREEN']), [])

    def test_g92_passes_get_the_same_colour(self):
        widget = self.plotter(code=self.G92_THREAD)

        self.assertEqual(len(self.lines_in(widget, COLORS['FANUC_YELLOW'])), 2)
        self.assertEqual(self.lines_in(widget, COLORS['CRT_GREEN']), [])

    def test_a_thread_pass_appears_with_its_cut_and_not_with_its_feed_in(self):
        widget = self.plotter(code=self.G76_THREAD)
        call = 3
        widget.set_live_max_line(call - 1)

        def passes():
            return len(self.lines_in(widget, COLORS['FANUC_YELLOW']))

        widget.set_live_step(call, 1)      # feed in: a rapid
        self.assertEqual(passes(), 0)
        widget.set_live_step(call, 2)      # the cut of the first pass
        self.assertEqual(passes(), 1)
        widget.set_live_step(call, 5)      # the second pass's feed in
        self.assertEqual(passes(), 1)
        widget.set_live_step(call, 6)      # its cut
        self.assertEqual(passes(), 2)

    def test_a_thread_pass_is_drawn_above_an_ordinary_cut_and_below_a_finishing_run(self):
        widget = self.plotter(code=self.G76_THREAD)

        thread = self.lines_in(widget, COLORS['FANUC_YELLOW'])[0].get_zorder()

        self.assertGreater(thread, 2)       # a cut
        self.assertLess(thread, 2.5)        # a finishing run

    def test_the_finishing_cycle_adds_no_lines_to_skip(self):
        # The contour blocks were stepped past by the G71 above it, and only
        # by that.
        self.assertEqual(self.plotter(code=self.FINISHED).skipped_lines(),
                         frozenset({5, 6, 7, 8}))

    def test_the_contour_blocks_are_the_lines_a_playback_goes_past(self):
        self.assertEqual(self.plotter().skipped_lines(), frozenset({5, 6, 7, 8}))

    def test_the_cursor_ring_follows_the_move_being_drawn(self):
        widget = self.plotter()
        widget.highlight_line(self.CALL)

        widget.set_live_step(self.CALL, 3)

        x, z = widget.step_end(self.CALL, 3)
        ring = widget.highlight_artist
        self.assertEqual((ring.get_xdata()[0], ring.get_ydata()[0]), (z, x))

    def test_a_collision_appears_with_its_move_not_with_the_line(self):
        # The chuck at Z-20 is crossed by the later passes, not by the first.
        widget = self.plotter(chuck_z=-20.0)
        total = widget.steps_in_line(self.CALL)

        def red():
            return [ln for ln in widget.ax.lines if ln.get_color() == '#FF0000']

        widget.set_live_step(self.CALL, 1)
        self.assertEqual(red(), [])
        widget.set_live_step(self.CALL, total)
        self.assertTrue(red())
