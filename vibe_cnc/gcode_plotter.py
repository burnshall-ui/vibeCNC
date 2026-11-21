# gcode_plotter.py — 2D workpiece visualization for Fanuc lathes
import math
import hashlib
import time
import numpy as np
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QSizePolicy, QMenu
from PyQt6.QtCore import QTimer, pyqtSignal, Qt
from PyQt6.QtGui import QCursor
import matplotlib
matplotlib.use('QtAgg')
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.collections import LineCollection
from matplotlib.colors import to_rgba
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter
from matplotlib.path import Path
import matplotlib.patches as patches


# GCodeParser lives in gcode_parser so it stays importable without a GUI stack.
# Re-exported here because this module has always been its import site.
from vibe_cnc.gcode_parser import GCodeParser
from vibe_cnc.arc_geometry import arc_thetas, sample_arc
from vibe_cnc.stock_model import StockPlayback, body_for, tool_at

# The look: graphite under the part, and Fanuc yellow (`ui.fanuc_yellow` in
# config.yaml) for the move being made and nothing else, so it stays a signal.
BACKGROUND = '#14161A'
GRID = '#24272D'
SPINE = '#3A3F47'
AXIS_TEXT = '#8A929E'
LABEL = '#6B7380'
MARKER = '#C9CED6'
CENTRE_LINE = '#566070'
# The chuck zone: red like a collision, but darker, so its boundary line is
# never taken for one.
CHUCK = '#B23A48'

# One colour per kind of move. The tests read them from here.
PATH_COLOURS = {
    'rapid': '#8C95A3',
    'cut': '#3FA7FF',
    'finish': '#58E86A',
    'thread': '#F472B6',
    'drill': '#B48CFF',
    'comp': '#56D6FF',
    'collision': '#FF5468',
}
# The moves a playback has not reached yet.
GHOST_ALPHA = 0.28
# Chords an arc ahead of the playback is drawn with.
ARC_CHORDS = 24

# The part: a hatched section above the centre line, the turned surface below.
SECTION_FILL = '#3A4350'
SECTION_HATCH = '#4C5666'
SECTION_EDGE = '#D5DCE5'
VIEW_EDGE = '#B9C2CE'
BLANK_EDGE = '#5A626E'
STEEL = np.array([0.47, 0.52, 0.58])
# A step in diameter this big (mm) is a shoulder, drawn as a line across the
# view. Smaller ones are the slices of a chamfer or a radius.
SHOULDER_MIN = 1.0

# A datum mark: a circle with two opposite quarters filled.
ORIGIN_MARK = Path.make_compound_path(Path.wedge(0, 90), Path.wedge(180, 270))


def _segments(points, mirror=False):
    """A polyline of (X, Z) points as ((z, x), (z, x)) segments.

    Stretches that lie on the centre line are left out: that is where the
    section ends, and the centre line is drawn there already.
    """
    sign = -1.0 if mirror else 1.0
    return [((z0, sign * x0), (z1, sign * x1))
            for (x0, z0), (x1, z1) in zip(points, points[1:])
            if not (x0 == 0.0 and x1 == 0.0)]


