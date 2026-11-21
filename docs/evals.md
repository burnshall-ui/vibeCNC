# Evals

Two scripts in `evals/` measure how a model does on this application's own
tasks. Without `--run` they send nothing and only list what they would ask.
Every answer is saved to `_tmp/` (not under version control) as it arrives,
so a run that stops halfway keeps what it has, and saved answers can be
scored again without paying for them twice.

Single runs vary from case to case: compare with `--repeat 3` or more, and
read the saved answers before trusting a number.

## Review eval

Does a model find the faults the linter knows about?

`evals/review_eval.py` puts one known fault into a copy of a program (G40
taken out of the header, the G50 before G96 removed, no retract before M30,
F0 on a G71, a decimal point in P or Q, and so on) and sends exactly what
**AI ANALYZE** sends. It then checks whether the answer names the fault at the
right block. The linter is the ground truth: a fault is used only where the
linter reports it on the copy. Each program is also sent once unchanged, to
see how much a model reports on a program the linter finds clean.

```bash
python3 -m evals.review_eval                     # list the cases, send nothing
python3 -m evals.review_eval --run --repeat 3    # ask the model config.yaml names
python3 -m evals.review_eval --run --model claude-haiku-5-5
python3 -m evals.review_eval --run --no-cards    # without the cycle cards in the prompt
python3 -m evals.review_eval --rescore _tmp/review_eval/<file>.jsonl
```

It reads `programs/*.nc` unless files are given;
`tests/fixtures/reference.nc` adds compensation and arcs outside a cycle.
The scoring looks for the block's N number or line number and a word for the
fault in the same item of the answer. It is a heuristic.

## Generate eval

How often does **AI GENERATE** end lint clean?

`evals/generate_eval.py` sends a fixed list of requests (threads, G71/G70,
grooving, peck drilling, whole programs, in English and German) through the
same draft–lint–correct loop the app runs. It counts how often the first
draft was lint clean, how often the result is, how many corrections it took
and how often the delete guard rejected one.

```bash
python3 -m evals.generate_eval                   # list the tasks, send nothing
python3 -m evals.generate_eval --run --repeat 3
python3 -m evals.generate_eval --run --no-cards
python3 -m evals.generate_eval --summarize _tmp/generate_eval/<file>.jsonl
```

It measures lint cleanliness, not whether the code does what was asked: a
program that cuts the wrong diameter can be lint clean.

## Known limits

- The generate tasks ask for the same parts as the examples in the cycle
  cards (G71, G72, G73, G75, G76). With the cards in the prompt a model can
  copy the answer, so the eval says nothing about new parts until the tasks
  are changed.
- One model has been measured so far (`tencent/hy4-flash` via OpenRouter,
  2026-11-21). For it, first drafts were lint clean 18 times in 24 with and
  without the cards, and reviews found 74 and 76 of 84 faults: no measurable
  difference. See [PLAN.md](PLAN.md), step C.
- Since the retract rule judges against the part's face, taking the retract
  out of a program that ends at a cycle's start point in front of the face is
  no fault any more, and the eval leaves those copies out: the sample programs
  give 24 fault cases, not 28 (`no_retract` only in O0004, beside the part).
  Results counted out of 28 per run do not compare with later ones.
