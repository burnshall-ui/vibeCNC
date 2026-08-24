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
