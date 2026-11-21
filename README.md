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
- **Program End** - Nothing after M30 (or M02, M99) is drawn, the way the control never runs it; the linter says when code follows the end
- **One-Block Cycles** - G90 (turning) and G94 (facing) are drawn as the four moves each block makes: in at rapid, the cut, back out at feed, back to the start at rapid; G92 the same with the thread pass in yellow and a rapid out. A repeat block carries only the word that changes. Which legs are feeds is the usual description of the cycles, not checked against the 0i-TF manual. G32 is drawn as one thread pass per block
- **Canned Cycles** - G71/G72 roughing, G73 pattern repeating, G70 finishing (drawn in cyan), G76 and G92 threading (drawn in yellow), G75 grooving and G74 face grooving or peck drilling are expanded into the moves they make and played back one at a time; every cycle from G70 to G76 is drawn. G73 cuts the contour between P and Q R times, each pass a copy of it shifted along the axes: the first by the relief U/W of its first block (U a radius) plus the allowance, the last by the allowance alone, the ones between evenly spaced, each pass starting from and returning straight to the start point — that spacing is the usual reading of the cycle and is not confirmed by any source this was built from. G75 pecks in X (P, a radius) with a return after each peck (R of its first block), steps along Z (Q) and makes the last step short so the groove ends on the programmed Z; G74 is the same turned through 90 degrees, pecking along Z (Q) and stepping in X (P, a radius), and without X it drills a deep hole on the centre line; the relief at the bottom (R of the second block) is not drawn, because the sources it was built from disagree about its direction, and the width of the grooving tool is not drawn either. G76 follows the control's usual depth schedule (shallower passes, a minimum cut, finishing passes at full depth) and feeds straight in rather than along the flank. A decimal point in P or Q of G70–G76 (`P1.5` instead of `P1500`) is drawn as meant, and the linter reports it, because the control rejects it with an alarm

###  AI-Powered Assistance
- **Three AI Backends** - Claude (cloud), Ollama (local), or any server that speaks the OpenAI API (OpenAI, LM Studio, llama.cpp, vLLM, OpenRouter)
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
- **Python 3.13** (or 3.11+: the spec files in `specs/` are read with `tomllib`)
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

### For an OpenAI-compatible server

`ai.mode: openai` talks to anything that speaks the OpenAI Chat Completions API:
OpenAI itself, or a local server such as LM Studio or llama.cpp's `llama-server`,
vLLM, OpenRouter, or Ollama's `/v1` address. No extra package is needed.

```bash
# Only if the server wants a key (OpenAI, OpenRouter). A local server needs none.
export OPENAI_API_KEY="sk-..."            # macOS / Linux: put it in your shell profile
setx OPENAI_API_KEY "sk-..."              # Windows
```

In **Settings → AI** choose `openai`, enter the server's address (the API root,
as a rule ending in `/v1`, for example `http://localhost:1234/v1` for LM Studio)
and press **Test connection**: with no model entered it lists the models the
server offers. There is no default model, because what a server offers depends on
the server. Like the Claude test it does not run the model, so it cannot see a
credit balance or quota; on OpenAI an empty account shows up on the first analysis
as "no quota or credit left (429)".

