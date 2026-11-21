"""What the position display shows for a block. GUI-free.

Speeds and feeds are worked out by hand: 1000 S / (pi D) under G96, capped by
G50 S; F times the speed under G99.
"""
import math
import unittest

from vibe_cnc.gcode_parser import GCodeParser
from vibe_cnc.readout import active_codes, feed_per_minute, readout, spindle_rpm

# What the parser records after N120 G96 S150 M03 of O0002, the shaft.
ROUGHING = {'motion': 'G01', 'plane': 'G18', 'units': 'G21', 'comp': 'G40',
            'feed_mode': 'G99', 'css': 'G96', 'wcs': 'G54', 'spindle': 'M03',
            'tool': 1, 's': 150, 's_max': 2500, 'f': 0.25}


class SpeedTests(unittest.TestCase):
    def test_constant_surface_speed_turns_faster_on_a_smaller_diameter(self):
        # 1000 * 150 / (pi * 24) = 1989.4
        self.assertAlmostEqual(spindle_rpm(ROUGHING, 24.0), 150000 / (math.pi * 24.0))
        self.assertGreater(spindle_rpm(ROUGHING, 20.0), spindle_rpm(ROUGHING, 24.0))

    def test_g50_caps_it(self):
        # On X10 it would be 4775 /min; G50 S2500 holds it at 2500.
        self.assertEqual(spindle_rpm(ROUGHING, 10.0), 2500.0)

    def test_on_the_centre_line_it_runs_at_the_cap(self):
        self.assertEqual(spindle_rpm(ROUGHING, 0.0), 2500.0)
        self.assertIsNone(spindle_rpm(dict(ROUGHING, s_max=0), 0.0))

    def test_g97_is_the_speed_programmed(self):
        self.assertEqual(spindle_rpm(dict(ROUGHING, css='G97', s=2000), 30.0), 2000.0)

    def test_a_stopped_spindle_has_no_speed(self):
        self.assertIsNone(spindle_rpm(dict(ROUGHING, spindle='M05'), 24.0))

    def test_feed_per_revolution_times_the_speed_is_the_feed_per_minute(self):
        self.assertAlmostEqual(feed_per_minute(ROUGHING, 2000.0), 500.0)
        self.assertEqual(feed_per_minute(dict(ROUGHING, feed_mode='G98', f=300.0), None), 300.0)
        self.assertIsNone(feed_per_minute(ROUGHING, None))


class ReadoutTests(unittest.TestCase):
    TOOL = {"t": 1, "name": "CNMG1204P-S External"}

    def test_the_display_during_a_roughing_pass(self):
        shown = readout(ROUGHING, 24.0, -10.0396, self.TOOL)

        self.assertEqual(shown['x'], "24.000")
        self.assertEqual(shown['z'], "−10.040")
        self.assertEqual((shown['tool'], shown['tool_name']), ("T01", "CNMG1204P-S External"))
        self.assertEqual(shown['speed'], "150 m/min")
        self.assertEqual(shown['speed_actual'], "SACT 1 989 /min")
        self.assertEqual(shown['feed'], "0.25 mm/rev")
        # 0.25 * 1989.4 = 497.4
        self.assertEqual(shown['feed_actual'], "ACT.F 497 mm/min")
        self.assertEqual(shown['codes'],
                         ['G01', 'G18', 'G21', 'G40', 'G99', 'G96', 'G54', 'M03'])

    def test_a_pass_of_a_cycle_cuts_at_feed_though_the_block_left_g00(self):
        # After N150 G71 P160 Q230 ... F0.25 the modal motion is the G00 of
        # the approach; the passes the cycle makes are cuts.
        shown = readout(dict(ROUGHING, motion='G00'), 24.0, -10.0, move='cut')
        self.assertEqual(shown['feed'], "0.25 mm/rev")
        self.assertEqual(shown['codes'][0], 'G01')
        # and its retracts are rapids
        self.assertEqual(readout(ROUGHING, 25.0, -19.4, move='rapid')['feed'], "rapid")

    def test_an_arc_keeps_its_own_code(self):
        self.assertEqual(readout(dict(ROUGHING, motion='G02'), 24.0, -20.0, move='arc')['codes'][0],
                         'G02')

    def test_a_rapid_says_so_rather_than_invent_a_speed(self):
        shown = readout(dict(ROUGHING, motion='G00'), 42.0, 2.0)
        self.assertEqual((shown['feed'], shown['feed_actual']), ("rapid", ""))

    def test_a_stopped_spindle_says_so(self):
        self.assertEqual(readout(dict(ROUGHING, spindle='M05'), 40.0, 0.0)['speed_actual'],
                         "stopped")

    def test_before_the_program_sets_anything_there_is_nothing_to_show(self):
        shown = readout(None, None, None)
        self.assertEqual({key: value for key, value in shown.items() if value}, {})

    def test_unset_groups_are_left_out(self):
        self.assertEqual(active_codes({'motion': 'G00', 'plane': None, 'units': 'G21'}),
                         ['G00', 'G21'])

    def test_the_parser_state_of_the_shaft_reads_as_expected(self):
        program = ("N10 G18 G40 G80 G97 G99\nN20 G21 G54\nN30 G50 S2500\n"
                   "N110 T0101\nN120 G96 S150 M03\nN130 G00 X42. Z2.\nN140 G01 Z-20. F0.25")
        modal = GCodeParser().parse(program)['modal']

        shown = readout(modal[7], 42.0, -10.0)
        self.assertEqual(shown['codes'], ['G01', 'G18', 'G21', 'G40', 'G99', 'G96', 'G54', 'M03'])
        # G50 S2500 is the cap, not a speed: the speed is the G96 S150.
        self.assertEqual(shown['speed'], "150 m/min")
        self.assertEqual(shown['speed_actual'], f"SACT {150000 / (math.pi * 42):,.0f} /min".replace(",", " "))


if __name__ == "__main__":
    unittest.main()
