# vibe_cnc.py — Hauptprogramm (UI + Wiring) — Vibe CNC
import os, sys, json, re, subprocess, shutil
from datetime import datetime

from PyQt6.QtCore import Qt, QSize, QRect, QTimer, QSettings, QEvent, QObject, pyqtSignal, QThread, QRunnable, QThreadPool, pyqtSlot
from PyQt6.QtGui import QFont, QColor, QTextFormat, QAction, QKeySequence, QIcon, QCloseEvent, QTextDocument
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTableView, QPlainTextEdit, QTextEdit, QLineEdit, QLabel, QPushButton,
    QHeaderView, QFileDialog, QAbstractItemView, QFrame, QMessageBox, QStatusBar,
    QDialog, QCheckBox, QComboBox, QSpinBox, QFormLayout, QMenu, QTabWidget,
    QDoubleSpinBox
)

from vibe_cnc.settings_manager import SettingsManager
from vibe_cnc.tool_model import ToolModel, load_tools_json
from vibe_cnc.macro_model import MacroModel
from vibe_cnc.gcode_highlighter import GCodeHighlighter, GCodeEditor, TitlePanel
from vibe_cnc.lint_engine import LintEngine
from vibe_cnc.claude_client import AIClient
from vibe_cnc.camotics_bridge import CamoticsBridge
from vibe_cnc.gcode_plotter import GCodePlotterWidget

HERE = os.path.dirname(os.path.abspath(__file__))

class AIWorkerSignals(QObject):
    """Signals for AIWorker (QRunnable can't have signals directly)"""
    finished = pyqtSignal(bool, str)

class AIWorker(QRunnable):
    def __init__(self, fn, args, callback):
        super().__init__()
        self.fn = fn
        self.args = args
        self.callback = callback
        self.signals = AIWorkerSignals()
        self.signals.finished.connect(callback)

    @pyqtSlot()
    def run(self):
        try:
            ok, resp = self.fn(*self.args)
        except Exception as e:
            ok, resp = False, f"❌ KI-Threadfehler: {type(e).__name__}: {e}"
        self.signals.finished.emit(ok, resp)


class FindReplaceDialog(QDialog):
    """Find & Replace Dialog für den G-Code Editor"""

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.last_match_pos = -1

        self.setWindowTitle("Suchen & Ersetzen")
        self.setModal(False)  # Non-modal, damit man weiter im Editor arbeiten kann
        self.resize(500, 200)

        # Layout
        layout = QVBoxLayout(self)

        # --- Find Row ---
        find_row = QHBoxLayout()
        find_row.addWidget(QLabel("Suchen:"))
        self.find_input = QLineEdit()
        self.find_input.setPlaceholderText("Text eingeben...")
        find_row.addWidget(self.find_input)
        layout.addLayout(find_row)

        # --- Replace Row ---
        replace_row = QHBoxLayout()
        replace_row.addWidget(QLabel("Ersetzen:"))
        self.replace_input = QLineEdit()
        self.replace_input.setPlaceholderText("Ersetzungstext...")
        replace_row.addWidget(self.replace_input)
        layout.addLayout(replace_row)

        # --- Options ---
        options_row = QHBoxLayout()
        self.case_sensitive = QCheckBox("Groß-/Kleinschreibung")
        self.whole_words = QCheckBox("Nur ganze Wörter")
        options_row.addWidget(self.case_sensitive)
        options_row.addWidget(self.whole_words)
        options_row.addStretch()
        layout.addLayout(options_row)

        # --- Buttons ---
        btn_row = QHBoxLayout()
        self.btn_find_next = QPushButton("Weiter")
        self.btn_find_prev = QPushButton("Zurück")
        self.btn_replace = QPushButton("Ersetzen")
        self.btn_replace_all = QPushButton("Alle ersetzen")
        self.btn_close = QPushButton("Schließen")

        btn_row.addWidget(self.btn_find_prev)
        btn_row.addWidget(self.btn_find_next)
        btn_row.addWidget(self.btn_replace)
        btn_row.addWidget(self.btn_replace_all)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_close)
        layout.addLayout(btn_row)

        # --- Signals ---
        self.btn_find_next.clicked.connect(self.find_next)
        self.btn_find_prev.clicked.connect(self.find_previous)
        self.btn_replace.clicked.connect(self.replace_current)
        self.btn_replace_all.clicked.connect(self.replace_all)
        self.btn_close.clicked.connect(self.close)
        self.find_input.returnPressed.connect(self.find_next)
        self.replace_input.returnPressed.connect(self.replace_current)

        # Focus auf Find-Input
        self.find_input.setFocus()

    def find_next(self):
        """Suche nächstes Vorkommen"""
        search_text = self.find_input.text()
        if not search_text:
            return

        # Suchoptionen
        flags = QTextDocument.FindFlag(0)
        if self.case_sensitive.isChecked():
            flags |= QTextDocument.FindFlag.FindCaseSensitively
        if self.whole_words.isChecked():
            flags |= QTextDocument.FindFlag.FindWholeWords

        # Suche ab aktueller Cursor-Position
        cursor = self.editor.textCursor()
        found_cursor = self.editor.document().find(search_text, cursor, flags)

        if not found_cursor.isNull():
            self.editor.setTextCursor(found_cursor)
            self.last_match_pos = found_cursor.position()
        else:
            # Wrap around - von Anfang suchen
            cursor.movePosition(cursor.MoveOperation.Start)
            found_cursor = self.editor.document().find(search_text, cursor, flags)
            if not found_cursor.isNull():
                self.editor.setTextCursor(found_cursor)
                self.last_match_pos = found_cursor.position()
            else:
                QMessageBox.information(self, "Suchen", f"'{search_text}' nicht gefunden.")

    def find_previous(self):
        """Suche vorheriges Vorkommen"""
        search_text = self.find_input.text()
        if not search_text:
            return

        # Suchoptionen
        flags = QTextDocument.FindFlag.FindBackward
        if self.case_sensitive.isChecked():
            flags |= QTextDocument.FindFlag.FindCaseSensitively
        if self.whole_words.isChecked():
            flags |= QTextDocument.FindFlag.FindWholeWords

        # Suche rückwärts ab aktueller Cursor-Position
        cursor = self.editor.textCursor()
        found_cursor = self.editor.document().find(search_text, cursor, flags)

        if not found_cursor.isNull():
            self.editor.setTextCursor(found_cursor)
            self.last_match_pos = found_cursor.position()
        else:
            # Wrap around - von Ende suchen
            cursor.movePosition(cursor.MoveOperation.End)
            found_cursor = self.editor.document().find(search_text, cursor, flags)
            if not found_cursor.isNull():
                self.editor.setTextCursor(found_cursor)
                self.last_match_pos = found_cursor.position()
            else:
                QMessageBox.information(self, "Suchen", f"'{search_text}' nicht gefunden.")

    def replace_current(self):
        """Ersetze aktuell markierte Fundstelle"""
        search_text = self.find_input.text()
        replace_text = self.replace_input.text()

        if not search_text:
            return

        cursor = self.editor.textCursor()
        if cursor.hasSelection() and cursor.selectedText() == search_text:
            cursor.insertText(replace_text)
            # Suche nächstes Vorkommen
            self.find_next()
        else:
            # Kein Match selektiert - suche erstmal
            self.find_next()

    def replace_all(self):
        """Ersetze alle Vorkommen"""
        search_text = self.find_input.text()
        replace_text = self.replace_input.text()

        if not search_text:
            return

        # Suchoptionen
        flags = QTextDocument.FindFlag(0)
        if self.case_sensitive.isChecked():
            flags |= QTextDocument.FindFlag.FindCaseSensitively
        if self.whole_words.isChecked():
            flags |= QTextDocument.FindFlag.FindWholeWords

        # Zähle Ersetzungen
        count = 0
        cursor = self.editor.textCursor()
        cursor.beginEditBlock()  # Undo-Block für alle Ersetzungen

        # Start vom Anfang
        cursor.movePosition(cursor.MoveOperation.Start)

        while True:
            found_cursor = self.editor.document().find(search_text, cursor, flags)
            if found_cursor.isNull():
                break
            found_cursor.insertText(replace_text)
            cursor = found_cursor
            count += 1

        cursor.endEditBlock()

        if count > 0:
            QMessageBox.information(self, "Ersetzen", f"{count} Vorkommen ersetzt.")
        else:
            QMessageBox.information(self, "Ersetzen", f"'{search_text}' nicht gefunden.")


