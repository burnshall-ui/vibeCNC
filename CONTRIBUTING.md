# Contributing

Code, comments, commit messages and documentation are in English.

This tool simulates. G-code it generates or edits must be verified by a
person before it reaches a machine; the lint rules and the collision check
are heuristics, not proof. Changes should keep that visible: nothing the
program produces says "verified".

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install ruff
```

## Tests

There are two suites, and the split is deliberate. Everything that decides
what the operator sees, or whether a move is reported as a collision, runs
without Qt: the parser, the linter, the spec files, the verifier loop and the
evals. A missing Qt plugin can then never be mistaken for a fault in that
logic.

```bash
# 1. GUI-free: runs on a bare interpreter, nothing installed
python3 -m unittest tests.test_lint_engine tests.test_gcode_parser \
    tests.test_reference tests.test_arc_geometry tests.test_tool_data \
    tests.test_collision tests.test_macro_data tests.test_gcode_text \
    tests.test_review_eval tests.test_verifier tests.test_generate_eval \
    tests.test_specs tests.test_spec_context tests.test_stock_model \
    tests.test_tool_shape

# 2. Everything, including the Qt tests (needs requirements.txt)
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests

# The lint gate
ruff check --select=F,E9 .
```

CI runs the same: [`.github/workflows/ci.yml`](.github/workflows/ci.yml)
lists every test module by name, so a new module has to be added there.
GUI-free modules go into the `core` job.

The tests for the AI client expect no API key in the environment. If you
have one set, run the second suite with `env -u OPENAI_API_KEY -u
OPENROUTER_API_KEY ...`.

Two kinds of test data have rules of their own:

- `tests/fixtures/reference.nc` exercises every branch of the parser. The
  expected path lengths in `tests/test_reference.py` are worked out by hand
  from the program text, not recorded from a parser run. Changing them to
  match new output deletes the test instead of fixing it.
- Every example in a cycle card must come through the linter without a
  finding and be drawn by the parser; `tests/test_specs.py` checks it.

## Spec files

`specs/fanuc_0i_tf/` holds what the simulator knows about the control:
`dialect.toml` and one card per cycle. What a cycle *does* (how G71 becomes
passes) stays in the parser; what its words are, their units, decimal
points and ranges is data in the card, and the linter reads it from there.

- Every card says where its content comes from (`[source]`), and every
  assumption the section of the manual that supports it (`manual = "4.2.7"`),
  or nothing when the manual does not say.
- `verified = true` is only for what was confirmed on the machine, with
  `verified_on` saying when and on which one. The manual alone is not enough.
- Manuals are copyrighted. Cite section numbers and state facts in your own
  words; never copy manual text or figures into the repository.
- A card that is wrong is rejected when it is loaded, with its file and the
  key, so check `python3 -m unittest tests.test_specs` after editing one.

## Commits

[Conventional commits](https://www.conventionalcommits.org/): `feat:`,
`fix:`, `docs:`, `test:`, `refactor:`, `chore:`, with a scope where it helps
(`fix(lint): ...`). The body says why, and what was not done or not verified.

Run both suites and the lint gate before pushing.

## Reporting a problem

Open an issue with the program (or the smallest part of it that shows the
problem), what the simulator drew or reported, what the control does, and
your operating system and Python version. Remove anything from the program
you may not share.

## License

Contributions are licensed under the Apache License 2.0, like the project.