def _turned_surface(model, rows=120, columns=600):
    """The lower half of the part as an RGBA image, lit like turned steel.

    Each column is a slice of the model, each row a diameter from the centre
    line down to the blank's. A slice is a cylinder seen side on: its surface
    faces the viewer at the centre line and turns away towards the edge, so
    the light it catches follows the height over its radius.

    At most `columns` slices are sampled: the edges are drawn exactly on top,
    and finer shading costs time on every playback step without showing.
    """
    every = max(1, math.ceil(len(model.outer) / columns))
    outer = np.asarray(model.outer[::every], dtype=float)
    solid = outer > np.asarray(model.inner[::every], dtype=float) + 1e-9
    reach = np.where(solid & (outer > 0.0), 2.0 * outer, 0.0)[None, :]
    height = np.linspace(0.0, -model.diameter, rows)[:, None]
    inside = (height >= -reach) & (reach > 0.0)
    t = np.clip(height / np.where(reach > 0.0, reach, 1.0), -1.0, 0.0)
    facing = np.sqrt(1.0 - t * t)

    light = np.array([-0.62, 0.78]) / np.hypot(-0.62, 0.78)
    half = light + np.array([0.0, 1.0])
    half /= np.hypot(*half)
    diffuse = np.clip(t * light[0] + facing * light[1], 0.0, 1.0)
    shine = np.clip(t * half[0] + facing * half[1], 0.0, 1.0) ** 28
    rim = np.exp(-((t + 0.93) / 0.05) ** 2)
    # Turning marks: a faint grain along Z, the same every time it is drawn.
    grain = 1.0 + 0.022 * np.convolve(np.random.default_rng(7).normal(0, 1, outer.size),
                                      np.ones(4) / 4, mode='same')

    colour = STEEL * (0.10 + 0.62 * diffuse)[..., None] * grain[None, :, None]
    colour += (1.0 - diffuse)[..., None] * np.array([0.0, 0.010, 0.035])
    colour += shine[..., None] * np.array([0.90, 0.94, 1.0]) * 0.34
    colour += rim[..., None] * np.array([0.55, 0.62, 0.70]) * 0.22
    return np.dstack([np.clip(colour, 0.0, 1.0), inside.astype(float)])


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
        # What is left of the blank, move by move. Rebuilt after every parse.
        self._stock_playback = None
        self._stock_cache = None  # (key, shapes) of the stock last drawn
        self._drill_lines = frozenset()  # Lines whose cuts a drill makes
        # Half section: the part's view drawn below the centre line too. It
        # doubles the height the part needs; the right-click menu turns it off.
        self.mirror = True
        # The layers (see _draw_scene): the artists of the scene by key, the
        # moves drawn as made, the two saved backgrounds, the yellow move.
        self._scene = {}
        self._done_moves = set()
        self._ahead_for = None
        self._tools_shown = None
        self._stock_drawn = None
        self._order_index = {}
        self._static_bg = None
        self._scene_bg = None
        self._glow = []
        # A move running its course: see _start_glide. 50 frames a second at most.
        self._glide = None
        self._glide_timer = QTimer(self)
        self._glide_timer.setInterval(20)
        self._glide_timer.timeout.connect(self._glide_frame)

        # Matplotlib Figure
        self.fig = Figure(figsize=(6, 4), dpi=100, facecolor=colors.get('BG_DARK', '#1A1A1A'))
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.ax = self.fig.add_subplot(111)

        # Mouse Events
        self.canvas.mpl_connect('scroll_event', self._on_scroll)
        self.canvas.mpl_connect('button_press_event', self._on_mouse_press)
        self.canvas.mpl_connect('button_release_event', self._on_mouse_release)
        self.canvas.mpl_connect('motion_notify_event', self._on_mouse_move)
        self.canvas.mpl_connect('resize_event', self._on_resize)
        self.canvas.mpl_connect('draw_event', self._on_draw)

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
        """Configures the plot layout: graphite, quiet axes, grid underneath."""
        self.ax.set_facecolor(BACKGROUND)
        # Z along the horizontal with the chuck to the left, X up the vertical.
        # That is how every lathe control draws it, and it matches the shape of
        # both the part (long in Z, small in X) and this panel (wide and short).
        self.ax.set_xlabel('Z (mm)', color=AXIS_TEXT, fontsize=9)
        self.ax.set_ylabel('X (Ø mm)', color=AXIS_TEXT, fontsize=9)
        self.ax.tick_params(colors=AXIS_TEXT, labelsize=8)
        # Below the centre line the half section mirrors the part, and a
        # diameter is not negative there either.
        self.ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _pos: f"{abs(value):g}"))
        self.ax.set_axisbelow(True)
        self.ax.grid(True, color=GRID, linestyle='-', linewidth=0.6)
        self.ax.spines['bottom'].set_color(SPINE)
        self.ax.spines['left'].set_color(SPINE)
        self.ax.spines['top'].set_visible(False)
        self.ax.spines['right'].set_visible(False)
        self.fig.tight_layout()

    def _draw_empty(self):
        """Shows empty plot with hint text"""
        self._stop_glide()
        self.ax.clear()
        self._scene = {}
        self._glow = []
        self.highlight_artist = None
        self.live_text = None
        self._invalidate()
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
        self._invalidate()

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
        """Stops the timers before the widgets they fire into go away."""
        self.update_timer.stop()
        self._stop_glide()

    def _draw_chuck(self):
        """Draws the chuck zone the collision check actually uses.

        Bounded by the jaw diameter when one is configured, so the picture and
        the check agree. Without a diameter the check treats every diameter as
        blocked, and the hatching spans the full width to say so.
        """
        diameter = self.parser.chuck_diameter
        colour = CHUCK
        # Faint: it is a boundary to stay out of, and it can fill half the
        # picture when the view is zoomed out.
        style = dict(linewidth=0.8, edgecolor=to_rgba(colour, 0.30),
                     facecolor=to_rgba(colour, 0.05), hatch='//', zorder=0)

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
        self.ax.axvline(x=self.parser.chuck_z, color=colour, linestyle=':',
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
        return self.ax.add_patch(patches.Arc((cz, cx), 2 * radius, 4 * radius,
                                             angle=0, theta1=90.0 - theta2,
                                             theta2=90.0 - theta1, **style))

    def _draw_blank(self):
        """The blank as it came, dashed: part of the static layer.

        What has been cut away shows where it was; the material left is drawn
        over it in the scene layer, by _sync_stock.
        """
        stock = self.paths_cache.get('stock') if self.paths_cache else None
        if not stock:
            return
        z_back = stock['z_front'] - stock['length']
        diameter = stock['diameter']
        blank = dict(linewidth=0.8, linestyle=(0, (4, 3)), edgecolor=BLANK_EDGE,
                     facecolor=(1.0, 1.0, 1.0, 0.03), zorder=0.6)
        # The upper half of the section, with the centre line as its bottom
        # edge -- the way the control draws a turned part.
        self.ax.add_patch(patches.Rectangle((z_back, 0.0), stock['length'], diameter,
                                            gid='stock', **blank))
        if self.mirror:
            self.ax.add_patch(patches.Rectangle((z_back, -diameter), stock['length'],
                                                diameter, gid='stock_view', **blank))
        # At the far end, clear of the rapids that arrive at the front face.
        self.ax.text(z_back, diameter, f" STOCK Ø{diameter:g} × {stock['length']:g}",
                     ha='left', va='bottom', color=LABEL, fontsize=7)

    def _stock_shapes(self, model) -> dict:
        """What the section and the view are drawn from, for this state of the stock.

        Kept until the material changes. A playback redraws on every move, and
        most moves -- rapids, retracts, moves in the air -- cut nothing.
        """
        key = (id(model), hash(tuple(model.outer)), hash(tuple(model.inner)))
        if self._stock_cache is not None and self._stock_cache[0] == key:
            return self._stock_cache[1]

        outlines, section, view, hidden = [], [], [], []
        for first, stop in model.pieces():
            outer = model.edge('outer', first, stop)
            inner = model.edge('inner', first, stop)
            outlines.append([(z, x) for x, z in outer + inner[::-1]])
            section += _segments(outer) + _segments(inner)
            view += _segments(outer, mirror=True)
            hidden += _segments(inner, mirror=True)
            # The back face and the front face: from the bore to the outside
            # in the section, from the centre line in the view.
            for i in (0, -1):
                z = outer[i][1]
                section.append(((z, inner[i][0]), (z, outer[i][0])))
                view.append(((z, 0.0), (z, -outer[i][0])))
        # A shoulder is the edge of a face, seen side on: a line across the
        # whole half, not only the step.
        radii, cores = model.outer, model.inner
        for i in range(len(radii) - 1):
            if (2.0 * abs(radii[i + 1] - radii[i]) > SHOULDER_MIN
                    and radii[i] > cores[i] and radii[i + 1] > cores[i + 1]):
                z = model.z_back + (i + 1) * model.cell
                view.append(((z, 0.0), (z, -2.0 * max(radii[i], radii[i + 1]))))

        shapes = {'outlines': outlines, 'section': section, 'view': view,
                  'hidden': hidden, 'surface': None}
        self._stock_cache = (key, shapes)
        return shapes

    def _add_shape(self, patch):
        """Adds a patch without matplotlib working out data limits from it.

        That costs milliseconds on an outline of thousands of points, and the
        view is framed by _fit_view anyway.
        """
        return self.ax.add_artist(patch)

    def _draw_section(self, shapes) -> list:
        """The material left, above the centre line, as a hatched section."""
        artists = []
        for xy in shapes['outlines']:
            artists.append(self._add_shape(patches.Polygon(
                xy, closed=True, facecolor=SECTION_FILL, edgecolor='none',
                zorder=0.65, gid='material')))
            # The hatching on a patch of its own: its colour is the patch's
            # edge colour, and this one has no edge to draw.
            artists.append(self._add_shape(patches.Polygon(
                xy, closed=True, facecolor='none', edgecolor=SECTION_HATCH,
                linewidth=0, hatch='////', zorder=0.66)))
        artists.append(self.ax.add_collection(
            LineCollection(shapes['section'], colors=SECTION_EDGE, linewidths=1.1, zorder=0.7),
            autolim=False))
        return artists

    def _draw_view(self, model, shapes) -> list:
        """The material left, below the centre line, as the turned surface.

        Lit as a cylinder seen from the front. The bore is hidden in a view,
        so it is drawn dashed, the way a drawing shows a hidden edge.
        """
        if shapes['surface'] is None:
            shapes['surface'] = _turned_surface(model)
        image = self.ax.imshow(shapes['surface'],
                               extent=(model.z_back, model.z_front, -model.diameter, 0.0),
                               origin='upper', aspect='auto', interpolation='bilinear',
                               zorder=0.62)
        image.set_gid('view')
        artists = [image, self.ax.add_collection(
            LineCollection(shapes['view'], colors=VIEW_EDGE, linewidths=0.9, zorder=0.7),
            autolim=False)]
        if shapes['hidden']:
            artists.append(self.ax.add_collection(
                LineCollection(shapes['hidden'], colors=VIEW_EDGE, linewidths=0.8,
                               linestyles=(0, (3, 2)), alpha=0.55, zorder=0.7),
                autolim=False))
        return artists

    def _draw_tool_changes(self, shown) -> list:
        """Marks where each tool change happens, one label per position.

        Changes usually start from the same safe point, so labelling each one
        printed T5 and T1 on top of each other.
        """
        at_position = {}
        for tc in shown:
            key = (round(tc['z'], 3), round(tc['x'], 3))
            at_position.setdefault(key, []).append(tc['tool'])

        artists = []
        for (z, x), tools in at_position.items():
            label = ", ".join(f"T{t}" for t in dict.fromkeys(tools))
            artists += self.ax.plot(z, x, marker='D', color=MARKER, markersize=5, zorder=3)
            artists.append(self.ax.text(z, x, f"  {label}", color=AXIS_TEXT, fontsize=8,
                                        va='center'))
        return artists

    def _colour_of(self, kind, line):
        """The colour of a move: its kind, and for a cut, whether a drill makes it."""
        if kind in ('cut', 'arc') and line in self._drill_lines:
            return PATH_COLOURS['drill']
        return PATH_COLOURS['cut' if kind == 'arc' else kind]

    def _points_of(self, kind, index):
        """One move as the (X, Z) points it runs through, an arc along its chords."""
        if kind == 'arc':
            return sample_arc(self.paths_cache['arc'][index], ARC_CHORDS)
        start, end, _line = self.paths_cache[kind][index]
        return [start, end]

    def _segments_of(self, kind, index):
        """One move as ((z, x), (z, x)) segments, an arc along its chords."""
        points = self._points_of(kind, index)
        return [((z0, x0), (z1, x1)) for (x0, z0), (x1, z1) in zip(points, points[1:])]

    # --- the picture in three layers ---------------------------------------
    #
    # A playback step used to clear the axes and draw everything again: 170 ms
    # on the machine this was written on, for a timer that wants a step every
    # 80. Now the picture has three layers:
    #
    #   static  axes, grid, chuck zone, the blank. canvas.draw() renders it,
    #           and only that, when the view or the program changes.
    #   scene   the material, the moves made and the moves ahead, markers.
    #           Animated artists, drawn over a copy of the static layer when
    #           the playback reaches a move, and changed one move at a time.
    #   motion  the move being made, the cursor ring, the live position. Drawn
    #           over a copy of the scene many times a second while a move
    #           glides.

    def _draw_scene(self) -> bool:
        """Clears the axes and builds every layer for the program as parsed.

        Returns whether the program has any path to draw.
        """
        self._stop_glide()
        self.ax.clear()
        self._scene = {}
        self._done_moves = set()
        self._ahead_for = None
        self._tools_shown = None
        self._stock_drawn = None
        self._invalidate()
        self.highlight_artist = None  # Invalid after clear() - will be recreated
        self.live_text = None  # Invalid after clear() - will be recreated
        self._setup_plot()

        self._draw_blank()
        self._draw_chuck()
        self._put('centre', [self.ax.axhline(0.0, color=CENTRE_LINE, linewidth=0.8,
                                             linestyle=(0, (14, 3, 2, 3)), zorder=0.8)])
        self._put('origin', self._draw_origin())
        self._glow = [self.ax.add_collection(
            LineCollection([], colors=self.colors['FANUC_YELLOW'], alpha=alpha,
                           linewidths=width, zorder=6, capstyle='round'), autolim=False)
            for width, alpha in ((7.0, 0.16), (2.2, 1.0))]
        for collection in self._glow:
            collection.set_animated(True)
        self._sync_scene()

        paths = self.paths_cache
        return bool(paths['order'] or paths.get('comp_cut') or paths.get('comp_arc'))

    def _put(self, key, artists):
        """Files artists in the scene layer under `key`."""
        artists = list(artists)
        for artist in artists:
            artist.set_animated(True)
        self._scene.setdefault(key, []).extend(artists)
        return artists

    def _drop(self, key):
        for artist in self._scene.pop(key, ()):
            artist.remove()

    def _done(self, i, kind, index, line) -> bool:
        """Whether move i of the program counts as made: reached, and not still gliding."""
        if self._glide is not None and self._glide['index'] == i:
            return False
        return self._shown(line, (kind, index))

    def _sync_scene(self):
        """Brings the scene layer to the playback's position.

        Only what changed is touched: a step adds the line of the move it
        reached and takes it out of the moves ahead. The material is redrawn
        when the cut changed it.
        """
        paths = self.paths_cache
        order = paths['order']
        done = {i for i, (kind, index, line) in enumerate(order)
                if self._done(i, kind, index, line)}

        for i in self._done_moves - done:
            self._drop(('move', i))
        for i in sorted(done - self._done_moves):
            self._put(('move', i), self._draw_move(*order[i]))
        self._done_moves = done
        if self._ahead_for != done:
            self._sync_ahead(done)

        # Compensated paths (G41/G42): where the nose centre runs. Dashed, and
        # filed under their line only.
        comp = PATH_COLOURS['comp']
        for name in ('comp_cut', 'comp_arc'):
            for i, item in enumerate(paths.get(name, [])):
                line = item['line'] if name == 'comp_arc' else item[2]
                key = (name, i)
                if self._shown(line) and key not in self._scene:
                    if name == 'comp_arc':
                        arc = item
                        drawn = [self._draw_arc(arc, color=comp, linewidth=1.2,
                                                linestyle='--', alpha=0.9, zorder=3)]
                    else:
                        (x1, z1), (x2, z2), _line = item
                        drawn = self.ax.plot([z1, z2], [x1, x2], color=comp, linewidth=1.2,
                                             linestyle='--', alpha=0.9, zorder=3)
                    self._put(key, drawn)
                elif not self._shown(line) and key in self._scene:
                    self._drop(key)

        # Collisions. A straight one is the very segment that was filed under
        # cut or rapid, so it appears with its move.
        for i, segment in enumerate(paths['collisions']):
            (x1, z1), (x2, z2), line = segment
            move = self._move_of.get(id(segment))
            show = (self._order_index[move] in done) if move else self._shown(line)
            key = ('collision', i)
            if show and key not in self._scene:
                self._put(key, self.ax.plot([z1, z2], [x1, x2],
                                            color=PATH_COLOURS['collision'], linewidth=3,
                                            alpha=0.85, zorder=5, label='Collision!'))
            elif not show and key in self._scene:
                self._drop(key)

        shown = [tc for tc in paths['tool_changes'] if self._shown(tc['line'])]
        if self._tools_shown != [tc['line'] for tc in shown]:
            self._drop('tools')
            self._put('tools', self._draw_tool_changes(shown))
            self._tools_shown = [tc['line'] for tc in shown]

        self._sync_stock()

    def _draw_move(self, kind, index, line) -> list:
        """The artists of one move made: a line of its own, or an arc."""
        colour = self._colour_of(kind, line)
        if kind == 'arc':
            arc = self.paths_cache['arc'][index]
            return [self._draw_arc(arc, color=colour, linewidth=1.6, zorder=2)]
        (x1, z1), (x2, z2), _line = self.paths_cache[kind][index]
        if kind == 'rapid':
            return self.ax.plot([z1, z2], [x1, x2], color=colour, linewidth=0.9,
                                linestyle=(0, (1.5, 2.5)), alpha=0.8, zorder=1)
        # Thread passes run along Z like turning; the colour is what makes
        # them read as a thread. The finishing run lies a fraction of a
        # millimetre off the roughing, so it is drawn over it in a colour of
        # its own.
        zorder = {'cut': 2, 'thread': 2.4, 'finish': 2.5}[kind]
        return self.ax.plot([z1, z2], [x1, x2], color=colour, linewidth=1.6,
                            zorder=zorder, solid_capstyle='round')

    def _sync_ahead(self, done):
        """The moves not made yet, faint, so the program ahead stays in view.

        One collection per line style rather than a line each: every move
        that has been made is a line of its own, and only those.
        """
        self._drop('ahead')
        styles = {'solid': ([], []), 'dotted': ([], [])}  # segments, colours
        for i, (kind, index, line) in enumerate(self.paths_cache['order']):
            if i in done:
                continue
            segments = self._segments_of(kind, index)
            target = styles['dotted' if kind == 'rapid' else 'solid']
            target[0].extend(segments)
            target[1].extend([self._colour_of(kind, line)] * len(segments))
        for style, (segments, colours) in styles.items():
            if segments:
                self._put('ahead', [self.ax.add_collection(LineCollection(
                    segments, colors=colours, alpha=GHOST_ALPHA, zorder=0.9,
                    linewidths=0.8 if style == 'dotted' else 1.0,
                    linestyles=(0, (1.5, 2.5)) if style == 'dotted' else 'solid'),
                    autolim=False)])
        self._ahead_for = set(done)

    def _sync_stock(self):
        """The material left at this point of the playback, redrawn when it changed."""
        if not self.paths_cache.get('stock') or self._stock_playback is None:
            return
        model = self._stock_playback.at(self._moves_reached())
        if model is None:
            return
        shapes = self._stock_shapes(model)
        if shapes is self._stock_drawn:
            return
        self._drop('stock')
        artists = self._draw_section(shapes)
        if self.mirror:
            artists += self._draw_view(model, shapes)
        self._put('stock', artists)
        self._stock_drawn = shapes

    def _draw_origin(self) -> list:
        """The programmed zero, as a drawing marks a datum."""
        artists = self.ax.plot(0, 0, marker='o', markersize=9, markerfacecolor=BACKGROUND,
                               markeredgecolor=MARKER, markeredgewidth=1.1, zorder=7)
        artists += self.ax.plot(0, 0, marker=ORIGIN_MARK, markersize=9, color=MARKER,
                                markeredgewidth=0, zorder=7.1)
        artists.append(self.ax.text(0, 0, '   G54', color=AXIS_TEXT, fontsize=8, va='top',
                                    zorder=7))
        return artists

    def _fit_bounds(self):
        """(z_min, z_max, x_min, x_max) of what the picture should frame.

        That is what the tool cuts, plus the stock and the origin. The rapid
        moves are left out on purpose: they run out to the tool-change
        position, and framing them shrank the part to a sliver in one corner.
        They are still drawn and stay reachable by zooming out.
        """
        paths = self.paths_cache
        points = []  # (x, z)
        for key in ('cut', 'finish', 'thread', 'comp_cut', 'collisions'):
            for start, end, _line in paths.get(key, []):
                points.extend((start, end))
        for key in ('arc', 'comp_arc'):
            for arc in paths.get(key, []):
                points.extend((arc['start'], arc['end']))
        stock = paths.get('stock')
        if stock:
            points.append((stock['diameter'], stock['z_front']))
            points.append((0.0, stock['z_front'] - stock['length']))
            if self.mirror:
                # The view below the centre line is the same size again.
                points.append((-stock['diameter'], stock['z_front'] - stock['length']))
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
        self._stock_playback = StockPlayback(self.paths_cache, self.parser.tool_items)
        self._drill_lines = self._lines_drilled()

        if not self._draw_scene():
            self._draw_empty()
            return

        self._fit_view()
        self._update_motion()
        # Async rendering for better performance
        self.canvas.draw_idle()

    # --- rendering the layers ----------------------------------------------

    def _invalidate(self):
        """The saved layers no longer match the view; the next full draw renews them."""
        self._static_bg = None
        self._scene_bg = None

    def _on_draw(self, _event):
        """After a full draw, which rendered the static layer: keep a copy of
        it, then draw the scene and the motion over it."""
        try:
            self._static_bg = self.canvas.copy_from_bbox(self.fig.bbox)
            self._draw_layer(self._scene_artists())
            self._scene_bg = self.canvas.copy_from_bbox(self.fig.bbox)
            self._draw_layer(self._motion_artists())
        except Exception:
            # A canvas without a renderer yet, or one being torn down: the next
            # full draw tries again.
            self._invalidate()

    def _scene_artists(self):
        return [artist for artists in self._scene.values() for artist in artists]

    def _motion_artists(self):
        artists = list(self._glow)
        for artist in (self.highlight_artist, self.live_text):
            if artist is not None and artist.axes is self.ax:
                artists.append(artist)
        return artists

    def _draw_layer(self, artists):
        for artist in sorted(artists, key=lambda a: a.get_zorder()):
            if artist.get_visible():
                self.fig.draw_artist(artist)

    def _render_scene(self):
        """Redraws the scene and the motion over the saved static layer."""
        if self._static_bg is None or not self.canvas.isVisible():
            self.canvas.draw_idle()
            return
        self.canvas.restore_region(self._static_bg)
        self._draw_layer(self._scene_artists())
        self._scene_bg = self.canvas.copy_from_bbox(self.fig.bbox)
        self._draw_layer(self._motion_artists())
        self.canvas.blit(self.fig.bbox)

    def _render_motion(self):
        """Redraws only the motion over the saved scene."""
        if self._scene_bg is None or not self.canvas.isVisible():
            self.canvas.draw_idle()
            return
        self.canvas.restore_region(self._scene_bg)
        self._draw_layer(self._motion_artists())
        self.canvas.blit(self.fig.bbox)

    # --- the move being made -------------------------------------------------

    def _current_move(self):
        """(order index, kind, index) of the move the playback is on, or None."""
        if self.live_max_line is None or not self.paths_cache:
            return None
        if self._glide is not None:
            i = self._glide['index']
        else:
            i = self._moves_reached() - 1
            if i < 0:
                return None
        kind, index, _line = self.paths_cache['order'][i]
        return i, kind, index

    def _tool_point(self):
        """Where the tool is, (x, z): along the gliding move, or at the end of
        the move just made. None outside a playback."""
        if self._glide is not None:
            return self._glide['at']
        current = self._current_move()
        if current is None:
            return None
        _i, kind, index = current
        return self._points_of(kind, index)[-1]

    def _update_motion(self):
        """Puts the motion layer where the playback is."""
        segments = []
        current = self._current_move()
        if current is not None:
            _i, kind, index = current
            if self._glide is not None:
                segments = self._glide['done']
            else:
                segments = self._segments_of(kind, index)
        for collection in self._glow:
            collection.set_segments(segments)
        self._draw_highlight()
        self._refresh_live_text()

    def _start_glide(self, glide_ms, made_before):
        """Lets the move just reached run its course over glide_ms.

        Only a move newly reached glides: one that was already made before
        this step -- a jump back, a repeat -- is shown where it is.
        """
        current = self._current_move() if glide_ms > 0 else None
        if current is None:
            return
        i, kind, index = current
        if i in made_before or i not in self._done_moves:
            return
        points = self._points_of(kind, index)
        # Length as drawn on a lathe: X is a diameter, so it counts half.
        steps = [math.hypot((x1 - x0) / 2.0, z1 - z0)
                 for (x0, z0), (x1, z1) in zip(points, points[1:])]
        self._glide = {'index': i, 'points': points, 'steps': steps,
                       'length': sum(steps), 'start': time.monotonic(),
                       'duration': glide_ms / 1000.0, 'at': points[0], 'done': []}
        self._sync_scene()          # the move goes back among the moves ahead
        self._place_glide(0.0)
        self._glide_timer.start()

    def _place_glide(self, progress):
        """Moves the gliding tool to `progress` (0 to 1) of its move."""
        glide = self._glide
        points, steps = glide['points'], glide['steps']
        left = progress * glide['length']
        done = []
        at = points[0]
        for (p0, p1), step in zip(zip(points, points[1:]), steps):
            if step <= 0.0:
                continue
            if left >= step:
                done.append(((p0[1], p0[0]), (p1[1], p1[0])))
                left -= step
                at = p1
                continue
            t = left / step
            at = (p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t)
            done.append(((p0[1], p0[0]), (at[1], at[0])))
            break
        glide['at'] = at
        glide['done'] = done

    def _glide_frame(self):
        glide = self._glide
        if glide is None:
            self._glide_timer.stop()
            return
        progress = (time.monotonic() - glide['start']) / max(glide['duration'], 1e-6)
        if progress >= 1.0:
            self._finish_glide()
            return
        self._place_glide(progress)
        self._update_motion()
        self._render_motion()

    def _finish_glide(self):
        """The gliding move arrives: it counts as made, and the material follows."""
        if self._glide is None:
            return
        self._stop_glide()
        if self.paths_cache:
            self._sync_scene()
            self._update_motion()
            self._render_scene()

    def _stop_glide(self):
        self._glide_timer.stop()
        self._glide = None

    def _draw_highlight(self):
        """The white ring: at the end of the cursor's line, or on the tool
        while a playback runs."""
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
        if self.live_max_line is not None and self.current_line == self.live_max_line:
            target_pos = self._tool_point() or target_pos

        # Check tool changes
        if not target_pos:
            for tc in self.paths_cache['tool_changes']:
                if tc['line'] == self.current_line:
                    target_pos = (tc['x'], tc['z'])
                    break

        if target_pos:
            # Create marker or update position
            if self.highlight_artist is None:
                # White, not red: red already means a collision here.
                self.highlight_artist = self.ax.plot(target_pos[1], target_pos[0],
                                                     marker='o',
                                                     color=self.colors.get('WHITE', '#EDEDED'),
                                                     markersize=10, markeredgewidth=2,
                                                     markerfacecolor='none', zorder=10)[0]
                self.highlight_artist.set_animated(True)
            else:
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
        for segment_list in [self.paths_cache['rapid'], self.paths_cache['cut'],
                             self.paths_cache['finish'], self.paths_cache['thread']]:
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

        self._invalidate()
        self.canvas.draw_idle()

    def _on_mouse_press(self, event):
        """Mouse press event"""
        if event.inaxes != self.ax:
            return

        # Double click: reactivate auto-zoom
        if event.dblclick and event.button == 1:
            self._fit_again()
            return

        # Right click: what the picture shows
        if event.button == 3:
            self._show_view_menu()
            return

        # Left click: store start position for click/drag detection
        if event.button == 1:
            self._click_start = (event.x, event.y)  # Pixel coordinates
            self._pan_start = (event.xdata, event.ydata)  # Data coordinates
            self._is_panning = False

    def _fit_again(self):
        """Frames the work again and forgets any zoom of the user's."""
        self._manual_zoom = False
        # Forget the view too: a small zoom is within the 20% the fit
        # tolerates, so the old limits would otherwise survive the reset.
        self._last_xlim = None
        self._last_ylim = None
        self._last_code_hash = None  # Invalidate cache
        if self.pending_code:
            self._do_update()

    def _show_view_menu(self):
        menu = QMenu(self)
        half = menu.addAction("Half section: the part's view below the centre line")
        half.setCheckable(True)
        half.setChecked(self.mirror)
        fit = menu.addAction("Fit the view (double-click)")
        chosen = menu.exec(QCursor.pos())
        if chosen is half:
            self.set_mirror(not self.mirror)
        elif chosen is fit:
            self._fit_again()

    def set_mirror(self, on: bool):
        """Turns the half section on or off and frames the picture for it."""
        if self.mirror == bool(on):
            return
        self.mirror = bool(on)
        self._fit_again()

    def _lines_drilled(self) -> frozenset:
        """The lines whose cuts a drill makes, so they get the drill's colour."""
        changes = sorted((change['line'], change['tool'])
                         for change in self.paths_cache['tool_changes'])
        tools = self.parser.tool_items
        return frozenset(line for _start, _end, line in self.paths_cache['cut']
                         if body_for(tools.get(tool_at(line, changes))).kind == 'drill')

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

            self._invalidate()
            self.canvas.draw_idle()

    def highlight_line(self, line_num: int):
        """Highlights the current line in the plot (cursor sync)"""
        # Optimization: Only update on line change
        if self.current_line == line_num:
            return

        self.current_line = line_num

        if not self.paths_cache:
            return

        self._draw_highlight()
        self._render_motion()

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
        self._refresh_live_text()
        self._render_motion()

    def clear_live_position(self):
        """Removes the live position display"""
        self.live_x = None
        self.live_z = None
        self.live_tool = None
        self.live_s = None
        self.live_f = None
        if self.live_text:
            self.live_text.set_visible(False)
            self._render_motion()

    def set_live_max_line(self, line_num: int, glide_ms: int = 0):
        """Sets the maximum line for live drawing (only draws up to this line)

        Every move of that line is drawn; set_live_step shows fewer of them.
        With glide_ms the last of them runs its course over that time, the
        tool travelling along it, before it counts as made.
        """
        if self.live_max_line != line_num or self.live_step is not None:
            self.live_max_line = line_num
            self.live_step = None
            self._advance(glide_ms)

    def clear_live_max_line(self):
        """Removes the live drawing limit (all lines will be drawn again)"""
        if self.live_max_line is not None or self.live_step is not None:
            self.live_max_line = None
            self.live_step = None
            self._advance(0)

    def set_live_step(self, line_num: int, step: int, glide_ms: int = 0):
        """Draws everything before `line_num`, and the first `step` moves of it.

        For playing a cycle back move by move: a G71 is one block, but the
        passes it cuts are what the operator is watching. glide_ms as for
        set_live_max_line.
        """
        if self.live_max_line != line_num or self.live_step != step:
            self.live_max_line = line_num
            self.live_step = step
            self._advance(glide_ms)

    def _advance(self, glide_ms=0):
        """The playback moved: the scene follows, one move at a time.

        A move still gliding arrives at once, so the scene never falls behind
        the playback; the move reached now glides in its place.
        """
        if not self.paths_cache:
            return
        self._stop_glide()
        made_before = set(self._done_moves)
        self._sync_scene()
        self._start_glide(glide_ms, made_before)
        self._update_motion()
        self._render_scene()

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
        self._order_index = {}
        for i, (kind, index, line) in enumerate(self.paths_cache['order']):
            moves = self._moves_of_line.setdefault(line, [])
            self._rank[(kind, index)] = len(moves)
            self._order_index[(kind, index)] = i
            moves.append((kind, index))
        # Collisions on straight moves are the same segment object as the
        # move, so they can borrow its place in the line.
        self._move_of = {id(self.paths_cache[kind][index]): (kind, index)
                         for (kind, index) in self._rank if kind != 'arc'}

    def _moves_reached(self) -> int:
        """How many moves have been made, in the order they run.

        All of them outside a playback, so the static picture is the finished
        part. A move still gliding is not made yet.
        """
        order = self.paths_cache['order']
        for count, (kind, index, line) in enumerate(order):
            if not self._done(count, kind, index, line):
                return count
        return len(order)

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

    def _refresh_live_text(self):
        """The live position box: where the tool is, its tool, speed and feed.

        While a move glides, X and Z follow the tool rather than jump to the
        end of the move.
        """
        if self.live_x is None and self.live_z is None:
            if self.live_text:
                self.live_text.set_visible(False)
            return

        x, z = self.live_x, self.live_z
        if self._glide is not None:
            x, z = self._glide['at']

        info_lines = ["LIVE POSITION"]
        if x is not None:
            info_lines.append(f"X: {x:.2f} Ø")
        if z is not None:
            info_lines.append(f"Z: {z:.2f}")
        if self.live_tool is not None and self.live_tool > 0:
            info_lines.append(f"T: {self.live_tool:02d}")
        if self.live_s is not None and self.live_s > 0:
            info_lines.append(f"S: {self.live_s}")
        if self.live_f is not None and self.live_f > 0:
            info_lines.append(f"F: {self.live_f:.2f}")
        info_text = "\n".join(info_lines)

        if self.live_text is None or not self.live_text.axes:
            self.live_text = self.ax.text(
                0.98, 0.98, info_text,
                transform=self.ax.transAxes,
                fontsize=9,
                verticalalignment='top',
                horizontalalignment='right',
                bbox=dict(boxstyle='round,pad=0.6', facecolor=BACKGROUND,
                          edgecolor=self.colors['FANUC_YELLOW'], linewidth=1.2, alpha=0.95),
                color=MARKER,
                family='monospace',
                weight='bold',
                zorder=100
            )
            self.live_text.set_animated(True)
        else:
            self.live_text.set_text(info_text)
            self.live_text.set_visible(True)
