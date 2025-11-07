# gcode_completer.py — Context-aware Autocomplete für G-Code
import re
from PyQt6.QtCore import Qt, QStringListModel, QAbstractListModel, QModelIndex, QVariant, QObject, QEvent
from PyQt6.QtWidgets import QCompleter
from PyQt6.QtGui import QTextCursor, QKeyEvent

class GCodeCompleterModel(QAbstractListModel):
    """Custom Model für context-aware Autocomplete"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.suggestions = []
        self.descriptions = []  # Zusätzliche Beschreibungen für Hover
        
    def rowCount(self, parent=QModelIndex()):
        return len(self.suggestions)
    
    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.suggestions):
            return QVariant()
        
        if role == Qt.ItemDataRole.DisplayRole:
            return self.suggestions[index.row()]
        elif role == Qt.ItemDataRole.ToolTipRole:
            if index.row() < len(self.descriptions):
                return self.descriptions[index.row()]
        
        return QVariant()
    
    def update_suggestions(self, suggestions, descriptions=None):
        """Aktualisiere die Vorschlagsliste"""
        self.beginResetModel()
        self.suggestions = suggestions
        self.descriptions = descriptions or [""] * len(suggestions)
        self.endResetModel()


class GCodeCompleter(QCompleter):
    """Intelligenter G-Code Autocompleter mit Context-Awareness"""
    
    # G-Code Definitionen mit Parametern
    GCODES = {
        "G00": "Eilgang (G00 X... Z...)",
        "G01": "Linearinterpolation (G01 X... Z... F...)",
        "G02": "Kreisinterpolation im Uhrzeigersinn (G02 X... Z... R... F...)",
        "G03": "Kreisinterpolation gegen Uhrzeigersinn (G03 X... Z... R... F...)",
        "G04": "Verweilzeit (G04 P... oder G04 U...)",
        "G18": "ZX-Ebene (Drehmaschine)",
        "G20": "Zoll-Eingabe",
        "G21": "Metrische Eingabe",
        "G28": "Referenzpunkt anfahren (G28 U0 W0)",
        "G40": "Werkzeugkorrektur ausschalten",
        "G41": "Werkzeugkorrektur links (G41)",
        "G42": "Werkzeugkorrektur rechts (G42)",
        "G50": "Koordinatensystem setzen (G50 S... / X... Z...)",
        "G54": "Nullpunktverschiebung 1",
        "G70": "Fertigbearbeitungszyklus (G70 P... Q...)",
        "G71": "Schrupp-Zyklus außen (G71 U... R... P... Q... D... F...)",
        "G72": "Schrupp-Zyklus Plan (G72 W... R... P... Q... D... F...)",
        "G73": "Wiederholzyklus unregelmäßig (G73 U... W... R... P... Q... D... F...)",
        "G74": "Plan-Einstechzyklus (G74 R... X... Z... P... Q... F...)",
        "G75": "Radial-Einstechzyklus (G75 R... X... Z... P... Q... F...)",
        "G76": "Gewindezyklus (G76 P... Q... R... X... Z... F...)",
        "G80": "Bohrzyklen abwählen",
        "G90": "Absolutmaß-Programmierung",
        "G91": "Inkrementalmaß-Programmierung",
        "G92": "Gewindeschneiden (G92 X... Z... F...)",
        "G94": "Vorschub in mm/min",
        "G95": "Vorschub in mm/U",
        "G96": "Konstante Schnittgeschwindigkeit EIN (G96 S...)",
        "G97": "Konstante Schnittgeschwindigkeit AUS (G97 S...)",
        "G98": "Vorschub pro Minute",
        "G99": "Vorschub pro Umdrehung",
    }
    
    MCODES = {
        "M00": "Programmstopp",
        "M01": "Wahlweiser Halt",
        "M02": "Programmende",
        "M03": "Spindel rechts (M03 S...)",
        "M04": "Spindel links (M04 S...)",
        "M05": "Spindel stopp",
        "M08": "Kühlmittel EIN",
        "M09": "Kühlmittel AUS",
        "M30": "Programmende mit Reset",
        "M98": "Unterprogramm rufen (M98 P...)",
        "M99": "Unterprogramm Ende",
    }
    
    # Achsen-Parameter
    AXES = {
        "X": "X-Achse (Durchmesser)",
        "Z": "Z-Achse (Länge)",
        "U": "U-Achse (inkremental X)",
        "W": "W-Achse (inkremental Z)",
        "I": "I-Achse (Kreis-Mittelpunkt X)",
        "K": "K-Achse (Kreis-Mittelpunkt Z)",
        "R": "R-Radius",
    }
    
    # Weitere Parameter
    PARAMS = {
        "F": "Vorschub (Feed)",
        "S": "Spindeldrehzahl (Speed)",
        "T": "Werkzeug (Tool)",
        "P": "Parameter P",
        "Q": "Parameter Q",
        "D": "Tiefe/Schnitttiefe",
        "N": "Satznummer",
    }
    
    def __init__(self, tool_model=None, macro_model=None, parent=None):
        self.custom_model = GCodeCompleterModel()
        super().__init__(self.custom_model, parent)
        
        self.tool_model = tool_model
        self.macro_model = macro_model
        
        # Completer Einstellungen
        self.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.setModelSorting(QCompleter.ModelSorting.CaseInsensitivelySortedModel)
        self.setMaxVisibleItems(10)
        
        # Style für Popup
        self.popup().setStyleSheet("""
            QListView {
                background: #1A1A1A;
                color: #FFC800;
                border: 2px solid #FFC800;
                font-family: Consolas, 'Fira Code', monospace;
                font-size: 11pt;
                padding: 4px;
            }
            QListView::item {
                padding: 6px;
                border-bottom: 1px solid #333;
            }
            QListView::item:selected {
                background: #FFC800;
                color: #000;
                font-weight: bold;
            }
            QListView::item:hover {
                background: rgba(255, 200, 0, 0.3);
            }
        """)
    
    def get_context_suggestions(self, text_before_cursor):
        """Generiere kontextbasierte Vorschläge"""
        suggestions = []
        descriptions = []
        
        # Letztes Wort vor Cursor
        words = text_before_cursor.split()
        if not words:
            # Am Anfang: Zeige G-Codes und N
            suggestions = ["N", "G00", "G01", "G71", "T", "M03", "M98"]
            descriptions = [
                "Satznummer",
                self.GCODES["G00"],
                self.GCODES["G01"],
                self.GCODES["G71"],
                "Werkzeug",
                self.MCODES["M03"],
                self.MCODES["M98"]
            ]
            return suggestions, descriptions
        
        last_word = words[-1].upper()
        
        # 1. G-Code Completion
        if last_word.startswith("G") and len(last_word) <= 3:
            for code, desc in self.GCODES.items():
                if code.startswith(last_word):
                    suggestions.append(code)
                    descriptions.append(desc)
        
        # 2. M-Code Completion
        elif last_word.startswith("M") and len(last_word) <= 3:
            for code, desc in self.MCODES.items():
                if code.startswith(last_word):
                    suggestions.append(code)
                    descriptions.append(desc)
        
        # 3. Tool Completion (T01, T02, ...)
        elif last_word.startswith("T") and len(last_word) <= 5:
            if self.tool_model:
                for row in self.tool_model.rows:
                    tool_num = row[0]  # T-Nummer
                    tool_name = row[2]  # Name
                    tool_code = f"T{tool_num:02d}01"
                    if tool_code.startswith(last_word):
                        suggestions.append(tool_code)
                        descriptions.append(f"Werkzeug {tool_num}: {tool_name}")
        
        # 4. Parameter Completion (X, Z, F, S, ...)
        elif len(last_word) == 1 and last_word in "XZUWIKRFSTPQDN":
            param = last_word
            # Zeige Parameter-Hint
            if param in self.AXES:
                suggestions.append(param + "0.0")
                descriptions.append(self.AXES[param])
            elif param in self.PARAMS:
                suggestions.append(param + "100")
                descriptions.append(self.PARAMS[param])
        
        # 5. Makro Completion (M98 P1234)
        elif "M98" in text_before_cursor and last_word.startswith("P"):
            if self.macro_model:
                # Hole alle Makro-Nummern
                for row_idx in range(self.macro_model.rowCount()):
                    macro_nr = self.macro_model.data(
                        self.macro_model.index(row_idx, 0), 
                        Qt.ItemDataRole.DisplayRole
                    )
                    macro_name = self.macro_model.data(
                        self.macro_model.index(row_idx, 1), 
                        Qt.ItemDataRole.DisplayRole
                    )
                    macro_code = f"P{macro_nr}"
                    if macro_code.startswith(last_word):
                        suggestions.append(macro_code)
                        descriptions.append(f"Makro {macro_nr}: {macro_name}")
        
        # 6. Fallback: Alle G-Codes anzeigen wenn nichts gefunden
        if not suggestions:
            # Zeige Top 10 häufigste G-Codes
            common = ["G00", "G01", "G71", "G40", "G50", "M03", "M05", "M30"]
            for code in common:
                if code.startswith("G"):
                    suggestions.append(code)
                    descriptions.append(self.GCODES.get(code, ""))
                elif code.startswith("M"):
                    suggestions.append(code)
                    descriptions.append(self.MCODES.get(code, ""))
        
        return suggestions, descriptions
    
    def update_for_context(self, text_before_cursor):
        """Update completer basierend auf dem Kontext"""
        suggestions, descriptions = self.get_context_suggestions(text_before_cursor)
        
        # Prefix extrahieren (letztes Wort)
        words = text_before_cursor.split()
        if words:
            prefix = words[-1].upper()  # Uppercase für Case-Insensitive
        else:
            prefix = ""
        
        # Wenn nur einzelner Buchstabe (G, M, T), dann Prefix leeren
        # damit alle Vorschläge angezeigt werden
        if len(prefix) == 1 and prefix in "GMT":
            self.setCompletionPrefix("")
        else:
            self.setCompletionPrefix(prefix)
        
        # Model updaten
        self.custom_model.update_suggestions(suggestions, descriptions)
        
        return len(suggestions) > 0


class CompleterEventFilter(QObject):
    """Event Filter für Autocomplete"""
    
    def __init__(self, editor, completer):
        super().__init__(editor)
        self.editor = editor
        self.completer = completer
        
    def eventFilter(self, obj, event):
        # Handhabe Events von Editor UND Popup
        if event.type() != QEvent.Type.KeyPress:
            return False
        
        try:
            key_event = event
            
            # Wenn Popup sichtbar ist
            if self.completer.popup().isVisible():
                if key_event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Tab):
                    # Manuell den aktuellen Vorschlag holen und einfügen
                    index = self.completer.popup().currentIndex()
                    if index.isValid():
                        completion = self.completer.completionModel().data(index, Qt.ItemDataRole.DisplayRole)
                        if completion:  # Nur wenn nicht leer
                            insert_completion(self.editor, self.completer, completion)
                        self.completer.popup().hide()
                    return True  # Event konsumiert
                elif key_event.key() == Qt.Key.Key_Escape:
                    self.completer.popup().hide()
                    return True  # Event konsumiert
                elif key_event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                    # Navigation durchlassen an Popup
                    return False
            
            # Nur auf Editor: Ctrl+Space triggert Autocomplete
            if obj == self.editor:
                if key_event.key() == Qt.Key.Key_Space and key_event.modifiers() == Qt.KeyboardModifier.ControlModifier:
                    self._trigger_completer()
                    return True  # Event konsumiert
            
            # Alle anderen Events durchlassen
            return False
            
        except Exception as e:
            print(f"[Autocomplete] Error in eventFilter: {e}")
            import traceback
            traceback.print_exc()
            return False  # Bei Fehler: Lasse Event durch
    
    def _trigger_completer(self):
        """Zeige Autocomplete"""
        try:
            cursor = self.editor.textCursor()
            cursor.select(QTextCursor.SelectionType.LineUnderCursor)
            text_before = cursor.selectedText()
            
            if self.completer.update_for_context(text_before):
                # Einfacher Aufruf ohne custom rect
                self.completer.complete()
        except Exception as e:
            print(f"[Autocomplete] Error: {e}")


def install_completer(editor, tool_model=None, macro_model=None):
    """Installiere Autocomplete auf einem GCodeEditor"""
    completer = GCodeCompleter(tool_model, macro_model, editor)
    
    # WICHTIG: Completer mit Widget verbinden
    completer.setWidget(editor)
    
    # Event Filter installieren (auf Editor UND Popup!)
    event_filter = CompleterEventFilter(editor, completer)
    editor.installEventFilter(event_filter)
    completer.popup().installEventFilter(event_filter)  # WICHTIG!
    
    # Mausklick ist instabil - nur Tastatur-Steuerung
    # Enter/Tab wird vom Event Filter behandelt
    
    print(f"[Autocomplete] Bereit ({len(tool_model.rows) if tool_model else 0} Tools geladen)")
    print("[Autocomplete] Ctrl+Space zum Oeffnen | Pfeiltasten Navigation | Enter Uebernehmen")
    
    return completer


def insert_completion(editor, completer, completion):
    """Füge ausgewählten Vorschlag ein"""
    try:
        cursor = editor.textCursor()
        
        # Hole aktuellen Text in der Zeile
        cursor.select(QTextCursor.SelectionType.LineUnderCursor)
        line_text = cursor.selectedText()
        
        # Finde wie viel wir löschen müssen (letztes Wort)
        words = line_text.split()
        if words:
            prefix = words[-1].upper()
            
            # Gehe zum Ende und lösche Prefix
            cursor = editor.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.EndOfLine)
            cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.KeepAnchor, len(prefix))
            cursor.removeSelectedText()
        
        # Füge Completion ein
        cursor = editor.textCursor()
        cursor.insertText(completion)
        editor.setTextCursor(cursor)
        
    except Exception as e:
        print(f"[Autocomplete] Error: {e}")

