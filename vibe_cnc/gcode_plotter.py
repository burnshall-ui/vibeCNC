# gcode_plotter.py — 2D workpiece visualization for Fanuc lathes
import re
import math
import hashlib
from typing import List, Tuple, Optional
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QSizePolicy
from PyQt6.QtCore import QTimer, pyqtSignal, Qt
import matplotlib
matplotlib.use('QtAgg')
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np


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
        self.f = 0.0  # Feed rate
        self.mode = 'G00'  # G00=rapid, G01=cut, G02=CW-arc, G03=CCW-arc
        self.i = 0.0  # Arc parameter (X-offset center)
        self.k = 0.0  # Arc parameter (Z-offset center)
        # Tool Nose Radius Compensation (G40/G41/G42)
        self.comp = 'G40'   # current: G40=off, G41=left, G42=right (relative to movement direction)
        self.tnr = 0.0      # Corner radius (mm)
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

    def _parse_line(self, line: str, line_num: int):
        """Parses a single G-code line"""
        # Remove comments
        line = re.sub(r'\(.*?\)', '', line)
        line = re.sub(r';.*', '', line).strip()
        if not line or line.startswith('%'):
            return

        # Extract modal codes
        g_codes = re.findall(r'G(\d+)', line, re.IGNORECASE)
        for g in g_codes:
            if g in ['00', '0']:
                self.mode = 'G00'
            elif g in ['01', '1']:
                self.mode = 'G01'
            elif g in ['02', '2']:
                self.mode = 'G02'
            elif g in ['03', '3']:
                self.mode = 'G03'
            elif g in ['40']:
                self.comp = 'G40'
            elif g in ['41']:
                self.comp = 'G41'
            elif g in ['42']:
                self.comp = 'G42'
            elif g in ['71']:  # G71 Roughing cycle (simplified)
                self.mode = 'G71'
            elif g in ['72']:  # G72 Facing cycle (simplified)
                self.mode = 'G72'

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

        # Extract X/Z/I/K coordinates
        x_match = re.search(r'X([-+]?\d*\.?\d+)', line, re.IGNORECASE)
        z_match = re.search(r'Z([-+]?\d*\.?\d+)', line, re.IGNORECASE)
        i_match = re.search(r'I([-+]?\d*\.?\d+)', line, re.IGNORECASE)
        k_match = re.search(r'K([-+]?\d*\.?\d+)', line, re.IGNORECASE)

        # Extract S (spindle speed) and F (feed rate)
        s_match = re.search(r'S(\d+)', line, re.IGNORECASE)
        f_match = re.search(r'F([-+]?\d*\.?\d+)', line, re.IGNORECASE)

        if s_match:
            self.s = int(s_match.group(1))
        if f_match:
            self.f = float(f_match.group(1))

        new_x = float(x_match.group(1)) if x_match else self.x
        new_z = float(z_match.group(1)) if z_match else self.z
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
                # Circular arc: calculate center in radius space
                r = self.x / 2.0
                new_r = new_x / 2.0
                cr = r + self.i
                cz = self.z + self.k
                radius = math.hypot(self.i, self.k)
                clockwise = (self.mode == 'G02')
                arc_data = {
                    'start': (self.x, self.z),
                    'end': (new_x, new_z),
                    'center': (cr * 2.0, cz), # Store center as diameter
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

            self.x = new_x
            self.z = new_z

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


class GCodePlotterWidget(QWidget):
    """2D visualization for turned parts with Matplotlib"""

    line_clicked = pyqtSignal(int)  # Signal: Line was clicked

    def __init__(self, colors: dict, chuck_z: float = -5.0, parent=None):
        super().__init__(parent)
        self.colors = colors
        self.parser = GCodeParser(chuck_z=chuck_z)
        self.current_line = None
        self.paths_cache = None
        self.highlight_artist = None  # Red marker for current line
        self._last_code_hash = None  # Hash of the last drawn code
        self._plot_drawn = False  # Flag: Plot was drawn
        self._last_xlim = None  # Last X-limits for zoom stabilization
        self._last_ylim = None  # Last Y-limits for zoom stabilization
        self._manual_zoom = False  # Flag: User zoomed manually
        self._pan_start = None  # Start position for pan
        self._is_panning = False  # Flag: Pan mode active
        self._click_start = None  # Start position for click detection

        # Live-Position Tracking
        self.live_x = None
        self.live_z = None
        self.live_tool = None
        self.live_s = None
        self.live_f = None
        self.live_text = None  # Text artist for live display

        # Live-Drawing (only draw up to this line, None = all)
        self.live_max_line = None

        # Matplotlib Figure
        self.fig = Figure(figsize=(6, 4), dpi=100, facecolor='#1A1A1A')
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.ax = self.fig.add_subplot(111)

        # Mouse Events
        self.canvas.mpl_connect('scroll_event', self._on_scroll)
        self.canvas.mpl_connect('button_press_event', self._on_mouse_press)
        self.canvas.mpl_connect('button_release_event', self._on_mouse_release)
        self.canvas.mpl_connect('motion_notify_event', self._on_mouse_move)

        # Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas)

        # Update-Timer (Debounce for live update)
        self.update_timer = QTimer()
        self.update_timer.setSingleShot(True)
        self.update_timer.timeout.connect(self._do_update)
        self.pending_code = None

        # Initial-Plot
        self._setup_plot()
        self._draw_empty()

    def _setup_plot(self):
        """Configures the plot layout (Fanuc style)"""
        self.ax.set_facecolor('#0A0A0A')
        self.ax.set_xlabel('X (Ø mm)', color=self.colors['CRT_GREEN'], fontsize=9)
        self.ax.set_ylabel('Z (mm)', color=self.colors['CRT_GREEN'], fontsize=9)
        self.ax.tick_params(colors=self.colors['CRT_GREEN'], labelsize=8)
        self.ax.grid(True, color='#2A2A2A', linestyle='--', linewidth=0.5)
        self.ax.spines['bottom'].set_color('#444')
        self.ax.spines['left'].set_color('#444')
        self.ax.spines['top'].set_visible(False)
        self.ax.spines['right'].set_visible(False)
        self.fig.tight_layout()

    def _draw_empty(self):
        """Shows empty plot with hint text"""
        self.ax.clear()
        self._setup_plot()
        self.ax.text(0.5, 0.5, 'No Toolpaths\n(G00/G01 with X/Z)',
                     ha='center', va='center', color='#555', fontsize=10,
                     transform=self.ax.transAxes)
        self.ax.set_xlim(-10, 50)
        self.ax.set_ylim(-50, 10)
        self.canvas.draw_idle()

    def update_plot(self):
        """Starts delayed update (150ms debounce)"""
        self.update_timer.stop()
        self.update_timer.start(150)  # Shortened to 150ms (from 500ms) thanks to caching

    def update_plot_immediate(self, gcode: str):
        """Immediate update without debounce"""
        self.pending_code = gcode
        self._do_update()

    def _do_update(self):
        """Performs the actual plot update"""
        if not self.pending_code:
            return

        # Calculate hash - only redraw if code has changed
        code_hash = hashlib.md5(self.pending_code.encode('utf-8')).hexdigest()

        if code_hash == self._last_code_hash and self._plot_drawn:
            # Code has not changed - no redraw necessary
            return

        self._last_code_hash = code_hash
        self._plot_drawn = True

        # Parse G-Code
        self.paths_cache = self.parser.parse(self.pending_code)

        # Redraw plot
        self.ax.clear()
        self.highlight_artist = None  # Invalid after clear() - will be recreated
        self.live_text = None  # Invalid after clear() - will be recreated
        self._setup_plot()

        has_paths = False

        # Chuck zone (clamping area) - red hatching
        xlims = self.ax.get_xlim()
        chuck_rect = patches.Rectangle(
            (xlims[0] if xlims[0] < 0 else 0, self.parser.chuck_z - 50),
            xlims[1] - (xlims[0] if xlims[0] < 0 else 0), 50,
            linewidth=1, edgecolor='#AA0000', facecolor='#AA0000',
            alpha=0.15, hatch='//', zorder=0
        )
        self.ax.add_patch(chuck_rect)
        self.ax.axhline(y=self.parser.chuck_z, color='#AA0000', linestyle=':', linewidth=1,
                       alpha=0.5, zorder=0, label='Chuck Limit')

        # Rapid moves (G00) - gray dashed
        for segment in self.paths_cache['rapid']:
            (x1, z1), (x2, z2), line = segment
            # Live drawing: Only draw up to live_max_line
            if self.live_max_line is None or line <= self.live_max_line:
                self.ax.plot([x1, x2], [z1, z2], color='#555', linestyle='--', linewidth=1, zorder=1)
                has_paths = True

        # Cutting paths (G01) - green solid
        for segment in self.paths_cache['cut']:
            (x1, z1), (x2, z2), line = segment
            # Live drawing: Only draw up to live_max_line
            if self.live_max_line is None or line <= self.live_max_line:
                self.ax.plot([x1, x2], [z1, z2], color=self.colors['CRT_GREEN'], linewidth=2, zorder=2)
                has_paths = True

        # Compensated cutting paths (G41/G42) - yellow dashed
        for segment in self.paths_cache.get('comp_cut', []):
            (x1, z1), (x2, z2), line = segment
            if self.live_max_line is None or line <= self.live_max_line:
                self.ax.plot([x1, x2], [z1, z2], color=self.colors['FANUC_YELLOW'], linewidth=1.5,
                            linestyle='--', alpha=0.9, zorder=3)
                has_paths = True

        # Compensated arcs (G41/G42) - yellow dashed
        for arc in self.paths_cache.get('comp_arc', []):
            if self.live_max_line is None or arc['line'] <= self.live_max_line:
                cx, cz = arc['center']
                radius = arc['radius']
                x1, z1 = arc['start']
                x2, z2 = arc['end']

                # Calculate angle (in radius space)
                cr = cx / 2.0
                r1 = x1 / 2.0
                r2 = x2 / 2.0
                angle1 = math.degrees(math.atan2(z1 - cz, r1 - cr))
                angle2 = math.degrees(math.atan2(z2 - cz, r2 - cr))

                if arc['cw']:  # G02: Clockwise
                    if angle2 > angle1:
                        angle2 -= 360
                else:  # G03: Counter-clockwise
                    if angle1 > angle2:
                        angle1 -= 360

                # Width is 4*radius (since diameter), height is 2*radius
                arc_patch = patches.Arc((cx, cz), 4*radius, 2*radius,
                                       angle=0, theta1=angle1, theta2=angle2,
                                       color=self.colors['FANUC_YELLOW'], linewidth=1.5,
                                       linestyle='--', alpha=0.9, zorder=3)
                self.ax.add_patch(arc_patch)
                has_paths = True

        # Circular arcs (G02/G03) - green arc
        for arc in self.paths_cache['arc']:
            # Live drawing: Only draw up to live_max_line
            if self.live_max_line is None or arc['line'] <= self.live_max_line:
                x1, z1 = arc['start']
                x2, z2 = arc['end']
                cx, cz = arc['center']
                radius = arc['radius']

                # Calculate angle (in radius space)
                cr = cx / 2.0
                r1 = x1 / 2.0
                r2 = x2 / 2.0
                angle1 = math.degrees(math.atan2(z1 - cz, r1 - cr))
                angle2 = math.degrees(math.atan2(z2 - cz, r2 - cr))

                # Draw arc (matplotlib Arc uses X/Y, we have X/Z)
                if arc['cw']:  # G02: Clockwise
                    if angle2 > angle1:
                        angle2 -= 360
                else:  # G03: Counter-clockwise
                    if angle1 > angle2:
                        angle1 -= 360

                # Width is 4*radius (since diameter), height is 2*radius
                arc_patch = patches.Arc((cx, cz), 4*radius, 2*radius,
                                       angle=0, theta1=angle1, theta2=angle2,
                                       color=self.colors['CRT_GREEN'], linewidth=2, zorder=2)
                self.ax.add_patch(arc_patch)
                has_paths = True

        # Collisions (highlighted red)
        for segment in self.paths_cache['collisions']:
            (x1, z1), (x2, z2), line = segment
            # Live drawing: Only draw up to live_max_line
            if self.live_max_line is None or line <= self.live_max_line:
                self.ax.plot([x1, x2], [z1, z2], color='#FF0000', linewidth=3,
                            alpha=0.7, zorder=5, label='Collision!')

        # Tool changes - yellow markers
        for tc in self.paths_cache['tool_changes']:
            # Live drawing: Only draw up to live_max_line
            if self.live_max_line is None or tc['line'] <= self.live_max_line:
                self.ax.plot(tc['x'], tc['z'], marker='o', color=self.colors['FANUC_YELLOW'],
                            markersize=6, zorder=3)
                self.ax.text(tc['x'], tc['z'], f" T{tc['tool']}", color=self.colors['FANUC_YELLOW'],
                            fontsize=8, va='center')

        if not has_paths:
            self._draw_empty()
            return

        # Auto-zoom with padding
        all_x = []
        all_z = []
        for segments in [self.paths_cache['rapid'], self.paths_cache['cut']]:
            for segment in segments:
                (x1, z1), (x2, z2), _ = segment
                all_x.extend([x1, x2])
                all_z.extend([z1, z2])

        if all_x and all_z:
            x_min, x_max = min(all_x), max(all_x)
            z_min, z_max = min(all_z), max(all_z)
            x_pad = max(5, (x_max - x_min) * 0.1)
            z_pad = max(5, (z_max - z_min) * 0.1)
            # Include chuck zone
            z_min = min(z_min, self.parser.chuck_z - 2)

            new_xlim = (x_min - x_pad, x_max + x_pad)
            new_ylim = (z_min - z_pad, z_max + z_pad)

            # Zoom stabilization: Only adjust for significant changes (>20%)
            # BUT: Only if the user has not zoomed manually
            should_update_zoom = False
            if not self._manual_zoom:
                if self._last_xlim is None or self._last_ylim is None:
                    # First zoom - always set
                    should_update_zoom = True
                else:
                    # Check if bounding box has changed significantly
                    x_range_old = self._last_xlim[1] - self._last_xlim[0]
                    x_range_new = new_xlim[1] - new_xlim[0]
                    z_range_old = self._last_ylim[1] - self._last_ylim[0]
                    z_range_new = new_ylim[1] - new_ylim[0]

                    x_change = abs(x_range_new - x_range_old) / max(x_range_old, 0.1)
                    z_change = abs(z_range_new - z_range_old) / max(z_range_old, 0.1)

                    # Update if change > 20%
                    if x_change > 0.2 or z_change > 0.2:
                        should_update_zoom = True

                if should_update_zoom:
                    self.ax.set_xlim(new_xlim)
                    self.ax.set_ylim(new_ylim)
                    self._last_xlim = new_xlim
                    self._last_ylim = new_ylim
            else:
                # Manual zoom active - keep current limits
                if self._last_xlim and self._last_ylim:
                    self.ax.set_xlim(self._last_xlim)
                    self.ax.set_ylim(self._last_ylim)

        # Zero point marker
        self.ax.plot(0, 0, marker='+', color=self.colors['CYAN'], markersize=10, zorder=4, linewidth=2)
        self.ax.text(0, 0, ' G54', color=self.colors['CYAN'], fontsize=8, va='bottom')

        # Redraw cursor highlight (if set)
        if self.current_line is not None:
            self._draw_highlight()

        # Redraw live position (if active)
        if self.live_x is not None or self.live_z is not None:
            self._update_live_display()

        # Async rendering for better performance
        self.canvas.draw_idle()

    def _draw_highlight(self):
        """Draws red marker for current cursor position"""
        if self.current_line is None or not self.paths_cache:
            if self.highlight_artist:
                self.highlight_artist.set_visible(False)
            return

        # Find segment with this line number
        target_pos = None
        for segment_list in [self.paths_cache['rapid'], self.paths_cache['cut']]:
            for segment in segment_list:
                (x1, z1), (x2, z2), line = segment
                if line == self.current_line:
                    target_pos = (x2, z2)  # End position of the segment
                    break
            if target_pos:
                break

        # Check tool changes
        if not target_pos:
            for tc in self.paths_cache['tool_changes']:
                if tc['line'] == self.current_line:
                    target_pos = (tc['x'], tc['z'])
                    break

        if target_pos:
            # Create marker or update position
            if self.highlight_artist is None:
                # Create marker for the first time
                self.highlight_artist = self.ax.plot(target_pos[0], target_pos[1],
                                                     marker='o', color='#FF3333',
                                                     markersize=10, markeredgewidth=2,
                                                     markerfacecolor='none', zorder=10)[0]
            else:
                # Only update position (much faster!)
                self.highlight_artist.set_data([target_pos[0]], [target_pos[1]])
                self.highlight_artist.set_visible(True)
        else:
            # No position found - hide marker
            if self.highlight_artist:
                self.highlight_artist.set_visible(False)

    def _find_closest_line(self, click_x, click_z):
        """Finds the closest G-code line to the click position"""
        if not self.paths_cache:
            return None

        min_dist = float('inf')
        closest_line = None

        # Search all segments
        for segment_list in [self.paths_cache['rapid'], self.paths_cache['cut']]:
            for segment in segment_list:
                (x1, z1), (x2, z2), line = segment
                # Distance to end point
                dist = math.sqrt((x2 - click_x)**2 + (z2 - click_z)**2)
                if dist < min_dist:
                    min_dist = dist
                    closest_line = line

        # Check tool changes
        for tc in self.paths_cache['tool_changes']:
            dist = math.sqrt((tc['x'] - click_x)**2 + (tc['z'] - click_z)**2)
            if dist < min_dist:
                min_dist = dist
                closest_line = tc['line']

        return closest_line

    def _on_scroll(self, event):
        """Mouse wheel zoom event"""
        if event.inaxes != self.ax:
            return

        # Zoom factor
        zoom_factor = 1.2 if event.button == 'up' else 0.8

        # Current zoom range
        xlim = self.ax.get_xlim()
        ylim = self.ax.get_ylim()

        # Mouse position in data coordinates
        xdata, ydata = event.xdata, event.ydata

        # New zoom range (centered on mouse position)
        new_width = (xlim[1] - xlim[0]) / zoom_factor
        new_height = (ylim[1] - ylim[0]) / zoom_factor

        # Calculate offset to mouse position
        relx = (xdata - xlim[0]) / (xlim[1] - xlim[0])
        rely = (ydata - ylim[0]) / (ylim[1] - ylim[0])

        new_xlim = (xdata - new_width * relx, xdata + new_width * (1 - relx))
        new_ylim = (ydata - new_height * rely, ydata + new_height * (1 - rely))

        # Set new limits
        self.ax.set_xlim(new_xlim)
        self.ax.set_ylim(new_ylim)
        self._last_xlim = new_xlim
        self._last_ylim = new_ylim
        self._manual_zoom = True  # User zoomed manually

        self.canvas.draw_idle()

    def _on_mouse_press(self, event):
        """Mouse press event"""
        if event.inaxes != self.ax:
            return

        # Double click: reactivate auto-zoom
        if event.dblclick and event.button == 1:
            self._manual_zoom = False
            self._last_code_hash = None  # Invalidate cache
            if self.pending_code:
                self._do_update()
            return

        # Left click: store start position for click/drag detection
        if event.button == 1:
            self._click_start = (event.x, event.y)  # Pixel coordinates
            self._pan_start = (event.xdata, event.ydata)  # Data coordinates
            self._is_panning = False

    def _on_mouse_release(self, event):
        """Mouse release event"""
        if event.button != 1:
            return

        # Reset cursor
        self.canvas.setCursor(Qt.CursorShape.ArrowCursor)

        # If no panning occurred -> jump to line
        if not self._is_panning and event.inaxes == self.ax and self._click_start:
            closest_line = self._find_closest_line(event.xdata, event.ydata)
            if closest_line is not None:
                self.line_clicked.emit(closest_line)

        # Reset
        self._pan_start = None
        self._is_panning = False
        self._click_start = None

    def _on_mouse_move(self, event):
        """Mouse move event for pan"""
        if self._pan_start is None or event.inaxes != self.ax:
            return

        # Check if mouse moved enough (threshold: 5 pixels)
        if not self._is_panning and self._click_start:
            dx_pixel = abs(event.x - self._click_start[0])
            dy_pixel = abs(event.y - self._click_start[1])
            if dx_pixel > 5 or dy_pixel > 5:
                # Activate pan mode
                self._is_panning = True
                self.canvas.setCursor(Qt.CursorShape.ClosedHandCursor)

        # Perform pan (only if pan mode active)
        if self._is_panning:
            # Calculate displacement
            dx = event.xdata - self._pan_start[0]
            dz = event.ydata - self._pan_start[1]

            # Shift plot
            xlim = self.ax.get_xlim()
            ylim = self.ax.get_ylim()

            new_xlim = (xlim[0] - dx, xlim[1] - dx)
            new_ylim = (ylim[0] - dz, ylim[1] - dz)

            self.ax.set_xlim(new_xlim)
            self.ax.set_ylim(new_ylim)
            self._last_xlim = new_xlim
            self._last_ylim = new_ylim
            self._manual_zoom = True  # User shifted manually

            # Update pan start for smooth shifting
            self._pan_start = (event.xdata, event.ydata)

            self.canvas.draw_idle()

    def highlight_line(self, line_num: int):
        """Highlights the current line in the plot (cursor sync)"""
        # Optimization: Only update on line change
        if self.current_line == line_num:
            return

        self.current_line = line_num

        if not self.paths_cache:
            return

        # Update highlight (efficient with set_data)
        self._draw_highlight()
        self.canvas.draw_idle()

    def set_gcode(self, gcode: str):
        """Sets G-code and updates plot immediately"""
        self.pending_code = gcode
        self._do_update()

    def set_live_position(self, x: float = None, z: float = None, tool: int = None, s: int = None, f: float = None):
        """Sets the live position for the simulation display"""
        self.live_x = x
        self.live_z = z
        self.live_tool = tool
        self.live_s = s
        self.live_f = f
        self._update_live_display()

    def clear_live_position(self):
        """Removes the live position display"""
        self.live_x = None
        self.live_z = None
        self.live_tool = None
        self.live_s = None
        self.live_f = None
        if self.live_text:
            self.live_text.set_visible(False)
            self.canvas.draw_idle()

    def set_live_max_line(self, line_num: int):
        """Sets the maximum line for live drawing (only draws up to this line)"""
        if self.live_max_line != line_num:
            self.live_max_line = line_num
            # Only redraw plot if paths exist
            if self.paths_cache:
                self._redraw_live()

    def clear_live_max_line(self):
        """Removes the live drawing limit (all lines will be drawn again)"""
        if self.live_max_line is not None:
            self.live_max_line = None
            if self.paths_cache:
                self._redraw_live()

    def _redraw_live(self):
        """Redraws plot without cache check (for live drawing)"""
        # Jump directly to drawing, without hash check
        if not self.pending_code or not self.paths_cache:
            return

        # Redraw plot
        self.ax.clear()
        self.highlight_artist = None
        self.live_text = None
        self._setup_plot()

        has_paths = False

        # Chuck zone (clamping area) - red hatching
        xlims = self.ax.get_xlim()
        chuck_rect = patches.Rectangle(
            (xlims[0] if xlims[0] < 0 else 0, self.parser.chuck_z - 50),
            xlims[1] - (xlims[0] if xlims[0] < 0 else 0), 50,
            linewidth=1, edgecolor='#AA0000', facecolor='#AA0000',
            alpha=0.15, hatch='//', zorder=0
        )
        self.ax.add_patch(chuck_rect)
        self.ax.axhline(y=self.parser.chuck_z, color='#AA0000', linestyle=':', linewidth=1,
                       alpha=0.5, zorder=0, label='Chuck Limit')

        # Rapid moves (G00) - gray dashed
        for segment in self.paths_cache['rapid']:
            (x1, z1), (x2, z2), line = segment
            if self.live_max_line is None or line <= self.live_max_line:
                self.ax.plot([x1, x2], [z1, z2], color='#555', linestyle='--', linewidth=1, zorder=1)
                has_paths = True

        # Cutting paths (G01) - green solid
        for segment in self.paths_cache['cut']:
            (x1, z1), (x2, z2), line = segment
            if self.live_max_line is None or line <= self.live_max_line:
                self.ax.plot([x1, x2], [z1, z2], color=self.colors['CRT_GREEN'], linewidth=2, zorder=2)
                has_paths = True

        # Compensated cutting paths (G41/G42) - yellow dashed
        for segment in self.paths_cache.get('comp_cut', []):
            (x1, z1), (x2, z2), line = segment
            if self.live_max_line is None or line <= self.live_max_line:
                self.ax.plot([x1, x2], [z1, z2], color=self.colors['FANUC_YELLOW'], linewidth=1.5,
                            linestyle='--', alpha=0.9, zorder=3)
                has_paths = True

        # Compensated arcs (G41/G42) - yellow dashed
        for arc in self.paths_cache.get('comp_arc', []):
            if self.live_max_line is None or arc['line'] <= self.live_max_line:
                cx, cz = arc['center']
                radius = arc['radius']
                x1, z1 = arc['start']
                x2, z2 = arc['end']

                # Calculate angle (in radius space)
                cr = cx / 2.0
                r1 = x1 / 2.0
                r2 = x2 / 2.0
                angle1 = math.degrees(math.atan2(z1 - cz, r1 - cr))
                angle2 = math.degrees(math.atan2(z2 - cz, r2 - cr))

                if arc['cw']:  # G02: Clockwise
                    if angle2 > angle1:
                        angle2 -= 360
                else:  # G03: Counter-clockwise
                    if angle1 > angle2:
                        angle1 -= 360

                # Width is 4*radius (since diameter), height is 2*radius
                arc_patch = patches.Arc((cx, cz), 4*radius, 2*radius,
                                       angle=0, theta1=angle1, theta2=angle2,
                                       color=self.colors['FANUC_YELLOW'], linewidth=1.5,
                                       linestyle='--', alpha=0.9, zorder=3)
                self.ax.add_patch(arc_patch)
                has_paths = True

        # Circular arcs (G02/G03)
        for arc in self.paths_cache['arc']:
            if self.live_max_line is None or arc['line'] <= self.live_max_line:
                x1, z1 = arc['start']
                x2, z2 = arc['end']
                cx, cz = arc['center']
                radius = arc['radius']
                # Calculate angle (in radius space)
                cr = cx / 2.0
                r1 = x1 / 2.0
                r2 = x2 / 2.0
                angle1 = math.degrees(math.atan2(z1 - cz, r1 - cr))
                angle2 = math.degrees(math.atan2(z2 - cz, r2 - cr))
                if arc['cw']:
                    if angle2 > angle1:
                        angle2 -= 360
                else:
                    if angle1 > angle2:
                        angle1 -= 360
                
                # Width is 4*radius (since diameter), height is 2*radius
                arc_patch = patches.Arc((cx, cz), 4*radius, 2*radius,
                                       angle=0, theta1=angle1, theta2=angle2,
                                       color=self.colors['CRT_GREEN'], linewidth=2, zorder=2)
                self.ax.add_patch(arc_patch)
                has_paths = True

        # Collisions
        for segment in self.paths_cache['collisions']:
            (x1, z1), (x2, z2), line = segment
            if self.live_max_line is None or line <= self.live_max_line:
                self.ax.plot([x1, x2], [z1, z2], color='#FF0000', linewidth=3,
                            alpha=0.7, zorder=5, label='Collision!')

        # Tool changes
        for tc in self.paths_cache['tool_changes']:
            if self.live_max_line is None or tc['line'] <= self.live_max_line:
                self.ax.plot(tc['x'], tc['z'], marker='o', color=self.colors['FANUC_YELLOW'],
                            markersize=6, zorder=3)
                self.ax.text(tc['x'], tc['z'], f" T{tc['tool']}", color=self.colors['FANUC_YELLOW'],
                            fontsize=8, va='center')

        # Zero point marker
        self.ax.plot(0, 0, marker='+', color=self.colors['CYAN'], markersize=10, zorder=4, linewidth=2)
        self.ax.text(0, 0, ' G54', color=self.colors['CYAN'], fontsize=8, va='bottom')

        # Keep zoom if present
        if self._last_xlim and self._last_ylim:
            self.ax.set_xlim(self._last_xlim)
            self.ax.set_ylim(self._last_ylim)

        # Cursor highlight
        if self.current_line is not None:
            self._draw_highlight()

        # Live position
        if self.live_x is not None or self.live_z is not None:
            self._update_live_display()

        self.canvas.draw_idle()

    def _update_live_display(self):
        """Updates the live position overlay"""
        # If no position is set, hide the display
        if self.live_x is None and self.live_z is None:
            if self.live_text:
                self.live_text.set_visible(False)
                self.canvas.draw_idle()
            return

        # Assemble text
        info_lines = ["🎯 LIVE POSITION"]
        if self.live_x is not None:
            info_lines.append(f"X: {self.live_x:.2f} Ø")
        if self.live_z is not None:
            info_lines.append(f"Z: {self.live_z:.2f}")
        if self.live_tool is not None and self.live_tool > 0:
            info_lines.append(f"T: {self.live_tool:02d}")
        if self.live_s is not None and self.live_s > 0:
            info_lines.append(f"S: {self.live_s}")
        if self.live_f is not None and self.live_f > 0:
            info_lines.append(f"F: {self.live_f:.2f}")

        info_text = "\n".join(info_lines)

        # Create or update text box
        if self.live_text is None or not self.live_text.axes:
            # Create text box (top right)
            self.live_text = self.ax.text(
                0.98, 0.98, info_text,
                transform=self.ax.transAxes,
                fontsize=9,
                verticalalignment='top',
                horizontalalignment='right',
                bbox=dict(boxstyle='round,pad=0.6', facecolor='#1A1A1A', edgecolor=self.colors['FANUC_YELLOW'], linewidth=2, alpha=0.95),
                color=self.colors['FANUC_YELLOW'],
                family='monospace',
                weight='bold',
                zorder=100
            )
        else:
            # Only update text
            self.live_text.set_text(info_text)
            self.live_text.set_visible(True)

        self.canvas.draw_idle()