class MacroEditorDialog(QDialog):
    """Dialog zum Bearbeiten/Erstellen von Macros"""

    def __init__(self, macro_model, macro_nr=None, parent=None):
        super().__init__(parent)
        self.macro_model = macro_model
        self.macro_nr = macro_nr  # None = Neu erstellen, sonst bearbeiten
        self.is_new = (macro_nr is None)

        self.setWindowTitle("Macro bearbeiten" if not self.is_new else "Neues Macro")
        self.setModal(True)
        self.resize(600, 500)

        # Layout
        layout = QVBoxLayout(self)

        # --- Form Fields ---
        form = QFormLayout()

        # Macro-Nummer
        self.nr_input = QSpinBox()
        self.nr_input.setRange(1, 99999)
        self.nr_input.setValue(9000 if self.is_new else macro_nr)
        form.addRow("Nummer (P-Wert):", self.nr_input)

        # Name
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("z.B. Bohrzyklus Peck")
        form.addRow("Name:", self.name_input)

        # Category
        self.category_input = QLineEdit()
        self.category_input.setPlaceholderText("z.B. Bohren, Drehen, Antasten")
        form.addRow("Kategorie:", self.category_input)

        # Call-Type
        self.call_type_combo = QComboBox()
        self.call_type_combo.addItems(["M98", "G65"])
        form.addRow("Call-Type:", self.call_type_combo)

        layout.addLayout(form)

        # --- Description/Code ---
        layout.addWidget(QLabel("Beschreibung / Code:"))
        self.description_input = QTextEdit()
        self.description_input.setPlaceholderText("Beschreibung oder G-Code hier eingeben...")
        self.description_input.setMinimumHeight(200)
        layout.addWidget(self.description_input)

        # --- Buttons ---
        btn_row = QHBoxLayout()
        self.btn_save = QPushButton("Speichern")
        self.btn_delete = QPushButton("Löschen")
        self.btn_cancel = QPushButton("Abbrechen")

        self.btn_save.setObjectName("Softkey")
        self.btn_delete.setObjectName("Softkey")
        self.btn_cancel.setObjectName("Softkey")

        # Delete-Button nur bei Edit anzeigen
        if self.is_new:
            self.btn_delete.hide()

        btn_row.addWidget(self.btn_save)
        btn_row.addWidget(self.btn_delete)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_cancel)
        layout.addLayout(btn_row)

        # --- Signals ---
        self.btn_save.clicked.connect(self.save_macro)
        self.btn_delete.clicked.connect(self.delete_macro)
        self.btn_cancel.clicked.connect(self.reject)

        # --- Load existing macro ---
        if not self.is_new:
            self._load_macro()

    def _load_macro(self):
        """Lade Macro-Daten aus DB"""
        macro_data = self.macro_model.get_macro(self.macro_nr)
        if not macro_data:
            QMessageBox.warning(self, "Fehler", f"Macro {self.macro_nr} nicht gefunden.")
            self.reject()
            return

        self.nr_input.setValue(macro_data['nr'])
        self.name_input.setText(macro_data['name'])
        self.category_input.setText(macro_data['category'])
        self.description_input.setPlainText(macro_data['description'])

        # Set call-type
        call_type = macro_data['call_type'] or 'M98'
        index = self.call_type_combo.findText(call_type)
        if index >= 0:
            self.call_type_combo.setCurrentIndex(index)

    def save_macro(self):
        """Speichere Macro in DB"""
        nr = self.nr_input.value()
        name = self.name_input.text().strip()
        category = self.category_input.text().strip()
        call_type = self.call_type_combo.currentText()
        description = self.description_input.toPlainText().strip()

        if not name:
            QMessageBox.warning(self, "Validierung", "Name darf nicht leer sein.")
            return

        # DB Update
        import sqlite3
        from vibe_cnc.macro_model import DB_PATH

        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()

            if self.is_new:
                # Prüfe ob NR schon existiert
                cur.execute("SELECT COUNT(1) FROM macros WHERE nr=?;", (nr,))
                exists = cur.fetchone()[0] > 0
                if exists:
                    QMessageBox.warning(self, "Fehler", f"Macro-Nummer {nr} existiert bereits.")
                    conn.close()
                    return

                # INSERT
                cur.execute(
                    "INSERT INTO macros (nr, name, category, call_type, description) VALUES (?, ?, ?, ?, ?);",
                    (nr, name, category, call_type, description)
                )
            else:
                # UPDATE
                cur.execute(
                    "UPDATE macros SET nr=?, name=?, category=?, call_type=?, description=? WHERE nr=?;",
                    (nr, name, category, call_type, description, self.macro_nr)
                )

            conn.commit()
            conn.close()

            # Refresh model
            self.macro_model.rows = self.macro_model._load_rows()
            self.macro_model.layoutChanged.emit()

            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Speichern fehlgeschlagen:\n{e}")

    def delete_macro(self):
        """Lösche Macro aus DB"""
        if self.is_new:
            return

        # Bestätigung
        reply = QMessageBox.question(
            self, "Löschen bestätigen",
            f"Macro {self.macro_nr} wirklich löschen?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        # DB DELETE
        import sqlite3
        from vibe_cnc.macro_model import DB_PATH

        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("DELETE FROM macros WHERE nr=?;", (self.macro_nr,))
            conn.commit()
            conn.close()

            # Refresh model
            self.macro_model.rows = self.macro_model._load_rows()
            self.macro_model.layoutChanged.emit()

            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Löschen fehlgeschlagen:\n{e}")


class ToolEditorDialog(QDialog):
    """Dialog zum Bearbeiten/Erstellen von Tools"""

    def __init__(self, tool_model, tool_num=None, parent=None):
        super().__init__(parent)
        self.tool_model = tool_model
        self.tool_num = tool_num
        self.is_new = (tool_num is None)

        self.setWindowTitle("Tool bearbeiten" if not self.is_new else "Neues Tool")
        self.setModal(True)
        self.resize(500, 600)

        layout = QVBoxLayout(self)

        # --- Form Fields ---
        form = QFormLayout()

        # Tool Number
        self.t_input = QSpinBox()
        self.t_input.setRange(1, 999)
        self.t_input.setValue(1 if self.is_new else tool_num)
        form.addRow("Tool-Nummer (T):", self.t_input)

        # Name
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("z.B. CNMG1204P-S Außen")
        form.addRow("Name:", self.name_input)

        # Type
        self.type_combo = QComboBox()
        self.type_combo.addItems([
            "turn_rough", "turn_finish", "groove", "drill",
            "thread_form", "boring", "parting", "other"
        ])
        form.addRow("Typ:", self.type_combo)

        # Diameter (optional)
        self.d_mm_input = QDoubleSpinBox()
        self.d_mm_input.setRange(0, 999)
        self.d_mm_input.setDecimals(2)
        self.d_mm_input.setSuffix(" mm")
        self.d_mm_input.setSpecialValueText("(leer)")
        form.addRow("Durchmesser (d):", self.d_mm_input)

        # Insert Radius (optional)
        self.insert_radius_input = QDoubleSpinBox()
        self.insert_radius_input.setRange(0, 99)
        self.insert_radius_input.setDecimals(2)
        self.insert_radius_input.setSuffix(" mm")
        self.insert_radius_input.setSpecialValueText("(leer)")
        form.addRow("Eckenradius:", self.insert_radius_input)

        # Holder (optional)
        self.holder_input = QLineEdit()
        self.holder_input.setPlaceholderText("z.B. PCLNR2525")
        form.addRow("Halter:", self.holder_input)

        layout.addLayout(form)

        # --- Limits Section ---
        layout.addWidget(QLabel("<b>Bearbeitungsgrenzen:</b>"))
        limits_form = QFormLayout()

        self.vc_max_input = QDoubleSpinBox()
        self.vc_max_input.setRange(0, 9999)
        self.vc_max_input.setDecimals(0)
        self.vc_max_input.setSuffix(" m/min")
        self.vc_max_input.setValue(150)
        limits_form.addRow("Vc max:", self.vc_max_input)

        self.ap_max_input = QDoubleSpinBox()
        self.ap_max_input.setRange(0, 99)
        self.ap_max_input.setDecimals(2)
        self.ap_max_input.setSuffix(" mm")
        self.ap_max_input.setValue(2.0)
        limits_form.addRow("Ap max (Zustellung):", self.ap_max_input)

        self.f_max_input = QDoubleSpinBox()
        self.f_max_input.setRange(0, 9.99)
        self.f_max_input.setDecimals(3)
        self.f_max_input.setSuffix(" mm/U")
        self.f_max_input.setValue(0.25)
        limits_form.addRow("F max (Vorschub):", self.f_max_input)

        layout.addLayout(limits_form)

        # --- Buttons ---
        btn_row = QHBoxLayout()
        self.btn_save = QPushButton("Speichern")
        self.btn_delete = QPushButton("Löschen")
        self.btn_cancel = QPushButton("Abbrechen")

        self.btn_save.setObjectName("Softkey")
        self.btn_delete.setObjectName("Softkey")
        self.btn_cancel.setObjectName("Softkey")

        if self.is_new:
            self.btn_delete.hide()

        btn_row.addWidget(self.btn_save)
        btn_row.addWidget(self.btn_delete)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_cancel)
        layout.addLayout(btn_row)

        # --- Signals ---
        self.btn_save.clicked.connect(self.save_tool)
        self.btn_delete.clicked.connect(self.delete_tool)
        self.btn_cancel.clicked.connect(self.reject)

        # --- Load existing tool ---
        if not self.is_new:
            self._load_tool()

    def _load_tool(self):
        """Lade Tool-Daten aus JSON"""
        tool_data = self.tool_model.get_tool_info(self.tool_num)
        if not tool_data:
            QMessageBox.warning(self, "Fehler", f"Tool {self.tool_num} nicht gefunden.")
            self.reject()
            return

        self.t_input.setValue(tool_data.get('t', 1))
        self.name_input.setText(tool_data.get('name', ''))

        tool_type = tool_data.get('type', 'other')
        index = self.type_combo.findText(tool_type)
        if index >= 0:
            self.type_combo.setCurrentIndex(index)

        self.d_mm_input.setValue(tool_data.get('d_mm', 0))
        self.insert_radius_input.setValue(tool_data.get('insert_radius_mm', 0))
        self.holder_input.setText(tool_data.get('holder', ''))

        limits = tool_data.get('limits', {})
        self.vc_max_input.setValue(limits.get('vc_max', 150))
        self.ap_max_input.setValue(limits.get('ap_max', 2.0))
        self.f_max_input.setValue(limits.get('f_max', 0.25))

    def save_tool(self):
        """Speichere Tool in tools.json"""
        t = self.t_input.value()
        name = self.name_input.text().strip()

        if not name:
            QMessageBox.warning(self, "Validierung", "Name darf nicht leer sein.")
            return

        # Build tool dict
        tool_data = {
            "t": t,
            "name": name,
            "type": self.type_combo.currentText(),
            "limits": {
                "vc_max": self.vc_max_input.value(),
                "ap_max": self.ap_max_input.value(),
                "f_max": self.f_max_input.value()
            }
        }

        # Optional fields
        if self.d_mm_input.value() > 0:
            tool_data["d_mm"] = self.d_mm_input.value()
        if self.insert_radius_input.value() > 0:
            tool_data["insert_radius_mm"] = self.insert_radius_input.value()
        if self.holder_input.text().strip():
            tool_data["holder"] = self.holder_input.text().strip()

        # Load JSON
        try:
            from vibe_cnc.tool_model import load_tools_json
            import json

            tools_json_path = os.path.join(HERE, "tools", "tools.json")
            j = load_tools_json()

            # Find existing tool or add new
            tool_table = j.get("tool_table", [])
            found = False
            for i, item in enumerate(tool_table):
                if item.get("t") == self.tool_num if not self.is_new else item.get("t") == t:
                    # Update existing
                    tool_table[i] = tool_data
                    found = True
                    break

            if not found:
                # Check if T already exists (for new tools)
                if self.is_new and any(item.get("t") == t for item in tool_table):
                    QMessageBox.warning(self, "Fehler", f"Tool-Nummer {t} existiert bereits.")
                    return
                # Add new
                tool_table.append(tool_data)

            j["tool_table"] = tool_table

            # Save JSON
            with open(tools_json_path, "w", encoding="utf-8") as f:
                json.dump(j, f, indent=2, ensure_ascii=False)

            # Refresh model
            self.tool_model.rows = self.tool_model._load_tools()
            self.tool_model.tool_data = self.tool_model._load_tool_data()
            self.tool_model.layoutChanged.emit()

            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Speichern fehlgeschlagen:\n{e}")

    def delete_tool(self):
        """Lösche Tool aus tools.json"""
        if self.is_new:
            return

        reply = QMessageBox.question(
            self, "Löschen bestätigen",
            f"Tool {self.tool_num} wirklich löschen?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            from vibe_cnc.tool_model import load_tools_json
            import json

            tools_json_path = os.path.join(HERE, "tools", "tools.json")
            j = load_tools_json()

            tool_table = j.get("tool_table", [])
            tool_table = [item for item in tool_table if item.get("t") != self.tool_num]
            j["tool_table"] = tool_table

            # Save JSON
            with open(tools_json_path, "w", encoding="utf-8") as f:
                json.dump(j, f, indent=2, ensure_ascii=False)

            # Refresh model
            self.tool_model.rows = self.tool_model._load_tools()
            self.tool_model.tool_data = self.tool_model._load_tool_data()
            self.tool_model.layoutChanged.emit()

            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Löschen fehlgeschlagen:\n{e}")


class SettingsDialog(QDialog):
    """Settings Dialog für VibeCNC"""

    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.cfg = config_manager
        self.setWindowTitle("Einstellungen")
        self.setModal(True)
        self.resize(700, 500)

        layout = QVBoxLayout(self)

        # Tab Widget
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # --- Tab 1: Maschine ---
        machine_tab = QWidget()
        machine_layout = QFormLayout(machine_tab)

        self.chuck_z_input = QDoubleSpinBox()
        self.chuck_z_input.setRange(-999, 0)
        self.chuck_z_input.setValue(self.cfg.data.get('machine', {}).get('chuck_z_limit', -5.0))
        self.chuck_z_input.setSuffix(" mm")
        self.chuck_z_input.setDecimals(1)
        machine_layout.addRow("Chuck Z-Limit (Kollisionsgrenze):", self.chuck_z_input)

        self.tabs.addTab(machine_tab, "⚙️ Maschine")

        # --- Tab 2: KI ---
        ai_tab = QWidget()
        ai_layout = QFormLayout(ai_tab)

        self.ai_mode_combo = QComboBox()
        self.ai_mode_combo.addItems(["claude", "ollama"])
        current_mode = self.cfg.data.get('ai', {}).get('mode', 'ollama')
        self.ai_mode_combo.setCurrentText(current_mode)
        ai_layout.addRow("KI-Modus:", self.ai_mode_combo)

        self.ai_offline_check = QCheckBox("Offline-Modus (keine API-Calls)")
        self.ai_offline_check.setChecked(self.cfg.data.get('ai', {}).get('offline', False))
        ai_layout.addRow("", self.ai_offline_check)

        self.claude_model_input = QLineEdit()
        self.claude_model_input.setText(
            self.cfg.data.get('ai', {}).get('anthropic', {}).get('model', 'claude-sonnet-4-20250514')
        )
        ai_layout.addRow("Claude Model:", self.claude_model_input)

        self.ollama_model_input = QLineEdit()
        self.ollama_model_input.setText(
            self.cfg.data.get('ai', {}).get('ollama', {}).get('model', 'granite3.3:8b')
        )
        ai_layout.addRow("Ollama Model:", self.ollama_model_input)

        self.tabs.addTab(ai_tab, "🤖 KI")

        # --- Tab 3: Pfade ---
        paths_tab = QWidget()
        paths_layout = QFormLayout(paths_tab)

        self.camotics_path_input = QLineEdit()
        self.camotics_path_input.setText(
            self.cfg.data.get('paths', {}).get('camotics_exe', 'C:\\Program Files (x86)\\CAMotics\\camotics.exe')
        )
        paths_layout.addRow("CAMotics EXE:", self.camotics_path_input)

        camotics_browse = QPushButton("Durchsuchen...")
        camotics_browse.clicked.connect(self._browse_camotics)
        paths_layout.addRow("", camotics_browse)

        self.vm_share_input = QLineEdit()
        self.vm_share_input.setText(
            self.cfg.data.get('paths', {}).get('sim_share', '\\\\linuxcnc-vm\\sim\\incoming')
        )
        paths_layout.addRow("VM-Share (SMB):", self.vm_share_input)

        self.tabs.addTab(paths_tab, "📁 Pfade")

        # --- Tab 4: UI ---
        ui_tab = QWidget()
        ui_layout = QFormLayout(ui_tab)

        self.font_size_input = QSpinBox()
        self.font_size_input.setRange(8, 24)
        self.font_size_input.setValue(self.cfg.data.get('ui', {}).get('font_base_pt', 12))
        self.font_size_input.setSuffix(" pt")
        ui_layout.addRow("Font-Größe:", self.font_size_input)

        self.tabs.addTab(ui_tab, "🎨 UI")

        # --- Buttons ---
        btn_row = QHBoxLayout()
        self.btn_save = QPushButton("Speichern")
        self.btn_cancel = QPushButton("Abbrechen")

        self.btn_save.setObjectName("Softkey")
        self.btn_cancel.setObjectName("Softkey")

        btn_row.addWidget(self.btn_save)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_cancel)
        layout.addLayout(btn_row)

        # Signals
        self.btn_save.clicked.connect(self.save_settings)
        self.btn_cancel.clicked.connect(self.reject)

    def _browse_camotics(self):
        """Browse-Dialog für CAMotics EXE"""
        path, _ = QFileDialog.getOpenFileName(
            self, "CAMotics EXE auswählen", "", "Executable (*.exe);;Alle Dateien (*)"
        )
        if path:
            self.camotics_path_input.setText(path)

    def save_settings(self):
        """Speichere Settings in config.yaml"""
        import yaml

        # Update config dict
        self.cfg.data['machine']['chuck_z_limit'] = self.chuck_z_input.value()
        self.cfg.data['ai']['mode'] = self.ai_mode_combo.currentText()
        self.cfg.data['ai']['offline'] = self.ai_offline_check.isChecked()
        self.cfg.data['ai']['anthropic']['model'] = self.claude_model_input.text().strip()
        self.cfg.data['ai']['ollama']['model'] = self.ollama_model_input.text().strip()
        self.cfg.data['paths']['camotics_exe'] = self.camotics_path_input.text().strip()
        self.cfg.data['paths']['sim_share'] = self.vm_share_input.text().strip()
        self.cfg.data['ui']['font_base_pt'] = self.font_size_input.value()

        # Schreibe YAML
        try:
            config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(self.cfg.data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

            QMessageBox.information(self, "Gespeichert", "Einstellungen wurden gespeichert.\nBitte VibeCNC neu starten, damit alle Änderungen wirksam werden.")
            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Speichern fehlgeschlagen:\n{e}")


class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Vibe CNC")
        self.setMinimumSize(QSize(1100, 650))
        self.settings = QSettings("VibeCNC", "VibeCNC")
        self.current_file = None
        self._ai_busy = False

        # --- Simulation State ---
        self.sim_state = "STOPPED"  # STOPPED, RUNNING, PAUSED
        self.sim_current_line = 0
        self.sim_lines = []
        self.sim_timer = QTimer()
        self.sim_timer.timeout.connect(self._sim_step)

        # --- Live Position Tracking ---
        self.sim_x = 0.0
        self.sim_z = 0.0
        self.sim_tool = 0
        self.sim_s = 0
        self.sim_f = 0.0

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

        # Tool-Panel mit New-Button
        toolPanel = QWidget()
        toolPanelLayout = QVBoxLayout(toolPanel)
        toolPanelLayout.setContentsMargins(0, 0, 0, 0)
        toolPanelLayout.setSpacing(4)
        toolPanelLayout.addWidget(self.table)

        self.btnNewTool = QPushButton("+ NEUES TOOL")
        self.btnNewTool.setObjectName("Softkey")
        toolPanelLayout.addWidget(self.btnNewTool)

        # Macro-Panel mit New-Button
        macroPanel = QWidget()
        macroPanelLayout = QVBoxLayout(macroPanel)
        macroPanelLayout.setContentsMargins(0, 0, 0, 0)
        macroPanelLayout.setSpacing(4)
        macroPanelLayout.addWidget(self.macroTable)

        self.btnNewMacro = QPushButton("+ NEUES MACRO")
        self.btnNewMacro.setObjectName("Softkey")
        macroPanelLayout.addWidget(self.btnNewMacro)

        from PyQt6.QtWidgets import QStackedWidget
        self.leftStack = QStackedWidget()
        self.leftStack.addWidget(toolPanel)
        self.leftStack.addWidget(macroPanel)

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

        # --- BOTTOM: 2D Plotter ---
        chuck_z = self.cfg.data.get('machine', {}).get('chuck_z_limit', -5.0)
        self.plotter = GCodePlotterWidget(self.cfg_colors, chuck_z=chuck_z)
        bottom = TitlePanel("WERKSTÜCK (2D)", self.plotter, self.cfg_colors)

        # --- SPLITTER ---
        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.split.addWidget(left); self.split.addWidget(self.title_center); self.split.addWidget(right)
        self.split.setStretchFactor(0, 1); self.split.setStretchFactor(1, 2); self.split.setStretchFactor(2, 1)

        # --- Control Buttons (Fanuc-Style) ---
        ctrl = QWidget(); ch = QHBoxLayout(ctrl); ch.setContentsMargins(0,8,0,4); ch.setSpacing(8)
        self.btnCycleStart = QPushButton("▶ CYCLE START"); self.btnCycleStart.setObjectName("CycleStart")
        self.btnFeedHold = QPushButton("⏸ FEED HOLD"); self.btnFeedHold.setObjectName("FeedHold")
        self.btnOptStop = QPushButton("⊙ OPT STOP"); self.btnOptStop.setObjectName("ControlToggle")
        self.btnOptStop.setCheckable(True)
        self.btnSingleBlock = QPushButton("⊙ SINGLE BLOCK"); self.btnSingleBlock.setObjectName("ControlToggle")
        self.btnSingleBlock.setCheckable(True)
        ch.addWidget(self.btnCycleStart)
        ch.addWidget(self.btnFeedHold)
        ch.addWidget(self.btnOptStop)
        ch.addWidget(self.btnSingleBlock)
        ch.addStretch()

        # --- Softkeys ---
        soft = QWidget(); sh = QHBoxLayout(soft); sh.setContentsMargins(0,4,0,8); sh.setSpacing(8)
        self.btnOpen = QPushButton("OPEN ▼"); self.btnOpen.setObjectName("Softkey")
        self.btnSave = QPushButton("SAVE"); self.btnSave.setObjectName("Softkey")

        # Recent Files Menu für OPEN Button
        self.recent_files_menu = QMenu(self)
        self.btnOpen.setMenu(self.recent_files_menu)
        self._update_recent_files_menu()
        self.btnSim  = QPushButton("SEND 2 SIM"); self.btnSim.setObjectName("Softkey")
        self.btnAna  = QPushButton("KI: ANALYZE"); self.btnAna.setObjectName("Softkey")
        self.btnGen  = QPushButton("KI: GEN-CODE"); self.btnGen.setObjectName("Softkey")
        self.btnSettings = QPushButton("⚙️ SETTINGS"); self.btnSettings.setObjectName("Softkey")
        for w in [self.btnOpen, self.btnSave, self.btnSim, self.btnAna, self.btnGen, self.btnSettings]:
            sh.addWidget(w)

        # --- Status Bar ---
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Bereit", 3000)

        # --- Vertical Splitter (Top: Editor/Chat | Bottom: Plot) ---
        self.vsplit = QSplitter(Qt.Orientation.Vertical)
        self.vsplit.addWidget(self.split)
        self.vsplit.addWidget(bottom)
        self.vsplit.setStretchFactor(0, 3)  # Editor-Bereich: 75%
        self.vsplit.setStretchFactor(1, 1)  # Plot-Bereich: 25%

        # --- Root ---
        root = QWidget(); v = QVBoxLayout(root)
        v.setContentsMargins(10,10,10,10); v.setSpacing(0)
        v.addWidget(self.vsplit, stretch=1)
        v.addWidget(ctrl, stretch=0)  # Control-Buttons oben
        v.addWidget(soft, stretch=0)  # Softkeys unten
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
        self.btnSettings.clicked.connect(self.action_settings)
        self.input.returnPressed.connect(self.action_analyze)

        # --- Control Signals ---
        self.btnCycleStart.clicked.connect(self.action_cycle_start)
        self.btnFeedHold.clicked.connect(self.action_feed_hold)
        self.btnOptStop.toggled.connect(self.action_opt_stop_toggled)
        self.btnSingleBlock.toggled.connect(self.action_single_block_toggled)

        # --- Plotter Live-Update ---
        self.editor.textChanged.connect(self._on_editor_changed)
        self.editor.cursorPositionChanged.connect(self._on_cursor_changed)
        self.plotter.line_clicked.connect(self._on_plot_clicked)
        
        # --- Tool Integration ---
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.on_tool_right_click)
        self.table.doubleClicked.connect(self.on_tool_double_click)
        self.btnNewTool.clicked.connect(self.on_new_tool_clicked)

        # --- Macro Integration ---
        self.macroTable.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.macroTable.customContextMenuRequested.connect(self.on_macro_right_click)
        self.macroTable.doubleClicked.connect(self.on_macro_double_click)
        self.btnNewMacro.clicked.connect(self.on_new_macro_clicked)
        self.btnTabTools.clicked.connect(lambda: self._switch_left_tab(0))
        self.btnTabMacros.clicked.connect(lambda: self._switch_left_tab(1))
        
        # --- Keyboard Shortcuts ---
        self.addAction(QAction(self, shortcut=Qt.Key.Key_F5, triggered=self.quick_sim))
        self.addAction(QAction(self, shortcut=QKeySequence("Ctrl+Shift+S"), triggered=self.save_and_vm))
        self.addAction(QAction(self, shortcut=QKeySequence("Ctrl+L"), triggered=self.lint_only))
        self.addAction(QAction(self, shortcut=QKeySequence.StandardKey.Find, triggered=self.show_find_dialog))  # Ctrl+F
        self.addAction(QAction(self, shortcut=QKeySequence("Ctrl+H"), triggered=self.show_replace_dialog))  # Ctrl+H
        self.addAction(QAction(self, shortcut=QKeySequence.StandardKey.ZoomIn, triggered=self.zoom_in))
        self.addAction(QAction(self, shortcut=QKeySequence.StandardKey.ZoomOut, triggered=self.zoom_out))
        self.addAction(QAction(self, shortcut=QKeySequence.StandardKey.Save, triggered=self.action_save))
        self.addAction(QAction(self, shortcut=QKeySequence.StandardKey.Open, triggered=self.action_open))

        # Find/Replace Dialog (erstellt bei Bedarf)
        self.find_replace_dialog = None

        # --- Control Shortcuts ---
        self.addAction(QAction(self, shortcut=Qt.Key.Key_Space, triggered=self.action_cycle_start))
        self.addAction(QAction(self, shortcut=Qt.Key.Key_F, triggered=self.action_feed_hold))
        self.addAction(QAction(self, shortcut=Qt.Key.Key_Escape, triggered=self._sim_stop))

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

            /* Control Buttons (Fanuc-Style) */
            QPushButton#CycleStart {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #00DD00, stop:1 #009900);
                color: #FFF;
                font-weight: bold;
                font-size: 14px;
                border: 2px solid #00FF00;
                border-radius: 4px;
                padding: 10px 20px;
            }}
            QPushButton#CycleStart:hover {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #00FF00, stop:1 #00BB00); }}
            QPushButton#CycleStart:pressed {{ background: #006600; border-color: #00AA00; }}

            QPushButton#FeedHold {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #FF9900, stop:1 #CC6600);
                color: #000;
                font-weight: bold;
                border: 2px solid #FF8800;
                border-radius: 4px;
                padding: 10px 20px;
            }}
            QPushButton#FeedHold:hover {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #FFAA00, stop:1 #DD7700); }}
            QPushButton#FeedHold:pressed {{ background: #AA5500; }}

            QPushButton#ControlToggle {{
                background: #333;
                color: #AAA;
                border: 2px solid #555;
                border-radius: 4px;
                padding: 10px 16px;
                font-weight: bold;
            }}
            QPushButton#ControlToggle:hover {{ background: #444; border-color: #666; }}
            QPushButton#ControlToggle:checked {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {c['FANUC_YELLOW']}, stop:1 #CC9900);
                color: #000;
                border-color: {c['FANUC_YELLOW']};
            }}
            QPushButton#ControlToggle:checked:hover {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #FFDD00, stop:1 #DDAA00);
            }}
        """)

    def _apply_scaling(self):
        w = max(self.width(), 1100)
        scale = max(0.9, min(1.6, w / 1600))
        def set_font(widget, mul=1.0):
            f = QFont("Consolas"); f.setPointSizeF(self.base_pt * scale * mul); widget.setFont(f)
        set_font(self.editor, 0.95)  # Editor: kleiner
        set_font(self.chat, 0.9)     # Chat: kleiner
        set_font(self.input, 0.9)    # Input: kleiner
        set_font(self.table, 0.9)    # Tabelle: kleiner
        set_font(self.macroTable, 0.9)  # Makro-Tabelle: kleiner
        for b in [self.btnOpen, self.btnSave, self.btnSim, self.btnAna, self.btnGen, self.btnSettings]:
            set_font(b, 0.9); b.setMinimumHeight(int(34 * scale))
        for l in self.findChildren(QLabel, "PanelTitle"):
            f = l.font(); f.setPointSizeF(self.base_pt * scale * 0.95); f.setBold(True); l.setFont(f)
        # Tabs (WERKZEUGE/MAKROS): normale Größe
        for tab in [self.btnTabTools, self.btnTabMacros]:
            set_font(tab, 1.0)
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

        worker = AIWorker(fn, args, lambda ok, resp, handler=completion_handler: self._on_ai_task_finished(ok, resp, handler))
        QThreadPool.globalInstance().start(worker)

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
        """Doppelklick auf Macro → Insert in Editor"""
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

        # Insert in Editor
        text = f"{call_type} P{nr}"
        cursor = self.editor.textCursor()
        cursor.insertText(text)

    def on_macro_right_click(self, position):
        """Rechtsklick auf Macro → Edit/Delete Dialog"""
        index = self.macroTable.indexAt(position)
        if not index.isValid():
            return

        row = index.row()
        nr_idx = self.macroTable.model().index(row, 0)
        try:
            nr = int(self.macroTable.model().data(nr_idx, Qt.ItemDataRole.DisplayRole))
        except Exception:
            return

        # Öffne Macro-Editor Dialog
        dialog = MacroEditorDialog(self.macroTable.model(), macro_nr=nr, parent=self)
        dialog.exec()

    def on_new_tool_clicked(self):
        """'+ NEUES TOOL' Button → Öffne Dialog für neues Tool"""
        dialog = ToolEditorDialog(self.table.model(), tool_num=None, parent=self)
        dialog.exec()

    def on_new_macro_clicked(self):
        """'+ NEUES MACRO' Button → Öffne Dialog für neues Macro"""
        dialog = MacroEditorDialog(self.macroTable.model(), macro_nr=None, parent=self)
        dialog.exec()

    def _append_user_message(self, text: str):
        """Zeigt User-Nachricht im Chat an (mit Trennlinie und blauem Stil)"""
        self.chat.append("<hr style='border:1px solid #444; margin:8px 0;'>")
        self.chat.append(f"<div style='background:#1A2530; padding:8px; border-left:3px solid {self.cfg_colors['CYAN']}; margin:4px 0;'>"
                        f"<span style='color:{self.cfg_colors['CYAN']}; font-weight:bold;'>👤 Du:</span> {text}</div>")

    def _append_assistant_message(self, role: str, text: str, is_error: bool = False):
        """Zeigt Assistant-Nachricht im Chat an (mit grünem/gelben Stil)"""
        color = self.cfg_colors['FANUC_YELLOW'] if is_error else self.cfg_colors['CRT_GREEN']
        icon = "⚠️" if is_error else "🤖"
        self.chat.append(f"<div style='background:#1A1A1A; padding:8px; border-left:3px solid {color}; margin:4px 0;'>"
                        f"<span style='color:{color}; font-weight:bold;'>{icon} {role}:</span><br>{text}</div>")

    def _append_system_message(self, text: str):
        """Zeigt System-Nachricht im Chat an (CNC/Simulation, neutral grau)"""
        self.chat.append(f"<div style='background:#0A0A0A; padding:6px; border-left:2px solid #555; margin:2px 0;'>"
                        f"<span style='color:#888; font-size:0.9em;'>⚙️ CNC:</span> <span style='color:#AAA;'>{text}</span></div>")

    def _handle_analyze_response(self, ok: bool, resp: str, role: str):
        if ok:
            self._append_assistant_message(role, resp)
            self.status.showMessage("✅ KI-Analyse: Fertig", 5000)
        else:
            self._append_assistant_message("Fehler", resp, is_error=True)
            self.status.showMessage("❌ KI-Fehler", 5000)

    def _handle_generate_response(self, ok: bool, resp: str, who: str):
        if ok:
            self._append_assistant_message(who, resp)
            self.status.showMessage("✅ Code generiert", 5000)
        else:
            self._append_assistant_message("Fehler", resp, is_error=True)
            self.status.showMessage("❌ KI-Fehler", 5000)

    def _on_editor_changed(self):
        """Editor-Änderung → Plot-Update (mit 500ms Debounce)"""
        code = self.editor.toPlainText()
        self.plotter.pending_code = code
        self.plotter.update_plot()

    def _on_cursor_changed(self):
        """Cursor-Position im Editor → Highlight im Plot"""
        cursor = self.editor.textCursor()
        line_num = cursor.blockNumber() + 1  # QTextEdit zählt ab 0
        self.plotter.highlight_line(line_num)

    def _on_plot_clicked(self, line_num: int):
        """Klick im Plot → Springe zu Zeile im Editor"""
        cursor = self.editor.textCursor()
        # Gehe zu Zeile (line_num ist 1-basiert)
        block = self.editor.document().findBlockByLineNumber(line_num - 1)
        if block.isValid():
            cursor.setPosition(block.position())
            self.editor.setTextCursor(cursor)
            self.editor.centerCursor()
            self.editor.setFocus()

    # --- Control Actions ---
    def action_cycle_start(self):
        """CYCLE START Button → Startet/Fortsetzt 2D-Simulation"""
        single_block = self.btnSingleBlock.isChecked()
        opt_stop = self.btnOptStop.isChecked()

        mode_info = []
        if single_block:
            mode_info.append("SINGLE BLOCK")
        if opt_stop:
            mode_info.append("OPT STOP")

        mode_str = f" ({', '.join(mode_info)})" if mode_info else ""
        self._append_system_message(f"▶ CYCLE START{mode_str}")

        # Starte 2D-Simulation
        self._sim_start()

    def action_feed_hold(self):
        """FEED HOLD Button → Pausiert Simulation"""
        self._append_system_message("⏸ FEED HOLD gedrückt")
        self._sim_pause()

    def action_opt_stop_toggled(self, checked: bool):
        """OPTIONAL STOP Toggle → M01 aktiv/inaktiv"""
        state = "EIN" if checked else "AUS"
        icon = "⊙" if checked else "○"
        self.status.showMessage(f"{icon} OPTIONAL STOP: {state}", 2000)
        self._append_system_message(f"⊙ OPTIONAL STOP → {state}")

    def action_single_block_toggled(self, checked: bool):
        """SINGLE BLOCK Toggle → Einzelsatz-Modus"""
        state = "EIN" if checked else "AUS"
        icon = "⊙" if checked else "○"
        self.status.showMessage(f"{icon} SINGLE BLOCK: {state}", 2000)
        self._append_system_message(f"⊙ SINGLE BLOCK → {state}")

    # --- Simulation Engine ---
    def _sim_start(self):
        """Startet oder setzt die Simulation fort"""
        if self.sim_state == "STOPPED":
            # Simulation neu starten
            code = self.editor.toPlainText()
            self.sim_lines = [line.strip() for line in code.split('\n')]
            self.sim_current_line = 0
            self.sim_state = "RUNNING"

            # Live-Position zurücksetzen
            self.sim_x = 0.0
            self.sim_z = 0.0
            self.sim_tool = 0
            self.sim_s = 0
            self.sim_f = 0.0

            # Live-Drawing: Start bei Zeile 0 (nichts gezeichnet)
            self.plotter.set_live_max_line(0)

            # Geschwindigkeit: 200ms pro Zeile (Standard), 500ms für SINGLE BLOCK
            interval = 500 if self.btnSingleBlock.isChecked() else 200
            self.sim_timer.start(interval)

            self.status.showMessage("▶ Simulation läuft", 0)
            self._append_system_message("🎬 Simulation gestartet")

            # Gelben Marker einblenden
            self.editor.set_sim_line(1)

        elif self.sim_state == "PAUSED":
            # Simulation fortsetzen
            self.sim_state = "RUNNING"
            interval = 500 if self.btnSingleBlock.isChecked() else 200
            self.sim_timer.start(interval)
            self.status.showMessage("▶ Simulation fortgesetzt", 0)
            self._append_system_message("▶ Simulation fortgesetzt")

    def _sim_step(self):
        """Führt einen Simulations-Schritt aus (eine G-Code-Zeile)"""
        if self.sim_state != "RUNNING":
            return

        # Prüfe ob Simulation fertig
        if self.sim_current_line >= len(self.sim_lines):
            self._sim_stop()
            self._append_system_message("✅ Programm beendet (M30)")
            return

        # Aktuelle Zeile holen
        line = self.sim_lines[self.sim_current_line]
        line_num = self.sim_current_line + 1  # 1-basiert

        # Zeile im Editor highlighten + Gelben Marker setzen
        self.editor.set_sim_line(line_num)
        self.plotter.highlight_line(line_num)
        block = self.editor.document().findBlockByLineNumber(self.sim_current_line)
        if block.isValid():
            cursor = self.editor.textCursor()
            cursor.setPosition(block.position())
            self.editor.setTextCursor(cursor)
            self.editor.centerCursor()

        # Status aktualisieren
        self.status.showMessage(f"▶ SIM: N{line_num} {line[:30]}...", 0)

        # Parse aktuelle Zeile für Live-Position
        self._update_sim_position(line)

        # Live-Drawing: Zeichne nur bis zur aktuellen Zeile
        self.plotter.set_live_max_line(line_num)

        # M-Codes prüfen
        if 'M01' in line.upper() and self.btnOptStop.isChecked():
            # Optional Stop
            self._sim_pause()
            self._append_system_message(f"⏸ M01 - OPTIONAL STOP (Zeile {line_num})")
            self.status.showMessage(f"⏸ M01 - OPTIONAL STOP (N{line_num})", 0)
            self.sim_current_line += 1  # Zeile ist abgearbeitet
            return

        if 'M30' in line.upper() or 'M02' in line.upper():
            # Programm-Ende
            self.sim_current_line += 1
            self._sim_stop()
            self._append_system_message("✅ Programm beendet (M30/M02)")
            return

        # Nächste Zeile
        self.sim_current_line += 1

        # SINGLE BLOCK: Nach einer Zeile pausieren
        if self.btnSingleBlock.isChecked():
            self._sim_pause()

    def _sim_pause(self):
        """Pausiert die Simulation"""
        if self.sim_state == "RUNNING":
            self.sim_state = "PAUSED"
            self.sim_timer.stop()
            self.status.showMessage("⏸ Simulation pausiert", 0)

    def _sim_stop(self):
        """Stoppt die Simulation komplett"""
        self.sim_state = "STOPPED"
        self.sim_timer.stop()
        self.sim_current_line = 0
        self.sim_lines = []
        self.status.showMessage("⏹ Simulation gestoppt", 3000)

        # Gelben Marker entfernen
        self.editor.clear_sim_line()

        # Live-Position-Anzeige entfernen
        self.plotter.clear_live_position()

        # Live-Drawing zurücksetzen (alle Linien anzeigen)
        self.plotter.clear_live_max_line()

    def _update_sim_position(self, line: str):
        """Parst eine G-Code-Zeile und aktualisiert die Live-Position"""
        # Kommentare entfernen
        line = re.sub(r'\(.*?\)', '', line).strip()
        if not line:
            return

        # X/Z-Koordinaten extrahieren
        x_match = re.search(r'X([-+]?\d*\.?\d+)', line, re.IGNORECASE)
        z_match = re.search(r'Z([-+]?\d*\.?\d+)', line, re.IGNORECASE)

        if x_match:
            self.sim_x = float(x_match.group(1))
        if z_match:
            self.sim_z = float(z_match.group(1))

        # Werkzeug
        t_match = re.search(r'T(\d+)', line, re.IGNORECASE)
        if t_match:
            self.sim_tool = int(t_match.group(1)) // 100  # T0101 -> T1

        # Spindeldrehzahl
        s_match = re.search(r'S(\d+)', line, re.IGNORECASE)
        if s_match:
            self.sim_s = int(s_match.group(1))

        # Vorschub
        f_match = re.search(r'F([-+]?\d*\.?\d+)', line, re.IGNORECASE)
        if f_match:
            self.sim_f = float(f_match.group(1))

        # An Plotter übergeben (zeige immer X und Z, auch wenn 0)
        self.plotter.set_live_position(
            x=self.sim_x,
            z=self.sim_z,
            tool=self.sim_tool if self.sim_tool > 0 else None,
            s=self.sim_s if self.sim_s > 0 else None,
            f=self.sim_f if self.sim_f > 0.0 else None
        )

    def resizeEvent(self, e):
        super().resizeEvent(e); self._apply_scaling()

    def zoom_in(self):  self.base_pt = min(self.base_pt + 1, 24); self._apply_scaling()
    def zoom_out(self): self.base_pt = max(self.base_pt - 1, 8);  self._apply_scaling()

    # --- Find/Replace ---
    def show_find_dialog(self):
        """Ctrl+F: Öffne Find/Replace Dialog im Find-Modus"""
        if self.find_replace_dialog is None:
            self.find_replace_dialog = FindReplaceDialog(self.editor, self)

        # Wenn Text selektiert ist, übernehme ihn als Suchtext
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            selected_text = cursor.selectedText()
            self.find_replace_dialog.find_input.setText(selected_text)

        self.find_replace_dialog.show()
        self.find_replace_dialog.raise_()
        self.find_replace_dialog.activateWindow()
        self.find_replace_dialog.find_input.setFocus()
        self.find_replace_dialog.find_input.selectAll()

    def show_replace_dialog(self):
        """Ctrl+H: Öffne Find/Replace Dialog im Replace-Modus"""
        if self.find_replace_dialog is None:
            self.find_replace_dialog = FindReplaceDialog(self.editor, self)

        # Wenn Text selektiert ist, übernehme ihn als Suchtext
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            selected_text = cursor.selectedText()
            self.find_replace_dialog.find_input.setText(selected_text)

        self.find_replace_dialog.show()
        self.find_replace_dialog.raise_()
        self.find_replace_dialog.activateWindow()
        self.find_replace_dialog.replace_input.setFocus()  # Focus auf Replace-Feld

    # --- Recent Files ---
    def _get_recent_files(self):
        """Lade Recent Files aus Settings (max 5)"""
        recent = self.settings.value("recent_files", [])
        if not isinstance(recent, list):
            recent = []
        return recent[:5]  # Max 5

    def _add_to_recent_files(self, filepath):
        """Füge Datei zur Recent Files Liste hinzu"""
        recent = self._get_recent_files()

        # Entferne Duplikate
        if filepath in recent:
            recent.remove(filepath)

        # Füge an erster Stelle ein
        recent.insert(0, filepath)

        # Max 5 behalten
        recent = recent[:5]

        # Speichern
        self.settings.setValue("recent_files", recent)

        # UI aktualisieren
        self._update_recent_files_menu()

    def _update_recent_files_menu(self):
        """Aktualisiere das Recent Files Dropdown-Menü"""
        self.recent_files_menu.clear()

        # "Datei öffnen..." als erste Option
        open_action = self.recent_files_menu.addAction("📂 Datei öffnen...")
        open_action.triggered.connect(self._open_file_dialog)

        recent = self._get_recent_files()

        if recent:
            self.recent_files_menu.addSeparator()
            self.recent_files_menu.addSection("Zuletzt geöffnet:")

            for filepath in recent:
                # Prüfe ob Datei noch existiert
                if not os.path.exists(filepath):
                    continue

                filename = os.path.basename(filepath)
                action = self.recent_files_menu.addAction(f"📄 {filename}")
                # Lambda mit default argument um filepath zu capturen
                action.triggered.connect(lambda checked=False, p=filepath: self._open_recent_file(p))

    def _open_file_dialog(self):
        """Öffne normalen File-Dialog"""
        path, _ = QFileDialog.getOpenFileName(self, "Programm öffnen", "", "G-Code (*.nc *.txt *.tap *.gcode);;Alle Dateien (*)")
        if path:
            self._load_file(path)

    def _open_recent_file(self, filepath):
        """Öffne eine Datei aus der Recent Files Liste"""
        if not os.path.exists(filepath):
            QMessageBox.warning(self, "Datei nicht gefunden", f"Datei existiert nicht mehr:\n{filepath}")
            # Entferne aus Recent Files
            recent = self._get_recent_files()
            if filepath in recent:
                recent.remove(filepath)
                self.settings.setValue("recent_files", recent)
                self._update_recent_files_menu()
            return

        self._load_file(filepath)

    def _load_file(self, path):
        """Lade Datei in Editor"""
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

        # Zur Recent Files hinzufügen
        self._add_to_recent_files(path)

    # --- File Ops ---
    def action_open(self):
        """Wird vom Shortcut aufgerufen - öffne Dialog"""
        self._open_file_dialog()

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

        # Zur Recent Files hinzufügen
        self._add_to_recent_files(target_path)

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
            self._append_system_message(f"✅ Datei in VM kopiert: {msg}")
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
            lint_text = f"<b>{len(findings)} Fund(e):</b><br>"
            for f in findings:
                lint_text += f"<span style='color:{self.cfg_colors['CRT_GREEN']}'>• Zeile {f['line']}: {f['rule']}</span> — {f['message']}<br>"
            self._append_assistant_message("Lint", lint_text.rstrip('<br>'), is_error=True)
        else:
            self.editor.clear_error_lines()
            self.status.showMessage("✅ Lint: OK", 3000)
            self._append_assistant_message("Lint", "✅ Keine Probleme gefunden")

    # --- Lint & AI ---
    def action_analyze(self):
        """KI: ANALYZE Button + Enter im Input"""
        # User-Eingabe anzeigen und leeren (falls vorhanden)
        user_text = self.input.text().strip()
        if user_text:
            self._append_user_message(user_text)
            self.input.clear()

        code = self.editor.toPlainText()
        findings = self.linter.run_all(code)
        
        if findings:
            error_lines = [f['line'] for f in findings]
            self.editor.set_error_lines(error_lines)
            lint_text = f"<b>{len(findings)} Fund(e):</b><br>"
            for f in findings:
                lint_text += f"<span style='color:{self.cfg_colors['CRT_GREEN']}'>• Zeile {f['line']}: {f['rule']}</span> — {f['message']}<br>"
            self._append_assistant_message("Lint", lint_text.rstrip('<br>'), is_error=True)
        else:
            self.editor.clear_error_lines()
            self._append_assistant_message("Lint", "✅ Keine Probleme gefunden")

        if self.cfg.data['ai'].get('offline', False):
            self._append_assistant_message("VibeCNC", "Offline-Modus aktiv — keine KI-Abfrage.", is_error=True)
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

        format_rules = """
