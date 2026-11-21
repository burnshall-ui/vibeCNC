# tool_shape.py — what a tool looks like, from the ISO codes in tools.json,
# free of GUI dependencies.
#
# The simulation draws the insert and its holder where the tool is, and the
# stock model wants to know how far the insert clears the surface behind its
# tip. Neither is in the tool table as such, but most turning tools carry
# their ISO codes there:
#
#   holder  ISO 5608, e.g. PCLNR2525: clamping (P lever), insert shape (C),
#           holder style, which fixes the approach angle (L = 95 degrees),
#           clearance (N), hand (R), shank height and width (25 x 25).
#   insert  ISO 1832, the start of the tool's name, e.g. CNMG12...: shape
#           (C, an 80 degree rhombus), clearance, tolerance, type, then the
#           cutting edge length in mm (12).
#
# Only the letters whose angle is certain are listed. Anything else -- a
# facing style, a round insert, no codes at all -- gives no shape, and the
# simulation falls back on a ring for the tool and a square shoulder for the
# stock: drawing a guess would look like knowledge.
#
# Geometry is in millimetres, (dz, dr) from the programmed point: Z along the
# axis, r the radius (not the diameter). The programmed point is the
# imaginary tool nose, where the tangents along Z and along X to the nose
# radius meet; tool_data.NOSE_OFFSETS says on which side of it the insert
# lies, and that side is where everything here is drawn.
import math
import re
from dataclasses import dataclass

from .tool_data import NOSE_OFFSETS, nose_direction_of

# ISO 5608 holder styles for turning along Z, by approach angle (kappa r): the
# angle between the main cutting edge and the direction of feed.
APPROACH_DEG = {'A': 90.0, 'B': 75.0, 'D': 45.0, 'G': 90.0, 'J': 93.0,
                'L': 95.0, 'N': 62.5, 'S': 45.0, 'V': 72.5}
# ISO 1832 insert shapes by corner angle (epsilon r). Rhombi, the square and
# the triangle; the trigon W, round R and the parallelograms are left out.
CORNER_DEG = {'C': 80.0, 'D': 55.0, 'E': 75.0, 'M': 86.0, 'S': 90.0,
              'T': 60.0, 'V': 35.0}
TRIANGLES = ('T',)

DEFAULT_EDGE_MM = 12.0
DEFAULT_SHANK_MM = 20.0
# A drill's usual point, as stock_model assumes it.
DRILL_POINT_DEG = 118.0

_HOLDER = re.compile(r'(?:^|[-\s])([CMPSD])([A-Z])([A-Z])([A-Z])([RLN])(\d{2})?(\d{2})?')
_INSERT = re.compile(r'\b([A-Z])[A-Z]{3}(\d{2})')


def holder_code(text):
    """(clamping, shape, style, clearance, hand, shank) from an ISO 5608
    holder code, or None. shank is the height in mm, None when not given."""
    match = _HOLDER.search(str(text or '').upper())
    if not match:
        return None
    clamp, shape, style, clearance, hand, height, _width = match.groups()
    return clamp, shape, style, clearance, hand, (float(height) if height else None)


def insert_code(text):
    """(shape, edge length in mm) from the ISO 1832 code a name starts with, or None."""
    match = _INSERT.search(str(text or '').upper())
    if not match:
        return None
    return match.group(1), float(match.group(2))


@dataclass(frozen=True)
class TurningTool:
    """An insert in a holder, as far as the drawing and the stock need it."""
    corner_deg: float      # the insert's corner at the tip (epsilon r)
    approach_deg: float    # the holder's approach angle (kappa r)
    edge_mm: float         # cutting edge length
    nose_mm: float         # nose radius
    shank_mm: float        # shank height, drawn as its width
    out: int               # +1: the insert lies away from the centre line (OD)
    back: int              # +1: it lies towards +Z, the tool cuts towards the chuck
    triangle: bool = False

    @property
    def trailing_deg(self) -> float:
        """How far the edge behind the tip rises from the surface it leaves."""
        return 180.0 - self.approach_deg - self.corner_deg


def turning_tool(tool):
    """The TurningTool of one tools.json record, or None when the codes do
    not say enough."""
    if not tool:
        return None
    holder = holder_code(tool.get('holder'))
    insert = insert_code(tool.get('name'))
    if holder is None:
        return None
    _clamp, shape, style, _clearance, _hand, shank = holder
    corner = CORNER_DEG.get(shape)
    approach = APPROACH_DEG.get(style)
    if corner is None or approach is None or approach + corner > 180.0:
        return None
    out, back = NOSE_OFFSETS[nose_direction_of(tool)]
    if out == 0 or back == 0:
        return None
    edge = insert[1] if insert is not None and insert[0] == shape else DEFAULT_EDGE_MM
    try:
        nose = max(0.0, float(tool.get('insert_radius_mm') or 0.0))
    except (TypeError, ValueError):
        nose = 0.0
    return TurningTool(corner_deg=corner, approach_deg=approach, edge_mm=edge,
                       nose_mm=min(nose, edge / 4.0), shank_mm=shank or DEFAULT_SHANK_MM,
                       out=1 if out > 0 else -1, back=1 if back > 0 else -1,
                       triangle=shape in TRIANGLES)


