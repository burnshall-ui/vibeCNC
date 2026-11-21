# Vibe CNC — Policies (Fanuc style)

**Purpose:** The AI and the linter enforce these rules. The goal is robust, traceable NC programming.

## Header guards
- `G18 G40 G80 G97` must be set in the header.
- Units: `G21` (mm).
- Origin: `G54` (or document it explicitly in the program).

## CSS & spindle speed limit
- `G50` MUST come before the first `G96` (limit the spindle speed before setting a surface speed).

## Process invariance
- Robot/air M-codes are **invariant** and must not be changed or removed
  (the list is defined in `config.yaml`).

## Cycles & parameters
- G7x cycles: no negative or zero depths of cut or feeds; keep value ranges plausible.
- G76 threading: sensible number of passes and final depth; warn on extreme deviations.
- `G70`–`G76`: `P` and `Q` are written **without a decimal point**. In G70–G73 they are
  block numbers (`P100`); in G74–G76 they are amounts in 0.001 mm (`P1500` = 1.5 mm,
  `Q400` = 0.4 mm). The control rejects a decimal point there with an alarm.
- Contour of `G70`–`G73`: the block `P` names writes `G00` or `G01` itself, and
  for `G71` moves `X`, for `G72` moves `Z`. No `G40`/`G41`/`G42` between `P` and
  `Q`: compensation goes before the cycle and is cancelled after it. The block
  numbers `P` and `Q` name occur only once in the program.
- `G76`: in `P(mmrraa)` of the first block, `mm` is 01–99 and `aa` one of 80, 60,
  55, 30, 29, 00; the finishing allowance `R` is smaller than the thread height
  `P` of the second block.
- `G74`, `G75`, `G76`: no tool nose radius compensation; `G40` before the cycle.
- `G4` dwell: ≤ 5 s without a comment, otherwise document it.

## Safe sequence
- Start the spindle (`M03`/`M04`) before the first cutting move or cycle, and again
  after every `M05`.
- Before `M30`: move `Z` to a safe plane first, then retract `X`. The linter checks
  where the tool ends: in front of the part's face, or after a `G28`/`G30`. The face
  is the one of the `(STOCK ...)` comment, or Z0 without one, or further out where the
  program cuts further out. A cycle's start point in front of the face counts as
  retracted; beside the part it does not.
- A program ends with `M30` (or `M02`). Nothing after the end is run.
- Do not change cooling air or signals at the end if they are marked "invariant".

## Comments & documentation
- Comment header with: tool table, material, date, version (`REV:`).
- The AI may only **suggest fixes** or output a diff; changes are applied manually.