WICHTIG - Formatierung:
- Nutze HTML-Tags für Struktur (wird in HTML-Widget angezeigt)
- Überschriften: <b>Überschrift</b>
- Absätze: Doppelte Zeilenumbrüche <br><br>
- Listen: Bullet Points mit • oder nummeriert (1., 2., ...)
- Code-Blöcke: <pre style='color:#6CFF6C; background:#0A0A0A; padding:4px;'>code</pre>
- KEINE Markdown (**, ##, etc.) - nur HTML!

Beispiel gute Antwort:
<b>Analyse-Ergebnis:</b><br><br>
• Zeile 5: G21 fehlt im Header<br>
• Zeile 12: G40 vor Werkzeugwechsel setzen<br><br>
<b>Empfohlene Fixes:</b><br>
1. Header erweitern mit G21<br>
2. G40 vor T-Code einfügen
"""
        prompt = f"Maschine: FANUC 0i‑TF, Material: 42CrMo4\nPolicies:\n{policies}\nTools(JSON):\n{json.dumps(tools_json, ensure_ascii=False)}\n\nCode:\n```\n{snippet}\n```\n\nAufgabe: Liste Regelverstöße (Zeile/Regel/Fix). Optional unified diff. Keine kosmetischen Änderungen.\n\n{format_rules}"
        ai_fn = self.ai.ask_claude if mode == 'claude' else self.ai.ask_ollama
        role = "Claude" if mode == 'claude' else "LLM"
        self._start_ai_task("⚙️ KI-Analyse läuft...", ai_fn, (prompt,), lambda ok, resp, role=role: self._handle_analyze_response(ok, resp, role))

    def action_generate(self):
        """KI: GEN-CODE Button"""
        user = self.input.text().strip()
        if not user:
            user = "Erzeuge G71-Schruppzyklus für T1, Zustellung 0.4, f0.25."

        # User-Eingabe anzeigen und leeren
        self._append_user_message(user)
        self.input.clear()

        if self.cfg.data['ai'].get('offline', False):
            offline_msg = "Offline — Gen‑Stub: G00/G01‑Block vorgeschlagen.<br><pre style='color:#6CFF6C;'>G00 X36. Z2.\nG01 Z0. F0.25\nG01 X-5.\nG00 X200.\n</pre>"
            self._append_assistant_message("VibeCNC", offline_msg, is_error=True)
            self.status.showMessage("⚠️ Offline-Modus", 3000)
            return

        # Formatierungs-Regeln hinzufügen
        format_rules = """

WICHTIG - Formatierung deiner Antwort:
- Nutze HTML-Tags (wird in HTML-Widget angezeigt)
- Überschriften: <b>Überschrift</b>
- Absätze: Doppelte Zeilenumbrüche <br><br> zwischen Abschnitten
- Listen: Bullet Points mit • oder nummeriert (1., 2., ...)
- G-Code: <pre style='color:#6CFF6C; background:#0A0A0A; padding:4px;'>G-Code hier</pre>
- KEINE Markdown (**, ##, ```), nur HTML!

Beispiel:
<b>G-Code für Schruppzyklus:</b><br><br>
<pre style='color:#6CFF6C; background:#0A0A0A; padding:4px;'>
G71 U1.0 R0.5
G71 P10 Q20 U0.4 W0.1 D500 F0.25
</pre><br>
<b>Erklärung:</b><br>
• U1.0: Zustellung<br>
• D500: Drehzahl
"""
        enhanced_prompt = user + format_rules

        mode = self.cfg.data['ai'].get('mode', 'claude')
        who = "Claude" if mode == 'claude' else "LLM"
        self._start_ai_task("⚙️ KI generiert Code...", self.ai.ask, (enhanced_prompt,), lambda ok, resp, who=who: self._handle_generate_response(ok, resp, who))

    # --- Settings ---
    def action_settings(self):
        """⚙️ SETTINGS Button → Öffne Settings Dialog"""
        dialog = SettingsDialog(self.cfg, parent=self)
        dialog.exec()

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
                self._append_system_message(f"✅ Datei in VM‑Share kopiert: {msg2}")
                self.status.showMessage(f"✅ VM-Copy: {os.path.basename(msg2)}", 5000)
            else:
                self._append_assistant_message("VibeCNC", f"{msg} — Share fehlgeschlagen: {msg2}", is_error=True)
                self.status.showMessage(f"❌ Sim-Fehler", 5000)
        else:
            self._append_system_message(f"✅ CAMotics gestartet ({msg})")
            self.status.showMessage(f"✅ CAMotics gestartet", 5000)

    # --- Tool Integration Handlers ---
    def on_tool_right_click(self, position):
        """Right click: Open tool editor dialog"""
        index = self.table.indexAt(position)
        if not index.isValid():
            return

        row = index.row()
        tool_row = self.table.model().rows[row]
        tool_num = tool_row[0]

        # Open tool editor
        dlg = ToolEditorDialog(self.table.model(), tool_num, self)
        dlg.exec()
    
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
        self.settings.setValue("vsplitter", self.vsplit.saveState())
        self.settings.setValue("base_pt", self.base_pt)
        self.settings.setValue("opt_stop", self.btnOptStop.isChecked())
        self.settings.setValue("single_block", self.btnSingleBlock.isChecked())

    def _restore_state(self):
        g = self.settings.value("geometry")
        if g is not None: self.restoreGeometry(g)
        s = self.settings.value("splitter")
        if s is not None: self.split.restoreState(s)
        vs = self.settings.value("vsplitter")
        if vs is not None: self.vsplit.restoreState(vs)
        bp = self.settings.value("base_pt")
        if bp is not None: self.base_pt = int(bp)
        opt = self.settings.value("opt_stop", False, type=bool)
        if opt: self.btnOptStop.setChecked(True)
        sb = self.settings.value("single_block", False, type=bool)
        if sb: self.btnSingleBlock.setChecked(True)

if __name__ == "__main__":
    # High-DPI Support für Windows 11
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    
    app = QApplication(sys.argv)
    win = Main(); win.show()
    sys.exit(app.exec())

