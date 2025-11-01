# gcode_plotter.py — 2D Werkstück-Visualisierung für Fanuc Drehmaschinen
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
    """Parser für Fanuc Drehmaschinen G-Code (X=Durchmesser, Z=Länge)"""

    def __init__(self, chuck_z: float = -5.0):
        self.chuck_z = chuck_z  # Spannfutter-Grenze
        self.reset()

    def reset(self):
        self.x = 0.0  # Durchmesser
        self.z = 0.0  # Längsachse
        self.tool = 0
        self.s = 0    # Spindeldrehzahl
        self.f = 0.0  # Vorschub
        self.mode = 'G00'  # G00=Eilgang, G01=Schnitt, G02=CW-Bogen, G03=CCW-Bogen
        self.i = 0.0  # Bogen-Parameter (X-Offset Mittelpunkt)
        self.k = 0.0  # Bogen-Parameter (Z-Offset Mittelpunkt)
        # Tool Nose Radius Kompensation (G40/G41/G42)
        self.comp = 'G40'   # aktuell: G40=aus, G41=links, G42=rechts (bezogen auf Bewegungsrichtung)
        self.tnr = 0.0      # Eckenradius (mm)
        self.paths = {
            'rapid': [],       # G00 (grau gestrichelt)
            'cut': [],         # G01 (grün durchgezogen)
            'arc': [],         # G02/G03 (grün Bogen)
            'tool_changes': [], # Werkzeugwechsel
            'collisions': [],   # Kollisionen (rot)
            'comp_cut': [],     # kompensierte Bahn (G41/G42), gelb
            'comp_arc': []      # kompensierte Bögen (G41/G42), gelb
        }

    def parse(self, gcode: str) -> dict:
        """Parst G-Code und extrahiert Werkzeugbahnen"""
        self.reset()

        lines = gcode.split('\n')
        for line_num, line in enumerate(lines, 1):
            self._parse_line(line, line_num)

        # Post-Processing: Ecken verschneiden (Lookahead Corner Handling)
        self._intersect_compensated_corners()

        return self.paths

    def _parse_line(self, line: str, line_num: int):
        """Parst eine einzelne G-Code-Zeile"""
        # Kommentare entfernen
        line = re.sub(r'\(.*?\)', '', line).strip()
        if not line or line.startswith('%'):
            return

        # Modal-Codes extrahieren
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
            elif g in ['71']:  # G71 Schruppzyklus (vereinfacht)
                self.mode = 'G71'
            elif g in ['72']:  # G72 Planzyklus (vereinfacht)
                self.mode = 'G72'

        # Werkzeugwechsel
        t_match = re.search(r'T(\d+)', line, re.IGNORECASE)
        if t_match:
            self.tool = int(t_match.group(1)) // 100  # T0101 -> T1
            self.paths['tool_changes'].append({
                'x': self.x,
                'z': self.z,
                'tool': self.tool,
                'line': line_num
            })
            # Eckenradius aus Tools laden (tools.json) – optional
            try:
                from .tool_model import load_tools_json
                j = load_tools_json()
                tool_items = {int(it.get('t', 0)): it for it in j.get('tool_table', [])}
                tool_info = tool_items.get(self.tool, {})
                self.tnr = float(tool_info.get('insert_radius_mm', 0.0) or 0.0)
            except Exception:
                self.tnr = 0.0

        # X/Z/I/K-Koordinaten extrahieren
        x_match = re.search(r'X([-+]?\d*\.?\d+)', line, re.IGNORECASE)
        z_match = re.search(r'Z([-+]?\d*\.?\d+)', line, re.IGNORECASE)
        i_match = re.search(r'I([-+]?\d*\.?\d+)', line, re.IGNORECASE)
        k_match = re.search(r'K([-+]?\d*\.?\d+)', line, re.IGNORECASE)

        # S (Spindeldrehzahl) und F (Vorschub) extrahieren
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

        # Bewegung aufzeichnen (wenn sich Position ändert)
        if new_x != self.x or new_z != self.z:
            segment_data = [(self.x, self.z), (new_x, new_z), line_num]

            # Kollisions-Check (Z < Chuck-Grenze)
            if new_z < self.chuck_z or self.z < self.chuck_z:
                self.paths['collisions'].append(segment_data)

            if self.mode == 'G00':
                self.paths['rapid'].append(segment_data)
            elif self.mode == 'G01':
                self.paths['cut'].append(segment_data)
                # Kompensierte Bahn (vereinfachte TNR-Kompensation nur für Linearzüge)
                if self.comp in ('G41', 'G42') and self.tnr > 0.0:
                    (x1, z1), (x2, z2), _ = segment_data
                    dx = x2 - x1
                    dz = z2 - z1
                    seg_len = math.hypot(dx, dz)
                    if seg_len > 1e-6:
                        # Linksnormale/rechtsnormale relativ zur Bewegungsrichtung
                        nx_left = -dz / seg_len
                        nz_left = dx / seg_len
                        if self.comp == 'G41':
                            offx, offz = nx_left * self.tnr, nz_left * self.tnr
                        else:  # G42
                            offx, offz = -nx_left * self.tnr, -nz_left * self.tnr
                        cx1, cz1 = x1 + offx, z1 + offz
                        cx2, cz2 = x2 + offx, z2 + offz
                        self.paths['comp_cut'].append([(cx1, cz1), (cx2, cz2), line_num])
            elif self.mode in ['G02', 'G03']:
                # Kreisbogen: Mittelpunkt = (x + i, z + k)
                cx = self.x + self.i
                cz = self.z + self.k
                radius = math.sqrt(self.i**2 + self.k**2)
                clockwise = (self.mode == 'G02')
                arc_data = {
                    'start': (self.x, self.z),
                    'end': (new_x, new_z),
                    'center': (cx, cz),
                    'radius': radius,
                    'cw': clockwise,
                    'line': line_num
                }
                self.paths['arc'].append(arc_data)

                # Kompensierte Bögen (G41/G42)
                if self.comp in ('G41', 'G42') and self.tnr > 0.0:
                    # G41 (links): Radius vergrößern (Werkzeug außen)
                    # G42 (rechts): Radius verkleinern (Werkzeug innen)
                    if self.comp == 'G41':
                        comp_radius = radius + self.tnr
                    else:  # G42
                        comp_radius = radius - self.tnr

                    # Verhindere negative Radien (würde Fehler geben)
                    if comp_radius > 0.0:
                        self.paths['comp_arc'].append({
                            'start': (self.x, self.z),
                            'end': (new_x, new_z),
                            'center': (cx, cz),
                            'radius': comp_radius,
                            'cw': clockwise,
                            'line': line_num
                        })

            self.x = new_x
            self.z = new_z

    def _intersect_compensated_corners(self):
        """Verschneidet Ecken von kompensierten Pfaden (Lookahead Corner Handling)"""
        if len(self.paths['comp_cut']) < 2:
            return

        # Iteriere durch aufeinanderfolgende Segmente
        for i in range(len(self.paths['comp_cut']) - 1):
            seg1 = self.paths['comp_cut'][i]
            seg2 = self.paths['comp_cut'][i + 1]

            (x1, z1), (x2, z2), line1 = seg1
            (x3, z3), (x4, z4), line2 = seg2

            # Prüfe ob Segmente verbunden sind (Endpunkt von seg1 nahe Startpunkt von seg2)
            dist = math.hypot(x2 - x3, z2 - z3)
            if dist > 0.1:  # Nicht verbunden, skip
                continue

            # Berechne Schnittpunkt der beiden Geraden
            intersection = self._line_intersection(x1, z1, x2, z2, x3, z3, x4, z4)

            if intersection is not None:
                ix, iz = intersection
                # Aktualisiere Endpunkt von seg1 und Startpunkt von seg2
                self.paths['comp_cut'][i] = [(x1, z1), (ix, iz), line1]
                self.paths['comp_cut'][i + 1] = [(ix, iz), (x4, z4), line2]

    def _line_intersection(self, x1, z1, x2, z2, x3, z3, x4, z4):
        """
        Berechnet Schnittpunkt zweier Geraden (nicht Segmente, sondern unendliche Geraden).
        Gerade 1: durch (x1,z1) und (x2,z2)
        Gerade 2: durch (x3,z3) und (x4,z4)
        Returns: (x, z) oder None falls parallel
        """
        # Richtungsvektoren
        dx1 = x2 - x1
        dz1 = z2 - z1
        dx2 = x4 - x3
        dz2 = z4 - z3

        # Determinante (Kreuzprodukt in 2D)
        det = dx1 * dz2 - dz1 * dx2

        # Parallel oder identisch
        if abs(det) < 1e-10:
            return None

        # Parameter t für Gerade 1
        t = ((x3 - x1) * dz2 - (z3 - z1) * dx2) / det

        # Schnittpunkt
        ix = x1 + t * dx1
        iz = z1 + t * dz1

        return (ix, iz)


