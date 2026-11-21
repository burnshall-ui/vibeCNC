# Vibe CNC — architecture plan

Revised on 2026-11-21 from the author's own plan (German, written for a coding
agent: multi-controller spec files, an intermediate representation, a verifier
loop) after going through it section by section. Where the two differ, this
file decides. It is meant to be built to, and changed when a decision changes.

## Principles

- **The simulator simulates; a person verifies.** Nothing it produces says
  "verified". The verifier loop's green result is called `lint_clean`: the
  linter finds nothing it knows. See the safety rule at the top of README.md.
- **No fine-tuning (LoRA) and no retrieval over PDF manuals.** Manuals are
  copyrighted and never enter the repository; chunked PDFs tear multi-block
  cycles and tables apart.
- **Spec files are the one place control data lives**: which words a cycle
  takes, their units, where a decimal point is forbidden, value ranges,
  pitfalls, examples. Linter, prompts and autocomplete read them. **Behaviour
  stays Python**: how a G71 is expanded into passes is an algorithm, in the
  parser, not data.
- **Fanuc 0i-TF (Plus), G-code system A, only.** A dialect abstraction is built
  when a second control actually arrives, not guessed in advance.
- **TOML read with `tomllib`, dataclasses, `unittest`.** Standard library only,
  so spec loading runs in the GUI-free suite next to the parser and linter.
- **Every card says where its content comes from** and whether it was checked
  on the real machine. The parser's assumptions (G76 depth schedule, G73 pass
  spacing, G75 retract) are listed in the cards as assumptions, unverified until
  someone confirms them on the 0i-TF Plus.
- **Works with every backend** (Claude, Ollama, OpenAI-compatible) and without
  tool calling.
- **Measured, not hoped for.** Every change that affects what a model sees is
  run through the evals before and after.

## Layout

Flat, in the existing package. New files arrive with the step that needs them.

```
vibe_cnc/
├── diagnostics.py     (A)  Diagnostic, severity
├── verifier.py        (A)  generate -> lint -> correct; GUI-free, takes the client as a parameter
├── specs.py           (B)  dataclasses, TOML loader, validation naming file and key
├── spec_context.py    (C)  cards into prompts; keyword pre-pass
├── ir.py              (D)  typed moves
└── ... everything that exists stays
specs/fanuc_0i_tf/
├── dialect.toml       (B)  system A; G and M codes with a short text (feeds autocomplete)
└── cycles/G70.toml ... G76.toml   (B)
evals/generate_eval.py (A)  measures the verifier as review_eval measures the analysis
```

Not created: `src/`, a parser base class, a second linter beside
`lint_engine.py`, a machine profile, a job file. Tools stay in
`tools/tools.json`, chuck limits in `config.yaml`, shop rules in `policies.md`,
until there is a reason to move them. New GUI-free test modules are listed in
the core job of `.github/workflows/ci.yml`.

## Data contracts

### Cycle card

English (it is in the repository); keywords in both languages, because the
shop asks in German.

```toml
code = "G76"
name = "Multiple threading cycle"
keywords = ["thread", "gewinde", "steigung", "pitch", "g76"]

[source]
text = "mentorcnc.com training pages citing the Fanuc manual; the manual itself not read"
verified = false            # true once checked on the 0i-TF Plus
verified_on = ""            # date, machine

[[block]]                   # first block: no X, Z, U or W
[block.words.P]
form = "mmrraa"             # finishing passes, run-out in 0.1 lead, tool angle
decimal_point = false
[block.words.Q]
unit = "0.001 mm"
decimal_point = false
min = 0
meaning = "minimum depth of cut, radius"
[block.words.R]
unit = "mm"
meaning = "finishing allowance"

[[block]]                   # second block
recognized_by = ["X", "U", "Z", "W"]
[block.words.R]
unit = "mm"
meaning = "taper: radius difference start to end"
# X/U, Z/W, P (thread height, 0.001 mm radius), Q (first cut), F (lead)

[[assumption]]
text = "Depth after n passes is first * sqrt(n), never less than the minimum cut"
verified = false

[[assumption]]
text = "F is the lead whether G98 or G99 is active"
verified = false

[[example]]
title = "M20x2.5 external, 30 long"
code = '''
G00 X24. Z5.
G76 P020060 Q100 R0.05
G76 X16.93 Z-30. P1534 Q400 F2.5
'''
```

**Examples are tests.** Every example in a card must come through the linter
without a finding and be drawn by the parser, so a card and the tools can never
disagree in silence.

### Diagnostic

```python
Severity = Literal["ERROR", "WARNING", "INFO"]

@dataclass(frozen=True)
class Diagnostic:
    line: int                           # 1-based line in the program
    severity: Severity
    rule: str                           # stable id, e.g. "DECIMAL_POINT"
    message: str                        # for the operator
    token: Optional[str] = None         # e.g. "Q0.4"; None where there is nothing to point at
    hint: Optional[str] = None          # a concrete fix, for the operator and the model
    related_code: Optional[str] = None  # e.g. "G76", to fetch its card
```

Severity follows the consequence:

| Severity | Meaning | Rules |
|---|---|---|
| ERROR | the control alarms, the machine can crash, or a shop safety rule breaks | decimal point in P/Q of G70–G76; cycle blocks missing or no contour; an arc that cannot be made; feed or depth of cut zero; G50 not before G96; a protected M-code commented out; no retract before M30; **chuck collision** (it used to show only in the plot); a cut or a cycle before the spindle runs (whole programs only); header without **G40** or without **G21**; an arc too tight for the tool nose |
| WARNING | a shop rule about form | rest of the header (G18, G80, G97), work offset G54, the other G41/G42 checks, too many cycle passes |
| INFO | a hint without a finding | the G76 "check F/S/R" heuristic |

