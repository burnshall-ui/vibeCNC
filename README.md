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
- **Canned Cycles** - G71/G72 roughing, G70 finishing (drawn in cyan), G76 and G92 threading (drawn in yellow) and G75 grooving are expanded into the moves they make and played back one at a time; G73 and G74 are not drawn yet. G75 pecks in X (P, a radius) with a return after each peck (R of its first block), steps along Z (Q) and makes the last step short so the groove ends on the programmed Z; the relief at the groove bottom (R of the second block) is not drawn, because the sources it was built from disagree about its direction, and the width of the grooving tool is not drawn either. G76 follows the control's usual depth schedule (shallower passes, a minimum cut, finishing passes at full depth) and feeds straight in rather than along the flank. A decimal point in P or Q of G70–G76 (`P1.5` instead of `P1500`) is drawn as meant, and the linter reports it, because the control rejects it with an alarm

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

Ollama needs macOS 14 (Sonoma) or newer, Windows or Linux. On an Intel Mac it runs on the CPU only.

```bash
# Install Ollama from https://ollama.com

# Pull a model. The default is qwen3.8:27b: about 18 GB of weights, so a
# 32 GB machine or a GPU with 24 GB. For less memory try a smaller one, for
# example qwen3.5:4b (about 3.5 GB), and check it on your own programs.
ollama pull qwen3.8:27b

# Start Ollama (if not running as service)
ollama serve
```

Ollama can also run on another machine on the network: set `ai.ollama.base_url`
to `http://<host>:11434/api/chat`.

### For Cloud AI (Claude)

```bash
# Set your API key
export ANTHROPIC_API_KEY="sk-ant-..."     # macOS / Linux: put it in your shell profile
setx ANTHROPIC_API_KEY "sk-ant-..."       # Windows

# Update config.yaml
# Change ai.mode to "claude"
```

Then open **Settings → AI** and press **Test connection**: it asks the API whether
the key works and the model exists, without running the model. It does not see the
credit balance: an account without API credits passes the test and is turned away on
the first analysis ("Your credit balance is too low"). API credits are bought in the
Anthropic Console and are separate from a claude.ai subscription. The default model is
`claude-sonnet-5-5`; the Claude SDK (`anthropic`) is installed with `requirements.txt`.

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
    model: "qwen3.8:27b"    # about 18 GB; smaller: qwen3.5:4b
    num_ctx: 8192           # Ollama's default is 4096 below 24 GiB of VRAM
    think: false            # skip the thinking of qwen3.x / gpt-oss; remove for other models

  anthropic:
    model: "claude-sonnet-5-5"
    api_key_env: "ANTHROPIC_API_KEY"
    max_output_tokens: 16000   # thinking counts too; about 21000 is the most without streaming
    effort: "medium"           # low | medium | high | xhigh | max; empty for models without it
```

`claude-sonnet-4-20250514`, the earlier default, was retired on 2026-06-15 and
requests to it fail. `ai.system_prompt` and `ai.generate_prompt` set what a review and
a generation are told to do.

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

**Quick Edit:** Right-click any tool in the app → Tool Editor, or use **+ NEW TOOL** button.

**Manual Edit:** Edit `tools/tools.json`:
```json
{
  "units": "metric",
  "tool_table": [
    {
      "t": 1,
      "name": "CNMG1204P-S External",
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
| 1 / 2 / 3 / 4 | -X+Z / -X-Z / +X-Z / +X+Z | 2 = OD turning, 3 = boring |
| 5 / 6 / 7 / 8 | -X / -Z / +X / +Z | facing and grooving tools |
| 0 / 9 | on the centre | probe, or a radius you do not want compensated |

Fanuc prints two of these figures, one per tool post position, mirrored in X
against each other. The table above is the one that matches this shop's
machine; if your tools are set the other way round, mirror `NOSE_OFFSETS` in
`vibe_cnc/tool_data.py` rather than renumbering the tools.

Leave the field out and the simulation assumes 0 and says so in the lint pane,
rather than guessing a tip for you.

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
