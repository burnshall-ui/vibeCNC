# vibe_cnc.py — Hauptprogramm (UI + Wiring) — Vibe CNC
import os, sys, json, re, subprocess, shutil
from datetime import datetime

from PyQt6.QtCore import Qt, QSize, QRect, QTimer, QSettings, QEvent, QObject, pyqtSignal, QThread
from PyQt6.QtGui import QFont, QColor, QTextFormat, QAction, QKeySequence, QIcon, QCloseEvent
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTableView, QPlainTextEdit, QTextEdit, QLineEdit, QLabel, QPushButton,
    QHeaderView, QFileDialog, QAbstractItemView, QFrame, QMessageBox, QStatusBar
)

from vibe_cnc.settings_manager import SettingsManager
from vibe_cnc.tool_model import ToolModel, load_tools_json
from vibe_cnc.macro_model import MacroModel
from vibe_cnc.gcode_highlighter import GCodeHighlighter, GCodeEditor, TitlePanel
from vibe_cnc.lint_engine import LintEngine
from vibe_cnc.claude_client import AIClient
from vibe_cnc.camotics_bridge import CamoticsBridge

HERE = os.path.dirname(os.path.abspath(__file__))

class AIWorker(QObject):
    finished = pyqtSignal(bool, str)

    def __init__(self, fn, *args):
        super().__init__()
        self.fn = fn
        self.args = args

    def run(self):
        try:
            ok, resp = self.fn(*self.args)
        except Exception as e:
            ok, resp = False, f"❌ KI-Threadfehler: {type(e).__name__}: {e}"
        self.finished.emit(ok, resp)


