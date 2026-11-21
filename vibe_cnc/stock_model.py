# stock_model.py — what is left of the blank after each move, free of GUI
# dependencies.
#
# The plotter drew the blank as it was before the first cut, all the way to
# M30. This works out what a program has cut away, so a playback can show the
# part taking shape. It decides what the operator sees, so it sits in the
# GUI-free suite with the parser.
#
# A turned part is the same all the way round, so it is described by one slice
# per cell along Z: material from an inner radius (the bore, 0 without one) out
# to an outer radius. That covers turning, facing, boring and drilling. A face
# groove leaves material on both sides of the tool and does not fit in it; a
# radial groove would, but the tool table gives no width to cut it with. Groove,
# parting and thread tools therefore remove nothing here. Their paths are still
# drawn, and so are the thread passes of G76 and G92, which would otherwise turn
# the thread down to its root diameter.
#
# A tool removes what its body passes through. Where the body is comes from the
# tip number in tools.json: it lies on the side of the programmed point that the
# nose radius centre is on (tool_data.NOSE_OFFSETS). Tip 2, the OD tool, reaches
# out and back from the tip; tip 3, the boring bar, in and back. Behind the
# tip the insert clears the finished surface by its trailing edge angle -- 5
# degrees for a CNMG in a 95 degree holder, more for a 35 degree VBMT -- which
# tool_shape works out from the ISO codes of the holder and the insert. Where
# the codes do not say, the body is the whole quadrant, a square shoulder
# behind the tip, and a move that runs down towards the chuck shows a gouge
# here sooner than it would on the machine.
#
# Rapids remove nothing. A rapid through material is a crash, and that is the
# collision check's business. This is a picture, not a check: the collision
# check does not read it, and a part that looks right here still has to be
# proven on the machine.
import bisect
import math
from dataclasses import dataclass

from .arc_geometry import sample_arc
from .tool_data import NOSE_OFFSETS, nose_direction_of
from .tool_shape import turning_tool

# A twist drill's usual point. tools.json has no point angle.
DRILL_POINT_DEG = 118.0
# A drill only drills on the centre line; a move further out than this (in
# diameter) is not drilling, and a lathe drill does not cut sideways.
ON_AXIS = 1e-6
# The finest slice, and how many slices at most: a 50 mm blank gets 2000 of
# 0.025 mm, a 1 m bar 2000 of 0.5 mm.
MIN_CELL = 0.02
MAX_CELLS = 2000
# Chords per arc, as the parser samples them for its own contour work.
ARC_STEPS = 24
# Tool types whose cut depends on a width or profile the table does not have.
UNMODELLED_TYPES = ('groove', 'parting', 'thread')
# The moves that cut. Rapids and thread passes do not, see above.
CUTTING_KINDS = ('cut', 'finish', 'arc')


@dataclass(frozen=True)
class Body:
    """Where a tool's body lies relative to its programmed point.

    kind is 'outer' for a body that reaches away from the centre line (an OD
    tool), 'inner' for one that reaches towards it (a boring bar), 'drill' for
    a drill on the centre line `radius` wide, and 'none' when it is not known --
    such a tool removes nothing here.

    towards is +1 when the body reaches back towards +Z, away from the chuck,
    which is the tool that cuts towards the chuck; -1 the other way round.

    trailing_deg is the edge behind the tip, measured up from the surface the
    tip leaves. 0 is a square shoulder.
    """
    kind: str
    towards: int = 1
    trailing_deg: float = 0.0
    radius: float = 0.0


NO_BODY = Body('none')


