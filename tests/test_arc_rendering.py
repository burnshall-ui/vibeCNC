"""Arc direction against real matplotlib (VC-02).

tests/test_arc_geometry.py models matplotlib's rule as (theta2 - theta1) % 360
so it can run on a bare interpreter. This file checks that the model matches
what matplotlib actually draws, by measuring the patch it produces. It needs
matplotlib and therefore runs in the GUI-backed CI job.
"""
import math
import unittest

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.patches as patches
except ImportError:  # pragma: no cover - covered by the GUI-free suite instead
    patches = None

from vibe_cnc.arc_geometry import arc_sweep, arc_thetas


def drawn_span(theta1, theta2):
    """Degrees actually swept by the patch, measured from its own vertices."""
    path = patches.Arc((0, 0), 2, 2, angle=0, theta1=theta1, theta2=theta2).get_path()
    angles = [math.degrees(math.atan2(y, x)) % 360 for x, y in path.vertices]
    return sum((b - a) % 360 for a, b in zip(angles, angles[1:]))


@unittest.skipIf(patches is None, "matplotlib not installed")
class ArcRenderingTests(unittest.TestCase):
    QUARTER = {'start': (30.0, 0.0), 'end': (40.0, -5.0), 'center': (30.0, -5.0),
               'radius': 5.0, 'line': 1}

    def test_g03_quarter_circle_is_drawn_as_a_quarter_circle(self):
        # Start due right of the centre, end due above it: a quarter turn
        # counter-clockwise in the picture the control draws.
        theta1, theta2 = arc_thetas(dict(self.QUARTER, cw=False))

        self.assertAlmostEqual(drawn_span(theta1, theta2), 90.0, delta=0.5)

    def test_g02_over_the_same_endpoints_is_drawn_as_the_rest_of_the_circle(self):
        theta1, theta2 = arc_thetas(dict(self.QUARTER, cw=True))

        self.assertAlmostEqual(drawn_span(theta1, theta2), 270.0, delta=0.5)

    def test_a_full_circle_is_drawn_as_a_full_circle(self):
        circle = {'start': (50.0, -10.0), 'end': (50.0, -10.0),
                  'center': (50.0, -20.0), 'radius': 10.0, 'cw': True, 'line': 1}
        theta1, theta2 = arc_thetas(circle)

        self.assertAlmostEqual(drawn_span(theta1, theta2), 360.0, delta=0.5)

    def test_arc_sweep_matches_what_matplotlib_draws(self):
        circle = {'start': (50.0, -10.0), 'end': (50.0, -10.0),
                  'center': (50.0, -20.0), 'radius': 10.0, 'cw': True, 'line': 1}

        for name, arc in (("quarter cw", dict(self.QUARTER, cw=True)),
                          ("quarter ccw", dict(self.QUARTER, cw=False)),
                          ("full circle", circle)):
            theta1, theta2 = arc_thetas(arc)
            with self.subTest(arc=name):
                self.assertAlmostEqual(drawn_span(theta1, theta2),
                                       arc_sweep(arc), delta=0.5)
