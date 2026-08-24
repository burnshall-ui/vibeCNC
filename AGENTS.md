# AGENTS.md

Orientation for AI agents working in this repository.

## What this is

Vibe CNC is a single-process PyQt6 desktop simulator for Fanuc-style lathe
G-code, with optional AI assistance. No Docker, no server, no database.

**Safety rule first:** this tool simulates. G-code it generates or edits must be
verified by a human before it reaches a machine — the lint rules and the
collision check are heuristics, not proof. See the top of `README.md`.

## Tests

Two suites, and the split is deliberate.

```bash
# 1. GUI-free — runs on a bare interpreter with nothing installed
python3 -m unittest tests.test_lint_engine tests.test_gcode_parser \
    tests.test_reference tests.test_arc_geometry

# 2. GUI-backed — needs requirements.txt
QT_QPA_PLATFORM=offscreen python -m unittest \
    tests.test_tool_model tests.test_arc_rendering
```

Anything that decides what the operator *sees*, or whether a move is flagged as
a collision, belongs in suite 1: the parser, the lint engine, the arc direction
maths in `vibe_cnc/arc_geometry.py`. Keep those importable without Qt, so a
missing Qt plugin can never be mistaken for a failure in the logic.
`.github/workflows/ci.yml` runs the two as separate jobs.

`tests/fixtures/reference.nc` exercises every parser branch: arcs both ways,
I/K and R forms, U/W increments, a dwell, a roughing cycle, compensation. The
expected path lengths in `tests/test_reference.py` are computed by hand from the
program text, not recorded from a parser run. If you edit them to match new
output, you have deleted the test rather than fixed it.

## Running the app

```bash
python3 vibe_cnc.py
```

Headless — CI, containers, a cloud agent:

```bash
Xvfb :99 -screen 0 1280x1024x24 &
DISPLAY=:99 QT_QPA_PLATFORM=xcb python3 vibe_cnc.py
```

PyQt6 needs system libraries: `libegl1`, `libgl1`, `libopengl0`,
`libxkbcommon-x11-0`, `libdbus-1-3`, `libfontconfig1`, `libxcb-cursor0`,
`libxcb-icccm4`, `libxcb-keysyms1`, `libxcb-shape0`, `libxcb-xkb1`,
`libxcb-render-util0`, `xvfb`. The CI workflow installs its own list for the
headless job.

## The AI backend is optional

Everything but the assistant works without one: editor, plotter, simulation,
lint engine, tool and macro libraries. Set `ai.offline: true` in `config.yaml`
to run without a backend; `ai.mode` picks `ollama` or `anthropic`.

## Layout

| path | what |
|---|---|
| `vibe_cnc.py` | entry point and main window |
| `vibe_cnc/gcode_parser.py` | G-code to toolpaths — GUI-free |
| `vibe_cnc/arc_geometry.py` | arc angle maths for the plotter — GUI-free |
| `vibe_cnc/lint_engine.py` | G-code policy checks — GUI-free |
| `vibe_cnc/gcode_plotter.py` | matplotlib rendering in a PyQt6 widget |
| `config.yaml` | machine limits, policies, AI backend |
| `tools/tools.json` | tool library (the single source for tool data) |
| `policies.md` | the shop rules the lint engine enforces |
| `programs/O0001_DEMO.nc` | sample program |

## Conventions

- The repository language is English: code, comments, commit messages, PR text.
- Work lands directly on `main`. Run both suites before pushing — there is no
  pull request gate in front of you.
- No linter is enforced yet; `ruff check --select=F,E9 .` is the intended gate.
