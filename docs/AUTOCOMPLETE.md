# 🎯 Autocomplete & IntelliSense

**Context-aware G-Code Completion für Vibe CNC**

---

## 📋 Features

### ✅ Was wird unterstützt?

#### 1. **G-Code Completion**
Alle wichtigen Fanuc G-Codes mit Beschreibungen:
```
G00 → Eilgang (G00 X... Z...)
G01 → Linearinterpolation (G01 X... Z... F...)
G71 → Schrupp-Zyklus außen (G71 U... R... P... Q... D... F...)
G76 → Gewindezyklus (G76 P... Q... R... X... Z... F...)
```

#### 2. **M-Code Completion**
```
M03 → Spindel rechts (M03 S...)
M08 → Kühlmittel EIN
M98 → Unterprogramm rufen (M98 P...)
M30 → Programmende mit Reset
```

#### 3. **Tool Completion**
Automatische Vervollständigung aus deiner Tool-Library:
```
T → T0101 (Werkzeug 1: CNMG1204P-SM)
    T0201 (Werkzeug 2: DCMT11T3)
    T0301 (Werkzeug 3: Stechplatte)
```

#### 4. **Achsen & Parameter**
```
X → X0.0 (X-Achse Durchmesser)
Z → Z0.0 (Z-Achse Länge)
F → F100 (Vorschub)
S → S100 (Spindeldrehzahl)
```

#### 5. **Makro Completion**
Nach `M98` werden alle Makros aus deiner Library vorgeschlagen:
```
M98 P → P9001 (Makro 9001: Bohrzyklus Peck)
        P1000 (Makro 1000: Nutprogramm)
```

---

## 🎮 Bedienung

### Automatischer Trigger
Das Autocomplete wird **automatisch** beim Tippen aktiviert, wenn du:
- `G` tippst (zeigt G-Codes)
- `M` tippst (zeigt M-Codes)
- `T` tippst (zeigt Tools aus Library)
- Achsen-Buchstaben tippst (`X`, `Z`, `F`, `S`, etc.)

### Manueller Trigger
**Ctrl+Space** - Zeigt Vorschläge an der aktuellen Cursor-Position

### Navigation
- **↑/↓** - Durch Vorschläge navigieren
- **Enter/Tab** - Vorschlag übernehmen
- **Esc** - Popup schließen

> **Hinweis:** Mausklicks im Popup sind derzeit nicht unterstützt. Bitte nutze die Tastatur-Navigation.

---

## 💡 Beispiele

### Beispiel 1: G-Code eingeben
```
Du tippst: G7
Vorschläge:
  → G70 (Fertigbearbeitungszyklus)
  → G71 (Schrupp-Zyklus außen) ← auswählen
  → G72 (Schrupp-Zyklus Plan)
  → G73 (Wiederholzyklus unregelmäßig)

Ergebnis: G71
```

### Beispiel 2: Werkzeug wählen
```
Du tippst: T0
Vorschläge:
  → T0101 (Werkzeug 1: CNMG1204P-SM) ← auswählen
  → T0201 (Werkzeug 2: DCMT11T3)
  → T0301 (Werkzeug 3: Stechplatte)

Ergebnis: T0101
```

### Beispiel 3: Makro rufen
```
Du tippst: M98 P9
Vorschläge:
  → P9001 (Makro 9001: Bohrzyklus Peck) ← auswählen
  → P9002 (Makro 9002: Ansenken 90°)
  → P9010 (Makro 9010: Antasten Z)

Ergebnis: M98 P9001
```

### Beispiel 4: Parameter-Hint
```
Du tippst: G01 X50 Z
Vorschläge:
  → Z0.0 (Z-Achse Länge) ← auswählen

Ergebnis: G01 X50 Z0.0
```

---

## 🎨 UI-Design

Das Autocomplete-Popup verwendet **Fanuc-Yellow** auf schwarzem Hintergrund:
- **Normaler Eintrag:** Gelber Text auf schwarz
- **Ausgewählter Eintrag:** Schwarzer Text auf gelbem Hintergrund (bold)
- **Hover:** Leicht transparentes Gelb
- **Tooltip:** Zeigt ausführliche Beschreibung

---

