"""Chuck collision detection (VC-09).

The chuck is the region behind the chuck face that is no wider than the jaws:
z < chuck_z and |X| < chuck_diameter. The old check looked only at whether
either endpoint of a segment sat behind the face, so it reported a rapid that
passes well clear of the jaws and stayed quiet on an arc that bulges into them.

The geometry is exercised through _hits_chuck directly. Driving it through
whole programs would mean every case also carries the approach move from the
origin, which crosses the chuck itself and drowns out what is being tested.
"""
import unittest

from vibe_cnc.gcode_parser import GCodeParser

CHUCK_Z = -45.0
CHUCK_DIAMETER = 200.0


def parser(diameter=CHUCK_DIAMETER):
    return GCodeParser(chuck_z=CHUCK_Z, chuck_diameter=diameter)


class ChuckGeometryTests(unittest.TestCase):
    def setUp(self):
        self.p = parser()

    def hits(self, start, end):
        return self.p._hits_chuck(start[0], start[1], end[0], end[1])

    def test_move_through_the_jaws_is_a_collision(self):
        self.assertTrue(self.hits((50.0, -20.0), (50.0, -60.0)))

    def test_move_behind_the_face_but_clear_of_the_jaws_is_not(self):
        # Ø300 passes over a Ø200 chuck. The old check called this a crash.
        self.assertFalse(self.hits((300.0, -50.0), (300.0, -60.0)))

    def test_grazing_the_jaw_diameter_is_not_a_collision(self):
        self.assertFalse(self.hits((200.0, -50.0), (200.0, -60.0)))

    def test_just_inside_the_jaw_diameter_is(self):
        self.assertTrue(self.hits((199.9, -50.0), (199.9, -60.0)))

    def test_running_along_the_chuck_face_is_not_a_collision(self):
        self.assertFalse(self.hits((50.0, CHUCK_Z), (300.0, CHUCK_Z)))

    def test_diagonal_from_clear_into_the_jaws_is(self):
        self.assertTrue(self.hits((300.0, -20.0), (50.0, -60.0)))

    def test_a_move_entirely_in_front_of_the_face_is_not(self):
        self.assertFalse(self.hits((50.0, -10.0), (50.0, -20.0)))

    def test_the_mirrored_half_of_the_plot_counts_too(self):
        # X is a diameter and the plot mirrors about the centre line.
        self.assertTrue(self.hits((-50.0, -50.0), (-50.0, -60.0)))

    def test_a_move_crossing_the_jaws_sideways_is_caught(self):
        # Both endpoints clear of the jaw diameter, the path between them not.
        self.assertTrue(self.hits((-300.0, -60.0), (300.0, -60.0)))


class UnconfiguredDiameterTests(unittest.TestCase):
    """No diameter measured yet: err towards a false alarm, not a missed crash."""

    def test_every_diameter_counts_when_none_is_configured(self):
        p = parser(diameter=None)

        self.assertTrue(p._hits_chuck(300.0, -50.0, 300.0, -60.0))

    def test_zero_is_treated_as_unconfigured(self):
        self.assertIsNone(parser(diameter=0.0).chuck_diameter)

    def test_a_move_in_front_is_still_clear(self):
        p = parser(diameter=None)

        self.assertFalse(p._hits_chuck(300.0, -10.0, 300.0, -20.0))


class ArcCollisionTests(unittest.TestCase):
    """An arc bulges away from its chord, so the chord alone can miss a crash."""

    def test_arc_bulging_behind_the_face_is_caught(self):
        # Half circle, centre Ø50/Z-40, r10: both ends sit at Z-40, in front of
        # the face at Z-45, but the arc reaches Z-50.
        paths = parser().parse("G00 X30. Z-40.\nG03 X70. Z-40. I10. K0.")

        self.assertEqual(len(paths["arc"]), 1)
        self.assertEqual(len(paths["collisions"]), 1)
        self.assertEqual(paths["collisions"][0][2], 2)   # the arc's line

    def test_endpoints_alone_would_not_have_seen_it(self):
        paths = parser().parse("G00 X30. Z-40.\nG03 X70. Z-40. I10. K0.")
        arc = paths["arc"][0]

        for (_x, z) in (arc["start"], arc["end"]):
            self.assertGreater(z, CHUCK_Z)

    def test_arc_bulging_the_other_way_is_clear(self):
        paths = parser().parse("G00 X30. Z-40.\nG02 X70. Z-40. I10. K0.")

        self.assertEqual(len(paths["arc"]), 1)
        self.assertEqual(paths["collisions"], [])

    def test_arc_clear_of_the_jaw_diameter_is_not_reported(self):
        # Same shape, moved out to Ø600 where the jaws cannot reach.
        paths = parser().parse("G00 X580. Z-40.\nG03 X620. Z-40. I10. K0.")

        self.assertEqual(len(paths["arc"]), 1)
        self.assertEqual(paths["collisions"], [])


class ProgramLevelTests(unittest.TestCase):
    def test_a_program_that_stays_clear_reports_nothing(self):
        paths = parser().parse("\n".join([
            "G00 X300. Z10.",      # approach clear of the face
            "G00 X300. Z-60.",     # down the outside, past the jaws
            "G00 X250. Z-60.",     # still clear of Ø200
            "G00 X300. Z10.",
        ]))

        self.assertEqual(paths["collisions"], [])

    def test_the_offending_block_is_the_one_reported(self):
        paths = parser().parse("\n".join([
            "G00 X300. Z10.",
            "G00 X300. Z-60.",
            "G00 X100. Z-60.",     # into the jaws
        ]))

        self.assertEqual(len(paths["collisions"]), 1)
        self.assertEqual(paths["collisions"][0][2], 3)
