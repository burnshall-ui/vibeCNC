# dro_panel.py — the position display above the simulation.
#
# Two rows, like the position screen of the control: the axes in large
# figures and the tool on the first; spindle, feed, the modal codes in force
# and the block on the second. What it says is worked out in readout.py; this
# only lays it out. A row across the top rather than a column beside the plot:
# the half section is bound by the panel's height and width alike, and a
# column cost the part a third of its size where a row costs a tenth.
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from .readout import readout

PANEL = '#14161A'
EDGE = '#25272C'
TEXT = '#E6EAF0'
MUTED = '#8A929E'
FAINT = '#5F6875'
CHIP = '#1D1F23'
CHIP_EDGE = '#33363C'
# The modal codes that move as the program runs are lit, the way the control
# shows the active motion: G00 to G03 and the cycles.
MOTION_CODES = ('G00', 'G01', 'G02', 'G03', 'G32', 'G90', 'G92', 'G94')


class DroPanel(QWidget):
    """X and Z, tool, spindle, feed, modal codes and block, for one moment."""

    def __init__(self, colors: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("Dro")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._accent = colors.get('FANUC_YELLOW', '#FFC800')
        self._modal = None
        self._tool = None
        self._x = None
        self._z = None
        self._move = None

        def label(name, text=''):
            widget = QLabel(text)
            widget.setObjectName(name)
            return widget

        self.x_value = label("DroValue")
        self.z_value = label("DroValue")
        for value in (self.x_value, self.z_value):
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.tool = label("DroTool")
        self.tool_name = label("DroSub")
        self.speed = label("DroItem")
        self.speed_actual = label("DroSub")
        self.feed = label("DroItem")
        self.feed_actual = label("DroSub")
        self.block = label("DroBlock")
        self.block.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._codes = QHBoxLayout()
        self._codes.setSpacing(4)
        self._chips = []

        top = QHBoxLayout()
        top.setSpacing(10)
        for axis, value in (("X", self.x_value), ("Z", self.z_value)):
            top.addWidget(label("DroAxis", axis))
            if axis == "X":
                top.addWidget(label("DroUnit", "Ø"))
            top.addWidget(value)
            top.addSpacing(14)
        # The block between the axes and the tool: the second row is full of
        # speeds and codes, and there the block was squeezed to nothing.
        top.addWidget(self.block, 1)
        top.addSpacing(10)
        top.addWidget(self.tool)
        top.addWidget(self.tool_name)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        for caption, value, actual in (("S", self.speed, self.speed_actual),
                                       ("F", self.feed, self.feed_actual)):
            bottom.addWidget(label("DroAxis", caption))
            bottom.addWidget(value)
            bottom.addWidget(actual)
            bottom.addSpacing(10)
        bottom.addLayout(self._codes)
        bottom.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(2)
        layout.addLayout(top)
        layout.addLayout(bottom)

        self.setStyleSheet(f"""
            QWidget#Dro {{ background:{PANEL}; border-bottom:1px solid {EDGE}; }}
            QLabel {{ background:transparent; }}
            QLabel#DroAxis {{ color:{MUTED}; font-weight:bold; }}
            QLabel#DroUnit {{ color:{FAINT}; }}
            QLabel#DroValue {{ color:{TEXT}; font-weight:bold; }}
            QLabel#DroTool {{ color:{TEXT}; font-weight:bold; }}
            QLabel#DroItem {{ color:{TEXT}; }}
            QLabel#DroSub {{ color:{MUTED}; }}
            QLabel#DroBlock {{ color:{MUTED}; }}
            QLabel#DroCode {{ color:{TEXT}; background:{CHIP}; border:1px solid {CHIP_EDGE};
                              border-radius:3px; padding:0px 4px; }}
            QLabel#DroCodeLit {{ color:{self._accent}; background:#2B2610;
                                 border:1px solid #6B5A12; border-radius:3px; padding:0px 4px; }}
        """)
        self.apply_scale(10)
        self.clear()

    def apply_scale(self, base_pt: float):
        """Font sizes from the window's base size: the axes large, the rest small."""
        for widget, factor in ((self.x_value, 2.0), (self.z_value, 2.0)):
            font = QFont(widget.font())
            font.setPointSizeF(base_pt * factor)
            font.setBold(True)
            widget.setFont(font)
        small = [self.tool, self.tool_name, self.speed, self.speed_actual, self.feed,
                 self.feed_actual, self.block] + self._chips
        small += [w for w in self.findChildren(QLabel)
                  if w.objectName() in ("DroAxis", "DroUnit")]
        for widget in small:
            font = QFont(widget.font())
            font.setPointSizeF(base_pt * 0.9)
            widget.setFont(font)
        self._base_pt = base_pt
        # Wide enough for -999.999 in the large figures, so they do not jump.
        width = self.x_value.fontMetrics().horizontalAdvance("−999.999")
        for value in (self.x_value, self.z_value):
            value.setMinimumWidth(width)

    def set_state(self, modal, tool=None):
        """The modal state of the block being run, and its tool's record."""
        self._modal = modal
        self._tool = tool
        self._refresh()

    def set_block(self, text: str):
        self.block.setText(text)

    def set_position(self, x, z, move=None):
        """Where the tool is, and the kind of move it is making. Under G96 the
        spindle speed follows the diameter."""
        self._x = x
        self._z = z
        self._move = move or None
        self._refresh()

    def clear(self):
        self._modal = None
        self._tool = None
        self._x = None
        self._z = None
        self._move = None
        self.block.setText('')
        self._refresh()

    def text(self) -> dict:
        """What is shown, as readout gives it -- for tests and the curious."""
        return readout(self._modal, self._x, self._z, self._tool, self._move)

    def _refresh(self):
        shown = self.text()
        self.x_value.setText(shown['x'] or '—')
        self.z_value.setText(shown['z'] or '—')
        self.tool.setText(shown['tool'])
        self.tool_name.setText(shown['tool_name'])
        self.speed.setText(shown['speed'] or '—')
        self.speed_actual.setText(shown['speed_actual'])
        self.feed.setText(shown['feed'] or '—')
        self.feed_actual.setText(shown['feed_actual'])
        self._set_codes(shown['codes'])

    def _set_codes(self, codes):
        if [chip.text() for chip in self._chips] == codes:
            return
        for chip in self._chips:
            # Out of the panel at once: a widget only marked for deletion
            # stays visible until the event loop gets to it, and one taken out
            # of a layout keeps its default size -- it covered the display.
            self._codes.removeWidget(chip)
            chip.hide()
            chip.setParent(None)
            chip.deleteLater()
        self._chips = []
        for code in codes:
            chip = QLabel(code)
            chip.setObjectName("DroCodeLit" if code in MOTION_CODES else "DroCode")
            font = QFont(chip.font())
            font.setPointSizeF(getattr(self, '_base_pt', 10) * 0.85)
            chip.setFont(font)
            self._codes.addWidget(chip)
            self._chips.append(chip)

    def codes(self) -> list:
        return [chip.text() for chip in self._chips]
