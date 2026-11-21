"""The position display above the simulation and the timeline below it."""
import unittest

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel

from vibe_cnc.dro_panel import DroPanel
from vibe_cnc.timeline import Timeline

COLORS = {'FANUC_YELLOW': '#FFC800'}
ROUGHING = {'motion': 'G00', 'plane': 'G18', 'units': 'G21', 'comp': 'G40',
            'feed_mode': 'G99', 'css': 'G96', 'wcs': 'G54', 'spindle': 'M03',
            'tool': 1, 's': 150, 's_max': 2500, 'f': 0.25}


class DroPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def panel(self):
        panel = DroPanel(COLORS)
        self.addCleanup(panel.deleteLater)
        return panel

    def test_it_shows_the_tool_where_it_is_and_what_it_does(self):
        panel = self.panel()
        panel.set_state(ROUGHING, {"name": "CNMG1204P-S External"})
        panel.set_block("N150 G71 P160 Q230 U0.4 W0.1 F0.25")

        panel.set_position(24.0, -10.04, 'cut')

        self.assertEqual((panel.x_value.text(), panel.z_value.text()), ("24.000", "−10.040"))
        self.assertEqual(panel.tool.text(), "T01")
        self.assertEqual(panel.feed.text(), "0.25 mm/rev")
        self.assertEqual(panel.codes(), ['G01', 'G18', 'G21', 'G40', 'G99', 'G96', 'G54', 'M03'])

    def test_the_motion_code_is_lit(self):
        panel = self.panel()
        panel.set_state(ROUGHING)
        panel.set_position(24.0, -10.0, 'cut')

        lit = [w.text() for w in panel.findChildren(QLabel, "DroCodeLit")]
        self.assertEqual(lit, ['G01'])

    def test_codes_that_change_leave_no_old_chip_behind(self):
        # An old chip only marked for deletion stayed visible, at Qt's default
        # size, over the whole display.
        panel = self.panel()
        panel.set_state(ROUGHING)
        panel.set_position(24.0, -10.0, 'cut')
        panel.set_position(25.0, -19.0, 'rapid')

        chips = [w for w in panel.findChildren(QLabel)
                 if w.objectName() in ("DroCode", "DroCodeLit")]
        self.assertEqual(sorted(w.text() for w in chips), sorted(panel.codes()))

    def test_clear_shows_dashes(self):
        panel = self.panel()
        panel.set_state(ROUGHING)
        panel.set_position(24.0, -10.0)

        panel.clear()

        self.assertEqual((panel.x_value.text(), panel.speed.text()), ("—", "—"))
        self.assertEqual(panel.codes(), [])


class TimelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def timeline(self):
        timeline = Timeline(COLORS)
        self.addCleanup(timeline.deleteLater)
        timeline.resize(408, 30)
        timeline.set_moves([1.0, 1.0, 0.5, 0.5], ['#3FA7FF'] * 4)
        return timeline

    def test_a_point_of_the_band_counts_the_move_it_lands_in_as_made(self):
        timeline = self.timeline()
        self.assertEqual([timeline.moves_at(p) for p in (0.0, 0.99, 1.0, 2.2, 2.6, 3.0)],
                         [1, 1, 2, 3, 4, 4])

    def test_the_playhead_glides_with_the_move(self):
        timeline = self.timeline()
        timeline.set_progress(2, 0.5)
        self.assertEqual(timeline.position(), 2.25)

    def test_a_click_on_the_band_seeks(self):
        timeline = self.timeline()
        timeline.show()
        seen = []
        timeline.seek.connect(seen.append)
        track = timeline.track
        band = track._band()

        # Weights 1, 1, 0.5, 0.5: 80 % along is 2.4, inside the third move;
        # 95 % is 2.85, inside the last.
        for share in (0.8, 0.95):
            x = int(band.left() + band.width() * share)
            QTest.mouseClick(track, Qt.MouseButton.LeftButton, pos=QPoint(x, track.height() // 2))

        self.assertEqual(seen, [3, 4])

    def test_the_speed_buttons_say_which_speed_is_on(self):
        timeline = self.timeline()
        seen = []
        timeline.speed_changed.connect(seen.append)

        timeline.buttons[2.0].click()

        self.assertEqual(seen, [2.0])
        self.assertEqual([s for s, b in timeline.buttons.items() if b.isChecked()], [2.0])


if __name__ == "__main__":
    unittest.main()
