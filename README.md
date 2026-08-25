# Vibe CNC

```
██╗   ██╗██╗██████╗ ███████╗     ██████╗███╗   ██╗ ██████╗
██║   ██║██║██╔══██╗██╔════╝    ██╔════╝████╗  ██║██╔════╝
██║   ██║██║██████╔╝█████╗      ██║     ██╔██╗ ██║██║
╚██╗ ██╔╝██║██╔══██╗██╔══╝      ██║     ██║╚██╗██║██║
 ╚████╔╝ ██║██████╔╝███████╗    ╚██████╗██║ ╚████║╚██████╗
  ╚═══╝  ╚═╝╚═════╝ ╚══════╝     ╚═════╝╚═╝  ╚═══╝ ╚═════╝
```

**A modern, Fanuc-style CNC simulator with AI-powered G-Code assistance**

> ### ⚠️ Never run generated G-code on a real machine unverified
>
> Vibe CNC is a **simulator and an editing aid**. Its collision detection, lint
> rules and AI review are heuristics — they find common mistakes, they do not
> prove a program safe. An LLM will confidently produce plausible G-code that is
> wrong about your tooling, your offsets, your chuck and your stock.
>
> Before any program touches a machine: read every line yourself, verify the
> tool table and work offsets on the control, dry-run above the part with
> generous Z clearance, and keep a hand on the feed hold. Treat the output the
> way you would treat code from a stranger — because that is what it is.
>
> The authors accept no liability for crashes, scrapped parts or injury.

