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
- `G4` dwell: ≤ 5 s without a comment, otherwise document it.

## Safe sequence
- Before `M30`: move `Z` to a safe plane first, then retract `X`.
- Do not change cooling air or signals at the end if they are marked "invariant".

## Comments & documentation
- Comment header with: tool table, material, date, version (`REV:`).
- The AI may only **suggest fixes** or output a diff; changes are applied manually.
