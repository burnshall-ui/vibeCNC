# 🎯 Autocomplete & IntelliSense

**Context-aware G-code completion for Vibe CNC**

---

## 📋 Features

### ✅ What is supported?

#### 1. **G-code completion**
All the important Fanuc G-codes, with descriptions:
```
G00 → Rapid traverse (G00 X... Z...)
G01 → Linear interpolation (G01 X... Z... F...)
G71 → Roughing cycle OD (G71 U... R... P... Q... D... F...)
G76 → Threading cycle (G76 P... Q... R... X... Z... F...)
```

#### 2. **M-code completion**
```
M03 → Spindle CW (M03 S...)
M08 → Coolant ON
M98 → Subprogram call (M98 P...)
M30 → Program end with reset
```

#### 3. **Tool completion**
Automatic completion from your tool library:
```
T → T0101 (Tool 1: CNMG1204P-SM)
    T0201 (Tool 2: DCMT11T3)
    T0301 (Tool 3: Grooving insert)
```

#### 4. **Axes & parameters**
```
X → X0.0 (X axis, diameter)
Z → Z0.0 (Z axis, length)
F → F100 (Feed rate)
S → S100 (Spindle speed)
```

#### 5. **Macro completion**
After `M98`, all macros from your library are suggested:
```
M98 P → P9001 (Macro 9001: Peck Drilling Cycle)
        P1000 (Macro 1000: Grooving Program)
```

---

## 🎮 Usage

### Automatic trigger
Autocomplete is activated **automatically** while you type when you enter:
- `G` (shows G-codes)
- `M` (shows M-codes)
- `T` (shows tools from the library)
- an axis letter (`X`, `Z`, `F`, `S`, etc.)

### Manual trigger
**Ctrl+Space** - shows suggestions at the current cursor position

### Navigation
- **↑/↓** - move through the suggestions
- **Enter/Tab** - accept a suggestion
- **Esc** - close the popup

> **Note:** Mouse clicks in the popup are currently not supported. Please use keyboard navigation.

---

## 💡 Examples

### Example 1: Entering a G-code
```
You type: G7
Suggestions:
  → G70 (Finish cycle)
  → G71 (Roughing cycle OD) ← select
  → G72 (Roughing cycle facing)
  → G73 (Pattern repeat cycle)

Result: G71
```

### Example 2: Choosing a tool
```
You type: T0
Suggestions:
  → T0101 (Tool 1: CNMG1204P-SM) ← select
  → T0201 (Tool 2: DCMT11T3)
  → T0301 (Tool 3: Grooving insert)

Result: T0101
```

### Example 3: Calling a macro
```
You type: M98 P9
Suggestions:
  → P9001 (Macro 9001: Peck Drilling Cycle) ← select
  → P9002 (Macro 9002: Countersink 90°)
  → P9010 (Macro 9010: Probe Z)

Result: M98 P9001
```

### Example 4: Parameter hint
```
You type: G01 X50 Z
Suggestions:
  → Z0.0 (Z axis, length) ← select

Result: G01 X50 Z0.0
```

---

## 🎨 UI design

The autocomplete popup uses **Fanuc yellow** on a black background:
- **Normal entry:** yellow text on black
- **Selected entry:** black text on a yellow background (bold)
- **Hover:** slightly transparent yellow
- **Tooltip:** shows a detailed description

---

## 🔧 Technical details

### Architecture
```
GCodeCompleter (QCompleter)
  ├─ GCodeCompleterModel (QAbstractListModel)
  │   ├─ suggestions: List[str]
  │   └─ descriptions: List[str]
  │
  ├─ Context Detection
  │   ├─ Analyze the text before the cursor
  │   ├─ Extract the last word
  │   └─ Generate matching suggestions
  │
  └─ Integration with the editor
      ├─ keyPressEvent override
      ├─ Ctrl+Space trigger
      └─ Auto-trigger on G/M/T/axes
```

### Data sources
1. **Static G/M-code lists** (`GCODES`, `MCODES` in `gcode_completer.py`)
2. **Tool library** (`ToolModel` from `tools.json` / `tools.db`)
3. **Macro library** (`MacroModel` from `macros.db`)

### Performance
- **Lazy loading:** suggestions are only generated on demand
- **Caching:** tool/macro lists are kept in the model
- **Fast response:** < 10 ms for normal suggestions

---

## 🚀 Extensions (roadmap)

### Planned features
- [ ] **Snippet expansion:** `g71` → a complete G71 cycle with placeholders
- [ ] **Parameter hints while typing:** show `U` and `R` after `G71 `
- [ ] **Syntax correction:** `g 01` → `G01`
- [ ] **Frequency-based sorting:** frequently used codes first
- [ ] **Custom user snippets:** define your own abbreviations

---

## 📝 Configuration

No configuration needed at the moment - it works out of the box!

Planned for the future in `config.yaml`:
```yaml
autocomplete:
  enabled: true
  auto_trigger: true           # Automatic on G/M/T
  manual_trigger_key: "Ctrl+Space"
  max_suggestions: 10
  show_descriptions: true
```

---

## 🐛 Troubleshooting

### Problem: Autocomplete does not appear
**Solution:**
1. Make sure you type a **G-code-relevant letter** (`G`, `M`, `T`)
2. Try pressing **Ctrl+Space** manually
3. Check that the editor has focus

### Problem: Tool/macro suggestions are missing
**Solution:**
1. Check that `tools.json` / `tools.db` was loaded correctly
2. Check that `macros.db` exists and contains data
3. Open the tool/macro library in the UI (they should be visible)

### Problem: The popup disappears immediately
**Solution:**
- That is normal when no matching suggestions are found
- Try **Ctrl+Space** to force the trigger

---

## 👨‍💻 Development

### Extending autocomplete

#### Adding new G-codes
Edit `vibe_cnc/gcode_completer.py`:
```python
GCODES = {
    "G00": "Rapid traverse (G00 X... Z...)",
    "G01": "Linear interpolation (G01 X... Z... F...)",
    # Add new G-codes here:
    "G84": "Tapping cycle (G84 X... Z... R... F...)",
}
```

#### Adjusting the context logic
Edit `get_context_suggestions()` in `gcode_completer.py`:
```python
def get_context_suggestions(self, text_before_cursor):
    # Add your own logic
    if "G71" in text_before_cursor:
        # After G71 → show U, R, P, Q
        suggestions = ["U", "R", "P", "Q", "D", "F"]
    ...
```

---

## 📖 See also
- [Editor Shortcuts](../README.md#keyboard-shortcuts)
- [Tool Library](../README.md#tool-library)
- [Macro Library](../README.md#macro-library)

---

<div align="center">
  <p>🎯 <b>Autocomplete makes CNC programming faster & less error-prone!</b></p>
  <p>Built with ❤️ for Vibe CNC</p>
</div>
