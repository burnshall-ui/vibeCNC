# timeline.py — the playback's progress under the simulation, to seek in.
#
# One band, each move of the program a stretch of it in the colour the
# simulation draws that move in, as long as the playback takes over it: a
# move of a cycle is quicker than a block of its own. What has been made is
# bright, what lies ahead faint, and a yellow playhead stands where the
# playback is, gliding with the tool. A click or a drag seeks; the speed
# buttons beside it scale the playback's tempo.
from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QHBoxLayout, QPushButton, QSizePolicy, QWidget

TRACK = '#0F1115'
TRACK_EDGE = '#25272C'
AHEAD_ALPHA = 70


class _Track(QWidget):
    def __init__(self, owner):
        super().__init__(owner)
        self._owner = owner
        self.setMinimumHeight(22)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _band(self) -> QRectF:
        return QRectF(4.0, self.height() / 2.0 - 5.0, self.width() - 8.0, 10.0)

    def paintEvent(self, _event):
        owner = self._owner
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        band = self._band()
        painter.setPen(QPen(QColor(TRACK_EDGE), 1))
        painter.setBrush(QColor(TRACK))
        painter.drawRoundedRect(band.adjusted(-2, -2, 2, 2), 6, 6)
        total = sum(owner.weights)
        if total <= 0:
            return
        scale = band.width() / total
        x = band.left()
        painter.setPen(Qt.PenStyle.NoPen)
        for i, (weight, colour) in enumerate(zip(owner.weights, owner.colours)):
            width = weight * scale
            fill = QColor(colour)
            if owner.made is not None and i >= owner.made:
                fill.setAlpha(AHEAD_ALPHA)
            painter.setBrush(fill)
            painter.drawRect(QRectF(x, band.top(), max(width, 0.5), band.height()))
            x += width
        if owner.made is None:
            return
        head = band.left() + owner.position() * scale
        painter.setPen(QPen(QColor(owner.accent), 2))
        painter.drawLine(int(head), int(band.top() - 4), int(head), int(band.bottom() + 4))
        painter.setBrush(QColor(owner.accent))
        painter.drawEllipse(QRectF(head - 5, band.center().y() - 5, 10, 10))

    def _seek_to(self, x):
        owner = self._owner
        total = sum(owner.weights)
        band = self._band()
        if total <= 0 or band.width() <= 0:
            return
        owner.seek.emit(owner.moves_at((x - band.left()) / band.width() * total))

    def mousePressEvent(self, event):
        self._seek_to(event.position().x())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._seek_to(event.position().x())


class Timeline(QWidget):
    """The band and the speed buttons."""

    seek = pyqtSignal(int)            # how many moves should have been made
    speed_changed = pyqtSignal(float)
    SPEEDS = (0.5, 1.0, 2.0, 5.0)

    def __init__(self, colors: dict, parent=None):
        super().__init__(parent)
        self.accent = colors.get('FANUC_YELLOW', '#FFC800')
        self.weights = []
        self.colours = []
        self.made = None        # None: no playback, the whole program bright
        self.gliding = 0.0      # how far the next move is on its way, 0 to 1
        self.speed = 1.0

        self.track = _Track(self)
        self.track.setToolTip("Click or drag to jump the simulation to that move")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)
        layout.addWidget(self.track, 1)
        self.buttons = {}
        for speed in self.SPEEDS:
            button = QPushButton(f"{speed:g}×")
            button.setObjectName("SpeedChip")
            button.setCheckable(True)
            button.setChecked(speed == self.speed)
            button.setToolTip(f"Play back at {speed:g} times the normal speed")
            button.clicked.connect(lambda _checked, s=speed: self.set_speed(s))
            layout.addWidget(button)
            self.buttons[speed] = button
        self.setStyleSheet(f"""
            QPushButton#SpeedChip {{ background:#1D1F23; color:#9AA3AF; border:1px solid #33363C;
                                     border-radius:4px; padding:2px 8px; }}
            QPushButton#SpeedChip:checked {{ background:#2B2610; color:{self.accent};
                                             border-color:#6B5A12; }}
        """)

    def set_moves(self, weights, colours):
        """The program's moves: how long each takes to play back, and its colour."""
        self.weights = [float(w) for w in weights]
        self.colours = list(colours)
        self.track.update()

    def set_progress(self, made, gliding=0.0):
        """Where the playback is: moves made, and how far the next has come."""
        self.made = made
        self.gliding = max(0.0, min(1.0, gliding))
        self.track.update()

    def clear_progress(self):
        self.made = None
        self.gliding = 0.0
        self.track.update()

    def set_speed(self, speed: float):
        self.speed = speed
        for value, button in self.buttons.items():
            button.setChecked(value == speed)
        self.speed_changed.emit(speed)

    def position(self) -> float:
        """The playhead, in the weights' units from the start."""
        made = self.made or 0
        done = sum(self.weights[:made])
        if made < len(self.weights):
            done += self.gliding * self.weights[made]
        return done

    def moves_at(self, position: float) -> int:
        """How many moves have been made at a point of the band: the move a
        click lands in counts as made."""
        run = 0.0
        for i, weight in enumerate(self.weights):
            run += weight
            if position < run:
                return i + 1
        return len(self.weights)