## 🔧 Technische Details

### Architektur
```
GCodeCompleter (QCompleter)
  ├─ GCodeCompleterModel (QAbstractListModel)
  │   ├─ suggestions: List[str]
  │   └─ descriptions: List[str]
  │
  ├─ Context Detection
  │   ├─ Text vor Cursor analysieren
  │   ├─ Letztes Wort extrahieren
  │   └─ Passende Vorschläge generieren
  │
  └─ Integration mit Editor
      ├─ keyPressEvent override
      ├─ Ctrl+Space Trigger
      └─ Auto-Trigger bei G/M/T/Achsen
```

### Datenquellen
1. **Statische G/M-Code-Listen** (`GCODES`, `MCODES` in `gcode_completer.py`)
2. **Tool Library** (`ToolModel` aus `tools.json` / `tools.db`)
3. **Macro Library** (`MacroModel` aus `macros.db`)

### Performance
- **Lazy Loading:** Vorschläge werden erst bei Bedarf generiert
- **Caching:** Tool/Macro-Listen werden im Model gehalten
- **Fast Response:** < 10ms für normale Vorschläge

---

## 🚀 Erweiterungen (Roadmap)

### Geplante Features
- [ ] **Snippet-Expansion:** `g71` → Kompletter G71-Zyklus mit Platzhaltern
- [ ] **Parameter-Hints während Eingabe:** Zeige `U` und `R` nach `G71 `
- [ ] **Fehlerhafte Syntax-Korrektur:** `g 01` → `G01`
- [ ] **Frequenz-basierte Sortierung:** Häufig genutzte Codes zuerst
- [ ] **Custom User-Snippets:** Eigene Abkürzungen definieren

---

## 📝 Konfiguration

Aktuell keine Konfiguration nötig - funktioniert out-of-the-box!

Zukünftig geplant in `config.yaml`:
```yaml
autocomplete:
  enabled: true
  auto_trigger: true           # Auto bei G/M/T
  manual_trigger_key: "Ctrl+Space"
  max_suggestions: 10
  show_descriptions: true
```

---

## 🐛 Troubleshooting

### Problem: Autocomplete erscheint nicht
**Lösung:**
1. Stelle sicher, dass du einen **G-Code-relevanten Buchstaben** tippst (`G`, `M`, `T`)
2. Versuche **Ctrl+Space** manuell zu drücken
3. Prüfe, ob der Editor Fokus hat

### Problem: Tool/Makro-Vorschläge fehlen
**Lösung:**
1. Prüfe, ob `tools.json` / `tools.db` korrekt geladen wurde
2. Prüfe, ob `macros.db` existiert und Daten enthält
3. Öffne die Tool/Makro-Library in der UI (sollten sichtbar sein)

### Problem: Popup verschwindet sofort
**Lösung:**
- Das ist normal, wenn keine passenden Vorschläge gefunden werden
- Versuche **Ctrl+Space** für Force-Trigger

---

## 👨‍💻 Development

### Autocomplete erweitern

#### Neue G-Codes hinzufügen
Editiere `vibe_cnc/gcode_completer.py`:
```python
GCODES = {
    "G00": "Eilgang (G00 X... Z...)",
    "G01": "Linearinterpolation (G01 X... Z... F...)",
    # Füge hier neue G-Codes hinzu:
    "G84": "Gewindebohrzyklus (G84 X... Z... R... F...)",
}
```

#### Context-Logik anpassen
Editiere `get_context_suggestions()` in `gcode_completer.py`:
```python
def get_context_suggestions(self, text_before_cursor):
    # Füge eigene Logik hinzu
    if "G71" in text_before_cursor:
        # Nach G71 → zeige U, R, P, Q
        suggestions = ["U", "R", "P", "Q", "D", "F"]
    ...
```

---

## 📖 Siehe auch
- [Editor Shortcuts](../README.md#keyboard-shortcuts)
- [Tool Library](../README.md#tool-library)
- [Macro Library](../README.md#macro-library)

---

<div align="center">
  <p>🎯 <b>Autocomplete macht CNC-Programmierung schneller & fehlerfreier!</b></p>
  <p>Built with ❤️ for Vibe CNC</p>
</div>