def body_for(tool) -> Body:
    """The body of one tool record from tools.json; NO_BODY for None."""
    if not tool:
        return NO_BODY
    kind = str(tool.get('type') or '').lower()
    if kind == 'drill':
        try:
            diameter = float(tool.get('d_mm'))
        except (TypeError, ValueError):
            return NO_BODY
        if not math.isfinite(diameter) or diameter <= 0:
            return NO_BODY
        return Body('drill', radius=diameter / 2.0)
    if kind.startswith(UNMODELLED_TYPES):
        return NO_BODY
    out, back = NOSE_OFFSETS[nose_direction_of(tool)]
    if out == 0 or back == 0:
        # 0 and 9 say nothing, 5 to 8 sit square to an axis: a tool pointing
        # straight at the part, whose cut is its width -- unknown here.
        return NO_BODY
    shape = turning_tool(tool)
    return Body('outer' if out > 0 else 'inner', towards=1 if back > 0 else -1,
                trailing_deg=shape.trailing_deg if shape is not None else 0.0)


class StockModel:
    """The blank, sliced along Z, and what is left of each slice.

    Built from the parser's `stock` entry. Each cell keeps an inner and an
    outer radius; a cell whose outer radius is not above its inner one is
    empty. Cut it with `cut`, read it with `at` and `outline`.
    """

    def __init__(self, stock: dict):
        self.diameter = float(stock['diameter'])
        length = float(stock['length'])
        self.z_front = float(stock.get('z_front', 0.0))
        self.z_back = self.z_front - length
        cells = max(1, min(MAX_CELLS, math.ceil(length / MIN_CELL)))
        self.cell = length / cells
        self.z = [self.z_back + (i + 0.5) * self.cell for i in range(cells)]
        self.outer = [self.diameter / 2.0] * cells
        self.inner = [0.0] * cells

    def cut(self, start, end, body: Body):
        """Removes what `body` sweeps through on a straight move.

        start and end are (X, Z) with X a diameter, as in the parsed paths.
        """
        if body.kind in ('outer', 'inner'):
            self._sweep(start, end, body)
        elif body.kind == 'drill':
            self._drill(start, end, body.radius)

    def _cells_from(self, back, towards):
        """The cells the body can reach from a tip at `back` (= towards * Z)."""
        if towards > 0:
            return range(bisect.bisect_left(self.z, back), len(self.z))
        return range(0, bisect.bisect_right(self.z, -back))

    def _sweep(self, start, end, body):
        """A quadrant body along a straight move.

        Worked in a frame where the body reaches towards +u (u = towards * Z),
        so it covers a cell at u from every point of the move at or before u.
        The edge it leaves there is the radius of such a point, moved by the
        trailing edge for every millimetre the cell lies behind it. That is
        linear along the move, so it is lowest (or, inside, highest) at one
        end of the part of the move that reaches the cell -- one of its two
        ends, or where it crosses u.
        """
        s = body.towards
        ra, ua = start[0] / 2.0, s * start[1]
        rb, ub = end[0] / 2.0, s * end[1]
        slope = math.tan(math.radians(body.trailing_deg))
        outer = body.kind == 'outer'

        for i in self._cells_from(min(ua, ub), s):
            u = s * self.z[i]
            reach = []
            if ua <= u:
                reach.append((ra, ua))
            if ub <= u:
                reach.append((rb, ub))
            if (ua - u) * (ub - u) < 0:
                reach.append((ra + (rb - ra) * (u - ua) / (ub - ua), u))
            if outer:
                edge = min(r + (u - up) * slope for r, up in reach)
                if edge < self.outer[i]:
                    self.outer[i] = edge
            else:
                edge = max(r - (u - up) * slope for r, up in reach)
                if edge > self.inner[i]:
                    self.inner[i] = edge

    def _drill(self, start, end, radius):
        """A drill entering from the front, its point at the deeper end."""
        if abs(start[0]) > ON_AXIS or abs(end[0]) > ON_AXIS:
            return
        tip = min(start[1], end[1])
        # Radius gained for every millimetre back from the point.
        rise = math.tan(math.radians(DRILL_POINT_DEG / 2.0))
        for i in range(bisect.bisect_left(self.z, tip), len(self.z)):
            r = min(radius, (self.z[i] - tip) * rise)
            if r > self.inner[i]:
                self.inner[i] = r

    def _solid(self, i) -> bool:
        return self.outer[i] > self.inner[i] + 1e-9

    def at(self, z):
        """(inner, outer) diameter of the slice at z, or None where there is none.

        None both outside the blank and where everything has been cut away.
        """
        if not self.z_back <= z <= self.z_front:
            return None
        i = min(int((z - self.z_back) / self.cell), len(self.z) - 1)
        if not self._solid(i):
            return None
        return 2.0 * self.inner[i], 2.0 * self.outer[i]

    def pieces(self) -> list:
        """(first, stop) slice ranges that still have material, back to front.

        One per stretch of Z the cuts have not gone right through.
        """
        found = []
        i, cells = 0, len(self.z)
        while i < cells:
            if not self._solid(i):
                i += 1
                continue
            j = i
            while j < cells and self._solid(j):
                j += 1
            found.append((i, j))
            i = j
        return found

    def outline(self) -> list:
        """What is left, as closed outlines of (X, Z) points with X a diameter.

        One outline per piece. Each runs along the outside from the back to
        the front and returns along the bore, with the slices as flat steps,
        so a shoulder stays square.
        """
        return [self.edge('outer', first, stop) + self.edge('inner', first, stop)[::-1]
                for first, stop in self.pieces()]

    def _boundary(self, i):
        """Z where slice i begins. Worked out from the index each time, and
        the last one is the face itself, so the ends come out exact."""
        if i >= len(self.z):
            return self.z_front
        return self.z_back + i * self.cell

    def edge(self, side, first, stop):
        """The 'outer' or 'inner' edge over slices first..stop-1, back to
        front, as (X, Z) points with X a diameter."""
        radii = self.outer if side == 'outer' else self.inner
        boundary = self._boundary
        points = []
        for i in range(first, stop):
            x = 2.0 * radii[i]
            if points and abs(points[-1][0] - x) < 1e-9:
                points[-1] = (x, boundary(i + 1))
            else:
                points.append((x, boundary(i)))
                points.append((x, boundary(i + 1)))
        return points