**OpenRouter:** address `https://openrouter.ai/api/v1`, key variable
`OPENROUTER_API_KEY` (enter the variable's *name* in Settings → AI, never the key).
Model ids look like `vendor/model`; free ones end in `:free` and have a low daily
limit. Its model list answers without a key, so there a green Test connection says
nothing about the key: the first analysis does. An empty account is reported as
"no quota or credit left (402)". Whatever is analysed is sent to OpenRouter and the
provider behind the model, so think about which programs leave the shop.

---

##  Usage

### Basic Workflow

1. **Load a Program** - `OPEN` button or `Ctrl+O`
2. **Edit G-Code** - Syntax highlighting and line numbers
3. **Run Simulation** - Press `CYCLE START` (green button) or `Spacebar`
4. **Watch Live** - Toolpaths draw progressively with real-time position info
5. **AI Review** - Click `KI: ANALYZE` for safety checks
6. **Generate Code** - Use `KI: GEN-CODE` or type in chat. The model's draft is
   linted, and what the linter finds is sent back for correction, at most three
   times. The chat shows the result, "Lint clean – no known faults" or how many
   findings remain, and the change from the draft. Lint clean means the linter
   finds nothing it knows about, not that the program is right; nothing is put
   into the editor, you read it and put it there.

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
  mode: "ollama"            # "claude", "ollama" or "openai"
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

  openai:                      # any OpenAI-compatible server
    base_url: "https://api.openai.com/v1"   # LM Studio: http://localhost:1234/v1
    model: ""                  # as the server names it; Test connection lists them
    api_key_env: "OPENAI_API_KEY"           # leave the variable unset for a local server
    # max_output_tokens: 16000   # sent as max_completion_tokens, only when set
    # reasoning_effort: medium   # only when set
```

`claude-sonnet-4-20250514`, the earlier default, was retired on 2026-06-15 and
requests to it fail. `ai.system_prompt` and `ai.generate_prompt` set what a review and
a generation are told to do.

### Measuring a model: the review eval

Does a model find what the linter finds? `evals/review_eval.py` puts one known fault
into a copy of your program: G40 taken out of the header, the G50 before G96 removed,
no retract before M30, F0 on a G71, a decimal point in P/Q, and so on, eleven kinds
in all. It then sends exactly what **AI ANALYZE** sends and checks whether the answer
names the fault at the right block:

```bash
python3 -m evals.review_eval                 # lists the cases, sends nothing
python3 -m evals.review_eval --run           # asks the model config.yaml names
python3 -m evals.review_eval --run --model claude-haiku-5-5 --repeat 3
python3 -m evals.review_eval --rescore _tmp/review_eval/<file>.jsonl
```

By default it reads `programs/*.nc`; pass files to use others
(`tests/fixtures/reference.nc` adds compensation and arcs outside a cycle). The linter
is the ground truth. A fault is used only where the linter reports it on the copy, so the eval
measures nothing the linter cannot see. Each program is also sent once unchanged, to
count how much a model reports on a program the linter finds clean. Every answer is
saved to `_tmp/review_eval/`, so `--rescore` can score them again without paying for
them twice. A failed call (no credits, wrong key) stops the run. The scoring looks for
the block's N number or line number and a word for the fault in the same item of the
answer. It is a heuristic, so read the saved answers before trusting a number.

### Measuring the loop: the generate eval

How often does **KI: GEN-CODE** end lint clean? `evals/generate_eval.py` sends a
fixed list of requests (threads, G71/G70, grooving, peck drilling, whole programs,
English and German) through the same loop and counts how often the first draft was
lint clean, how often the result is, how many corrections it took and how often the
delete guard rejected one. It measures lint cleanliness, not whether the code does
what was asked:

```bash
python3 -m evals.generate_eval                # lists the tasks, sends nothing
python3 -m evals.generate_eval --run          # asks the model config.yaml names
python3 -m evals.generate_eval --summarize _tmp/generate_eval/<file>.jsonl
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
│   ├── ai_client.py            # AI client (Claude, Ollama, OpenAI-compatible)
│   ├── analysis_prompt.py      # What AI ANALYZE sends (shared with the eval)
│   ├── gcode_highlighter.py    # Syntax highlighting + editor
│   ├── gcode_plotter.py        # 2D visualization + live drawing
│   ├── lint_engine.py          # Policy enforcement
│   ├── diagnostics.py          # One finding: line, severity, rule id, hint
│   ├── verifier.py             # GEN-CODE: draft, lint, correct
│   ├── specs.py                # Reads the cycle cards and the dialect
│   ├── camotics_bridge.py      # 3D simulation integration
│   ├── settings_manager.py     # Config loader
│   ├── tool_model.py           # Tool library
│   └── macro_model.py          # Macro library
│
├── specs/fanuc_0i_tf/
│   ├── dialect.toml            # G- and M-codes of G-code system A (autocomplete)
│   └── cycles/G70-G76.toml     # Cycle cards: words, units, ranges, assumptions
│
├── tools/
│   ├── tools.json              # Tool database
│   └── macros.db               # SQLite macro storage
│
├── evals/
│   ├── review_eval.py          # Does a model find the faults the linter knows?
│   └── generate_eval.py        # How often does the verifier loop end lint clean?
│
└── programs/                   # Sample G-Code programs
```

---

##  Roadmap

###  Completed
- [x] Live simulation with progressive drawing
- [x] Real-time position tracking
- [x] AI support: Claude, Ollama and OpenAI-compatible servers
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