class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Vibe CNC")
        self.setMinimumSize(QSize(1100, 650))
        self.settings = QSettings("VibeCNC", "VibeCNC")
        self.current_file = None
        self._ai_busy = False

        # --- Settings & Managers ---
        cfg_path = os.path.join(HERE, "config.yaml")
        self.cfg = SettingsManager(cfg_path)
        self.cfg_colors = self.cfg.colors()
        self.ai = AIClient(self.cfg)
        self.linter = LintEngine(self.cfg)
        self.camotics = CamoticsBridge(self.cfg)

        # --- LEFT: Tools/Macros with tabs ---
        self.table = QTableView()
        self.table.setModel(ToolModel())
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        self.macroTable = QTableView()
        self.macroTable.setModel(MacroModel())
        self.macroTable.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.macroTable.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.macroTable.verticalHeader().hide()
        self.macroTable.horizontalHeader().setStretchLastSection(True)
        self.macroTable.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        from PyQt6.QtWidgets import QStackedWidget
        self.leftStack = QStackedWidget()
        self.leftStack.addWidget(self.table)
        self.leftStack.addWidget(self.macroTable)

        header = QWidget(); hb = QHBoxLayout(header); hb.setContentsMargins(0,0,0,0); hb.setSpacing(1)
        self.btnTabTools = QPushButton("WERKZEUGE"); self.btnTabTools.setObjectName("PanelTab"); self.btnTabTools.setCheckable(True); self.btnTabTools.setChecked(True)
        self.btnTabMacros = QPushButton("MAKROS"); self.btnTabMacros.setObjectName("PanelTab"); self.btnTabMacros.setCheckable(True)
        hb.addWidget(self.btnTabTools); hb.addWidget(self.btnTabMacros)

        leftBox = QWidget(); lv = QVBoxLayout(leftBox); lv.setContentsMargins(0,0,0,0); lv.setSpacing(0)
        lv.addWidget(header); lv.addWidget(self.leftStack)
        left = leftBox

        # --- CENTER: Editor ---
        self.editor = GCodeEditor(self.cfg_colors)
        self.highlighter = GCodeHighlighter(self.editor.document(), self.cfg_colors)
        self.title_center = TitlePanel("PROGRAM (EDIT) — Vibe CNC", self.editor, self.cfg_colors)

        # --- RIGHT: Chat ---
        self.chat = QTextEdit(); self.chat.setReadOnly(True)
        self.input = QLineEdit(); self.input.setPlaceholderText("> Schreibe G71 für T2, Tiefe 5.0 …")
        rightBox = QWidget(); rv = QVBoxLayout(rightBox); rv.setContentsMargins(0,0,0,0); rv.setSpacing(6)
        rv.addWidget(self.chat); rv.addWidget(self.input)
        right = TitlePanel("Vibe CNC — ASSIST", rightBox, self.cfg_colors)

        # --- SPLITTER ---
        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.split.addWidget(left); self.split.addWidget(self.title_center); self.split.addWidget(right)
        self.split.setStretchFactor(0, 1); self.split.setStretchFactor(1, 2); self.split.setStretchFactor(2, 1)

        # --- Softkeys ---
        soft = QWidget(); sh = QHBoxLayout(soft); sh.setContentsMargins(0,8,0,8); sh.setSpacing(8)
        self.btnOpen = QPushButton("OPEN"); self.btnOpen.setObjectName("Softkey")
        self.btnSave = QPushButton("SAVE"); self.btnSave.setObjectName("Softkey")
        self.btnSim  = QPushButton("SEND 2 SIM"); self.btnSim.setObjectName("Softkey")
        self.btnAna  = QPushButton("KI: ANALYZE"); self.btnAna.setObjectName("Softkey")
        self.btnGen  = QPushButton("KI: GEN-CODE"); self.btnGen.setObjectName("Softkey")
        for w in [self.btnOpen, self.btnSave, self.btnSim, self.btnAna, self.btnGen]:
            sh.addWidget(w)

        # --- Status Bar ---
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Bereit", 3000)

        # --- Root ---
        root = QWidget(); v = QVBoxLayout(root)
        v.setContentsMargins(10,10,10,10); v.setSpacing(0)
        v.addWidget(self.split, stretch=1)
        v.addWidget(soft, stretch=0)
        v.setContentsMargins(10,10,10,10)
        self.setCentralWidget(root)

        # --- Style & Scaling ---
        self._apply_styles()
        self.base_pt = self.cfg.data['ui'].get('font_base_pt', 12)
        self._apply_scaling()

        # --- Signals ---
        self.btnOpen.clicked.connect(self.action_open)
        self.btnSave.clicked.connect(self.action_save)
        self.btnAna.clicked.connect(self.action_analyze)
        self.btnGen.clicked.connect(self.action_generate)
        self.btnSim.clicked.connect(self.action_send_to_sim)
        self.input.returnPressed.connect(self.action_analyze)
        
        # --- Tool Integration ---
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.on_tool_right_click)
        self.table.doubleClicked.connect(self.on_tool_double_click)
        self.macroTable.doubleClicked.connect(self.on_macro_double_click)
        self.btnTabTools.clicked.connect(lambda: self._switch_left_tab(0))
        self.btnTabMacros.clicked.connect(lambda: self._switch_left_tab(1))
        
        # --- Keyboard Shortcuts ---
        QAction(self, shortcut=Qt.Key.Key_F5, triggered=self.quick_sim)
        QAction(self, shortcut=QKeySequence("Ctrl+Shift+S"), triggered=self.save_and_vm)
        QAction(self, shortcut=QKeySequence("Ctrl+L"), triggered=self.lint_only)
        QAction(self, shortcut=QKeySequence.StandardKey.ZoomIn, triggered=self.zoom_in)
        QAction(self, shortcut=QKeySequence.StandardKey.ZoomOut, triggered=self.zoom_out)
        QAction(self, shortcut=QKeySequence.StandardKey.Save, triggered=self.action_save)
        QAction(self, shortcut=QKeySequence.StandardKey.Open, triggered=self.action_open)

        # UI state
        self._restore_state()

    # --- Style & Scaling ---
    def _apply_styles(self):
        c = self.cfg_colors
        self.setStyleSheet(f"""
            QWidget {{ background:{c['BG_DARK']}; color:{c['WHITE']}; font-family:Consolas, 'Fira Code', monospace; }}
            QPlainTextEdit, QTextEdit {{ background:{c['BG_BLACK']}; color:{c['WHITE']}; border:1px solid #2A2A2A; }}
            QLineEdit {{ background:{c['BG_BLACK']}; color:{c['WHITE']}; border:1px solid #2A2A2A; padding:6px; }}
            QTableView {{ background:#111; gridline-color:#333; }}
            QHeaderView::section {{ background:#222; color:{c['WHITE']}; border:0; padding:6px; }}
            QTableView::item:selected {{ background: rgba(58,255,122,0.25); color:{c['WHITE']}; }}
            QLabel#PanelTitle {{ background:{c['FANUC_YELLOW']}; color:#000; font-weight:600; padding:6px 10px; }}
            QPushButton#Softkey {{ background:#2A2A2A; border:1px solid #444; padding:8px 12px; }}
            QPushButton#Softkey:pressed {{ background:#333; }}
            QStatusBar {{ background:#1A1A1A; color:{c['CRT_GREEN']}; border-top:1px solid #333; }}
            QPushButton#PanelTab {{ background:{c['FANUC_YELLOW']}; color:#000; font-weight:600; padding:6px 10px; border:0; }}
            QPushButton#PanelTab:checked {{ background:{c['FANUC_YELLOW']}; }}
            QPushButton#PanelTab:!checked {{ background:#E0B300; opacity:0.75; }}
        """)

    def _apply_scaling(self):
        w = max(self.width(), 1100)
        scale = max(0.9, min(1.6, w / 1600))
        def set_font(widget, mul=1.0):
            f = QFont("Consolas"); f.setPointSizeF(self.base_pt * scale * mul); widget.setFont(f)
        set_font(self.editor, 1.1)
        set_font(self.chat, 1.0)
        set_font(self.input, 1.0)
        set_font(self.table, 1.0)
        for b in [self.btnOpen, self.btnSave, self.btnSim, self.btnAna, self.btnGen]:
            set_font(b, 0.95); b.setMinimumHeight(int(34 * scale))
        for l in self.findChildren(QLabel, "PanelTitle"):
            f = l.font(); f.setPointSizeF(self.base_pt * scale * 0.95); f.setBold(True); l.setFont(f)
        self.editor.setViewportMargins(self.editor.lineNumberAreaWidth(), 0, 0, 0)

    def _set_ai_busy(self, busy: bool):
        self._ai_busy = busy
        for w in [self.btnAna, self.btnGen, self.input]:
            w.setEnabled(not busy)

    def _start_ai_task(self, status_message: str, fn, args, completion_handler):
        if self._ai_busy:
            self.status.showMessage("⚠️ KI-Auftrag läuft bereits", 5000)
            return

        self._set_ai_busy(True)
        self.status.showMessage(status_message, 0)

        thread = QThread(self)
        worker = AIWorker(fn, *args)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(lambda ok, resp, handler=completion_handler: self._on_ai_task_finished(ok, resp, handler))
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _on_ai_task_finished(self, ok: bool, resp: str, handler):
        try:
            handler(ok, resp)
        finally:
            self._set_ai_busy(False)

    def _switch_left_tab(self, idx: int):
        self.leftStack.setCurrentIndex(idx)
        self.btnTabTools.setChecked(idx == 0)
        self.btnTabMacros.setChecked(idx == 1)

    def on_macro_double_click(self, index):
        if not index.isValid():
            return
        row = index.row()
        nr_idx = self.macroTable.model().index(row, 0)
        try:
            nr = int(self.macroTable.model().data(nr_idx, Qt.ItemDataRole.DisplayRole))
        except Exception:
            return
        # call_type aus DB ermitteln (M98 oder G65)
        try:
            macro = self.macroTable.model().get_macro(nr)
            call_type = (macro or {}).get('call_type', 'M98').upper()
        except Exception:
            call_type = 'M98'
        text = f"{call_type} P{nr}"
        cursor = self.editor.textCursor()
        cursor.insertText(text)

    def _handle_analyze_response(self, ok: bool, resp: str, role: str):
        if ok:
            self.chat.append(f"<span style='color:{self.cfg_colors['CRT_GREEN']}'>{role}:</span> {resp}")
            self.status.showMessage("✅ KI-Analyse: Fertig", 5000)
        else:
            self.chat.append(f"<span style='color:{self.cfg_colors['FANUC_YELLOW']}'>Fehler:</span> {resp}")
            self.status.showMessage("❌ KI-Fehler", 5000)

    def _handle_generate_response(self, ok: bool, resp: str, who: str):
        if ok:
            self.chat.append(f"<span style='color:{self.cfg_colors['CRT_GREEN']}'>{who}:</span> {resp}")
            self.status.showMessage("✅ Code generiert", 5000)
        else:
            self.chat.append(f"<span style='color:{self.cfg_colors['FANUC_YELLOW']}'>Fehler:</span> {resp}")
            self.status.showMessage("❌ KI-Fehler", 5000)

    def resizeEvent(self, e):
        super().resizeEvent(e); self._apply_scaling()

    def zoom_in(self):  self.base_pt = min(self.base_pt + 1, 24); self._apply_scaling()
    def zoom_out(self): self.base_pt = max(self.base_pt - 1, 8);  self._apply_scaling()

    # --- File Ops ---
    def action_open(self):
        path, _ = QFileDialog.getOpenFileName(self, "Programm öffnen", "", "G-Code (*.nc *.txt *.tap *.gcode);;Alle Dateien (*)")
        if path:
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except OSError as e:
                msg = e.strerror if getattr(e, "strerror", None) else str(e)
                QMessageBox.warning(self, "Datei öffnen fehlgeschlagen", f"{path}\n\n{msg}")
                self.status.showMessage("❌ Öffnen fehlgeschlagen", 5000)
                return

            self.editor.setPlainText(content)
            self.current_file = path
            title = f"PROGRAM (EDIT) — {os.path.basename(path)} — Vibe CNC"
            self.title_center.header.setText(title)
            self.editor.clear_error_lines()
            self.status.showMessage(f"Geladen: {os.path.basename(path)}", 3000)

    def action_save(self):
        target_path = self.current_file
        if not target_path:
            target_path, _ = QFileDialog.getSaveFileName(self, "Programm speichern", "DEIN_TEIL.nc", "G-Code (*.nc *.txt *.tap *.gcode)")
            if not target_path:
                return

        try:
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(self.editor.toPlainText())
        except OSError as e:
            msg = e.strerror if getattr(e, "strerror", None) else str(e)
            QMessageBox.warning(self, "Speichern fehlgeschlagen", f"{target_path}\n\n{msg}")
            self.status.showMessage("❌ Speichern fehlgeschlagen", 5000)
            return

        self.current_file = target_path
        title = f"PROGRAM (EDIT) — {os.path.basename(target_path)} — Vibe CNC"
        self.title_center.header.setText(title)
        self.status.showMessage(f"Gespeichert: {os.path.basename(target_path)}", 3000)

    # --- Quick Sim (F5) ---
    def quick_sim(self):
        """F5: Quick Sim mit CAMotics (Hot-Reload)"""
        code = self.editor.toPlainText()
        
        # Auto-Lint vor Sim
        findings = self.linter.run_all(code)
        if findings:
            # Zeige Error-Marker
            error_lines = [f['line'] for f in findings]
            self.editor.set_error_lines(error_lines)
            
            # Warnung bei kritischen Fehlern (>2)
            if len(findings) > 2:
                reply = QMessageBox.question(
                    self, 
                    "Lint-Fehler", 
                    f"{len(findings)} Fehler gefunden:\n\n" + 
                    "\n".join([f"• Zeile {f['line']}: {f['rule']}" for f in findings[:5]]) +
                    ("\n..." if len(findings) > 5 else "") +
                    "\n\nTrotzdem simulieren?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply != QMessageBox.StandardButton.Yes:
                    self.status.showMessage(f"❌ Sim abgebrochen: {len(findings)} Fehler", 5000)
                    return
        else:
            self.editor.clear_error_lines()
        
        # Starte CAMotics
        self.status.showMessage("⚙️ Starte CAMotics...", 2000)
        ok, msg = self.camotics.quick_sim(code)
        
        if ok:
            self.status.showMessage(f"✅ {msg}", 5000)
        else:
            self.status.showMessage(f"❌ {msg}", 5000)
            QMessageBox.warning(self, "CAMotics Fehler", msg)

    # --- Save + VM Copy (Ctrl+Shift+S) ---
    def save_and_vm(self):
        """Ctrl+Shift+S: Speichert und kopiert ins VM-Share"""
        code = self.editor.toPlainText()
        
        # Lint-Check
        findings = self.linter.run_all(code)
        if findings:
            error_lines = [f['line'] for f in findings]
            self.editor.set_error_lines(error_lines)
            self.status.showMessage(f"⚠️ {len(findings)} Fehler — trotzdem in VM kopiert", 5000)
        else:
            self.editor.clear_error_lines()
        
        # Dateiname bestimmen
        if self.current_file:
            filename = os.path.basename(self.current_file)
        else:
            filename = "live_test.nc"
        
        # Copy to VM
        self.status.showMessage("⚙️ Kopiere zu VM...", 2000)
        ok, msg = self.camotics.save_and_copy_to_vm(code, filename)
        
        if ok:
            self.status.showMessage(f"✅ VM-Copy: {filename}", 5000)
            self.chat.append(f"<span style='color:{self.cfg_colors['CRT_GREEN']}'>VibeCNC:</span> Datei in VM kopiert: {msg}")
        else:
            self.status.showMessage(f"❌ VM-Copy Fehler", 5000)
            QMessageBox.warning(self, "VM-Copy Fehler", msg)

    # --- Lint Only (Ctrl+L) ---
    def lint_only(self):
        """Ctrl+L: Nur Linting, ohne KI"""
        code = self.editor.toPlainText()
        findings = self.linter.run_all(code)
        
        if findings:
            error_lines = [f['line'] for f in findings]
            self.editor.set_error_lines(error_lines)
            self.status.showMessage(f"⚠️ Lint: {len(findings)} Fehler gefunden", 5000)
            self.chat.append(f"<span style='color:{self.cfg_colors['FANUC_YELLOW']}'>Lint:</span> {len(findings)} Fund(e)")
            for f in findings:
                self.chat.append(f"<span style='color:{self.cfg_colors['CRT_GREEN']}'>• Zeile {f['line']}: {f['rule']}</span> — {f['message']}")
        else:
            self.editor.clear_error_lines()
            self.status.showMessage("✅ Lint: OK", 3000)
            self.chat.append(f"<span style='color:{self.cfg_colors['CRT_GREEN']}'>Lint:</span> OK")

    # --- Lint & AI ---
    def action_analyze(self):
        """KI: ANALYZE Button + Enter im Input"""
        code = self.editor.toPlainText()
        findings = self.linter.run_all(code)
        
        if findings:
            error_lines = [f['line'] for f in findings]
            self.editor.set_error_lines(error_lines)
            self.chat.append(f"<span style='color:{self.cfg_colors['FANUC_YELLOW']}'>Lint:</span> {len(findings)} Fund(e)")
            for f in findings:
                self.chat.append(f"<span style='color:{self.cfg_colors['CRT_GREEN']}'>• Zeile {f['line']}: {f['rule']}</span> — {f['message']}")
        else:
            self.editor.clear_error_lines()
            self.chat.append(f"<span style='color:{self.cfg_colors['CRT_GREEN']}'>Lint:</span> OK")

        if self.cfg.data['ai'].get('offline', False):
            self.chat.append(f"<span style='color:{self.cfg_colors['CRT_GREEN']}'>VibeCNC:</span> Offline-Modus aktiv — keine KI-Abfrage.")
            self.status.showMessage("⚠️ Offline-Modus", 3000)
            return

        mode = self.cfg.data['ai'].get('mode', 'claude')
        # Kontext bauen
        selected = self.editor.textCursor().selectedText().replace('\u2029', '\n')
        snippet = selected if selected.strip() else code[:8000]
        tools_json = load_tools_json()
        policies_path = os.path.join(HERE, "policies.md")
        if os.path.exists(policies_path):
            with open(policies_path, "r", encoding="utf-8") as f:
                policies = f.read()
        else:
            policies = "Keine Policies gefunden."

        prompt = f"Maschine: FANUC 0i‑TF, Material: 42CrMo4\nPolicies:\n{policies}\nTools(JSON):\n{json.dumps(tools_json, ensure_ascii=False)}\n\nCode:\n```\n{snippet}\n```\nAufgabe: Liste Regelverstöße (Zeile/Regel/Fix). Optional unified diff. Keine kosmetischen Änderungen."
        ai_fn = self.ai.ask_claude if mode == 'claude' else self.ai.ask_ollama
        role = "Claude" if mode == 'claude' else "LLM"
        self._start_ai_task("⚙️ KI-Analyse läuft...", ai_fn, (prompt,), lambda ok, resp, role=role: self._handle_analyze_response(ok, resp, role))

    def action_generate(self):
        """KI: GEN-CODE Button"""
        user = self.input.text().strip()
        if not user:
            user = "Erzeuge G71-Schruppzyklus für T1, Zustellung 0.4, f0.25."
        
        if self.cfg.data['ai'].get('offline', False):
            self.chat.append(f"<span style='color:{self.cfg_colors['CRT_GREEN']}'>VibeCNC:</span> Offline — Gen‑Stub: G00/G01‑Block vorgeschlagen.")
            self.chat.append("<pre style='color:#6CFF6C;'>G00 X36. Z2.\nG01 Z0. F0.25\nG01 X-5.\nG00 X200.\n</pre>")
            self.status.showMessage("⚠️ Offline-Modus", 3000)
            return

        mode = self.cfg.data['ai'].get('mode', 'claude')
        who = "Claude" if mode == 'claude' else "LLM"
        self._start_ai_task("⚙️ KI generiert Code...", self.ai.ask, (user,), lambda ok, resp, who=who: self._handle_generate_response(ok, resp, who))

    # --- Simulation Bridge (Legacy) ---
    def action_send_to_sim(self):
        """SEND 2 SIM Button (mit Datei-Dialog)"""
        # Save temp file and launch CAMotics OR copy to VM share
        tmp_dir = os.path.join(HERE, "_tmp")
        try:
            os.makedirs(tmp_dir, exist_ok=True)
        except OSError as e:
            msg = e.strerror if getattr(e, "strerror", None) else str(e)
            QMessageBox.warning(self, "SIM-Export fehlgeschlagen", f"Temp-Verzeichnis konnte nicht erstellt werden:\n\n{msg}")
            self.status.showMessage("❌ SIM-Export fehlgeschlagen", 5000)
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(tmp_dir, f"sim_{ts}.nc")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.editor.toPlainText())
        except OSError as e:
            msg = e.strerror if getattr(e, "strerror", None) else str(e)
            QMessageBox.warning(self, "SIM-Export fehlgeschlagen", f"Datei konnte nicht geschrieben werden:\n{path}\n\n{msg}")
            self.status.showMessage("❌ SIM-Export fehlgeschlagen", 5000)
            return
        
        ok, msg = self.camotics.launch(path)
        if not ok:
            # try share copy
            ok2, msg2 = self.camotics.copy_to_share(path)
            if ok2:
                self.chat.append(f"<span style='color:{self.cfg_colors['CRT_GREEN']}'>VibeCNC:</span> Datei in VM‑Share kopiert: {msg2}")
                self.status.showMessage(f"✅ VM-Copy: {os.path.basename(msg2)}", 5000)
            else:
                self.chat.append(f"<span style='color:{self.cfg_colors['FANUC_YELLOW']}'>VibeCNC:</span> {msg} — Share fehlgeschlagen: {msg2}")
                self.status.showMessage(f"❌ Sim-Fehler", 5000)
        else:
            self.chat.append(f"<span style='color:{self.cfg_colors['CRT_GREEN']}'>VibeCNC:</span> CAMotics gestartet ({msg})")
            self.status.showMessage(f"✅ CAMotics gestartet", 5000)

    # --- Tool Integration Handlers ---
    def on_tool_right_click(self, position):
        """Right click: Show tool details dialog"""
        index = self.table.indexAt(position)
        if not index.isValid():
            return
        
        row = index.row()
        tool_row = self.table.model().rows[row]
        tool_num = tool_row[0]
        
        # Get tool details
        tool_model = self.table.model()
        tool_info = tool_model.get_tool_info(tool_num)
        
        # Build details text
        name = tool_info.get("name", "Unbekannt")
        radius = tool_info.get("insert_radius_mm", "-")
        holder = tool_info.get("holder", "-")
        
        limits = tool_info.get("limits", {})
        vc_max = limits.get("vc_max", "-")
        f_max = limits.get("f_max", "-")
        ap_max = limits.get("ap_max", "-")
        
        details = f"T{tool_num:02d}01: {name}\n\n"
        details += f"Radius: {radius}mm  |  Halter: {holder}\n\n"
        details += f"Vc_max: {vc_max} m/min\n"
        details += f"f_max: {f_max} mm/rev\n"
        details += f"ap_max: {ap_max} mm"
        
        # Show details dialog
        QMessageBox.information(self, f"Tool T{tool_num:02d}01 Details", details)
    
    def on_tool_double_click(self, index):
        """Double click: Insert tool code + S + F into editor"""
        if not index.isValid():
            return
        
        row = index.row()
        tool_row = self.table.model().rows[row]
        tool_num = tool_row[0]
        
        # Get tool details
        tool_model = self.table.model()
        tool_code = tool_model.get_tool_code(tool_num)
        S, F = tool_model.get_tool_speed_feed(tool_num)
        
        # Insert into editor at cursor position
        cursor = self.editor.textCursor()
        code = f"{tool_code} S{S} F{F}"
        cursor.insertText(code)
        
        # Update status
        tool_name = tool_model.get_tool_info(tool_num).get("name", "")
        self.status.showMessage(f"Tool T{tool_num:02d}01 geladen: {tool_name}", 3000)

    # --- Persistenz ---
    def closeEvent(self, e: QCloseEvent):
        self._save_state(); return super().closeEvent(e)
    
    def _save_state(self):
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("splitter", self.split.saveState())
        self.settings.setValue("base_pt", self.base_pt)
    
    def _restore_state(self):
        g = self.settings.value("geometry")
        if g is not None: self.restoreGeometry(g)
        s = self.settings.value("splitter")
        if s is not None: self.split.restoreState(s)
        bp = self.settings.value("base_pt")
        if bp is not None: self.base_pt = int(bp)

if __name__ == "__main__":
    # High-DPI Support für Windows 11
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    
    app = QApplication(sys.argv)
    win = Main(); win.show()
    sys.exit(app.exec())