def tool_at(line, changes):
    """The tool in the spindle on `line`: the last change at or before it.

    changes is (line, tool) pairs sorted by line, from the parser's
    `tool_changes`. 0 before the first change.
    """
    tool = 0
    for change_line, number in changes:
        if change_line > line:
            break
        tool = number
    return tool


class StockPlayback:
    """The stock after the first n moves of a parsed program.

    n counts entries of the parser's `order`, the moves as the control runs
    them. A playback asks for n, n + 1, ... and gets the model brought forward
    a move at a time. Asking for fewer moves than last time starts again from
    the blank: a cut cannot be put back.

    The model handed out is the one this keeps cutting; read it before asking
    for another n.
    """

    def __init__(self, paths: dict, tools: dict):
        self.stock = paths.get('stock')
        changes = sorted((change['line'], change['tool'])
                         for change in paths.get('tool_changes', []))
        self._moves = []
        for kind, index, line in paths.get('order', []):
            self._moves.append(self._move(paths, kind, index, line,
                                          body_for(tools.get(tool_at(line, changes)))))
        self._model = None
        self._applied = 0

    @staticmethod
    def _move(paths, kind, index, line, body):
        """(body, points) of one move, or None for a move that cuts nothing."""
        if kind not in CUTTING_KINDS or body.kind == 'none':
            return None
        if kind == 'arc':
            return body, sample_arc(paths['arc'][index], ARC_STEPS)
        start, end, _line = paths[kind][index]
        return body, [start, end]

    def __len__(self):
        return len(self._moves)

    def at(self, count=None):
        """The model after the first `count` moves; all of them for None.

        None when the program names no stock.
        """
        if self.stock is None:
            return None
        count = len(self._moves) if count is None else max(0, min(count, len(self._moves)))
        if self._model is None or count < self._applied:
            self._model = StockModel(self.stock)
            self._applied = 0
        for move in self._moves[self._applied:count]:
            if move is not None:
                body, points = move
                for start, end in zip(points, points[1:]):
                    self._model.cut(start, end, body)
        self._applied = count
        return self._model
