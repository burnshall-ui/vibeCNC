# CNC-Kopilot — Zielbild

## Architektur-Übersicht

**CNC-Kopilot** ist ein Fanuc-inspirierter Desktop-Assistent für die CNC-Programmierung mit integrierter KI-Unterstützung.

### Kern-Komponenten

#### 🎯 **CAD/CAM & Pfad-Generierung**
- **FreeCAD 1.0.2** mit Path-Workbench für CAM-Operationen
- Tool-Datenbank (SQLite + JSON) für Werkzeugverwaltung
- G-Code-Generierung mit Fanuc-Kompatibilität

#### 🔧 **Simulation & Backplot**
- **LinuxCNC 2.9.6** als Hauptsimulator für Drehmaschinen
  - Vollständige Unterstützung für G70/G71/G72/G76 Zyklen
  - Lathe-SIM für realistische Materialabtragung
- **CAMotics 1.2.0** als Zusatz-Viewer
  - Optimiert für 3-Achs-Fräsen
  - Eingeschränkte Lathe-Unterstützung

#### 🤖 **KI-Assistenz**
- **Claude Code** als primärer Coach/Reviewer
  - Strikte Policy-Enforcement
  - Regelverstoß-Analyse mit Zeilenangaben
  - Code-Generierung mit Fanuc-Syntax
- **Ollama** als lokaler Fallback
  - Wird mit neuer GPU wieder relevant
  - Offline-Betrieb möglich

### UI-Layout (Fanuc-Style)

```
┌─────────────────┬──────────────────────┬─────────────────┐
│   OFFSET        │    PROGRAM (EDIT)     │   KI-ASSIST     │
│   (WERKZEUGE)   │    O0001_DEMO.nc     │   (CLAUDE)      │
├─────────────────┼──────────────────────┼─────────────────┤
│ T1: D16 H1      │ G18 G40 G80 G97      │ > Schreibe G71  │
│ T2: D12 H2      │ G50 S2000            │   für T2...     │
│ T3: D8  H3      │ G96 S150 M03         │                 │
│                 │ G00 X36. Z2.         │ • Zeile 15:     │
│                 │ G71 U1. R0.5         │   Regelverstoß  │
│                 │ G71 P10 Q20 U0.4     │                 │
│                 │ N10 G00 X30.         │                 │
│                 │ G01 Z-20. F0.25      │                 │
│                 │ N20 G00 X36.         │                 │
└─────────────────┴──────────────────────┴─────────────────┘
│ OPEN │ SAVE │ SEND 2 SIM │ KI: ANALYZE │ KI: GEN-CODE │
└─────────────────────────────────────────────────────────┘
```

### Workflow

1. **Programmierung**: FreeCAD Path-WB → G-Code-Export
2. **Import**: G-Code in CNC-Kopilot laden
3. **Linting**: Automatische Regelprüfung (G18, G40, G80, G97)
4. **KI-Review**: Claude analysiert Code auf Policy-Verstöße
5. **Simulation**: 
   - **F5**: Quick-Sim mit CAMotics (Hot-Reload)
   - **Ctrl+Shift+S**: Speichern + VM-Copy zu LinuxCNC
6. **Optimierung**: KI-generierte Verbesserungsvorschläge

### Technische Spezifikationen

- **Target**: Fanuc 0i-TF Controller
- **Material**: 42CrMo4 (Standard)
- **Einheiten**: mm (G21)
- **Nullpunkt**: G54
- **Geschützte M-Codes**: 62, 63, 64, 65 (Roboter/Luft)

### Deployment

- **OS**: Windows 11 (primär)
- **Python**: 3.11+
- **UI**: PyQt6 mit Fanuc-Farben (Gelb, CRT-Grün, Cyan)
- **AI**: Claude Sonnet 4 (Cloud) oder Ollama (lokal)

---

*Status: Implementiert und produktiv einsatzbereit*