`lint_clean` means no ERROR and no WARNING.

**Snippets.** Code without M30 or M02 is a snippet, not a program. The rules
about a whole program (header, retract before M30) do not apply to it; the
rules about cycles, arcs, feeds and collisions always do.

### IR

What the parser already records, typed. X is always a diameter, in mm.
Nothing in it is a G-code or M-code: those are dialect.

```python
Point = Tuple[float, float]            # (x diameter, z)
Purpose = Literal["rapid", "cut", "finish", "thread", "comp"]

@dataclass(frozen=True)
class Move:
    line: int
    start: Point
    end: Point
    purpose: Purpose
    feed: Optional[float] = None
    feed_mode: Optional[Literal["mm_per_min", "mm_per_rev"]] = None

@dataclass(frozen=True)
class Arc:
    line: int
    start: Point
    end: Point
    centre: Point                      # absolute; I/K or R is only how it was written
    radius: float
    clockwise: bool
    purpose: Purpose
    feed: Optional[float] = None

@dataclass(frozen=True)
class ThreadPass:
    line: int
    start: Point
    end: Point
    lead: float

@dataclass(frozen=True)
class ToolChange:
    line: int
    tool: int
    offset: int
    at: Point

@dataclass(frozen=True)
class Spindle:
    line: int
    mode: Literal["css", "rpm", "clamp"]
    value: float
    direction: Optional[Literal["cw", "ccw", "stop"]] = None
```

Collisions are not instructions; they are diagnostics.

## Steps

### A — Verifier loop on the existing linter

1. The AI client holds a conversation: a list of messages, for all three
   backends. `ask()` stays as the one-question form.
2. `Diagnostic` with severity, as above. Chuck collisions become diagnostics.
   Snippets are linted without the whole-program rules.
3. The generation prompt asks for exactly one code block. None, or more than
   one, is shown as it came and is not `lint_clean`.
4. `verifier.py`: draft, lint, and on an ERROR or WARNING a correction message
   carrying the diagnostics. At most three corrections. A correction whose
   findings repeat an earlier attempt's stops the loop. **Delete guard:** every
   cycle and every tool of the draft must still be in a correction, or the
   correction is rejected and the model is told why. The best attempt wins:
   fewest ERRORs, then fewest WARNINGs.
5. The chat shows a status line ("Lint clean – no known faults" or "2 findings
   remain") and the diff from draft to result. Nothing is put into the editor;
   the operator does that.
6. `evals/generate_eval.py`: a fixed list of tasks; it measures how often the
   first draft is `lint_clean`, how often the result is, how many rounds it
   took, how often the delete guard fired. It measures lint cleanliness, not
   whether the code does what was asked.

Done when: a draft with `Q0.4` in a G76 is corrected in the second round (mock
client); an identical repeated finding stops the loop; the delete guard, the
snippet rule and a chuck collision as a diagnostic are tested; the generate
eval has run once against a real model.

### B — Specs, narrow

`specs.py`; `dialect.toml` for system A, which also feeds the autocomplete
table (its G90/G91/G94 entries describe another G-code system today); cards
G70–G76 drafted from what the parser, the linter and `policies.md` already
assume, every one `verified = false` until confirmed on the machine. The
linter reads decimal-point rules and value ranges from the cards; the code
that did it by hand is removed, not kept beside.

Done when: autocomplete reads `dialect.toml`; the decimal-point and range rules
come from the cards; every card example is `lint_clean` and drawn; a broken
card names its file and key when loaded.

### C — Cards in the prompt

`spec_context.py` finds G/M codes and keywords in the request and the program
and puts the one to three matching cards into the prompt, with a short sheet
from `dialect.toml`. For the review, the generation and the loop's corrections.

Done when: "cut an M20x2.5 thread" brings in `G76.toml`, deterministically;
both evals have run before and after. The concrete question: does the model
find the `R0.5` arc it missed on 2026-11-21 once it has the card?

Tool calling only if the evals show the pre-pass is not enough.

### D — IR

`ir.py`; the parser returns IR, and the old `paths` dict is derived from it as
long as anything reads it, so every existing test keeps running unchanged and
is the safety net. Then the plotter reads IR and the derived `paths` goes.
From the original dry-run idea only "no retract before a tool change" comes
along, as a rule.

Done when: `tests/test_reference.py` passes without one changed number; the
sample programs look the same (checked by eye, as before).

### Later

| Topic | When |
|---|---|
| Tool calling | when the evals show the pre-pass is not enough |
| Rapid through stock | needs a model of the material removed so far: stock changes as it is cut, so checking against the blank raises false alarms |
| Machine profile (travel, max rpm) | when the values are measured, not estimated |
| PDF importer for machine manuals | local only; produces card drafts with `verified = false` for a person to check; the manual never enters the repository |

## Cut from the original plan, and why

- Multi-controller layout, `DialectParser` base, Sinumerik: one control today.
- YAML and pydantic: neither is in the standard library; the GUI-free suite
  runs without dependencies.
- `src/` and pytest: no gain over the existing package and unittest.
- Three-level config merge and `active_job.yaml`: tools, chuck limits and the
  program header already hold this; a third copy would drift.
- `num_ctx` raised to 16384: already configurable; raised when a prompt needs it.
- `verified=True` as the loop's result: the linter knows a handful of rule
  families and does not see everything a person must.
