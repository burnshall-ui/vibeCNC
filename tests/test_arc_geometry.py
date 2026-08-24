"""Arc direction (VC-02).

matplotlib is not installed for the GUI-free CI job, so these tests model its
rule rather than calling it: Arc sweeps counter-clockwise from theta1 to theta2
after normalising both modulo 360, which makes the drawn span exactly
(theta2 - theta1) % 360. tests/test_arc_rendering.py pins that model against
the real matplotlib.
"""
import os
import unittest

from vibe_cnc.arc_geometry import arc_thetas
from vibe_cnc.gcode_parser import GCodeParser


def swept(arc):
    """Degrees matplotlib will actually sweep for this arc."""
    theta1, theta2 = arc_thetas(arc)
    return (theta2 - theta1) % 360


def quarter(cw):
    """Quarter circle, centre Ø30/Z-5, from (Ø30, Z0) to (Ø40, Z-5)."""
    return {'start': (30.0, 0.0), 'end': (40.0, -5.0), 'center': (30.0, -5.0),
            'radius': 5.0, 'cw': cw, 'line': 1}


class ArcDirectionTests(unittest.TestCase):
    def test_g02_quarter_circle_stays_a_quarter_circle(self):
        self.assertAlmostEqual(swept(quarter(cw=True)), 90.0, places=9)

    def test_g03_over_the_same_endpoints_is_the_three_quarter_arc(self):
        # Same two points, opposite direction — the rest of the circle.
        self.assertAlmostEqual(swept(quarter(cw=False)), 270.0, places=9)

    def test_g03_quarter_circle_stays_a_quarter_circle(self):
        arc = {'start': (40.0, -5.0), 'end': (30.0, 0.0), 'center': (30.0, -5.0),
               'radius': 5.0, 'cw': False, 'line': 1}
        self.assertAlmostEqual(swept(arc), 90.0, places=9)

    def test_direction_only_swaps_the_endpoints(self):
        cw = arc_thetas(quarter(cw=True))
        ccw = arc_thetas(quarter(cw=False))
        self.assertEqual(cw, tuple(reversed(ccw)))


class ArcDirectionFromProgramTests(unittest.TestCase):
    """The same check driven through the parser, in real G-code."""

    def test_programmed_quarter_circles_come_out_as_quarter_circles(self):
        paths = GCodeParser().parse("\n".join([
            "G00 X30. Z0.",
            "G02 X40. Z-5. R5.",   # outside corner radius, clockwise
            "G03 X50. Z-10. R5.",  # inside corner radius, counter-clockwise
        ]))

        self.assertEqual(len(paths["arc"]), 2)
        for arc in paths["arc"]:
            self.assertAlmostEqual(swept(arc), 90.0, places=6)


class NoSecondCopyTests(unittest.TestCase):
    """VC-02 was four copies of the same wrong formula. Keep it at one."""

    SOURCE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "vibe_cnc", "gcode_plotter.py")

    def test_plotter_builds_arcs_in_exactly_one_place(self):
        with open(self.SOURCE, "r", encoding="utf-8") as handle:
            source = handle.read()

        self.assertEqual(source.count("patches.Arc("), 1)
        self.assertNotIn("atan2", source)
        self.assertEqual(source.count("self._draw_arc(arc"), 4)
