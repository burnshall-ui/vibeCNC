# 🚀 Autocomplete Cheatsheet

**Schnellreferenz für G-Code IntelliSense in Vibe CNC**

---

## ⌨️ Shortcuts

| Taste | Aktion |
|-------|--------|
| **Ctrl+Space** | Autocomplete manuell triggern |
| **↑/↓** | Durch Vorschläge navigieren |
| **Enter/Tab** | Vorschlag übernehmen |
| **Esc** | Popup schließen |

---

## 🎯 Auto-Trigger

Das Autocomplete wird **automatisch** aktiviert beim Tippen von:

| Zeichen | Zeigt an... |
|---------|-------------|
| **G** | G-Codes (G00, G01, G71, G76, ...) |
| **M** | M-Codes (M03, M08, M30, M98, ...) |
| **T** | Tools aus deiner Library (T0101, T0201, ...) |
| **X, Z, U, W** | Achsen-Parameter (X0.0, Z0.0, ...) |
| **F, S, P, Q** | Feed, Speed, Parameter |

---

## 📖 Wichtigste G-Codes

| Code | Bedeutung | Syntax |
|------|-----------|--------|
| **G00** | Eilgang | `G00 X... Z...` |
| **G01** | Linear | `G01 X... Z... F...` |
| **G71** | Schrupp außen | `G71 U... R... P... Q... D... F...` |
| **G76** | Gewinde | `G76 P... Q... R... X... Z... F...` |
| **G40** | Korrektur AUS | `G40` |
| **G96** | Const. Vc EIN | `G96 S...` |
| **G97** | Const. Vc AUS | `G97 S...` |

---

## 🔧 Wichtigste M-Codes

| Code | Bedeutung |
|------|-----------|
| **M03** | Spindel rechts (CW) |
| **M05** | Spindel STOP |
| **M08** | Kühlmittel EIN |
| **M09** | Kühlmittel AUS |
| **M98** | Unterprogramm rufen |
| **M30** | Programm-Ende |

---

## 🛠️ Workflow-Beispiele

### 1. **Neues Programm starten**
```
Tippe: N10 G   → wähle G21
       N20 G   → wähle G18
       N30 G   → wähle G40
       N40 T0  → wähle T0101 (dein Tool)
       N50 M   → wähle M03
       N60 S   → S1200
```

### 2. **G71 Schrupp-Zyklus**
```
Tippe: N100 G7   → wähle G71
              U   → U1.0
              R   → R0.5
              P   → P110
              Q   → Q120
              D   → D500
              F   → F0.25
```

### 3. **Makro aufrufen**
```
Tippe: N200 M98 P9   → wähle P9001 (Bohrzyklus Peck)
```

---

## 💡 Pro-Tipps

### ✅ Best Practices
1. **Ctrl+Space ist dein Freund** - Nutze es, wenn du nicht weiterweißt
2. **Tippe nur Anfangsbuchstaben** - Autocomplete ergänzt den Rest
3. **Tooltips lesen** - Jeder Vorschlag hat eine Beschreibung (hover)
4. **Tool-Library pflegen** - Deine Tools erscheinen automatisch

### ⚠️ Vermeide
- Zu schnelles Tippen (gib dem Popup Zeit zu erscheinen)
- Komplette Codes ausschreiben (nutze Autocomplete!)
- Esc drücken aus Versehen (Popup verschwindet)

---

## 🔥 Power-User-Tricks

### Trick 1: Schnelles Werkzeug-Wechsel-Template
```
Tippe: T0   → T0101
       G    → G00
       X    → X200.
       Z    → Z200.
       M    → M03
       S    → S1200
       F    → F0.25
```
**Ergebnis:** Kompletter Tool-Change in Sekunden!

### Trick 2: Header-Boilerplate
```
N10 G21 G18 G40 G80 G97 ;
    └─ Jedes "G" via Autocomplete eingeben
```

### Trick 3: Makro-Chain
```
M98 P   → P9001 (Bohren)
M98 P   → P9002 (Ansenken)
M98 P   → P1000 (Nutprogramm)
```

---

## 📊 Statistik (was du sparst)

**Ohne Autocomplete:**
- G71-Zyklus eingeben: **~45 Sekunden**
- 10 Tool-Changes: **~3 Minuten**
- Tippfehler korrigieren: **~5 Minuten/Tag**

**Mit Autocomplete:**
- G71-Zyklus eingeben: **~10 Sekunden** ⚡
- 10 Tool-Changes: **~30 Sekunden** ⚡
- Tippfehler: **fast keine!** ✅

**Zeitersparnis: ~80%** 🚀

---

<div align="center">
  <h2>🎯 Master G-Code mit Autocomplete!</h2>
  <p><i>"Programmiere wie ein Profi - schnell, fehlerfrei, effizient."</i></p>
</div>

