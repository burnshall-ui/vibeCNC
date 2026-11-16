# gcode_plotter.py — 2D workpiece visualization for Fanuc lathes
import math
import hashlib
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QSizePolicy
from PyQt6.QtCore import QTimer, pyqtSignal, Qt
import matplotlib
matplotlib.use('QtAgg')
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
import matplotlib.patches as patches


# GCodeParser lives in gcode_parser so it stays importable without a GUI stack.
# Re-exported here because this module has always been its import site.
from vibe_cnc.gcode_parser import GCodeParser
from vibe_cnc.arc_geometry import arc_thetas



class GCodePlotterWidget(QWidget):
    """2D visualization for turned parts with Matplotlib"""

    line_clicked = pyqtSignal(int)  # Signal: Line was clicked

    def __init__(self, colors: dict, chuck_z: float = -5.0,
                 chuck_diameter: float = None, parent=None):
        super().__init__(parent)
        self.colors = colors
        self.parser = GCodeParser(chuck_z=chuck_z, chuck_diameter=chuck_diameter)
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
        # How many of that line's moves are drawn, None = all of them. A cycle
        # is one block that makes many moves; this is what lets it be played
        # back one at a time.
        self.live_step = None
        # Rebuilt after every parse: the moves of each line in order, and each
        # move's place in its line.
        self._moves_of_line = {}
        self._rank = {}
        self._move_of = {}  # id() of a straight segment -> (list, index)

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
        self.canvas.mpl_connect('resize_event', self._on_resize)

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
        # Z along the horizontal with the chuck to the left, X up the vertical.
        # That is how every lathe control draws it, and it matches the shape of
        # both the part (long in Z, small in X) and this panel (wide and short).
        self.ax.set_xlabel('Z (mm)', color=self.colors['CRT_GREEN'], fontsize=9)
        self.ax.set_ylabel('X (Ø mm)', color=self.colors['CRT_GREEN'], fontsize=9)
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
        self.ax.set_xlim(-50, 10)
        self.ax.set_ylim(-10, 50)
        self._match_aspect()
        self.canvas.draw_idle()

    def _match_aspect(self):
        """Widens the view until a millimetre of radius is as long as one of Z.

        X is a diameter, so a unit of it is drawn half as long as a unit of Z.
        Without this the part came out several times flatter than it is.
        matplotlib can enforce it (set_aspect with adjustable='datalim'), but
        it logs a warning on every draw when the limits were set by hand, as
        they are here. This does the same widening once, when the limits are
        set and when the panel changes shape.
        """
        box = self.ax.get_window_extent()
        if box.width < 1 or box.height < 1:
            return
        x0, x1 = self.ax.get_xlim()
        y0, y1 = self.ax.get_ylim()
        # The Y range that makes the scales 2:1 for this X range, and this
        # panel's shape. If the Y range already asked for is smaller, widen Y;
        # otherwise it is X that is too tight.
        y_needed = 2.0 * (x1 - x0) * box.height / box.width
        if y_needed >= y1 - y0:
            centre = 0.5 * (y0 + y1)
            self.ax.set_ylim(centre - y_needed / 2.0, centre + y_needed / 2.0)
        else:
            x_needed = (y1 - y0) * box.width / (2.0 * box.height)
            centre = 0.5 * (x0 + x1)
            self.ax.set_xlim(centre - x_needed / 2.0, centre + x_needed / 2.0)

    def _apply_view(self):
        """Puts the stored limits on the axes, widened to scale.

        clear() throws the old limits away, so every redraw has to set them
        again.
        """
        if self._last_xlim and self._last_ylim:
            self.ax.set_xlim(self._last_xlim)
            self.ax.set_ylim(self._last_ylim)
        self._match_aspect()

    def _on_resize(self, _event):
        """Keeps the picture to scale when the panel changes shape."""
        self._apply_view()

    def update_plot(self):
        """Starts delayed update (150ms debounce)"""
        self.update_timer.stop()
        self.update_timer.start(150)  # Shortened to 150ms (from 500ms) thanks to caching

    def update_plot_immediate(self, gcode: str):
        """Immediate update without debounce"""
        self.pending_code = gcode
        self._do_update()

    def shutdown(self):
        """Stops the debounce timer before the widgets it fires into go away."""
        self.update_timer.stop()

    def _draw_chuck(self):
        """Draws the chuck zone the collision check actually uses.

        Bounded by the jaw diameter when one is configured, so the picture and
        the check agree. Without a diameter the check treats every diameter as
        blocked, and the hatching spans the full width to say so.
        """
        diameter = self.parser.chuck_diameter
        style = dict(linewidth=1, edgecolor='#AA0000', facecolor='#AA0000',
                     alpha=0.15, hatch='//', zorder=0)

        if diameter is None:
            # Every diameter is blocked, so span the whole height. axvspan works
            # in axes coordinates: reading the limits here returned the default
            # (0, 1) because the axes were just cleared and the paths that set
            # the real limits are drawn after this, which left the hatching a
            # one-millimetre sliver at the centre line.
            self.ax.axvspan(self.parser.chuck_z - 50, self.parser.chuck_z, **style)
        else:
            self.ax.add_patch(patches.Rectangle(
                (self.parser.chuck_z - 50, -diameter), 50, 2 * diameter, **style))
        self.ax.axvline(x=self.parser.chuck_z, color='#AA0000', linestyle=':',
                        linewidth=1, alpha=0.5, zorder=0, label='Chuck Limit')

    def _draw_arc(self, arc, **style):
        """Draws one parsed G02/G03 arc onto the current axes.

        The ellipse is 4*radius wide and 2*radius high because X is a diameter
        and Z is not. At exactly that squash the parameter angle equals the
        real angle in radius space, which is what arc_thetas returns.
        """
        theta1, theta2 = arc_thetas(arc)
        radius = arc['radius']
        cx, cz = arc['center']
        # Transposing the axes reflects the plane, which reverses the sense of
        # rotation: a point at angle t in (radius, Z) sits at 90 - t in (Z,
        # diameter). Hence the swapped, mirrored pair -- matplotlib always
        # sweeps counter-clockwise from the first angle to the second.
        self.ax.add_patch(patches.Arc((cz, cx), 2 * radius, 4 * radius,
                                      angle=0, theta1=90.0 - theta2,
                                      theta2=90.0 - theta1, **style))

    def _draw_stock(self):
        """Draws the blank behind everything else, when the program names one."""
        stock = self.paths_cache.get('stock') if self.paths_cache else None
        if not stock:
            return
        z_front = stock['z_front']
        diameter = stock['diameter']
        # The upper half of the section, with the centre line as its bottom
        # edge -- the way the control draws a turned part.
        self.ax.add_patch(patches.Rectangle(
            (z_front - stock['length'], 0.0), stock['length'], diameter,
            linewidth=1, edgecolor='#666666', facecolor='#202020',
            zorder=0.5, gid='stock'))
        # At the far end, clear of the rapids that arrive at the front face.
        self.ax.text(z_front - stock['length'], diameter,
                     f" STOCK Ø{diameter:g} × {stock['length']:g}",
                     ha='left', va='bottom', color='#777777', fontsize=7)

    def _draw_tool_changes(self):
        """Marks where each tool change happens, one label per position.

        Changes usually start from the same safe point, so labelling each one
        printed T5 and T1 on top of each other.
        """
        at_position = {}
        for tc in self.paths_cache['tool_changes']:
            if self._shown(tc['line']):
                key = (round(tc['z'], 3), round(tc['x'], 3))
                at_position.setdefault(key, []).append(tc['tool'])

        for (z, x), tools in at_position.items():
            label = ", ".join(f"T{t}" for t in dict.fromkeys(tools))
            self.ax.plot(z, x, marker='o', color=self.colors['FANUC_YELLOW'],
                         markersize=6, zorder=3)
            self.ax.text(z, x, f" {label}", color=self.colors['FANUC_YELLOW'],
                         fontsize=8, va='center')

    def _draw_scene(self) -> bool:
        """Clears the axes and draws the program up to live_max_line.

        Shared by the full update and the live redraw, which differ only in
        what they do with the zoom afterwards. Returns whether any path was
        drawn.
        """
        self.ax.clear()
        self.highlight_artist = None  # Invalid after clear() - will be recreated
        self.live_text = None  # Invalid after clear() - will be recreated
        self._setup_plot()

        has_paths = False

        self._draw_stock()
        self._draw_chuck()

        # Live drawing: only what the playback has reached. _shown decides.

        # Rapid moves (G00) - gray dashed
        for index, segment in enumerate(self.paths_cache['rapid']):
            (x1, z1), (x2, z2), line = segment
            if self._shown(line, ('rapid', index)):
                self.ax.plot([z1, z2], [x1, x2], color='#555', linestyle='--', linewidth=1, zorder=1)
                has_paths = True

        # Cutting paths (G01) - green solid
        for index, segment in enumerate(self.paths_cache['cut']):
            (x1, z1), (x2, z2), line = segment
            if self._shown(line, ('cut', index)):
                self.ax.plot([z1, z2], [x1, x2], color=self.colors['CRT_GREEN'], linewidth=2, zorder=2)
                has_paths = True

        # Compensated cutting paths (G41/G42) - yellow dashed
        for segment in self.paths_cache.get('comp_cut', []):
            (x1, z1), (x2, z2), line = segment
            if self._shown(line):
                self.ax.plot([z1, z2], [x1, x2], color=self.colors['FANUC_YELLOW'], linewidth=1.5,
                            linestyle='--', alpha=0.9, zorder=3)
                has_paths = True

        # Compensated arcs (G41/G42) - yellow dashed
        for arc in self.paths_cache.get('comp_arc', []):
            if self._shown(arc['line']):
                self._draw_arc(arc, color=self.colors['FANUC_YELLOW'],
                               linewidth=1.5, linestyle='--', alpha=0.9,
                               zorder=3)
                has_paths = True

        # Circular arcs (G02/G03) - green arc
        for index, arc in enumerate(self.paths_cache['arc']):
            if self._shown(arc['line'], ('arc', index)):
                self._draw_arc(arc, color=self.colors['CRT_GREEN'],
                               linewidth=2, zorder=2)
                has_paths = True

        # Collisions (highlighted red). A straight one is the very segment that
        # was filed under cut or rapid, so it appears with its move.
        for segment in self.paths_cache['collisions']:
            (x1, z1), (x2, z2), line = segment
            if self._shown(line, self._move_of.get(id(segment))):
                self.ax.plot([z1, z2], [x1, x2], color='#FF0000', linewidth=3,
                            alpha=0.7, zorder=5, label='Collision!')

        self._draw_tool_changes()

        # Zero point marker
        self.ax.plot(0, 0, marker='+', color=self.colors['CYAN'], markersize=10, zorder=4, linewidth=2)
        self.ax.text(0, 0, ' G54', color=self.colors['CYAN'], fontsize=8, va='bottom')

        return has_paths

    def _fit_bounds(self):
        """(z_min, z_max, x_min, x_max) of what the picture should frame.

        That is what the tool cuts, plus the stock and the origin. The rapid
        moves are left out on purpose: they run out to the tool-change
        position, and framing them shrank the part to a sliver in one corner.
        They are still drawn and stay reachable by zooming out.
        """
        paths = self.paths_cache
        points = []  # (x, z)
        for key in ('cut', 'comp_cut', 'collisions'):
            for start, end, _line in paths.get(key, []):
                points.extend((start, end))
        for key in ('arc', 'comp_arc'):
            for arc in paths.get(key, []):
                points.extend((arc['start'], arc['end']))
        stock = paths.get('stock')
        if stock:
            points.append((stock['diameter'], stock['z_front']))
            points.append((0.0, stock['z_front'] - stock['length']))
        if not points:
            # Nothing is cut; a program of rapids alone still wants a frame.
            for start, end, _line in paths['rapid']:
                points.extend((start, end))
        if not points:
            return None
        # The centre line and the programmed zero: G54 is always drawn.
        points.append((0.0, 0.0))

        xs = [p[0] for p in points]
        zs = [p[1] for p in points]
        return min(zs), max(zs), min(xs), max(xs)

    @staticmethod
    def _range_changed(new, old) -> bool:
        """True when a view limit pair changed in size by more than 20%."""
        old_range = old[1] - old[0]
        return abs((new[1] - new[0]) - old_range) / max(old_range, 0.1) > 0.2

    def _fit_view(self):
        """Frames the work, unless the user has taken over the zoom.

        Small edits leave the view where it is -- re-framing on every keystroke
        makes the picture jump while typing -- and only a change of more than
        20% in either direction re-frames it. The stored limits are applied
        every time, because clear() throws the old ones away.
        """
        bounds = self._fit_bounds()
        if bounds is not None and not self._manual_zoom:
            z_min, z_max, x_min, x_max = bounds
            z_pad = max(5.0, (z_max - z_min) * 0.1)
            # X is a diameter: 10 of it is the same 5 mm of radius as the Z pad.
            x_pad = max(10.0, (x_max - x_min) * 0.1)
            new_xlim = (z_min - z_pad, z_max + z_pad)
            new_ylim = (x_min - x_pad, x_max + x_pad)

            if (self._last_xlim is None or self._last_ylim is None
                    or self._range_changed(new_xlim, self._last_xlim)
                    or self._range_changed(new_ylim, self._last_ylim)):
                self._last_xlim = new_xlim
                self._last_ylim = new_ylim

        self._apply_view()

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
        self._index_moves()

        if not self._draw_scene():
            self._draw_empty()
            return

        self._fit_view()

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

        # During a playback the ring follows the move being drawn, rather than
        # sitting on the first move of the line for the whole cycle.
        if self.live_step and self.current_line == self.live_max_line:
            target_pos = self.step_end(self.current_line, self.live_step) or target_pos

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
                # White, not red: red already means a collision here.
                self.highlight_artist = self.ax.plot(target_pos[1], target_pos[0],
                                                     marker='o',
                                                     color=self.colors.get('WHITE', '#EDEDED'),
                                                     markersize=10, markeredgewidth=2,
                                                     markerfacecolor='none', zorder=10)[0]
            else:
                # Only update position (much faster!)
                self.highlight_artist.set_data([target_pos[1]], [target_pos[0]])
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

        # Distances are measured as drawn: X is a diameter, so it counts half.
        # Search all segments
        for segment_list in [self.paths_cache['rapid'], self.paths_cache['cut']]:
            for segment in segment_list:
                (x1, z1), (x2, z2), line = segment
                # Distance to end point
                dist = math.sqrt(((x2 - click_x) / 2)**2 + (z2 - click_z)**2)
                if dist < min_dist:
                    min_dist = dist
                    closest_line = line

        # Check tool changes
        for tc in self.paths_cache['tool_changes']:
            dist = math.sqrt(((tc['x'] - click_x) / 2)**2 + (tc['z'] - click_z)**2)
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
            # Forget the view too: a small zoom is within the 20% the fit
            # tolerates, so the old limits would otherwise survive the reset.
            self._last_xlim = None
            self._last_ylim = None
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
            closest_line = self._find_closest_line(event.ydata, event.xdata)
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
        """Sets the maximum line for live drawing (only draws up to this line)

        Every move of that line is drawn; set_live_step shows fewer of them.
        """
        if self.live_max_line != line_num or self.live_step is not None:
            self.live_max_line = line_num
            self.live_step = None
            # Only redraw plot if paths exist
            if self.paths_cache:
                self._redraw_live()

    def clear_live_max_line(self):
        """Removes the live drawing limit (all lines will be drawn again)"""
        if self.live_max_line is not None or self.live_step is not None:
            self.live_max_line = None
            self.live_step = None
            if self.paths_cache:
                self._redraw_live()

    def set_live_step(self, line_num: int, step: int):
        """Draws everything before `line_num`, and the first `step` moves of it.

        For playing a cycle back move by move: a G71 is one block, but the
        passes it cuts are what the operator is watching.
        """
        if self.live_max_line != line_num or self.live_step != step:
            self.live_max_line = line_num
            self.live_step = step
            if self.paths_cache:
                self._redraw_live()

    def steps_in_line(self, line_num: int) -> int:
        """How many moves the line makes: 1 for an ordinary block, many for a cycle."""
        return len(self._moves_of_line.get(line_num, ()))

    def step_end(self, line_num: int, step: int):
        """Where the `step`-th move (counting from 1) of the line ends, as (x, z)."""
        moves = self._moves_of_line.get(line_num, ())
        if not 1 <= step <= len(moves):
            return None
        kind, index = moves[step - 1]
        if kind == 'arc':
            return self.paths_cache['arc'][index]['end']
        return self.paths_cache[kind][index][1]

    def skipped_lines(self):
        """Lines that only describe a roughing contour; a playback goes past them."""
        return self.paths_cache['skipped'] if self.paths_cache else frozenset()

    def _index_moves(self):
        """Works out each move's place in its line, after every parse."""
        self._moves_of_line = {}
        self._rank = {}
        for kind, index, line in self.paths_cache['order']:
            moves = self._moves_of_line.setdefault(line, [])
            self._rank[(kind, index)] = len(moves)
            moves.append((kind, index))
        # Collisions on straight moves are the same segment object as the
        # move, so they can borrow its place in the line.
        self._move_of = {id(self.paths_cache[kind][index]): (kind, index)
                         for (kind, index) in self._rank if kind != 'arc'}

    def _shown(self, line: int, move=None) -> bool:
        """Whether something filed under `line` is drawn at this point of the playback.

        `move` is (list, index) for a move that has a place in its line. Without
        one -- a tool change, a compensated path -- the line alone decides.
        """
        if self.live_max_line is None or line < self.live_max_line:
            return True
        if line > self.live_max_line:
            return False
        rank = self._rank.get(move) if move is not None else None
        return self.live_step is None or rank is None or rank < self.live_step

    def _redraw_live(self):
        """Redraws plot without cache check (for live drawing)"""
        # Jump directly to drawing, without hash check
        if not self.pending_code or not self.paths_cache:
            return

        self._draw_scene()

        # Keep zoom if present
        self._apply_view()

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
        info_lines = ["LIVE POSITION"]
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
