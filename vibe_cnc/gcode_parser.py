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

# The finishing cycle: the contour the roughing cycles left material on, run
# once exactly as programmed. It names the same blocks by P and Q.
FINISHING_CODES = frozenset({70})

# The multi-pass threading cycle. G92 cuts one pass per block and leaves the
# depth schedule to the program; G76 takes the whole thread from two blocks and
# works the passes out itself.
THREADING_CODES = frozenset({76})

# More passes than this is a slip, not a thread: a first cut of 0.001 asks for
# hundreds of thousands, and drawing them would hang the window.
THREAD_PASS_LIMIT = 200

# The pattern repeating cycle: the contour between P and Q, run again and
# again, each pass closer to it. Made for forgings and castings, whose
# material already follows the shape.
PATTERN_CODES = frozenset({73})

# More passes than this is a slip, as for a thread.
PATTERN_PASS_LIMIT = 200

# The peck cycles. G75 pecks in X and steps along Z, a groove in the diameter;
# G74 pecks along Z and steps in X, a groove in the face or a deep hole.
GROOVING_CODES = frozenset({74, 75})

# The same guard for a groove. A wide groove deep in small pecks is a few
# hundred; a P1 (one micron) is tens of thousands and hangs the window.
GROOVE_PECK_LIMIT = 1000


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
        # G76: what its first block sets holds until the next first block.
        self.thread_finishes = 1      # m: passes at full depth after the roughing
        self.thread_runout = 0        # r: 45-degree run-out, in tenths of the lead
        self.thread_min_depth = 0.0   # dmin: no pass shallower than this (mm of radius)
        self.thread_allowance = 0.0   # d: what the roughing leaves for the finishing
        # G75: the return after each peck, from its first block. Modal, and
        # without a first block it is whatever the control's parameter 5139
        # holds -- unknown here, so nothing is drawn for it.
        self.groove_retract = 0.0     # e, radius
        # G73: the relief and the number of passes from its first block. Modal;
        # without a first block they come from parameters 5135-5137, which are
        # not known here -- so no passes are drawn until a block sets R.
        self.pattern_relief_x = 0.0   # di, radius, signed
        self.pattern_relief_z = 0.0   # dk, signed
        self.pattern_passes = None    # d
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
            # What a G70 cuts. Its own kind rather than 'cut': the finishing
            # run lies within a fraction of a millimetre of the roughed
            # contour, so in the same colour it adds nothing to the picture.
            'finish': [],      # G70 finishing cycle (cyan solid)
            # What a thread cycle cuts, G92 and G76 alike. Its own kind for the
            # reason G70 has one: every pass of a thread is a straight line
            # along Z at its own depth, which is what turning looks like, so in
            # the colour of an ordinary cut a thread reads as turning.
            'thread': [],      # thread passes (yellow solid)
            'arc': [],         # G02/G03 (green arc)
            'tool_changes': [], # Tool changes
            'collisions': [],   # Collisions (red)
            'comp_cut': [],     # compensated path (G41/G42), yellow
            'comp_arc': [],     # compensated arcs (G41/G42), yellow
            'stock': None,      # blank from a (STOCK D.. L..) comment, or None
            # What was recorded, in the order it happened: (list, index, line).
            # The lists above lose the order across kinds, and a cycle makes
            # dozens of moves under one line -- this is how a playback can show
            # them one after another instead of all at once.
            'order': [],
            # Lines that describe a contour for a roughing cycle rather than
            # move the tool. The cycle has already cut them, so a playback
            # continues after them, the way the control does.
            'skipped': frozenset()
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
        self.paths['stock'] = self._read_stock(gcode)

        for line_num, line in enumerate(lines, 1):
            if line_num in self._skip:
                continue
            self._parse_line(line, line_num)
        self.paths['skipped'] = frozenset(self._skip)

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
                elif g in FINISHING_CODES:
                    self._read_finishing_cycle(line, line_num)
                elif g in THREADING_CODES:
                    self._read_threading_cycle(line, line_num)
                elif g in GROOVING_CODES:
                    self._read_peck_cycle(line, g, line_num)
                elif g in PATTERN_CODES:
                    self._read_pattern_cycle(line, line_num)

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
        self.paths['order'].append((kind, len(self.paths[kind]) - 1, line_num))
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
        self.paths['order'].append(('arc', len(self.paths['arc']) - 1, line_num))

        hit = self._arc_collision_span(arc_data)
        if hit is not None:
            self.paths['collisions'].append([hit[0], hit[1], line_num])

        if self._compensating():
            self._add_compensated_arc(arc_data, cr, cz, radius, clockwise, line_num)

    # --- Stock ------------------------------------------------------------

    @staticmethod
    def _read_stock(gcode: str):
        """The blank, from a comment such as `(STOCK D40 L50 Z0.5)`.

        NC code does not say what the blank looks like -- the control never
        needed to know -- so this is stated rather than guessed: D is the
        diameter, L the length, and Z the front face (default 0, the
        programmed zero). Without a valid comment there is no stock, and the
        plotter draws none, because a silhouette inferred from the toolpath
        would look authoritative while being a guess.
        """
        for body in re.findall(r'\(([^)]*)\)', gcode):
            match = re.match(r'\s*STOCK\b(.*)$', body, re.IGNORECASE)
            if not match:
                continue
            words = match.group(1)
            diameter = GCodeParser._word(words, 'D')
            length = GCodeParser._word(words, 'L')
            if diameter is None or length is None or diameter <= 0 or length <= 0:
                continue
            front = GCodeParser._word(words, 'Z')
            return {'diameter': diameter, 'length': length,
                    'z_front': 0.0 if front is None else front}
        return None

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

    def _read_finishing_cycle(self, line, line_num):
        """G70 P.. Q..: runs the contour once, as programmed, and comes back.

        The same blocks G71 or G72 roughed, now without the allowance: what the
        roughing left standing is what this takes off. The tool goes to the
        contour, follows it, and returns to where the cycle was called -- the
        start point -- before the next block is read.

        It skips no lines of its own. The contour blocks are normally the ones
        G71 already stepped past; where nothing did, they are ordinary blocks
        and run as such, as they would on the control.
        """
        first_block = self._word(line, 'P')
        last_block = self._word(line, 'Q')
        if first_block is None or last_block is None:
            # Nothing to run. A control would raise an alarm; there is no
            # contour to draw.
            return

        found = self._contour_points(int(first_block), int(last_block), line_num)
        if found is None:
            return
        contour, _contour_lines = found

        start = (self.x, self.z)
        self._follow_contour(contour, line_num, kind='finish')
        self._record_move('rapid', (self.x, self.z), start, line_num)
        self.x, self.z = start

    # --- Pattern repeating (G73) --------------------------------------------
    #
    #     G73 U(di) W(dk) R(d)
    #     G73 P(ns) Q(nf) U(du) W(dw) F
    #
    # The contour between P and Q is cut d times, each time shifted along the
    # axes, from far outside it to the finishing allowance. Every pass starts
    # at the cycle start point, goes to the shifted contour, follows it and
    # comes back. What a G70 then takes off is the allowance.
    #
    # From training material: di is a radius, signed by the direction of the
    # relief, dk the same along Z, d the number of passes, du and dw the
    # allowance as in G71. Not in any source read for this, and drawn as the
    # usual reading of the cycle: the first pass lies di/dk outside the
    # allowance contour, the last on it, and the passes in between are evenly
    # spaced -- di/(d-1) apart. The approach and the return are rapids, the
    # return straight back to the start point.

    def _read_pattern_cycle(self, line, line_num):
        """The block with P and Q cuts; the other sets relief and passes."""
        first_block = self._word(line, 'P')
        last_block = self._word(line, 'Q')
        if first_block is None or last_block is None:
            relief_x = self._word(line, 'U')
            relief_z = self._word(line, 'W')
            passes = self._word(line, 'R')
            if relief_x is not None:
                self.pattern_relief_x = relief_x
            if relief_z is not None:
                self.pattern_relief_z = relief_z
            if passes is not None:
                self.pattern_passes = int(round(passes))
            return

        self._expand_pattern_cycle(int(first_block), int(last_block),
                                   self._word(line, 'U') or 0.0,
                                   self._word(line, 'W') or 0.0,
                                   line_num)

    def _expand_pattern_cycle(self, first_block, last_block,
                              allowance_x, allowance_z, line_num):
        """Turns one G73 call into the passes it cuts."""
        found = self._contour_points(first_block, last_block, line_num)
        if found is None:
            return
        contour, contour_lines = found
        passes = self.pattern_passes
        if passes is None or passes < 1:
            # Leaving the contour lines to the main loop draws the shape at
            # least, as G71 does when it has no depth of cut.
            self._warn(line_num, 'CYCLE_NO_DEPTH',
                       'G73 without a number of passes (R of its first block) — '
                       'the contour is drawn, but no passes')
            return
        if passes > PATTERN_PASS_LIMIT:
            self._warn(line_num, 'CYCLE_TOO_MANY_PASSES',
                       f'G73 asks for {passes} passes — only the first '
                       f'{PATTERN_PASS_LIMIT} are drawn; check R')

        start = (self.x, self.z)
        for n in range(1, min(passes, PATTERN_PASS_LIMIT) + 1):
            # From all of the relief on the first pass to none on the last;
            # a single pass is the last. The relief is a radius, X a diameter.
            share = (passes - n) / (passes - 1) if passes > 1 else 0.0
            shift_x = 2.0 * self.pattern_relief_x * share + allowance_x
            shift_z = self.pattern_relief_z * share + allowance_z
            shifted = [(x + shift_x, z + shift_z) for (x, z) in contour]
            self._follow_contour(shifted, line_num)
            self._record_move('rapid', (self.x, self.z), start, line_num)
            self.x, self.z = start

        # Past the contour blocks, as after G71: they are the shape.
        self._skip.update(contour_lines)

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

    def _follow_contour(self, rough, line_num, kind='cut'):
        """Runs the contour once: how a roughing cycle ends, and all a G70 does."""
        self._record_move('rapid', (self.x, self.z), rough[0], line_num)
        previous = rough[0]
        for current in rough[1:]:
            self._record_move(kind, previous, current, line_num)
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
        self._record_move('thread', (entry_x, start_z), (target_x, target_z), line_num)
        self._record_move('rapid', (target_x, target_z), (start_x, target_z), line_num)
        self._record_move('rapid', (start_x, target_z), (start_x, start_z), line_num)

    # --- Multi-pass threading (G76) ---------------------------------------
    #
    #     G76 P(m)(r)(a) Q(dmin) R(d)
    #     G76 X(U) Z(W) R(i) P(k) Q(dd) F(lead)
    #
    # The first block says how the passes are made, the second says where the
    # thread is. Only the second moves anything. What is drawn follows the
    # control's schedule as it is generally described, not checked against a
    # manual: the roughing passes get shallower (see _thread_depths), and the
    # in-feed is drawn straight in. The tool angle `a` makes the real control
    # feed in along one flank, which shifts each pass a fraction of a
    # millimetre along Z; which way is not something to guess at, so it is not
    # drawn.

    @staticmethod
    def _mm(line: str, letter: str):
        """A G76 word in millimetres, or None when the block has none.

        With a decimal point the value is millimetres; without one it is in the
        control's least input increment, 0.001 mm -- Q350 is 0.35 mm and P1226
        is 1.226 mm. G76 is the cycle where it matters: its depths are
        conventionally written as bare integers.

        A real control rejects a point in P and Q with an alarm; it is drawn
        as the millimetres that were meant, and the lint engine reports it.

        The point may come last, Fanuc style: Q3. is 3 mm. The pattern every
        other word is read with drops a trailing point -- harmless for a value,
        but here the point is what decides the unit, and Q3. became 0.003.
        """
        match = re.search(rf'{letter}([-+]?(?:\d+\.?\d*|\.\d+))', line, re.IGNORECASE)
        if match is None:
            return None
        text = match.group(1)
        return float(text) if '.' in text else float(text) / 1000.0

    def _read_threading_cycle(self, line, line_num):
        """The block with an end point cuts the thread; the other sets it up."""
        if any(self._word(line, letter) is not None for letter in 'XUZW'):
            self._expand_multipass_thread(line, line_num)
        else:
            self._read_thread_parameters(line)

    def _read_thread_parameters(self, line):
        """G76 P(m)(r)(a) Q(dmin) R(d): what the passes of the next thread are made of."""
        packed = self._word(line, 'P')
        if packed is not None:
            # Six digits with the leading zeros dropped on the way in, so
            # P011060 arrives as 11060: m finishing passes, r run-out in tenths
            # of the lead, a tool angle. A zero count would mean "whatever the
            # control's parameter says"; one is the least that finishes.
            digits = f"{int(packed):06d}"[-6:]
            self.thread_finishes = max(int(digits[0:2]), 1)
            self.thread_runout = int(digits[2:4])
        minimum = self._mm(line, 'Q')
        if minimum is not None:
            self.thread_min_depth = minimum
        allowance = self._mm(line, 'R')
        if allowance is not None:
            self.thread_allowance = allowance

    def _thread_depths(self, height, first):
        """Depth of every pass, from the crest inwards, in the order they are cut.

        The roughing passes get shallower. The first is `first` deep and the
        control keeps the amount of material per pass about equal; for a V
        thread that makes the depth after n passes first * sqrt(n), so each
        pass adds first * (sqrt(n) - sqrt(n-1)). A pass never adds less than
        dmin, which is why the sequence ends in equal steps. Roughing stops
        short of the full height by the finishing allowance. Then come the
        finishing passes, all at the full height: the first takes off the
        allowance and the rest spring off what the tool pushed aside.

        Returns (depths, complete); incomplete means the pass limit was hit.
        """
        roughing_to = max(height - self.thread_allowance, 0.0)
        depths, depth, n = [], 0.0, 0
        while roughing_to - depth > 1e-9:
            if len(depths) >= THREAD_PASS_LIMIT:
                return depths, False
            n += 1
            step = max(first * (math.sqrt(n) - math.sqrt(n - 1)), self.thread_min_depth)
            depth = min(depth + step, roughing_to)
            depths.append(depth)
        depths.extend([height] * self.thread_finishes)
        return depths, True

    def _expand_multipass_thread(self, line, line_num):
        """G76 X Z R P Q F: the thread, as every pass it takes.

        Each pass is what a G92 block is: feed in to its depth, cut the thread,
        retract, return. The tool ends where it began, so the position stays.
        """
        start_x, start_z = self.x, self.z
        x_word, u_word = self._word(line, 'X'), self._word(line, 'U')
        z_word, w_word = self._word(line, 'Z'), self._word(line, 'W')
        # X and Z are absolute, U and W are measured from where the cycle starts.
        end_x = x_word if x_word is not None else start_x + (u_word or 0.0)
        end_z = z_word if z_word is not None else start_z + (w_word or 0.0)

        height = self._mm(line, 'P')       # thread height, a radius
        first = self._mm(line, 'Q')        # depth of the first pass, a radius
        taper = self._mm(line, 'R') or 0.0  # radius difference start to end
        lead = self._word(line, 'F')

        if height is None or height <= 0.0:
            self._warn(line_num, 'CYCLE_NO_DEPTH',
                       'G76 without a thread height (P) — nothing to cut')
            return
        if first is None or first <= 0.0:
            self._warn(line_num, 'CYCLE_NO_DEPTH',
                       'G76 without a first depth of cut (Q) — nothing to cut')
            return
        if end_x == start_x or end_z == start_z:
            self._warn(line_num, 'CYCLE_NO_PASS',
                       'G76 without a thread to cut — X and Z end where they start')
            return

        depths, complete = self._thread_depths(height, first)
        if not complete:
            self._warn(line_num, 'CYCLE_TOO_MANY_PASSES',
                       f'G76 would take more than {THREAD_PASS_LIMIT} passes — '
                       'only that many are drawn; check P and Q')

        # X is a diameter and the thread height a radius. An external thread
        # feeds towards the centre line, so its end diameter is the root and
        # the crest is a full height outside it; an internal one is the other
        # way round.
        inwards = -1.0 if end_x < start_x else 1.0
        crest_x = end_x - inwards * 2.0 * height
        towards = 1.0 if end_z > start_z else -1.0
        runout = 0.0
        if lead:
            runout = min(self.thread_runout * 0.1 * lead, abs(end_z - start_z))

        for depth in depths:
            x_end = crest_x + inwards * 2.0 * depth
            x_in = x_end + 2.0 * taper     # the taper makes the start wider
            self._record_move('rapid', (start_x, start_z), (x_in, start_z), line_num)
            if runout > 0.0:
                # The last stretch leaves the thread at 45 degrees: as far out
                # as it is along, so the tool is clear when it reaches the end.
                z_out = end_z - towards * runout
                x_out = x_in + (x_end - x_in) * (z_out - start_z) / (end_z - start_z)
                x_clear = x_out - inwards * 2.0 * runout
                x_clear = (min if inwards < 0 else max)(x_clear, start_x)
                self._record_move('thread', (x_in, start_z), (x_out, z_out), line_num)
                self._record_move('thread', (x_out, z_out), (x_clear, end_z), line_num)
                x_last = x_clear
            else:
                self._record_move('thread', (x_in, start_z), (x_end, end_z), line_num)
                x_last = x_end
            self._record_move('rapid', (x_last, end_z), (start_x, end_z), line_num)
            self._record_move('rapid', (start_x, end_z), (start_x, start_z), line_num)

    # --- Peck grooving and drilling (G74, G75) ---------------------------------
    #
    #     G75 R(e)                            G74 R(e)
    #     G75 X(U) Z(W) P(di) Q(dk) R(dd) F   G74 X(U) Z(W) P(di) Q(dk) R(dd) F
    #
    # G75 pecks in X: from the start point it feeds di at a time towards the
    # groove bottom, with a return of e after each peck to break the chip,
    # goes back out to the start diameter at the bottom, steps dk along Z and
    # pecks again, until it has cut at the end Z. G74 is the same turned
    # through 90 degrees: it pecks dk at a time along Z -- a groove in the
    # face, or without X a deep hole on the centre line -- and steps di in X.
    # Either way the tool ends where it began.
    #
    # Read from training material that cites the Fanuc manual, not from the
    # manual itself: di is a radius in both cycles, P and Q without a decimal
    # point are in thousandths of a millimetre, e is modal and comes from
    # parameter 5139 when no first block sets it -- one parameter for both
    # cycles, so one value here too. Drawn as an assumption: in G75 e is a
    # radius as well, the return after a peck and the step to the next plunge
    # are rapids, the next peck feeds through the returned distance again, and
    # the last step is shortened so the last plunge lands on the programmed
    # end. Not drawn: the relief dd at the bottom, because the sources
    # disagree about its direction. The width of the tool is not drawn either;
    # like every other path this is the path of the point the tool is set on.

    # How each cycle lies on the axes (0 is X, 1 is Z): the axis it pecks
    # along, the word that is the peck and the word that is the step.
    _PECK_CYCLES = {
        75: {'along': 0, 'peck_word': 'P', 'step_word': 'Q', 'cut': 'groove'},
        74: {'along': 1, 'peck_word': 'Q', 'step_word': 'P', 'cut': 'hole or groove'},
    }
    _AXIS_NAMES = ('X', 'Z')
    # X is a diameter, so an amount given as a radius counts twice along it.
    _AXIS_SCALE = (2.0, 1.0)

    def _read_peck_cycle(self, line, code, line_num):
        """The block with an end point cuts; the other sets the return."""
        if any(self._word(line, letter) is not None for letter in 'XUZW'):
            self._expand_peck_cycle(line, code, line_num)
        else:
            retract = self._mm(line, 'R')
            if retract is not None:
                self.groove_retract = abs(retract)

    def _expand_peck_cycle(self, line, code, line_num):
        """G74/G75 X Z P Q F: every peck and every step the cycle takes."""
        layout = self._PECK_CYCLES[code]
        name = f'G{code}'
        start = (self.x, self.z)
        x_word, u_word = self._word(line, 'X'), self._word(line, 'U')
        z_word, w_word = self._word(line, 'Z'), self._word(line, 'W')
        # X and Z are absolute, U and W are measured from where the cycle
        # starts. Without a word for the step axis it is one plunge where the
        # tool stands.
        end = (x_word if x_word is not None else start[0] + (u_word or 0.0),
               z_word if z_word is not None else start[1] + (w_word or 0.0))
        along = layout['along']           # the axis the pecks go along
        across = 1 - along                # the axis the steps go along
        peck = self._mm(line, layout['peck_word'])
        step = self._mm(line, layout['step_word'])

        if end[along] == start[along]:
            self._warn(line_num, 'CYCLE_NO_PASS',
                       f"{name} without a {layout['cut']} to cut — "
                       f"{self._AXIS_NAMES[along]} ends where it starts")
            return
        if peck is None or peck <= 0.0:
            self._warn(line_num, 'CYCLE_NO_DEPTH',
                       f"{name} without a depth of cut per peck ({layout['peck_word']}) "
                       "— nothing to cut")
            return
        width = abs(end[across] - start[across]) / self._AXIS_SCALE[across]
        if width > 0.0 and (step is None or step <= 0.0):
            article = 'an' if across == 0 else 'a'
            self._warn(line_num, 'CYCLE_NO_PASS',
                       f"{name} with {article} {self._AXIS_NAMES[across]} move but no step "
                       f"along it ({layout['step_word']}) — nothing to cut")
            return

        def point(along_value, across_value):
            p = [0.0, 0.0]
            p[along], p[across] = along_value, across_value
            return tuple(p)

        # Into the material and out again: towards the centre line for an
        # external groove, away from it for an internal one, towards the chuck
        # in the face. A return goes the other way and never past the start.
        scale = self._AXIS_SCALE[along]
        inwards = -1.0 if end[along] < start[along] else 1.0
        depth = abs(end[along] - start[along]) / scale
        peck_count = math.ceil(depth / peck - 1e-9)
        plunge_count = math.ceil(width / step - 1e-9) + 1 if width > 0.0 else 1
        if peck_count * plunge_count > GROOVE_PECK_LIMIT:
            self._warn(line_num, 'CYCLE_TOO_MANY_PASSES',
                       f'{name} would take more than {GROOVE_PECK_LIMIT} pecks — '
                       'only that many are drawn; check P and Q')

        # The last peck and the last plunge land exactly on the programmed
        # bottom and end, not on a multiple of the step that rounds near them.
        # Past the limit the pecks stop where they stop: no jump to the bottom
        # that the program never made.
        pecks = [start[along] + inwards * scale * peck * k
                 for k in range(1, min(peck_count, GROOVE_PECK_LIMIT) + 1)]
        if peck_count <= GROOVE_PECK_LIMIT:
            pecks[-1] = end[along]
        towards = 1.0 if end[across] > start[across] else -1.0
        back_to = min if inwards < 0 else max

        budget = GROOVE_PECK_LIMIT
        at = start[across]
        for plunge in range(plunge_count):
            if budget <= 0:
                break
            plunge_at = (end[across] if plunge == plunge_count - 1 else
                         start[across] + towards * self._AXIS_SCALE[across] * step * plunge)
            self._record_move('rapid', point(start[along], at), point(start[along], plunge_at),
                              line_num)
            at, depth_now = plunge_at, start[along]
            for n, peck_to in enumerate(pecks):
                if budget <= 0:
                    break
                budget -= 1
                self._record_move('cut', point(depth_now, at), point(peck_to, at), line_num)
                depth_now = peck_to
                if n < len(pecks) - 1 and self.groove_retract > 0.0:
                    returned = back_to(peck_to - inwards * scale * self.groove_retract,
                                       start[along])
                    self._record_move('rapid', point(depth_now, at), point(returned, at),
                                      line_num)
                    depth_now = returned
            self._record_move('rapid', point(depth_now, at), point(start[along], at), line_num)
        self._record_move('rapid', point(start[along], at), start, line_num)

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
