# gcode_parser.py — Fanuc lathe G-code parsing, free of GUI dependencies.
#
# Kept separate from gcode_plotter so the parser can be imported — and tested —
# without PyQt6 or matplotlib present.
import re
import math
from typing import List

# G-codes whose X/Z/U/W/R words are dwell times, offsets or cycle parameters
# rather than a motion target. Reading them as a move draws a line that was
# never programmed — `G04 X1.0` is the worst of them, it aims the tool at Ø1 mm
# straight through the part. The canned cycles G70-G76 are in the same set:
# their U/W are stock allowances and their R is a retract, not an increment.
NON_MOTION_CODES = frozenset({4, 10, 28, 30, 50, 65, 70, 71, 72, 73, 74, 75, 76, 92})

# Cycles recorded in self.cycle rather than self.mode. They are one-shot calls,
# not motion modes — leaving them modal made every following block invisible.
CYCLE_CODES = frozenset({70, 71, 72, 73, 74, 75, 76})


class GCodeParser:
    """Parser for Fanuc lathe G-code (X=diameter, Z=length)"""

    def __init__(self, chuck_z: float = -5.0):
        self.chuck_z = chuck_z  # Chuck limit
        # Load tool data once
        try:
            from .tool_model import load_tools_json
            j = load_tools_json()
            self.tool_items = {int(it.get('t', 0)): it for it in j.get('tool_table', [])}
        except Exception:
            self.tool_items = {}
        self.reset()

    def reset(self):
        self.x = 0.0  # Diameter
        self.z = 0.0  # Longitudinal axis
        self.tool = 0
        self.s = 0    # Spindle speed
        self.s_max = 0  # Spindle speed *limit* from G50 S.. (constant surface speed cap)
        self.f = 0.0  # Feed rate
        self.mode = 'G00'  # G00=rapid, G01=cut, G02=CW-arc, G03=CCW-arc
        self.cycle = None  # Last canned cycle seen (G70-G76), informational
        self.i = 0.0  # Arc parameter (X-offset center)
        self.k = 0.0  # Arc parameter (Z-offset center)
        # Tool Nose Radius Compensation (G40/G41/G42)
        self.comp = 'G40'   # current: G40=off, G41=left, G42=right (relative to movement direction)
        self.tnr = 0.0      # Corner radius (mm)
        self.warnings: List[dict] = []  # Geometry the parser could not make sense of
        self.paths = {
            'rapid': [],       # G00 (gray dashed)
            'cut': [],         # G01 (green solid)
            'arc': [],         # G02/G03 (green arc)
            'tool_changes': [], # Tool changes
            'collisions': [],   # Collisions (red)
            'comp_cut': [],     # compensated path (G41/G42), yellow
            'comp_arc': []      # compensated arcs (G41/G42), yellow
        }

    def parse(self, gcode: str) -> dict:
        """Parses G-code and extracts toolpaths"""
        self.reset()

        lines = gcode.split('\n')
        for line_num, line in enumerate(lines, 1):
            self._parse_line(line, line_num)

        # Post-processing: intersect corners (Lookahead Corner Handling)
        self._intersect_compensated_corners()

        return self.paths

    def _warn(self, line_num: int, code: str, message: str):
        """Records a geometry problem instead of silently drawing nothing."""
        self.warnings.append({'line': line_num, 'code': code, 'message': message})

    def _parse_line(self, line: str, line_num: int):
        """Parses a single G-code line"""
        # Remove comments
        line = re.sub(r'\(.*?\)', '', line)
        line = re.sub(r';.*', '', line).strip()
        if not line or line.startswith('%'):
            return

        # Extract modal codes. Compared as integers so that G1 and G01 — both
        # legal — cannot drift apart.
        g_codes = [int(g) for g in re.findall(r'G(\d+)', line, re.IGNORECASE)]
        for g in g_codes:
            if g == 0:
                self.mode = 'G00'
            elif g == 1:
                self.mode = 'G01'
            elif g == 2:
                self.mode = 'G02'
            elif g == 3:
                self.mode = 'G03'
            elif g == 40:
                self.comp = 'G40'
            elif g == 41:
                self.comp = 'G41'
            elif g == 42:
                self.comp = 'G42'
            elif g in CYCLE_CODES:
                # A cycle call, not a motion mode: the previously active G00/G01
                # stays in force for the blocks that follow (Fanuc behaviour).
                self.cycle = f'G{g:02d}'

        # Tool changes
        t_match = re.search(r'T(\d+)', line, re.IGNORECASE)
        if t_match:
            self.tool = int(t_match.group(1)) // 100  # T0101 -> T1
            self.paths['tool_changes'].append({
                'x': self.x,
                'z': self.z,
                'tool': self.tool,
                'line': line_num
            })
            # Load corner radius from cached tools
            tool_info = self.tool_items.get(self.tool, {})
            self.tnr = float(tool_info.get('insert_radius_mm', 0.0) or 0.0)

        # Feed is meaningful on every block, cycle definitions included.
        f_match = re.search(r'F([-+]?\d*\.?\d+)', line, re.IGNORECASE)
        if f_match:
            self.f = float(f_match.group(1))

        s_match = re.search(r'S(\d+)', line, re.IGNORECASE)
        if s_match:
            if 50 in g_codes:
                # G50 S3000 caps the spindle for G96. It is not a speed command.
                self.s_max = int(s_match.group(1))
            else:
                self.s = int(s_match.group(1))

        if any(g in NON_MOTION_CODES for g in g_codes):
            # Position, mode and paths stay untouched — see NON_MOTION_CODES.
            return

        # Extract coordinates. X/Z are absolute, U/W the matching incremental
        # words; U is a diameter increment just like X.
        x_match = re.search(r'X([-+]?\d*\.?\d+)', line, re.IGNORECASE)
        z_match = re.search(r'Z([-+]?\d*\.?\d+)', line, re.IGNORECASE)
        u_match = re.search(r'U([-+]?\d*\.?\d+)', line, re.IGNORECASE)
        w_match = re.search(r'W([-+]?\d*\.?\d+)', line, re.IGNORECASE)
        i_match = re.search(r'I([-+]?\d*\.?\d+)', line, re.IGNORECASE)
        k_match = re.search(r'K([-+]?\d*\.?\d+)', line, re.IGNORECASE)
        r_match = re.search(r'R([-+]?\d*\.?\d+)', line, re.IGNORECASE)

        # Absolute wins over incremental when a block carries both, and the two
        # axes are resolved independently so `G01 X30. W-5.` works.
        if x_match:
            new_x = float(x_match.group(1))
        elif u_match:
            new_x = self.x + float(u_match.group(1))
        else:
            new_x = self.x

        if z_match:
            new_z = float(z_match.group(1))
        elif w_match:
            new_z = self.z + float(w_match.group(1))
        else:
            new_z = self.z

        self.i = float(i_match.group(1)) if i_match else 0.0
        self.k = float(k_match.group(1)) if k_match else 0.0

        # Record movement (if position changes)
        if new_x != self.x or new_z != self.z:
            segment_data = [(self.x, self.z), (new_x, new_z), line_num]

            # Collision check (Z < Chuck limit)
            if new_z < self.chuck_z or self.z < self.chuck_z:
                self.paths['collisions'].append(segment_data)

            if self.mode == 'G00':
                self.paths['rapid'].append(segment_data)
            elif self.mode == 'G01':
                self.paths['cut'].append(segment_data)
                # Compensated path (simplified TNR compensation only for linear moves)
                if self.comp in ('G41', 'G42') and self.tnr > 0.0:
                    (x1, z1), (x2, z2), _ = segment_data
                    # Conversion to radius for geometric calculations
                    r1 = x1 / 2.0
                    r2 = x2 / 2.0
                    dr = r2 - r1
                    dz = z2 - z1
                    seg_len = math.hypot(dr, dz)
                    if seg_len > 1e-6:
                        # Left normal/right normal relative to movement direction
                        nr_left = -dz / seg_len
                        nz_left = dr / seg_len
                        if self.comp == 'G41':
                            offr, offz = nr_left * self.tnr, nz_left * self.tnr
                        else:  # G42
                            offr, offz = -nr_left * self.tnr, -nz_left * self.tnr
                        cr1, cz1 = r1 + offr, z1 + offz
                        cr2, cz2 = r2 + offr, z2 + offz
                        # Back to diameter
                        cx1 = cr1 * 2.0
                        cx2 = cr2 * 2.0
                        self.paths['comp_cut'].append([(cx1, cz1), (cx2, cz2), line_num])
            elif self.mode in ['G02', 'G03']:
                self._add_arc(new_x, new_z, i_match, k_match, r_match, line_num)

            self.x = new_x
            self.z = new_z

    def _add_arc(self, new_x, new_z, i_match, k_match, r_match, line_num):
        """Appends one G02/G03 arc, from I/K or from an R word."""
        # Everything below is radius space: X and I are diameter values.
        r1 = self.x / 2.0
        r2 = new_x / 2.0
        z1 = self.z
        z2 = new_z
        clockwise = (self.mode == 'G02')

        if i_match is not None or k_match is not None:
            # I/K wins over R when a block carries both (Fanuc behaviour).
            cr = r1 + self.i
            cz = z1 + self.k
            radius = math.hypot(self.i, self.k)
        elif r_match is not None:
            centre = self._center_from_r(r1, z1, r2, z2,
                                         float(r_match.group(1)), clockwise, line_num)
            if centre is None:
                return
            cr, cz, radius = centre
        else:
            self._warn(line_num, 'ARC_NO_CENTER',
                       'G02/G03 without I/K and without R — no arc can be drawn')
            return

        arc_data = {
            'start': (self.x, self.z),
            'end': (new_x, new_z),
            'center': (cr * 2.0, cz),  # Store center as diameter
            'radius': radius,
            'cw': clockwise,
            'line': line_num
        }
        self.paths['arc'].append(arc_data)

        # Compensated arcs (G41/G42)
        if self.comp in ('G41', 'G42') and self.tnr > 0.0:
            # G41 (left): increase radius (tool outside)
            # G42 (right): decrease radius (tool inside)
            if self.comp == 'G41':
                comp_radius = radius + self.tnr
            else:  # G42
                comp_radius = radius - self.tnr

            # Prevent negative radii (would cause errors)
            if comp_radius > 0.0:
                self.paths['comp_arc'].append({
                    'start': (self.x, self.z),
                    'end': (new_x, new_z),
                    'center': (cr * 2.0, cz),
                    'radius': comp_radius,
                    'cw': clockwise,
                    'line': line_num
                })

    def _center_from_r(self, r1, z1, r2, z2, r_word, clockwise, line_num):
        """Arc centre from an R word, in radius space.

        Fanuc convention: a positive R selects the arc of 180 degrees or less,
        a negative R the one greater than 180. Returns (cr, cz, radius), or
        None when no circle of that radius passes through both endpoints — in
        which case a warning has been recorded rather than a silent null arc.
        """
        radius = abs(r_word)
        dr = r2 - r1
        dz = z2 - z1
        chord = math.hypot(dr, dz)
        if chord < 1e-9:
            self._warn(line_num, 'ARC_R_ZERO_CHORD',
                       'R arc needs two distinct endpoints; a full circle needs I/K')
            return None

        half_chord = chord / 2.0
        if radius < half_chord - 1e-9:
            self._warn(line_num, 'ARC_R_TOO_SMALL',
                       f'R{r_word:g} is below half the chord ({half_chord:.4f} mm) — '
                       'no arc of that radius reaches both endpoints')
            return None

        # Distance from chord midpoint to centre.
        height = math.sqrt(max(radius * radius - half_chord * half_chord, 0.0))
        # Unit normal, 90 degrees counter-clockwise from the chord direction.
        nr, nz = -dz / chord, dr / chord
        # Which of the two candidate centres: the minor arc sits on the normal
        # side for G03 and the opposite side for G02; a negative R flips it.
        side = (-1.0 if clockwise else 1.0) * (-1.0 if r_word < 0 else 1.0)

        cr = (r1 + r2) / 2.0 + side * height * nr
        cz = (z1 + z2) / 2.0 + side * height * nz
        return cr, cz, radius

    def _intersect_compensated_corners(self):
        """Intersects corners of compensated paths (Lookahead Corner Handling)"""
        if len(self.paths['comp_cut']) < 2:
            return

        # Iterate through consecutive segments
        for i in range(len(self.paths['comp_cut']) - 1):
            seg1 = self.paths['comp_cut'][i]
            seg2 = self.paths['comp_cut'][i + 1]

            (x1, z1), (x2, z2), line1 = seg1
            (x3, z3), (x4, z4), line2 = seg2

            # Check if segments are connected (end point of seg1 near start point of seg2)
            dist = math.hypot(x2 - x3, z2 - z3)
            if dist > 0.1:  # Not connected, skip
                continue

            # Calculate intersection point of the two lines
            intersection = self._line_intersection(x1, z1, x2, z2, x3, z3, x4, z4)

            if intersection is not None:
                ix, iz = intersection
                # Update end point of seg1 and start point of seg2
                self.paths['comp_cut'][i] = [(x1, z1), (ix, iz), line1]
                self.paths['comp_cut'][i + 1] = [(ix, iz), (x4, z4), line2]

    def _line_intersection(self, x1, z1, x2, z2, x3, z3, x4, z4):
        """
        Calculates intersection point of two lines (not segments, but infinite lines).
        Line 1: through (x1,z1) and (x2,z2)
        Line 2: through (x3,z3) and (x4,z4)
        Returns: (x, z) or None if parallel
        """
        # Direction vectors
        dx1 = x2 - x1
        dz1 = z2 - z1
        dx2 = x4 - x3
        dz2 = z4 - z3

        # Determinant (cross product in 2D)
        det = dx1 * dz2 - dz1 * dx2

        # Parallel or identical
        if abs(det) < 1e-10:
            return None

        # Parameter t for Line 1
        t = ((x3 - x1) * dz2 - (z3 - z1) * dx2) / det

        # Intersection point
        ix = x1 + t * dx1
        iz = z1 + t * dz1

        return (ix, iz)
