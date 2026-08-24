# gcode_completer.py — Context-aware Autocomplete for G-Code
from PyQt6.QtCore import Qt, QAbstractListModel, QModelIndex, QVariant, QObject, QEvent
from PyQt6.QtWidgets import QCompleter
from PyQt6.QtGui import QTextCursor

class GCodeCompleterModel(QAbstractListModel):
    """Custom Model for context-aware Autocomplete"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.suggestions = []
        self.descriptions = []  # Additional descriptions for hover
        
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
        """Update the suggestion list"""
        self.beginResetModel()
        self.suggestions = suggestions
        self.descriptions = descriptions or [""] * len(suggestions)
        self.endResetModel()


class GCodeCompleter(QCompleter):
    """Intelligent G-Code Autocompleter with Context-Awareness"""
    
    # G-Code definitions with parameters
    GCODES = {
        "G00": "Rapid traverse (G00 X... Z...)",
        "G01": "Linear interpolation (G01 X... Z... F...)",
        "G02": "Circular interpolation CW (G02 X... Z... R... F...)",
        "G03": "Circular interpolation CCW (G03 X... Z... R... F...)",
        "G04": "Dwell (G04 P... or G04 U...)",
        "G10": "Programmable data input (G10 P... X... Z... R...)",
        "G18": "ZX plane (lathe)",
        "G20": "Inch input",
        "G21": "Metric input",
        "G27": "Reference point check (G27 X... Z...)",
        "G28": "Return to reference point (G28 U0 W0)",
        "G32": "Simple threading (G32 Z... F...)",
        "G40": "Tool nose radius compensation cancel",
        "G41": "Tool nose radius compensation left (G41)",
        "G42": "Tool nose radius compensation right (G42)",
        "G50": "Set coordinate system (G50 S... / X... Z...)",
        "G54": "Work offset 1",
        "G55": "Work offset 2",
        "G56": "Work offset 3",
        "G57": "Work offset 4",
        "G58": "Work offset 5",
        "G59": "Work offset 6",
        "G65": "Macro call (G65 P... A... B... C...)",
        "G66": "Macro modal call (G66 P... A... B...)",
        "G67": "Macro modal call cancel",
        "G70": "Finish cycle (G70 P... Q...)",
        "G71": "Roughing cycle OD (G71 U... R... P... Q... D... F...)",
        "G72": "Roughing cycle facing (G72 W... R... P... Q... D... F...)",
        "G73": "Pattern repeat cycle (G73 U... W... R... P... Q... D... F...)",
        "G74": "Face grooving cycle (G74 R... X... Z... P... Q... F...)",
        "G75": "Radial grooving cycle (G75 R... X... Z... P... Q... F...)",
        "G76": "Threading cycle (G76 P... Q... R... X... Z... F...)",
        "G80": "Canned cycle cancel",
        "G90": "Absolute programming",
        "G91": "Incremental programming",
        "G92": "Thread cutting (G92 X... Z... F...)",
        "G94": "Feed per minute",
        "G95": "Feed per revolution",
        "G96": "Constant surface speed ON (G96 S...)",
        "G97": "Constant surface speed OFF (G97 S...)",
        "G98": "Feed per minute",
        "G99": "Feed per revolution",
    }
    
    MCODES = {
        "M00": "Program stop",
        "M01": "Optional stop",
        "M02": "Program end",
        "M03": "Spindle CW (M03 S...)",
        "M04": "Spindle CCW (M04 S...)",
        "M05": "Spindle stop",
        "M06": "Tool change (M06 T...)",
        "M08": "Coolant ON",
        "M09": "Coolant OFF",
        "M10": "Chuck open (4th axis)",
        "M11": "Chuck close (4th axis)",
        "M19": "Spindle orientation (M19 R...)",
        "M30": "Program end with reset",
        "M41": "Gear range 1 (low)",
        "M42": "Gear range 2",
        "M43": "Gear range 3",
        "M44": "Gear range 4 (high)",
        "M78": "External program call (M78 P...)",
        "M79": "External program end",
        "M98": "Subprogram call (M98 P...)",
        "M99": "Subprogram end",
    }
    
    AXES = {
        "X": "X axis (diameter)",
        "Z": "Z axis (length)",
        "U": "U axis (incremental X)",
        "W": "W axis (incremental Z)",
        "I": "I axis (arc center X)",
        "K": "K axis (arc center Z)",
        "R": "R radius",
    }
    
    PARAMS = {
        "F": "Feed rate",
        "S": "Spindle speed",
        "T": "Tool",
        "P": "Parameter P",
        "Q": "Parameter Q",
        "D": "Depth of cut",
        "N": "Sequence number",
    }
    
    def __init__(self, tool_model=None, macro_model=None, parent=None):
        self.custom_model = GCodeCompleterModel()
        super().__init__(self.custom_model, parent)
        
        self.tool_model = tool_model
        self.macro_model = macro_model
        
        # Completer settings
        self.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.setModelSorting(QCompleter.ModelSorting.CaseInsensitivelySortedModel)
        self.setMaxVisibleItems(10)
        
        # Style for popup
        self.popup().setStyleSheet("""
            QListView {
                background: #1A1A1A;
                color: #FFC800;
                border: 2px solid #FFC800;
                font-family: Consolas, Menlo, 'SF Mono', 'DejaVu Sans Mono', 'Fira Code', monospace;
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
        """Generate context-based suggestions"""
        suggestions = []
        descriptions = []
        
        # Last word before cursor
        words = text_before_cursor.split()
        if not words:
            # At the beginning: Show G-codes and N
            suggestions = ["N", "G00", "G01", "G71", "T", "M03", "M98"]
            descriptions = [
                "Block number",
                self.GCODES["G00"],
                self.GCODES["G01"],
                self.GCODES["G71"],
                "Tool",
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
                    tool_num = row[0]  # T-Number
                    tool_name = row[2]  # Name
                    tool_code = f"T{tool_num:02d}01"
                    if tool_code.startswith(last_word):
                        suggestions.append(tool_code)
                        descriptions.append(f"Tool {tool_num}: {tool_name}")
        
        # 4. Parameter Completion (X, Z, F, S, ...)
        elif len(last_word) == 1 and last_word in "XZUWIKRFSTPQDN":
            param = last_word
            # Show parameter hint
            if param in self.AXES:
                suggestions.append(param + "0.0")
                descriptions.append(self.AXES[param])
            elif param in self.PARAMS:
                suggestions.append(param + "100")
                descriptions.append(self.PARAMS[param])
        
        # 5. Macro Completion (M98 P1234)
        elif "M98" in text_before_cursor and last_word.startswith("P"):
            if self.macro_model:
                # Get all macro numbers
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
                        descriptions.append(f"Macro {macro_nr}: {macro_name}")
        
        # 6. Fallback: Show all G-codes if nothing found
        if not suggestions:
            # Show top most frequent G-codes and M-codes
            common = ["G00", "G01", "G71", "G54", "G40", "G50", "G96", "M03", "M05", "M19", "M30"]
            for code in common:
                if code.startswith("G"):
                    suggestions.append(code)
                    descriptions.append(self.GCODES.get(code, ""))
                elif code.startswith("M"):
                    suggestions.append(code)
                    descriptions.append(self.MCODES.get(code, ""))
        
        return suggestions, descriptions
    
    def update_for_context(self, text_before_cursor):
        """Update completer based on context"""
        suggestions, descriptions = self.get_context_suggestions(text_before_cursor)
        
        # Extract prefix (last word)
        words = text_before_cursor.split()
        if words:
            prefix = words[-1].upper()  # Uppercase for case-insensitive
        else:
            prefix = ""
        
        # If only a single letter (G, M, T), then clear prefix
        # so that all suggestions are shown
        if len(prefix) == 1 and prefix in "GMT":
            self.setCompletionPrefix("")
        else:
            self.setCompletionPrefix(prefix)
        
        # Update model
        self.custom_model.update_suggestions(suggestions, descriptions)
        
        return len(suggestions) > 0


class CompleterEventFilter(QObject):
    """Event filter for autocomplete"""
    
    def __init__(self, editor, completer):
        super().__init__(editor)
        self.editor = editor
        self.completer = completer
        
    def eventFilter(self, obj, event):
        # Handle events from editor AND popup
        if event.type() != QEvent.Type.KeyPress:
            return False
        
        try:
            key_event = event
            
            # If popup is visible
            if self.completer.popup().isVisible():
                if key_event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Tab):
                    # Manually get the current suggestion and insert
                    index = self.completer.popup().currentIndex()
                    if index.isValid():
                        completion = self.completer.completionModel().data(index, Qt.ItemDataRole.DisplayRole)
                        if completion:  # Only if not empty
                            insert_completion(self.editor, self.completer, completion)
                        self.completer.popup().hide()
                        return True  # Event consumed
                    # No item selected: close popup and pass event
                    self.completer.popup().hide()
                    return False
                elif key_event.key() == Qt.Key.Key_Escape:
                    self.completer.popup().hide()
                    return True  # Event consumed
                elif key_event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                    # Pass navigation to popup
                    return False
            
            # Only on editor: Ctrl+Space triggers autocomplete
            if obj == self.editor:
                if key_event.key() == Qt.Key.Key_Space and key_event.modifiers() == Qt.KeyboardModifier.ControlModifier:
                    self._trigger_completer()
                    return True  # Event consumed
            
            # Pass all other events
            return False
            
        except Exception as e:
            print(f"[Autocomplete] Error in eventFilter: {e}")
            import traceback
            traceback.print_exc()
            return False  # On error: pass event
    
    def _trigger_completer(self):
        """Show autocomplete"""
        try:
            cursor = self.editor.textCursor()
            pos_in_block = cursor.positionInBlock()
            cursor.select(QTextCursor.SelectionType.LineUnderCursor)
            text_before = cursor.selectedText()[:pos_in_block]
            
            if self.completer.update_for_context(text_before):
                # Simple call without custom rect
                self.completer.complete()
        except Exception as e:
            print(f"[Autocomplete] Error: {e}")


def install_completer(editor, tool_model=None, macro_model=None):
    """Install autocomplete on a GCodeEditor"""
    completer = GCodeCompleter(tool_model, macro_model, editor)
    
    # IMPORTANT: Connect completer to widget
    completer.setWidget(editor)
    
    # Install event filter (on editor AND popup!)
    event_filter = CompleterEventFilter(editor, completer)
    editor.installEventFilter(event_filter)
    completer.popup().installEventFilter(event_filter)  # IMPORTANT!
    
    # Mouse click is unstable - keyboard control only
    # Enter/Tab is handled by the event filter
    
    print(f"[Autocomplete] Ready ({len(tool_model.rows) if tool_model else 0} tools loaded)")
    print("[Autocomplete] Ctrl+Space to open | Arrow keys to navigate | Enter to accept")
    
    return completer


def insert_completion(editor, completer, completion):
    """Insert selected suggestion"""
    try:
        cursor = editor.textCursor()
        
        # Get current text in the line
        cursor.select(QTextCursor.SelectionType.LineUnderCursor)
        line_text = cursor.selectedText()
        
        # Find how much we need to delete (last word)
        words = line_text.split()
        if words:
            prefix = words[-1].upper()

            # Delete prefix backwards from current cursor position
            cursor = editor.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.KeepAnchor, len(prefix))
            cursor.removeSelectedText()
        
        # Insert completion
        cursor = editor.textCursor()
        cursor.insertText(completion)
        editor.setTextCursor(cursor)
        
    except Exception as e:
        print(f"[Autocomplete] Error: {e}")

