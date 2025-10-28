# Vibe CNC — UPGRADE v2.0 🚀

## 🎯 Neue Features (Option C: Hybrid-Workflow)

### **1. Quick Sim (F5) — Hot-Reload**
```
[F5] → CAMotics startet/reloaded mit aktuellem Code
```
- ✅ Feste Temp-Datei (kein Timestamp-Chaos mehr)
- ✅ Auto-Lint vor Sim mit Warnung bei >2 Fehlern
- ✅ Hot-Reload: CAMotics wird terminiert und neu gestartet
- ✅ Status-Bar zeigt Fortschritt

**Typischer Workflow:**
```
Code schreiben → [F5] → CAMotics zeigt → Fehler sehen → Fix → [F5] → ...
```

---

### **2. Save + VM Copy (Ctrl+Shift+S)**
```
[Ctrl+Shift+S] → Speichert Code direkt ins LinuxCNC-VM-Share
```
- ✅ Kein manuelles Kopieren mehr
- ✅ Lint-Check vorher (mit Warnung)
- ✅ Dateiname aus aktuellem File oder "live_test.nc"
- ✅ Perfekt für VM-basierte Tests

**Workflow:**
```
Quick-Check (F5 in CAMotics) → Fix → Fein-Test (Ctrl+Shift+S in VM)
```

---

### **3. Error-Marker im Editor**
- ✅ **Rote Zeilen** bei Lint-Fehlern
- ✅ **Rote Zeilennummern** links
- ✅ Automatisch beim Lint/Quick-Sim
- ✅ Clear bei erfolgreicher Prüfung

**Beispiel:**
```gcode
N10 G18 G40 G80 G97 G54 G21          ← OK (grün)
N20 G96 S180 M3                      ← FEHLER (rot): G50 fehlt!
N30 G00 X50. Z2.
```

---

### **4. Status-Bar statt Chat-Spam**
- ✅ Sim-Status: `⚙️ Starte CAMotics...` → `✅ CAMotics gestartet`
- ✅ Lint-Status: `⚠️ 3 Fehler gefunden`
- ✅ VM-Copy: `✅ VM-Copy: live_test.nc`
- ✅ Chat bleibt clean, nur wichtige Infos

---

### **5. Lint-Only (Ctrl+L)**
```
[Ctrl+L] → Nur Linting, ohne KI
```
- ✅ Schneller Check ohne API-Call
- ✅ Error-Marker werden gesetzt
- ✅ Ergebnis im Chat

---

### **6. Windows-Snapping Support**
- ✅ High-DPI Scaling für Windows 11
- ✅ Aero Snap funktioniert (Win+Links/Rechts)
- ✅ Dynamische Skalierung bei Resize
- ✅ Window-State wird gespeichert

---

## ⌨️ Neue Keyboard-Shortcuts

| Shortcut | Funktion | Beschreibung |
|----------|----------|--------------|
| **F5** | Quick Sim | CAMotics Hot-Reload (schnellster Weg) |
| **Ctrl+Shift+S** | Save + VM | Speichert und kopiert zu LinuxCNC-VM |
| **Ctrl+L** | Lint Only | Nur Linting, keine KI |
| **Ctrl+S** | Save | Speichern (alt) |
| **Ctrl+O** | Open | Öffnen (alt) |
| **Ctrl++** | Zoom In | Schrift vergrößern |
| **Ctrl+-** | Zoom Out | Schrift verkleinern |

---

## 📦 Geänderte Dateien

### **1. vibe_cnc.py (Hauptprogramm)**
- ✅ Quick Sim (F5) hinzugefügt
- ✅ Save + VM (Ctrl+Shift+S) hinzugefügt
- ✅ Lint Only (Ctrl+L) hinzugefügt
- ✅ Status-Bar statt Chat-Spam
- ✅ Auto-Lint vor Sim mit Warnung
- ✅ Error-Marker Integration
- ✅ High-DPI Scaling verbessert

### **2. camotics_bridge.py**
- ✅ `quick_sim()` Methode: Hot-Reload mit fester Temp-Datei
- ✅ `save_and_copy_to_vm()`: Direkt-Copy ohne Umweg
- ✅ Process-Management: CAMotics wird terminiert vor Reload