class GCodePlotterWidget(QWidget):
    """2D-Visualisierung für Drehteile mit Matplotlib"""

    line_clicked = pyqtSignal(int)  # Signal: Zeile wurde geklickt

    def __init__(self, colors: dict, chuck_z: float = -5.0, parent=None):
        super().__init__(parent)
        self.colors = colors
        self.parser = GCodeParser(chuck_z=chuck_z)
        self.current_line = None
        self.paths_cache = None
        self.highlight_artist = None  # Roter Marker für aktuelle Zeile
        self._last_code_hash = None  # Hash des letzten gezeichneten Codes
        self._plot_drawn = False  # Flag: Plot wurde gezeichnet
        self._last_xlim = None  # Letzte X-Limits für Zoom-Stabilisierung
        self._last_ylim = None  # Letzte Y-Limits für Zoom-Stabilisierung
        self._manual_zoom = False  # Flag: Benutzer hat manuell gezoomt
        self._pan_start = None  # Start-Position für Pan
        self._is_panning = False  # Flag: Pan-Modus aktiv
        self._click_start = None  # Start-Position für Click-Detection

        # Live-Position Tracking
        self.live_x = None
        self.live_z = None
        self.live_tool = None
        self.live_s = None
        self.live_f = None
        self.live_text = None  # Text-Artist für Live-Anzeige

        # Live-Drawing (nur bis zu dieser Zeile zeichnen, None = alles)
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

        # Update-Timer (Debounce für Live-Update)
        self.update_timer = QTimer()
        self.update_timer.setSingleShot(True)
        self.update_timer.timeout.connect(self._do_update)
        self.pending_code = None

        # Initial-Plot
        self._setup_plot()
        self._draw_empty()

    def _setup_plot(self):
        """Konfiguriert das Plot-Layout (Fanuc-Style)"""
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
        """Zeigt leeren Plot mit Hinweistext"""
        self.ax.clear()
        self._setup_plot()
        self.ax.text(0.5, 0.5, 'Keine Werkzeugbahnen\n(G00/G01 mit X/Z)',
                     ha='center', va='center', color='#555', fontsize=10,
                     transform=self.ax.transAxes)
        self.ax.set_xlim(-10, 50)
        self.ax.set_ylim(-50, 10)
        self.canvas.draw_idle()

    def update_plot(self):
        """Startet verzögertes Update (150ms Debounce)"""
        self.update_timer.stop()
        self.update_timer.start(150)  # Verkürzt auf 150ms (von 500ms) dank Caching

    def update_plot_immediate(self, gcode: str):
        """Sofortiges Update ohne Debounce"""
        self.pending_code = gcode
        self._do_update()

    def _do_update(self):
        """Führt das eigentliche Plot-Update durch"""
        if not self.pending_code:
            return

        # Hash berechnen - nur neu zeichnen wenn sich Code geändert hat
        code_hash = hashlib.md5(self.pending_code.encode('utf-8')).hexdigest()

        if code_hash == self._last_code_hash and self._plot_drawn:
            # Code hat sich nicht geändert - kein Redraw nötig
            return

        self._last_code_hash = code_hash
        self._plot_drawn = True

        # Parse G-Code
        self.paths_cache = self.parser.parse(self.pending_code)

        # Plot neu zeichnen
        self.ax.clear()
        self.highlight_artist = None  # Nach clear() ungültig - wird neu erstellt
        self.live_text = None  # Nach clear() ungültig - wird neu erstellt
        self._setup_plot()

        has_paths = False

        # Chuck-Zone (Spannbereich) - rote Schraffur
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

        # Eilgänge (G00) - grau gestrichelt
        for segment in self.paths_cache['rapid']:
            (x1, z1), (x2, z2), line = segment
            # Live-Drawing: Nur bis live_max_line zeichnen
            if self.live_max_line is None or line <= self.live_max_line:
                self.ax.plot([x1, x2], [z1, z2], color='#555', linestyle='--', linewidth=1, zorder=1)
                has_paths = True

        # Schnittbahnen (G01) - grün durchgezogen
        for segment in self.paths_cache['cut']:
            (x1, z1), (x2, z2), line = segment
            # Live-Drawing: Nur bis live_max_line zeichnen
            if self.live_max_line is None or line <= self.live_max_line:
                self.ax.plot([x1, x2], [z1, z2], color=self.colors['CRT_GREEN'], linewidth=2, zorder=2)
                has_paths = True

        # Kompensierte Schnittbahnen (G41/G42) - gelb gestrichelt
        for segment in self.paths_cache.get('comp_cut', []):
            (x1, z1), (x2, z2), line = segment
            if self.live_max_line is None or line <= self.live_max_line:
                self.ax.plot([x1, x2], [z1, z2], color=self.colors['FANUC_YELLOW'], linewidth=1.5,
                            linestyle='--', alpha=0.9, zorder=3)
                has_paths = True

        # Kompensierte Bögen (G41/G42) - gelb gestrichelt
        for arc in self.paths_cache.get('comp_arc', []):
            if self.live_max_line is None or arc['line'] <= self.live_max_line:
                cx, cz = arc['center']
                radius = arc['radius']
                x1, z1 = arc['start']
                x2, z2 = arc['end']

                # Winkel berechnen
                angle1 = math.degrees(math.atan2(z1 - cz, x1 - cx))
                angle2 = math.degrees(math.atan2(z2 - cz, x2 - cx))

                if arc['cw']:  # G02: Clockwise
                    if angle2 > angle1:
                        angle2 -= 360
                else:  # G03: Counter-clockwise
                    if angle1 > angle2:
                        angle1 -= 360

                arc_patch = patches.Arc((cx, cz), 2*radius, 2*radius,
                                       angle=0, theta1=angle1, theta2=angle2,
                                       color=self.colors['FANUC_YELLOW'], linewidth=1.5,
                                       linestyle='--', alpha=0.9, zorder=3)
                self.ax.add_patch(arc_patch)
                has_paths = True

        # Kreisbögen (G02/G03) - grün Bogen
        for arc in self.paths_cache['arc']:
            # Live-Drawing: Nur bis live_max_line zeichnen
            if self.live_max_line is None or arc['line'] <= self.live_max_line:
                x1, z1 = arc['start']
                x2, z2 = arc['end']
                cx, cz = arc['center']
                radius = arc['radius']

                # Winkel berechnen
                angle1 = math.degrees(math.atan2(z1 - cz, x1 - cx))
                angle2 = math.degrees(math.atan2(z2 - cz, x2 - cx))

                # Bogen zeichnen (matplotlib Arc nutzt X/Y, wir haben X/Z)
                if arc['cw']:  # G02: Clockwise
                    if angle2 > angle1:
                        angle2 -= 360
                else:  # G03: Counter-clockwise
                    if angle1 > angle2:
                        angle1 -= 360

                arc_patch = patches.Arc((cx, cz), 2*radius, 2*radius,
                                       angle=0, theta1=angle1, theta2=angle2,
                                       color=self.colors['CRT_GREEN'], linewidth=2, zorder=2)
                self.ax.add_patch(arc_patch)
                has_paths = True

        # Kollisionen (rot hervorgehoben)
        for segment in self.paths_cache['collisions']:
            (x1, z1), (x2, z2), line = segment
            # Live-Drawing: Nur bis live_max_line zeichnen
            if self.live_max_line is None or line <= self.live_max_line:
                self.ax.plot([x1, x2], [z1, z2], color='#FF0000', linewidth=3,
                            alpha=0.7, zorder=5, label='Collision!')

        # Werkzeugwechsel - gelbe Marker
        for tc in self.paths_cache['tool_changes']:
            # Live-Drawing: Nur bis live_max_line zeichnen
            if self.live_max_line is None or tc['line'] <= self.live_max_line:
                self.ax.plot(tc['x'], tc['z'], marker='o', color=self.colors['FANUC_YELLOW'],
                            markersize=6, zorder=3)
                self.ax.text(tc['x'], tc['z'], f" T{tc['tool']}", color=self.colors['FANUC_YELLOW'],
                            fontsize=8, va='center')

        if not has_paths:
            self._draw_empty()
            return

        # Auto-Zoom mit Padding
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
            # Chuck-Zone einbeziehen
            z_min = min(z_min, self.parser.chuck_z - 2)

            new_xlim = (x_min - x_pad, x_max + x_pad)
            new_ylim = (z_min - z_pad, z_max + z_pad)

            # Zoom-Stabilisierung: Nur bei signifikanten Änderungen (>20%) anpassen
            # ABER: Nur wenn der Benutzer nicht manuell gezoomt hat
            should_update_zoom = False
            if not self._manual_zoom:
                if self._last_xlim is None or self._last_ylim is None:
                    # Erster Zoom - immer setzen
                    should_update_zoom = True
                else:
                    # Prüfe ob sich Bounding Box signifikant geändert hat
                    x_range_old = self._last_xlim[1] - self._last_xlim[0]
                    x_range_new = new_xlim[1] - new_xlim[0]
                    z_range_old = self._last_ylim[1] - self._last_ylim[0]
                    z_range_new = new_ylim[1] - new_ylim[0]

                    x_change = abs(x_range_new - x_range_old) / max(x_range_old, 0.1)
                    z_change = abs(z_range_new - z_range_old) / max(z_range_old, 0.1)

                    # Update wenn Änderung > 20%
                    if x_change > 0.2 or z_change > 0.2:
                        should_update_zoom = True

                if should_update_zoom:
                    self.ax.set_xlim(new_xlim)
                    self.ax.set_ylim(new_ylim)
                    self._last_xlim = new_xlim
                    self._last_ylim = new_ylim
            else:
                # Manueller Zoom aktiv - behalte aktuelle Limits
                if self._last_xlim and self._last_ylim:
                    self.ax.set_xlim(self._last_xlim)
                    self.ax.set_ylim(self._last_ylim)

        # Nullpunkt-Marker
        self.ax.plot(0, 0, marker='+', color=self.colors['CYAN'], markersize=10, zorder=4, linewidth=2)
        self.ax.text(0, 0, ' G54', color=self.colors['CYAN'], fontsize=8, va='bottom')

        # Cursor-Highlight neu zeichnen (falls gesetzt)
        if self.current_line is not None:
            self._draw_highlight()

        # Live-Position neu zeichnen (falls aktiv)
        if self.live_x is not None or self.live_z is not None:
            self._update_live_display()

        # Async-Rendering für bessere Performance
        self.canvas.draw_idle()

    def _draw_highlight(self):
        """Zeichnet roten Marker für aktuelle Cursor-Position"""
        if self.current_line is None or not self.paths_cache:
            if self.highlight_artist:
                self.highlight_artist.set_visible(False)
            return

        # Finde Segment mit dieser Zeilennummer
        target_pos = None
        for segment_list in [self.paths_cache['rapid'], self.paths_cache['cut']]:
            for segment in segment_list:
                (x1, z1), (x2, z2), line = segment
                if line == self.current_line:
                    target_pos = (x2, z2)  # Endposition des Segments
                    break
            if target_pos:
                break

        # Werkzeugwechsel prüfen
        if not target_pos:
            for tc in self.paths_cache['tool_changes']:
                if tc['line'] == self.current_line:
                    target_pos = (tc['x'], tc['z'])
                    break

        if target_pos:
            # Marker erstellen oder Position aktualisieren
            if self.highlight_artist is None:
                # Marker erstmalig erstellen
                self.highlight_artist = self.ax.plot(target_pos[0], target_pos[1],
                                                     marker='o', color='#FF3333',
                                                     markersize=10, markeredgewidth=2,
                                                     markerfacecolor='none', zorder=10)[0]
            else:
                # Nur Position aktualisieren (viel schneller!)
                self.highlight_artist.set_data([target_pos[0]], [target_pos[1]])
                self.highlight_artist.set_visible(True)
        else:
            # Keine Position gefunden - Marker verstecken
            if self.highlight_artist:
                self.highlight_artist.set_visible(False)

    def _find_closest_line(self, click_x, click_z):
        """Findet die nächste G-Code Zeile zur Klick-Position"""
        if not self.paths_cache:
            return None

        min_dist = float('inf')
        closest_line = None

        # Durchsuche alle Segmente
        for segment_list in [self.paths_cache['rapid'], self.paths_cache['cut']]:
            for segment in segment_list:
                (x1, z1), (x2, z2), line = segment
                # Distanz zum Endpunkt
                dist = math.sqrt((x2 - click_x)**2 + (z2 - click_z)**2)
                if dist < min_dist:
                    min_dist = dist
                    closest_line = line

        # Werkzeugwechsel prüfen
        for tc in self.paths_cache['tool_changes']:
            dist = math.sqrt((tc['x'] - click_x)**2 + (tc['z'] - click_z)**2)
            if dist < min_dist:
                min_dist = dist
                closest_line = tc['line']

        return closest_line

    def _on_scroll(self, event):
        """Mausrad-Zoom Event"""
        if event.inaxes != self.ax:
            return

        # Zoom-Faktor
        zoom_factor = 1.2 if event.button == 'up' else 0.8

        # Aktueller Zoom-Bereich
        xlim = self.ax.get_xlim()
        ylim = self.ax.get_ylim()

        # Mausposition in Daten-Koordinaten
        xdata, ydata = event.xdata, event.ydata

        # Neuer Zoom-Bereich (zentriert auf Mausposition)
        new_width = (xlim[1] - xlim[0]) / zoom_factor
        new_height = (ylim[1] - ylim[0]) / zoom_factor

        # Berechne Offset zur Mausposition
        relx = (xdata - xlim[0]) / (xlim[1] - xlim[0])
        rely = (ydata - ylim[0]) / (ylim[1] - ylim[0])

        new_xlim = (xdata - new_width * relx, xdata + new_width * (1 - relx))
        new_ylim = (ydata - new_height * rely, ydata + new_height * (1 - rely))

        # Setze neue Limits
        self.ax.set_xlim(new_xlim)
        self.ax.set_ylim(new_ylim)
        self._last_xlim = new_xlim
        self._last_ylim = new_ylim
        self._manual_zoom = True  # Benutzer hat manuell gezoomt

        self.canvas.draw_idle()

    def _on_mouse_press(self, event):
        """Mouse-Press Event"""
        if event.inaxes != self.ax:
            return

        # Doppelklick: Auto-Zoom reaktivieren
        if event.dblclick and event.button == 1:
            self._manual_zoom = False
            self._last_code_hash = None  # Cache ungültig machen
            if self.pending_code:
                self._do_update()
            return

        # Linksklick: Start-Position für Click/Drag Detection speichern
        if event.button == 1:
            self._click_start = (event.x, event.y)  # Pixel-Koordinaten
            self._pan_start = (event.xdata, event.ydata)  # Daten-Koordinaten
            self._is_panning = False

    def _on_mouse_release(self, event):
        """Mouse-Release Event"""
        if event.button != 1:
            return

        # Cursor zurücksetzen
        self.canvas.setCursor(Qt.CursorShape.ArrowCursor)

        # Wenn kein Panning stattgefunden hat -> Zeile anspringen
        if not self._is_panning and event.inaxes == self.ax and self._click_start:
            closest_line = self._find_closest_line(event.xdata, event.ydata)
            if closest_line is not None:
                self.line_clicked.emit(closest_line)

        # Reset
        self._pan_start = None
        self._is_panning = False
        self._click_start = None

    def _on_mouse_move(self, event):
        """Mouse-Move Event für Pan"""
        if self._pan_start is None or event.inaxes != self.ax:
            return

        # Prüfe ob sich Maus genug bewegt hat (Threshold: 5 Pixel)
        if not self._is_panning and self._click_start:
            dx_pixel = abs(event.x - self._click_start[0])
            dy_pixel = abs(event.y - self._click_start[1])
            if dx_pixel > 5 or dy_pixel > 5:
                # Pan-Modus aktivieren
                self._is_panning = True
                self.canvas.setCursor(Qt.CursorShape.ClosedHandCursor)

        # Pan durchführen (nur wenn Pan-Modus aktiv)
        if self._is_panning:
            # Berechne Verschiebung
            dx = event.xdata - self._pan_start[0]
            dz = event.ydata - self._pan_start[1]

            # Verschiebe Plot
            xlim = self.ax.get_xlim()
            ylim = self.ax.get_ylim()

            new_xlim = (xlim[0] - dx, xlim[1] - dx)
            new_ylim = (ylim[0] - dz, ylim[1] - dz)

            self.ax.set_xlim(new_xlim)
            self.ax.set_ylim(new_ylim)
            self._last_xlim = new_xlim
            self._last_ylim = new_ylim
            self._manual_zoom = True  # Benutzer hat manuell verschoben

            # Pan-Start aktualisieren für flüssiges Verschieben
            self._pan_start = (event.xdata, event.ydata)

            self.canvas.draw_idle()

    def highlight_line(self, line_num: int):
        """Hebt die aktuelle Zeile im Plot hervor (Cursor-Sync)"""
        # Optimierung: Nur bei Zeilen-Änderung aktualisieren
        if self.current_line == line_num:
            return

        self.current_line = line_num

        if not self.paths_cache:
            return

        # Update Highlight (effizient mit set_data)
        self._draw_highlight()
        self.canvas.draw_idle()

    def set_gcode(self, gcode: str):
        """Setzt G-Code und aktualisiert Plot sofort"""
        self.pending_code = gcode
        self._do_update()

    def set_live_position(self, x: float = None, z: float = None, tool: int = None, s: int = None, f: float = None):
        """Setzt die Live-Position für die Simulation-Anzeige"""
        self.live_x = x
        self.live_z = z
        self.live_tool = tool
        self.live_s = s
        self.live_f = f
        self._update_live_display()

    def clear_live_position(self):
        """Entfernt die Live-Position-Anzeige"""
        self.live_x = None
        self.live_z = None
        self.live_tool = None
        self.live_s = None
        self.live_f = None
        if self.live_text:
            self.live_text.set_visible(False)
            self.canvas.draw_idle()

    def set_live_max_line(self, line_num: int):
        """Setzt die maximale Zeile für Live-Drawing (nur bis zu dieser Zeile wird gezeichnet)"""
        if self.live_max_line != line_num:
            self.live_max_line = line_num
            # Nur Plot neu zeichnen wenn Paths vorhanden sind
            if self.paths_cache:
                self._redraw_live()

    def clear_live_max_line(self):
        """Entfernt das Live-Drawing Limit (alle Linien werden wieder gezeichnet)"""
        if self.live_max_line is not None:
            self.live_max_line = None
            if self.paths_cache:
                self._redraw_live()

    def _redraw_live(self):
        """Zeichnet Plot neu ohne Cache-Check (für Live-Drawing)"""
        # Springe direkt zur Zeichnung, ohne Hash-Check
        if not self.pending_code or not self.paths_cache:
            return

        # Plot neu zeichnen
        self.ax.clear()
        self.highlight_artist = None
        self.live_text = None
        self._setup_plot()

        has_paths = False

        # Chuck-Zone (Spannbereich) - rote Schraffur
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

        # Eilgänge (G00) - grau gestrichelt
        for segment in self.paths_cache['rapid']:
            (x1, z1), (x2, z2), line = segment
            if self.live_max_line is None or line <= self.live_max_line:
                self.ax.plot([x1, x2], [z1, z2], color='#555', linestyle='--', linewidth=1, zorder=1)
                has_paths = True

        # Schnittbahnen (G01) - grün durchgezogen
        for segment in self.paths_cache['cut']:
            (x1, z1), (x2, z2), line = segment
            if self.live_max_line is None or line <= self.live_max_line:
                self.ax.plot([x1, x2], [z1, z2], color=self.colors['CRT_GREEN'], linewidth=2, zorder=2)
                has_paths = True

        # Kompensierte Schnittbahnen (G41/G42) - gelb gestrichelt
        for segment in self.paths_cache.get('comp_cut', []):
            (x1, z1), (x2, z2), line = segment
            if self.live_max_line is None or line <= self.live_max_line:
                self.ax.plot([x1, x2], [z1, z2], color=self.colors['FANUC_YELLOW'], linewidth=1.5,
                            linestyle='--', alpha=0.9, zorder=3)
                has_paths = True

        # Kompensierte Bögen (G41/G42) - gelb gestrichelt
        for arc in self.paths_cache.get('comp_arc', []):
            if self.live_max_line is None or arc['line'] <= self.live_max_line:
                cx, cz = arc['center']
                radius = arc['radius']
                x1, z1 = arc['start']
                x2, z2 = arc['end']

                # Winkel berechnen
                angle1 = math.degrees(math.atan2(z1 - cz, x1 - cx))
                angle2 = math.degrees(math.atan2(z2 - cz, x2 - cx))

                if arc['cw']:  # G02: Clockwise
                    if angle2 > angle1:
                        angle2 -= 360
                else:  # G03: Counter-clockwise
                    if angle1 > angle2:
                        angle1 -= 360

                arc_patch = patches.Arc((cx, cz), 2*radius, 2*radius,
                                       angle=0, theta1=angle1, theta2=angle2,
                                       color=self.colors['FANUC_YELLOW'], linewidth=1.5,
                                       linestyle='--', alpha=0.9, zorder=3)
                self.ax.add_patch(arc_patch)
                has_paths = True

        # Kreisbögen (G02/G03)
        for arc in self.paths_cache['arc']:
            if self.live_max_line is None or arc['line'] <= self.live_max_line:
                x1, z1 = arc['start']
                x2, z2 = arc['end']
                cx, cz = arc['center']
                radius = arc['radius']
                angle1 = math.degrees(math.atan2(z1 - cz, x1 - cx))
                angle2 = math.degrees(math.atan2(z2 - cz, x2 - cx))
                if arc['cw']:
                    if angle2 > angle1:
                        angle2 -= 360
                else:
                    if angle1 > angle2:
                        angle1 -= 360
                arc_patch = patches.Arc((cx, cz), 2*radius, 2*radius,
                                       angle=0, theta1=angle1, theta2=angle2,
                                       color=self.colors['CRT_GREEN'], linewidth=2, zorder=2)
                self.ax.add_patch(arc_patch)
                has_paths = True

        # Kollisionen
        for segment in self.paths_cache['collisions']:
            (x1, z1), (x2, z2), line = segment
            if self.live_max_line is None or line <= self.live_max_line:
                self.ax.plot([x1, x2], [z1, z2], color='#FF0000', linewidth=3,
                            alpha=0.7, zorder=5, label='Collision!')

        # Werkzeugwechsel
        for tc in self.paths_cache['tool_changes']:
            if self.live_max_line is None or tc['line'] <= self.live_max_line:
                self.ax.plot(tc['x'], tc['z'], marker='o', color=self.colors['FANUC_YELLOW'],
                            markersize=6, zorder=3)
                self.ax.text(tc['x'], tc['z'], f" T{tc['tool']}", color=self.colors['FANUC_YELLOW'],
                            fontsize=8, va='center')

        # Nullpunkt-Marker
        self.ax.plot(0, 0, marker='+', color=self.colors['CYAN'], markersize=10, zorder=4, linewidth=2)
        self.ax.text(0, 0, ' G54', color=self.colors['CYAN'], fontsize=8, va='bottom')

        # Zoom beibehalten wenn vorhanden
        if self._last_xlim and self._last_ylim:
            self.ax.set_xlim(self._last_xlim)
            self.ax.set_ylim(self._last_ylim)

        # Cursor-Highlight
        if self.current_line is not None:
            self._draw_highlight()

        # Live-Position
        if self.live_x is not None or self.live_z is not None:
            self._update_live_display()

        self.canvas.draw_idle()

    def _update_live_display(self):
        """Aktualisiert das Live-Position-Overlay"""
        # Wenn keine Position gesetzt ist, verstecke die Anzeige
        if self.live_x is None and self.live_z is None:
            if self.live_text:
                self.live_text.set_visible(False)
                self.canvas.draw_idle()
            return

        # Text zusammenbauen
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

        # Text-Box erstellen oder aktualisieren
        if self.live_text is None or not self.live_text.axes:
            # Erstelle Text-Box (oben rechts)
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
            # Aktualisiere nur Text
            self.live_text.set_text(info_text)
            self.live_text.set_visible(True)

        self.canvas.draw_idle()
