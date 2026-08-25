# gcode_parser.py — Fanuc lathe G-code parsing, free of GUI dependencies.
#
# Kept separate from gcode_plotter so the parser can be imported — and tested —
# without PyQt6 or matplotlib present.
import re
import math
from typing import List

from .arc_geometry import arc_sweep, arc_thetas
from .tool_data import nose_direction_of, nose_offset

# G-codes whose X/Z/U/W/R words are dwell times, offsets or cycle parameters
# rather than a motion target. Reading them as a move draws a line that was
# never programmed — `G04 X1.0` is the worst of them, it aims the tool at Ø1 mm
# straight through the part. The canned cycles G70-G76 are in the same set:
# their U/W are stock allowances and their R is a retract, not an increment.
#
# G92 used to sit here too. Its words are cycle parameters, so reading them as
# one straight move was wrong -- but so is drawing nothing, because the cycle
# really does cut. It is a modal motion mode now, see _expand_thread_cycle.
NON_MOTION_CODES = frozenset({4, 10, 28, 30, 50, 65, 70, 71, 72, 73, 74, 75, 76})

# Cycles recorded in self.cycle rather than self.mode. They are one-shot calls,
# not motion modes — leaving them modal made every following block invisible.
CYCLE_CODES = frozenset({70, 71, 72, 73, 74, 75, 76})

# The two roughing cycles this parser expands into real passes. G71 feeds in X
# and cuts along Z, G72 the other way round.
ROUGHING_CODES = frozenset({71, 72})