### **3. gcode_highlighter.py (Editor)**
- ✅ `set_error_lines()`: Markiert Fehler-Zeilen rot
- ✅ `clear_error_lines()`: Entfernt Marker
- ✅ Rote Zeilennummern bei Fehlern
- ✅ Error-Highlighting im Editor

### **4. config.yaml**
- ✅ Claude Modell: `claude-sonnet-4-20250514` (Sonnet 4!)

---

## 🚀 Workflow-Beispiele

### **Workflow 1: Solo CAMotics (kein VM)**
```
1. Code schreiben
2. [F5] → Quick-Check in CAMotics
3. Fehler sehen (rot markiert)
4. Fix
5. [F5] → Erneut prüfen
6. [Ctrl+S] → Speichern
```

### **Workflow 2: Hybrid (CAMotics + VM)**
```
1. Code schreiben
2. [F5] → Quick-Check in CAMotics (grobe Fehler)
3. Fix
4. [Ctrl+Shift+S] → Copy to VM (Fein-Test auf echter Hardware/LinuxCNC)
5. Auf VM testen
6. Optional zurück zu Schritt 1
```

### **Workflow 3: Nur VM (ohne CAMotics)**
```
1. Code schreiben
2. [Ctrl+L] → Lint-Check (lokal)
3. Fix
4. [Ctrl+Shift+S] → Copy to VM
5. Testen
```

---

## 📝 Installation

**Alle Dateien (neu) in Vibe CNC:**
```
vibe-cnc/
├─ vibe_cnc.py
├─ config.yaml
└─ vibe_cnc/
   ├─ camotics_bridge.py
   ├─ gcode_highlighter.py
   ├─ lint_engine.py
   ├─ tool_model.py
   └─ settings_manager.py
```

**Weitere Module:**
- `lint_engine.py`, `tool_model.py`, `settings_manager.py`, `claude_client.py` (unter `vibe_cnc/`)

---

## ⚙️ Config-Anpassungen

**Keine zusätzlichen Änderungen nötig!** Die neue `config.yaml` ist kompatibel.

Optional: Wenn du **nur VM** nutzt (kein CAMotics):
```yaml
paths:
  camotics_exe: ''  # Leer lassen
  sim_share: '\\linuxcnc-vm\sim\incoming'  # Dein VM-Share
```

---

## 🎖️ Verbessert gegenüber Original

| Feature | Vorher | Jetzt |
|---------|--------|-------|
| **Sim-Workflow** | Button + Dialog | F5 (instant) |
| **Temp-Dateien** | Timestamp-Chaos | Feste Datei |
| **Fehler-Feedback** | Nur Chat-Text | Rote Marker im Editor |
| **VM-Copy** | Manuell | Ctrl+Shift+S (auto) |
| **Status** | Chat-Spam | Clean Status-Bar |
| **Lint** | Nur mit KI | Ctrl+L (solo) |
| **Window-Scaling** | Basic | High-DPI + Snapping |

---

## 🐛 Bekannte Einschränkungen

1. **CAMotics-Reload:** Terminiert Prozess → kurzes Flackern (CAMotics hat kein natives --reload)
2. **VM-Share:** Muss erreichbar sein (SMB/NFS), sonst Fehler
3. **Lint-Heuristik:** Noch keine State-Machine (modale G-Codes werden nicht getrackt)

---

## 💡 Nächste Schritte (optional)

- [ ] Parser mit State-Machine (modale G/F/S tracken)
- [ ] Diff-View für KI-Fixes
- [ ] Material-DB + Schnittdaten-Check
- [ ] Snippet-System (G71/G72/G76 Templates)

---

## ❓ Support

**Probleme?**
1. Check `config.yaml` → Pfade korrekt?
2. CAMotics installiert? → `paths.camotics_exe`
3. VM-Share erreichbar? → `paths.sim_share`
4. API-Key gesetzt? → `setx ANTHROPIC_API_KEY "sk-ant-..."`

**Feature-Requests?** Lass mich wissen was du brauchst!
