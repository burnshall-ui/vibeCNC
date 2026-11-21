# gcode_completer.py — Context-aware Autocomplete for G-Code
from PyQt6.QtCore import Qt, QAbstractListModel, QModelIndex, QVariant, QObject, QEvent
from PyQt6.QtWidgets import QCompleter
from PyQt6.QtGui import QTextCursor

from vibe_cnc.fonts import mono_css
from vibe_cnc.specs import load_specs


def _dialect_tables():
    """(G-codes, M-codes) with a short text each. An M-code whose meaning the
    machine builder decides says so."""
    dialect = load_specs().dialect
    m_codes = {code: text + (" — machine-specific, see the machine's manual"
                             if code in dialect.machine_specific else "")
               for code, text in dialect.m_codes.items()}
    return dict(dialect.g_codes), m_codes


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
    
    # G- and M-codes from specs/fanuc_0i_tf/dialect.toml, G-code system A. The
    # table that stood here described another system: G90/G91 as absolute and
    # incremental, G94/G95 as feed modes -- on system A G90 and G94 are
    # cycles and G91 does not exist.
    GCODES, MCODES = _dialect_tables()

    AXES = {
        "X": "X axis (diameter)",
        "Z": "Z axis (length)",
        "U": "U axis (incremental X)",
        "W": "W axis (incremental Z)",
        "I": "I axis (arc center X)",
        "K": "K axis (arc center Z)",
        "R": "R radius",
    }
    
    # No D: "D, depth of cut" belonged to the one-block G71 of older controls.
    # The 0i-TF takes G71 in two blocks, the depth as U of the first, and the
    # hint taught a model to write a D the control does not read.
    PARAMS = {
        "F": "Feed rate",
        "S": "Spindle speed",
        "T": "Tool",
        "P": "Parameter P",
        "Q": "Parameter Q",
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
                font-family: __MONO__;
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
        """.replace("__MONO__", mono_css()))
    
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
        elif len(last_word) == 1 and last_word in "XZUWIKRFSTPQN":
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