class GCodeParser:
    """Parser for Fanuc lathe G-code (X=diameter, Z=length)"""

    # Points an arc is sampled at. The collision check needs them because an
    # arc bulges away from its chord, and the roughing cycle needs them to know
    # where the contour actually runs.
    ARC_SAMPLE_STEPS = 24

    def __init__(self, chuck_z: float = -5.0, chuck_diameter: float = None):
        self.chuck_z = chuck_z  # Chuck face: anything behind it is inside the chuck
        # Outside diameter of the jaws. None or <= 0 means "not configured", and
        # then every diameter counts as blocked -- the check errs towards a false
        # alarm rather than towards a missed crash.
        self.chuck_diameter = chuck_diameter if (chuck_diameter or 0) > 0 else None
        # Load tool data once
        try:
            from .tool_data import load_tools_json, tools_by_number
            self.tool_items = tools_by_number(load_tools_json())
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
        # Modal parameters of the threading cycle. A repeat block carries only
        # the new X; Z and the taper come from the block that opened it.
        self.cycle_z = None
        self.cycle_taper = 0.0
        # Roughing cycle: depth of cut and retract come from the first G71/G72
        # block, the contour and the allowances from the second.
        self.cycle_depth = 0.0
        self.cycle_retract = 0.0
        self._lines = []
        self._blocks = {}
        self._skip = set()
        self.i = 0.0  # Arc parameter (X-offset center)
        self.k = 0.0  # Arc parameter (Z-offset center)
        # Tool Nose Radius Compensation (G40/G41/G42)
        self.comp = 'G40'   # current: G40=off, G41=left, G42=right (relative to movement direction)
        self.tnr = 0.0      # Corner radius (mm)
        self.nose_direction = 0  # Imaginary tool nose position, Fanuc tip number 0-9
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
        # The roughing cycle needs to reach the contour blocks by their N
        # number, and then keep the main loop from running them a second time:
        # between P and Q they describe a shape, they are not moves of their
        # own. Fanuc jumps straight past them once the cycle is done.
        self._lines = lines
        self._blocks = self._index_blocks(lines)
        self._skip = set()

        for line_num, line in enumerate(lines, 1):
            if line_num in self._skip:
                continue
            self._parse_line(line, line_num)

        # Post-processing: intersect corners (Lookahead Corner Handling)
        self._intersect_compensated_corners()

        return self.paths

    @staticmethod
    def _word(line: str, letter: str):
        """The value of one address word, or None when the block has none."""
        match = re.search(rf'{letter}([-+]?\d*\.?\d+)', line, re.IGNORECASE)
        return float(match.group(1)) if match else None

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
            elif g == 92:
                # A modal cycle, group 01 like G00-G03: the next block that
                # carries only an X repeats it, and the next G00/G01/G02/G03
                # cancels it. Keeping it in self.mode gets both for free.
                self.mode = 'G92'
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
                if g in ROUGHING_CODES:
                    self._read_roughing_cycle(line, g, line_num)

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
            self.nose_direction = nose_direction_of(tool_info)

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

        if self.mode == 'G92':
            # The cycle returns to where it started, so it has to be handled
            # ahead of the gate below -- which asks whether the position
            # changed, and for a thread pass it never does. A block with no
            # axis word at all (a bare F, say) does not repeat the cycle.
            if any(m is not None for m in (x_match, u_match, z_match, w_match)):
                self._expand_thread_cycle(new_x, new_z,
                                          z_match is not None or w_match is not None,
                                          r_match, line_num)
            return

        # A circular block with I/K but no X/Z is a full circle: start and end
        # are the same point. Gating everything on "did the position change"
        # dropped the whole block -- no arc, and no collision check either.
        if (new_x == self.x and new_z == self.z
                and self.mode in ('G02', 'G03')
                and (i_match is not None or k_match is not None or r_match is not None)):
            self._add_arc(new_x, new_z, i_match, k_match, r_match, line_num)
            return

        # Record movement (if position changes)
        if new_x != self.x or new_z != self.z:
            if self.mode == 'G00':
                self._record_move('rapid', (self.x, self.z), (new_x, new_z), line_num)
            elif self.mode == 'G01':
                segment_data = self._record_move('cut', (self.x, self.z),
                                                 (new_x, new_z), line_num)
                if self._compensating():
                    self._add_compensated_segment(segment_data)
            elif self.mode in ['G02', 'G03']:
                self._add_arc(new_x, new_z, i_match, k_match, r_match, line_num)

            self.x = new_x
            self.z = new_z

    def _record_move(self, kind, start, end, line_num):
        """Files one straight move under `kind` and checks it against the chuck.

        Returns the segment so callers can hang more off it. Arcs go through
        _add_arc instead: their collision check has to sample the curve.
        """
        segment = [start, end, line_num]
        if start == end:
            # A cycle that starts where it retracts to produces these; drawing
            # them would put a dot on the plot and a zero-length leg in any
            # length the paths are later summed for.
            return segment
        if self._hits_chuck(*start, *end):
            self.paths['collisions'].append(segment)
        self.paths[kind].append(segment)
        return segment

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

        hit = self._arc_collision_span(arc_data)
        if hit is not None:
            self.paths['collisions'].append([hit[0], hit[1], line_num])

        if self._compensating():
            self._add_compensated_arc(arc_data, cr, cz, radius, clockwise, line_num)

    # --- Canned cycles ----------------------------------------------------

    @staticmethod
    def _index_blocks(lines) -> dict:
        """N number -> line number, for the P and Q words of a cycle.

        Read as integers so N100 and N0100 are the same block, the way the
        control reads them.
        """
        blocks = {}
        for line_num, line in enumerate(lines, 1):
            match = re.match(r'\s*N(\d+)', line, re.IGNORECASE)
            if match:
                blocks.setdefault(int(match.group(1)), line_num)
        return blocks

    def _read_roughing_cycle(self, line, code, line_num):
        """G71/G72. The first block carries the parameters, the second cuts.

        Fanuc splits the call in two: `G71 U1.5 R0.5` sets depth of cut and
        retract, `G71 P.. Q.. U.. W..` names the contour and the allowances and
        runs it. The U of the first block is a depth, the U of the second an
        allowance -- same letter, different meaning, told apart by whether P
        and Q are present.
        """
        first_block = self._word(line, 'P')
        last_block = self._word(line, 'Q')

        if first_block is None or last_block is None:
            depth = self._word(line, 'U' if code == 71 else 'W')
            if depth is not None:
                self.cycle_depth = abs(depth)
            retract = self._word(line, 'R')
            if retract is not None:
                self.cycle_retract = abs(retract)
            return

        self._expand_roughing_cycle(code, int(first_block), int(last_block),
                                    self._word(line, 'U') or 0.0,
                                    self._word(line, 'W') or 0.0,
                                    line_num)

    def _expand_roughing_cycle(self, code, first_block, last_block,
                               allowance_x, allowance_z, line_num):
        """Turns one G71/G72 call into the passes it actually cuts."""
        found = self._contour_points(first_block, last_block, line_num)
        if found is None:
            return
        contour, contour_lines = found
        if self.cycle_depth <= 0.0:
            self._warn(line_num, 'CYCLE_NO_DEPTH',
                       f'G{code} without a depth of cut — '
                       'the contour is drawn, but no passes')
            return

        # The allowance is what is left standing for the finishing cut. Fanuc
        # shifts the contour along the axes by it rather than normal to it, so
        # a plain addition is the whole of it -- allowance_x is a diameter.
        rough = [(x + allowance_x, z + allowance_z) for (x, z) in contour]

        # G71 steps in X and cuts along Z; G72 the other way round. Everything
        # below works in (level, travel) so both fit through the same code.
        level, travel = (0, 1) if code == 71 else (1, 0)
        start = (self.x, self.z)

        # Which way the passes march, and which way they cut, is read off the
        # contour rather than off the sign of the allowance: turning outside
        # goes down in diameter, boring goes up, and the contour says which.
        levels = [point[level] for point in rough]
        far = min(levels) if min(levels) < start[level] else max(levels)
        # U of the first block is a depth in radius, so a G71 layer is twice
        # that in diameter. G72 steps along Z, where the word means itself.
        step = self.cycle_depth * (2.0 if level == 0 else 1.0)
        step = abs(step) if far > start[level] else -abs(step)

        passes = []
        position = start[level] + step
        while (position - far) * (1.0 if step > 0 else -1.0) < 0.0:
            passes.append(position)
            position += step
        # No pass on the contour itself: what the last layer leaves standing is
        # exactly what the contour run at the end of the cycle takes off.

        # The contour runs the way the passes cut -- towards the chuck for
        # G71, towards the centre line for G72. Reading the direction off it
        # beats comparing against the start position, which says nothing when
        # the contour begins level with it.
        forward = 1.0 if rough[-1][travel] > rough[0][travel] else -1.0

        for value in passes:
            self._one_roughing_pass(value, level, travel, start, rough,
                                    forward, line_num)

        # The last thing the cycle does is follow the roughed contour once.
        self._follow_contour(rough, line_num)

        # Back to where the cycle started, and past the contour blocks: they
        # describe the shape, they are not moves of their own. Skipping them is
        # the last thing done, so a cycle that could not be expanded leaves
        # them to the main loop and the contour is still drawn.
        self._record_move('rapid', (self.x, self.z), start, line_num)
        self.x, self.z = start
        self._skip.update(contour_lines)

    def _one_roughing_pass(self, value, level, travel, start, rough, forward,
                           line_num):
        """One layer: feed in, cut until the contour stops it, retract, return."""
        limit = self._contour_limit(rough, level, travel, value, forward)
        if limit is None:
            return

        def point(level_value, travel_value):
            return ((level_value, travel_value) if level == 0
                    else (travel_value, level_value))

        entry = point(value, start[travel])
        end = point(value, limit)
        self._record_move('rapid', (self.x, self.z), entry, line_num)
        self._record_move('cut', entry, end, line_num)

        # Retract at 45 degrees, away from the wall that stopped the pass.
        away = self.cycle_retract * (1.0 if start[level] > value else -1.0)
        back = -self.cycle_retract * forward
        corner = point(value + away * (2.0 if level == 0 else 1.0), limit + back)
        self._record_move('rapid', end, corner, line_num)
        self._record_move('rapid', corner, point(corner[level], start[travel]),
                          line_num)
        self.x, self.z = point(corner[level], start[travel])

    def _follow_contour(self, rough, line_num):
        """Runs the roughed contour once, which is how the cycle finishes."""
        self._record_move('rapid', (self.x, self.z), rough[0], line_num)
        previous = rough[0]
        for current in rough[1:]:
            self._record_move('cut', previous, current, line_num)
            previous = current
        self.x, self.z = previous

    @staticmethod
    def _contour_limit(rough, level, travel, value, forward):
        """How far a pass at `value` gets before the contour is in the way.

        The pass stops at the first material it meets, which is the crossing
        that comes first along the direction of travel. With no crossing at all
        the layer clears the whole contour and runs to its far end. This is the
        type I reading of the cycle: material behind a pocket is left for a
        later layer rather than undercut.
        """
        crossings = []
        for first, second in zip(rough, rough[1:]):
            low, high = sorted((first[level], second[level]))
            if low - 1e-9 <= value <= high + 1e-9:
                span = second[level] - first[level]
                if abs(span) < 1e-12:
                    crossings.extend((first[travel], second[travel]))
                else:
                    ratio = (value - first[level]) / span
                    crossings.append(first[travel]
                                     + ratio * (second[travel] - first[travel]))

        if not crossings:
            # Nothing at this level: the layer runs past the whole contour.
            beyond = [point[travel] for point in rough]
            return max(beyond) if forward > 0 else min(beyond)

        return min(crossings) if forward > 0 else max(crossings)

    def _contour_points(self, first_block, last_block, line_num):
        """Contour between two N numbers plus its line range, arcs sampled."""
        start_line = self._blocks.get(first_block)
        end_line = self._blocks.get(last_block)
        if start_line is None or end_line is None or end_line < start_line:
            self._warn(line_num, 'CYCLE_BLOCKS_MISSING',
                       f'P{first_block}/Q{last_block} do not name a block range '
                       'in this program — the cycle cannot be expanded')
            return None

        # Walked with a parser of its own so the contour blocks cannot leave
        # anything behind in the real paths -- they are a shape, not moves.
        reader = GCodeParser(chuck_z=self.chuck_z,
                             chuck_diameter=self.chuck_diameter)
        reader.tool_items = self.tool_items
        reader.x, reader.z = self.x, self.z
        reader.mode = self.mode
        reader.comp = 'G40'          # the cycle is not the place for compensation

        # The first block only brings the tool from the cycle start onto the
        # contour, so the shape begins where that block ends. Counting the
        # approach as contour laid a wall right across the stock, and every
        # pass stopped against it after a tenth of a millimetre.
        points = []
        for offset in range(start_line, end_line + 1):
            seen = len(reader.paths['arc'])
            reader._parse_line(self._lines[offset - 1], offset)
            for arc in reader.paths['arc'][seen:]:
                points.extend(reader._sample_arc(arc)[1:])
            if not points or (reader.x, reader.z) != points[-1]:
                points.append((reader.x, reader.z))

        if len(points) < 2:
            self._warn(line_num, 'CYCLE_NO_CONTOUR',
                       'The blocks between P and Q describe no contour')
            return None
        return points, range(start_line, end_line + 1)

    def _expand_thread_cycle(self, target_x, target_z, has_z, r_match, line_num):
        """G92: one threading pass, drawn as the four moves it really makes.

        A Fanuc lathe cuts *one* pass per G92 block -- the depth schedule is
        the sequence of blocks that follows, each with a smaller X, not
        something hidden inside a single block. So one block is: feed in to
        the diameter, cut the thread to Z, retract in X, return in Z. Only the
        second of those removes material; the other three are rapids.

        The tool ends the block where it began, which is what lets the next
        block carry nothing but an X. Position therefore stays untouched.
        """
        start_x, start_z = self.x, self.z

        # Z and the taper are modal within the cycle: a repeat block carries
        # only the next X, and the end of the thread stays where the opening
        # block put it. Reading Z off the current position instead turned every
        # repeat into a zero-length pass.
        if has_z:
            self.cycle_z = target_z
        elif self.cycle_z is not None:
            target_z = self.cycle_z

        # R is the taper: the difference in *radius* between the start of the
        # thread and its end, so the entry diameter is that much wider.
        if r_match is not None:
            self.cycle_taper = float(r_match.group(1))
        entry_x = target_x + 2.0 * self.cycle_taper

        if target_z == start_z and entry_x == start_x and target_x == start_x:
            self._warn(line_num, 'CYCLE_NO_PASS',
                       'G92 without a target — nothing to cut')
            return

        self._record_move('rapid', (start_x, start_z), (entry_x, start_z), line_num)
        self._record_move('cut', (entry_x, start_z), (target_x, target_z), line_num)
        self._record_move('rapid', (target_x, target_z), (start_x, target_z), line_num)
        self._record_move('rapid', (start_x, target_z), (start_x, start_z), line_num)

    # --- Tool nose radius compensation ----------------------------------
    #
    # Two vectors decide where a compensated path runs. The control keeps the
    # *centre* of the nose radius one radius off the contour, on the side
    # G41/G42 selects. The programmed point is not that centre but the
    # imaginary tool nose, and the tip number says where the one sits relative
    # to the other. What the axes move -- and what the position display shows
    # -- is the imaginary nose:
    #
    #     centre = contour + radius * normal
    #     path   = centre - nose vector
    #
    # With tip 0 the two points coincide, the second term drops out and the
    # result is what every program produced before the field existed.

    def _compensating(self) -> bool:
        return self.comp in ('G41', 'G42') and self.tnr > 0.0

    def _comp_side(self) -> float:
        """+1 with the tool left of the path (G41), -1 right of it (G42)."""
        return 1.0 if self.comp == 'G41' else -1.0

    def _nose_vector(self) -> tuple:
        """Imaginary tool nose -> centre of the nose radius, in radius space."""
        return nose_offset(self.nose_direction, self.tnr)

    @staticmethod
    def _left_normal(dr, dz, length) -> tuple:
        """Unit normal to the left of the direction of travel, as (X, Z).

        Compensation happens in the G18 plane, and ISO reads left from +Y.
        Drawn the way a lathe control draws it -- Z to the right, X upwards --
        +Y points at the reader, so left is the plain counter-clockwise quarter
        turn of that picture: (Z, X) -> (-X, Z), which in the (X, Z) ordering
        used here is (dr, dz) -> (dz, -dr). Turning in the (X, Z) plane instead
        mirrors the picture and swaps G41 with G42. That is what this code did
        before, and it put OD turning on a rear tool post -- G42 on this
        machine -- on the wrong side of the contour.
        """
        return dz / length, -dr / length

    def _add_compensated_segment(self, segment_data):
        """Compensated path for one linear move, in diameter coordinates."""
        (x1, z1), (x2, z2), line_num = segment_data
        r1, r2 = x1 / 2.0, x2 / 2.0
        dr, dz = r2 - r1, z2 - z1
        length = math.hypot(dr, dz)
        if length <= 1e-6:
            return

        nr, nz = self._left_normal(dr, dz, length)
        nose_r, nose_z = self._nose_vector()
        side = self._comp_side()
        offr = side * nr * self.tnr - nose_r
        offz = side * nz * self.tnr - nose_z

        self.paths['comp_cut'].append([((r1 + offr) * 2.0, z1 + offz),
                                       ((r2 + offr) * 2.0, z2 + offz),
                                       line_num])

    def _add_compensated_arc(self, arc_data, cr, cz, radius, clockwise, line_num):
        """Compensated path for one arc, concentric with the programmed one.

        Whether the compensated radius grows or shrinks follows from the
        geometry, not from the G-code alone -- reading G41 as "always larger"
        got every second arc backwards. The tangent at any point of an arc is
        the radius vector turned a quarter turn, counter-clockwise for G03 and
        clockwise for G02. Turning it once more to reach the left normal
        therefore lands on minus the radius vector for G03 and on plus it for
        G02: the offset runs along the radius, and which way is decided by
        direction of travel and compensation side together.

        The endpoints move with it. They used to be copied from the programmed
        arc while the radius changed underneath them, leaving a centre, a
        radius and two endpoints that no single arc could satisfy.
        """
        outward = self._comp_side() * (1.0 if clockwise else -1.0)
        comp_radius = radius + outward * self.tnr
        if comp_radius <= 1e-9:
            self._warn(line_num, 'ARC_COMP_TOO_TIGHT',
                       f'Nose radius {self.tnr:g} mm does not fit into the '
                       f'R{radius:g} arc — no compensated path exists')
            return

        nose_r, nose_z = self._nose_vector()
        moved = []
        for (x, z) in (arc_data['start'], arc_data['end']):
            ur, uz = x / 2.0 - cr, z - cz
            distance = math.hypot(ur, uz)
            if distance < 1e-9:
                return
            moved.append(((cr + ur / distance * comp_radius - nose_r) * 2.0,
                          cz + uz / distance * comp_radius - nose_z))

        self.paths['comp_arc'].append({
            'start': moved[0],
            'end': moved[1],
            'center': ((cr - nose_r) * 2.0, cz - nose_z),
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
        # Which of the two candidate centres. The centre of curvature lies to
        # the left of the direction of travel for a counter-clockwise arc and
        # to the right for a clockwise one; a negative R picks the other one,
        # which is the same circle traversed the long way round.
        #
        # Left is the left of the picture the control draws -- see
        # _left_normal. Taking the normal in the (X, Z) plane instead mirrors
        # it, and mirroring puts every R arc on the wrong side of its chord:
        # a hemisphere on the face came out as a hollow, and the I/K form of
        # the same arc disagreed with the R form.
        nr, nz = self._left_normal(dr, dz, chord)
        side = (-1.0 if clockwise else 1.0) * (-1.0 if r_word < 0 else 1.0)

        cr = (r1 + r2) / 2.0 + side * height * nr
        cz = (z1 + z2) / 2.0 + side * height * nz
        return cr, cz, radius

    def _hits_chuck(self, x1, z1, x2, z2) -> bool:
        """Does the straight move from (x1,z1) to (x2,z2) enter the chuck?

        The chuck is the region behind the chuck face that is no wider than the
        jaws: z < chuck_z and |X| < chuck_diameter. X values are diameters and
        the plot mirrors about the centre line, hence the absolute value.

        Clipping the segment's parameter interval against the three half-planes
        catches the case the old endpoint test could not: a move that crosses
        the chuck while both of its endpoints sit outside.
        """
        lo, hi = 0.0, 1.0
        lo, hi = self._clip_below(lo, hi, z1, z2 - z1, self.chuck_z)
        if self.chuck_diameter is not None:
            dx = x2 - x1
            lo, hi = self._clip_below(lo, hi, x1, dx, self.chuck_diameter)
            lo, hi = self._clip_above(lo, hi, x1, dx, -self.chuck_diameter)
        # A grazing touch leaves a single point, which is the limit, not a crash.
        return hi - lo > 1e-9

    @staticmethod
    def _clip_below(lo, hi, start, delta, bound):
        """Narrow [lo,hi] to the part where start + t*delta < bound."""
        if abs(delta) < 1e-12:
            return (lo, hi) if start < bound else (1.0, 0.0)
        t = (bound - start) / delta
        return (lo, min(hi, t)) if delta > 0 else (max(lo, t), hi)

    @staticmethod
    def _clip_above(lo, hi, start, delta, bound):
        """Narrow [lo,hi] to the part where start + t*delta > bound."""
        if abs(delta) < 1e-12:
            return (lo, hi) if start > bound else (1.0, 0.0)
        t = (bound - start) / delta
        return (max(lo, t), hi) if delta > 0 else (lo, min(hi, t))

    def _arc_collision_span(self, arc):
        """First sampled span of the arc that enters the chuck, or None.

        Returning the span rather than a flag keeps the recorded collision on
        the part of the arc that actually offends. Start and end coincide on a
        full circle, so the chord would have been a single point.
        """
        previous = None
        for point in self._sample_arc(arc):
            if previous is not None and self._hits_chuck(*previous, *point):
                return previous, point
            previous = point
        return None

    def _sample_arc(self, arc):
        """The arc as a list of points, both ends included, in travel order.

        arc_thetas hands back the pair matplotlib wants, which for a
        counter-clockwise arc means the end angle first -- fine for a static
        patch, and fine for the collision check, which only looks at
        neighbouring pairs. A contour has to come out the way the tool runs it,
        so that case is turned back round here.
        """
        theta1, _theta2 = arc_thetas(arc)
        sweep = arc_sweep(arc)
        cr, cz = arc['center'][0] / 2.0, arc['center'][1]
        radius = arc['radius']

        points = []
        for step in range(self.ARC_SAMPLE_STEPS + 1):
            angle = math.radians(theta1 + sweep * step / self.ARC_SAMPLE_STEPS)
            points.append((2.0 * (cr + radius * math.cos(angle)),
                           cz + radius * math.sin(angle)))
        if not arc['cw']:
            points.reverse()
        return points

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