[![Python](https://img.shields.io/badge/Python-3.13-blue.svg)](https://www.python.org/)
[![PyQt6](https://img.shields.io/badge/PyQt-6-green.svg)](https://www.riverbankcomputing.com/software/pyqt/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

---

##  Features

###  Live Simulation
- **Progressive Path Drawing** - Toolpaths appear line-by-line as the program executes, just like a real CNC
- **Real-Time Position Display** - Live X, Z, Tool, Spindle Speed, and Feedrate overlay
- **2D Visualization** - Interactive lathe simulation with zoom, pan, and click-to-jump
- **Collision Detection** - Automatic chuck limit warnings with visual highlights

###  AI-Powered Assistance
- **Dual AI Support** - Works with both Claude (cloud) and Ollama (local)
- **Smart Code Review** - Analyzes G-Code for policy violations and safety issues
- **Code Generation** - Generate G-Code snippets from natural language
- **Context-Aware** - Understands your tooling, material, and machine configuration

###  Professional Tools
- **Syntax Highlighting** - Fanuc-style color coding with line numbers
- **Autocomplete & IntelliSense** - Context-aware G/M-code completion with Ctrl+Space
- **Tool Library** - Quick access with double-click insertion, right-click to edit (in-app editor)
- **Macro Library** - Reusable G-Code macros (G65, M98) with in-app editor
- **Find & Replace** - Full-featured search with Ctrl+F/Ctrl+H, regex support
- **Recent Files** - Quick access to last 5 programs
- **Settings Dialog** - In-app configuration editor (machine, AI, paths, UI)
- **Policy Engine** - Enforce safety rules and coding standards
- **G41/G42 Compensation** - Tool nose radius compensation with arc handling and corner intersections
- **CAMotics Integration** - 3D simulation support (optional)

###  Fanuc-Style UI
- **Authentic Look** - CRT green on black, yellow highlights
- **Control Panel** - CYCLE START, FEED HOLD, OPT STOP, SINGLE BLOCK
- **Keyboard Shortcuts** - F5 for quick sim, Spacebar for cycle start
- **Adaptive Scaling** - Responsive font sizing for any screen

---

##  Screenshots

*Coming soon - Add your screenshots to `/docs/screenshots/`*

---

##  Installation

### Prerequisites
- **Python 3.13** (or 3.10+)
- **Ollama** (for local AI) - [Download](https://ollama.com)
- **CAMotics** (optional, for 3D simulation) - [Download](https://camotics.org)

### Quick Start

```bash
# Clone the repository
git clone https://github.com/burnshall-ui/vibeCNC.git
cd vibeCNC

# Install dependencies
pip install -r requirements.txt

# Run the application
python vibe_cnc.py
```

### For Local AI (Ollama)

```bash
# Install Ollama from https://ollama.com

# Pull a model (recommended: granite3.3:8b for speed)
ollama pull granite3.3:8b

# Start Ollama (if not running as service)
ollama serve
```

### For Cloud AI (Claude)

```bash
# Set your API key
setx ANTHROPIC_API_KEY "sk-ant-..."

# Update config.yaml
# Change ai.mode to "claude"
```

---

##  Usage

### Basic Workflow

1. **Load a Program** - `OPEN` button or `Ctrl+O`
2. **Edit G-Code** - Syntax highlighting and line numbers
3. **Run Simulation** - Press `CYCLE START` (green button) or `Spacebar`
4. **Watch Live** - Toolpaths draw progressively with real-time position info
5. **AI Review** - Click `KI: ANALYZE` for safety checks
6. **Generate Code** - Use `KI: GEN-CODE` or type in chat

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Space` | CYCLE START (run/resume simulation) |
| `F` | FEED HOLD (pause simulation) |
| `Esc` | Stop simulation |
| `F5` | Quick CAMotics simulation |
| `Ctrl+Space` | Trigger autocomplete (IntelliSense) |
| `Ctrl+L` | Lint only (no AI) |
| `Ctrl+F` | Find dialog |
| `Ctrl+H` | Find & Replace dialog |
| `Ctrl+S` | Save file |
| `Ctrl+Shift+S` | Save and copy to VM |
| `Ctrl+O` | Open file |
| `Ctrl++` | Zoom in |
| `Ctrl+-` | Zoom out |

### Simulation Controls

- **CYCLE START** (Green) - Start or resume program execution
- **FEED HOLD** (Orange) - Pause at current line
- **OPT STOP** (Gray) - Enable/disable M01 stops
- **SINGLE BLOCK** (Gray) - Execute one line at a time

---

##  Configuration

**Quick Config:** Use the in-app **⚙️ SETTINGS** button for GUI-based configuration.

**Manual Config:** Edit `config.yaml` to customize:

### UI Settings
```yaml
ui:
  font_base_pt: 12          # Base font size
  dark_bg: "#1A1A1A"        # Dark background
  fanuc_yellow: "#FFC800"   # Accent color
```

### AI Settings
```yaml
ai:
  mode: "ollama"            # "claude" or "ollama"
  offline: false            # true = no AI calls

  ollama:
    base_url: "http://127.0.0.1:11434/api/chat"
    model: "granite3.3:8b"  # Fast and accurate

  anthropic:
    model: "claude-sonnet-4-20250514"
    api_key_env: "ANTHROPIC_API_KEY"
    max_output_tokens: 800
```

### Machine Settings
```yaml
machine:
  chuck_z_limit: -5.0       # Collision warning threshold

policies:
  protected_m_codes: [62, 63, 64, 65]  # Don't modify these
  require_header_codes: ["G18", "G40", "G80", "G97"]
  require_units: "G21"      # Metric only
  require_origin: "G54"     # Work coordinate system
```

### Tool Library

**Quick Edit:** Right-click any tool in the app → Tool Editor, or use **+ NEUES TOOL** button.

**Manual Edit:** Edit `tools/tools.json`:
```json
{
  "units": "metric",
  "tool_table": [
    {
      "t": 1,
      "name": "CNMG1204P-S Außen",
      "type": "turn_rough",
      "insert_radius_mm": 0.8,
      "nose_direction": 3,
      "holder": "PCLNR2525",
      "limits": {
        "vc_max": 180,
        "ap_max": 2.0,
        "f_max": 0.35
      }
    }
  ]
}
```

**Nose radius compensation** (G41/G42) reads two of those fields.
`insert_radius_mm` is the corner radius of the insert; `nose_direction` is the
Fanuc tip number 0-9, saying where the programmed point — the imaginary tool
nose — sits relative to the centre of that radius:

| Tip | Nose sits | Typical tool |
|-----|-----------|--------------|
| 1 / 2 / 3 / 4 | +X+Z / +X-Z / -X-Z / -X+Z | 3 = OD turning, 2 = boring |
| 5 / 6 / 7 / 8 | +X / -Z / -X / +Z | facing and grooving tools |
| 0 / 9 | on the centre | probe, or a radius you do not want compensated |

Leave it out and the simulation assumes 0 and says so in the lint pane, rather
than guessing a tip for you.

---

##  Architecture

```
vibe_cnc/
├── vibe_cnc.py                 # Main application (UI + wiring)
├── config.yaml                 # Configuration file
├── requirements.txt            # Python dependencies
│
├── vibe_cnc/
│   ├── claude_client.py        # AI client (Claude + Ollama)
│   ├── gcode_highlighter.py    # Syntax highlighting + editor
│   ├── gcode_plotter.py        # 2D visualization + live drawing
│   ├── lint_engine.py          # Policy enforcement
│   ├── camotics_bridge.py      # 3D simulation integration
│   ├── settings_manager.py     # Config loader
│   ├── tool_model.py           # Tool library
│   └── macro_model.py          # Macro library
│
├── tools/
│   ├── tools.json              # Tool database
│   └── macros.db               # SQLite macro storage
│
└── programs/                   # Sample G-Code programs
```

---

##  Roadmap

###  Completed
- [x] Live simulation with progressive drawing
- [x] Real-time position tracking
- [x] Dual AI support (Claude + Ollama)
- [x] Interactive 2D plotter
- [x] Tool and macro libraries with in-app editors
- [x] Fanuc-style controls
- [x] Find & Replace in editor
- [x] G41/G42 tool nose radius compensation
- [x] Settings dialog for configuration
- [x] Recent files list
- [x] Autocomplete & IntelliSense (context-aware G-Code completion)

###  In Progress
- [ ] Program statistics (cycle time, tool changes)
- [ ] Snippet library / favorites

###  Planned
- [ ] Network simulation (RPC to LinuxCNC VM)
- [ ] DXF import for geometry
- [ ] Conversational programming wizard
- [ ] Multi-language support (EN/DE)

---

##  Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'feat: Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

##  License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

---

##  Acknowledgments

- **Fanuc** - For inspiring the UI design
- **CAMotics** - 3D simulation integration
- **Ollama** - Local LLM inference
- **Anthropic** - Claude AI API
- **PyQt6** - UI framework
- **Matplotlib** - 2D plotting

---

## 📧 Contact

**Project Link:** [https://github.com/burnshall-ui/vibeCNC](https://github.com/burnshall-ui/vibeCNC)

---

<div align="center">
  <p>Built by machinist and CNC enthusiast</p>
  <p> Enhanced with AI by <a href="https://claude.ai">Claude Code</a></p>
</div>