def _unit(degrees):
    return math.cos(math.radians(degrees)), math.sin(math.radians(degrees))


def turning_outline(tool: TurningTool, arc_points=12) -> dict:
    """The insert, its screw hole and its holder, as (dz, dr) polygons.

    Drawn for the ordinary case -- the insert above and behind the tip, the
    tool cutting towards the chuck -- and mirrored into the tool's own
    quadrant at the end. The leading edge leans back from square by the
    approach angle; the trailing edge rises from the surface by the trailing
    angle; the nose radius is tangent to both axes through the tip.
    """
    lead = _unit(180.0 - tool.approach_deg)
    trail = _unit(tool.trailing_deg)
    rn = tool.nose_mm
    half = math.radians(tool.corner_deg / 2.0)
    centre = (rn, rn)
    bisector = (lead[0] + trail[0], lead[1] + trail[1])
    norm = math.hypot(*bisector)
    bisector = (bisector[0] / norm, bisector[1] / norm)
    corner = (centre[0] - bisector[0] * rn / math.sin(half),
              centre[1] - bisector[1] * rn / math.sin(half))

    # The nose: from the tangent on the leading edge round to the one on the
    # trailing edge, the outside way.
    start = math.degrees(math.atan2(lead[1], lead[0])) + 90.0
    stop = math.degrees(math.atan2(trail[1], trail[0])) - 90.0 + 360.0
    if rn > 0.0:
        nose = [(centre[0] + rn * math.cos(math.radians(a)),
                 centre[1] + rn * math.sin(math.radians(a)))
                for a in (start + (stop - start) * k / arc_points for k in range(arc_points + 1))]
    else:
        nose = [corner]

    length = tool.edge_mm
    on_lead = (corner[0] + lead[0] * length, corner[1] + lead[1] * length)
    on_trail = (corner[0] + trail[0] * length, corner[1] + trail[1] * length)
    if tool.triangle:
        insert = nose + [on_trail, on_lead]
        middle = ((corner[0] + on_lead[0] + on_trail[0]) / 3.0,
                  (corner[1] + on_lead[1] + on_trail[1]) / 3.0)
    else:
        far = (on_lead[0] + trail[0] * length, on_lead[1] + trail[1] * length)
        insert = nose + [on_trail, far, on_lead]
        middle = ((corner[0] + far[0]) / 2.0, (corner[1] + far[1]) / 2.0)

    # The holder: a head that seats the insert's back half, and a shank
    # running out square to the axis. Its edges stay clear of the cutting
    # edges by a millimetre.
    clear = 1.0
    head_lead = (corner[0] + lead[0] * 0.5 * length + clear, corner[1] + lead[1] * 0.5 * length)
    neck = (corner[0] + lead[0] * 1.25 * length + clear, corner[1] + lead[1] * 1.25 * length)
    head_trail = (corner[0] + trail[0] * 0.47 * length, corner[1] + trail[1] * 0.47 * length + clear)
    heel = (corner[0] + trail[0] * 1.2 * length, corner[1] + trail[1] * 1.2 * length + clear)
    shank = tool.shank_mm
    reach = 6.0 * shank
    if tool.out > 0:
        holder = [head_lead, head_trail, heel,
                  (neck[0] + shank, max(heel[1], neck[1] - shank) + 0.55 * length),
                  (neck[0] + shank, neck[1] + reach), (neck[0], neck[1] + reach), neck]
    else:
        # A boring bar runs out of the bore along the axis, not across it.
        bar = 0.8 * shank
        holder = [head_lead, head_trail, heel, (heel[0] + reach, heel[1]),
                  (heel[0] + reach, heel[1] + bar), (head_lead[0], heel[1] + bar)]

    def place(points):
        return [(tool.back * dz, tool.out * dr) for dz, dr in points]

    hole = (place([middle])[0], 0.2 * length)
    return {'insert': place(insert), 'holder': place(holder), 'hole': hole}


def drill_outline(diameter: float, length: float = None) -> list:
    """A twist drill entering from the front, its point at the programmed
    point: a (dz, dr) polygon across the centre line."""
    radius = diameter / 2.0
    point = radius / math.tan(math.radians(DRILL_POINT_DEG / 2.0))
    length = length if length is not None else max(8.0 * diameter, 40.0)
    return [(0.0, 0.0), (point, radius), (length, radius),
            (length, -radius), (point, -radius)]
