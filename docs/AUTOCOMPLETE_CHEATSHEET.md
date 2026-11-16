# 🚀 Autocomplete Cheatsheet

**Quick reference for G-code IntelliSense in Vibe CNC**

---

## ⌨️ Shortcuts

| Key | Action |
|-----|--------|
| **Ctrl+Space** | Trigger autocomplete manually |
| **↑/↓** | Move through the suggestions |
| **Enter/Tab** | Accept a suggestion |
| **Esc** | Close the popup |

---

## 🎯 Auto-trigger

Autocomplete is activated **automatically** when you type:

| Character | Shows... |
|-----------|----------|
| **G** | G-codes (G00, G01, G71, G76, ...) |
| **M** | M-codes (M03, M08, M30, M98, ...) |
| **T** | Tools from your library (T0101, T0201, ...) |
| **X, Z, U, W** | Axis parameters (X0.0, Z0.0, ...) |
| **F, S, P, Q** | Feed, speed, parameters |

---

## 📖 Most important G-codes

| Code | Meaning | Syntax |
|------|---------|--------|
| **G00** | Rapid traverse | `G00 X... Z...` |
| **G01** | Linear | `G01 X... Z... F...` |
| **G71** | Roughing OD | `G71 U... R... P... Q... D... F...` |
| **G76** | Threading | `G76 P... Q... R... X... Z... F...` |
| **G40** | Compensation OFF | `G40` |
| **G96** | Constant surface speed ON | `G96 S...` |
| **G97** | Constant surface speed OFF | `G97 S...` |

---

## 🔧 Most important M-codes

| Code | Meaning |
|------|---------|
| **M03** | Spindle CW |
| **M05** | Spindle STOP |
| **M08** | Coolant ON |
| **M09** | Coolant OFF |
| **M98** | Subprogram call |
| **M30** | Program end |

---

## 🛠️ Workflow examples

### 1. **Starting a new program**
```
Type: N10 G   → select G21
      N20 G   → select G18
      N30 G   → select G40
      N40 T0  → select T0101 (your tool)
      N50 M   → select M03
      N60 S   → S1200
```

### 2. **G71 roughing cycle**
```
Type: N100 G7   → select G71
             U   → U1.0
             R   → R0.5
             P   → P110
             Q   → Q120
             D   → D500
             F   → F0.25
```

### 3. **Calling a macro**
```
Type: N200 M98 P9   → select P9001 (Peck Drilling Cycle)
```

---

## 💡 Pro tips

### ✅ Best practices
1. **Ctrl+Space is your friend** - use it when you are stuck
2. **Type only the first letters** - autocomplete fills in the rest
3. **Read the tooltips** - every suggestion has a description (hover)
4. **Maintain your tool library** - your tools show up automatically

### ⚠️ Avoid
- Typing too fast (give the popup time to appear)
- Spelling out complete codes (use autocomplete!)
- Pressing Esc by accident (the popup disappears)

---

## 🔥 Power-user tricks

### Trick 1: Quick tool-change template
```
Type: T0   → T0101
      G    → G00
      X    → X200.
      Z    → Z200.
      M    → M03
      S    → S1200
      F    → F0.25
```
**Result:** a complete tool change in seconds!

### Trick 2: Header boilerplate
```
N10 G21 G18 G40 G80 G97 ;
    └─ Enter every "G" via autocomplete
```

### Trick 3: Macro chain
```
M98 P   → P9001 (Drilling)
M98 P   → P9002 (Countersinking)
M98 P   → P1000 (Grooving Program)
```

---

## 📊 Statistics (what you save)

**Without autocomplete:**
- Entering a G71 cycle: **~45 seconds**
- 10 tool changes: **~3 minutes**
- Correcting typos: **~5 minutes/day**

**With autocomplete:**
- Entering a G71 cycle: **~10 seconds** ⚡
- 10 tool changes: **~30 seconds** ⚡
- Typos: **almost none!** ✅

**Time saved: ~80%** 🚀

---

<div align="center">
  <h2>🎯 Master G-code with autocomplete!</h2>
  <p><i>"Program like a pro - fast, error-free, efficient."</i></p>
</div>
