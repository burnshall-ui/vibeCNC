"""Generate eval: how often does the verifier loop end lint clean?

It sends a fixed list of requests through the same loop AI GEN-CODE runs
(vibe_cnc/verifier.py): the model drafts, the linter checks the draft, the
findings go back to the model, at most three times. For each request it
records whether the first draft was lint clean, whether the result is, how
many corrections it took, how often the delete guard rejected one, and why
the loop stopped.

It measures lint cleanliness, not whether the code does what was asked: a
program that cuts the wrong diameter can be lint clean. Read the saved
answers before trusting a number.

    python3 -m evals.generate_eval                 # plan: lists the tasks, sends nothing
    python3 -m evals.generate_eval --run           # asks the model config.yaml names
    python3 -m evals.generate_eval --run --model claude-haiku-5-5 --tasks thread_m20

Every outcome is written to _tmp/generate_eval/ (gitignored) as soon as it
arrives, one JSON object per line, so a run that stops halfway keeps what it
has.
"""
import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:            # run as a file rather than with -m
    sys.path.insert(0, ROOT)

from vibe_cnc.diagnostics import is_lint_clean
from vibe_cnc.lint_engine import LintEngine
from vibe_cnc.spec_context import cards_for_request, reference
from vibe_cnc.specs import load_specs
from vibe_cnc.verifier import (
    CLEAN, FAILED, MAX_CORRECTIONS, NO_CODE, OUT_OF_ROUNDS, REPEATED, generation_request, verify,
)

OUT_DIR = os.path.join(ROOT, "_tmp", "generate_eval")


@dataclass(frozen=True)
class Task:
    name: str
    request: str
    # A piece that goes into a program, or a whole program: the switch in
    # the assist box. The loop lints each as what was asked for.
    snippet: bool = True


# What a shop asks for, every value a program needs given, so a model has no
# reason to invent one. Programs and pieces of programs, English and German.
TASKS = [
    Task("thread_m20",
         "Cut an external M20x2.5 thread, 30 mm long, with G76. Threading tool T0303, "
         "start the cycle from X24. Z5."),
    Task("g71_shaft",
         "Rough a shaft from Ø50 stock with G71, then finish it with G70: Ø40 for 30 mm "
         "from the face at Z0, then Ø30 for the first 15 mm. Roughing T0101, 2 mm depth of "
         "cut, F0.25; finishing T0202, F0.1, allowance 0.4 in X and 0.1 in Z."),
    Task("groove_g75",
         "Cut a groove to Ø25 at Z-20 with G75 on a Ø40 bar, 3 mm grooving tool T0505, "
         "peck 1 mm, F0.05."),
    Task("peck_g74",
         "Peck drill a centre hole 20 mm deep with G74, Ø10 drill T0606, peck 2 mm, "
         "F0.08, at 800 rpm."),
    Task("face_turn_program",
         "Write a complete program O2001: face a Ø60 bar of 42CrMo4 at Z0 and turn it to "
         "Ø55 for 40 mm, one tool T0101, constant surface speed 180 m/min with a 2500 rpm "
         "limit, F0.2. With the safety header and a safe end of program.", snippet=False),
    Task("gewinde_programm",
         "Schreibe ein komplettes Programm O2002, Werkstoff C45: Außengewinde M24x3, 25 mm "
         "lang, ab Z0, mit G76, Gewindedrehmeißel T0303, Drehzahl 600 U/min. Mit "
         "Sicherheitsblock am Anfang und sicherem Programmende.", snippet=False),
    # It asked for "Ø80 down to Ø40 at Z-5, then Ø40 down to Ø20 at Z-10" until
    # 2026-11-21: deeper inside than outside, a pocket in the face, which G72
    # type I cannot cut. The answers of both runs followed it to the letter.
    Task("g72_face",
         "Rough a stepped face with G72 on a Ø80 bar whose face is at Z0: outside Ø40 "
         "face it down to Z-10, inside Ø40 down to Z-5, so a Ø40 boss stands 5 mm proud. "
         "T0101, 1.5 mm depth of cut, F0.2, finishing allowance 0.2 in X and 0.1 in Z."),
    Task("g73_blank",
         "Rough a forged blank with G73 in 4 passes, 3 mm stock in X (radius) and 2 mm in "
         "Z: contour Ø30 to Z-20, then Ø45 to Z-35. T0101, F0.2, then finish with G70."),
]
TASKS_BY_NAME = {t.name: t for t in TASKS}


def attempt_record(attempt) -> dict:
    return {"answer": attempt.answer, "code": attempt.code, "blocks": attempt.blocks,
            "findings": [d.as_dict() for d in attempt.findings], "dropped": list(attempt.dropped)}


def outcome_record(outcome) -> dict:
    best = outcome.attempts.index(outcome.best) if outcome.best is not None else None
    draft = outcome.draft
    return {"stop": outcome.stop, "error": outcome.error, "snippet": outcome.snippet,
            "draft_clean": bool(draft is not None and draft.code is not None
                                and is_lint_clean(draft.findings)),
            "result_clean": outcome.lint_clean, "corrections": outcome.corrections,
            "rejections": outcome.rejections, "best": best,
            "attempts": [attempt_record(a) for a in outcome.attempts]}


def summarize(records: List[dict]) -> str:
    done = [r for r in records if r["stop"] != FAILED or r["attempts"]]
    out = [f"{len(records)} request(s), {len(records) - len(done)} failed before a draft."]
    with_cards = {r.get("with_cards") for r in records}
    if with_cards == {True}:
        out.append("With the cycle cards and the dialect sheet.")
    elif with_cards == {False}:
        out.append("Without the cycle cards (--no-cards).")
    elif len(with_cards) > 1:
        out.append("Mixed: some requests with the cycle cards, some without.")
    if not done:
        return "\n".join(out)
    n = len(done)
    first = sum(r["draft_clean"] for r in done)
    result = sum(r["result_clean"] for r in done)
    out.append(f"First draft lint clean: {first}/{n}")
    out.append(f"Result lint clean:      {result}/{n}")
    rounds = [r["corrections"] for r in done]
    out.append(f"Corrections asked for:  {sum(rounds)} in all, at most {max(rounds)} for one request")
    guarded = sum(1 for r in done if r["rejections"])
    out.append(f"Delete guard fired:     in {guarded} request(s), "
               f"{sum(r['rejections'] for r in done)} correction(s) rejected")
    stops = Counter(r["stop"] for r in done)
    out.append("Stopped: " + ", ".join(f"{name} {stops[name]}" for name in
                                        (CLEAN, REPEATED, OUT_OF_ROUNDS, NO_CODE, FAILED)
                                        if stops[name]))
    left = Counter(f["rule"] for r in done if r["best"] is not None
                   for f in r["attempts"][r["best"]]["findings"] if f["severity"] != "INFO")
    if left:
        out.append("Findings left in the results: " +
                   ", ".join(f"{rule} {count}" for rule, count in left.most_common()))
    out.append("\nPer request:")
    for r in records:
        code = "-" if r["best"] is None else ("clean" if r["result_clean"] else "findings left")
        out.append(f"  {r['task']:<18} draft {'clean' if r['draft_clean'] else 'not clean':<9}  "
                   f"result {code:<13}  corrections {r['corrections']}  rejected {r['rejections']}"
                   f"  stop {r['stop']}"
                   + (f"  cards {','.join(r['cards']) or '-'}" if r.get("with_cards") else ""))
    out.append("\nLint clean is not correct: read the saved answers.")
    return "\n".join(out)


def run(tasks, client, linter, mode: str, model: str, repeat: int, out_path: str,
        keep_going: bool, specs=None) -> List[dict]:
    """Run each task through the loop; client is an AIClient or anything with
    chat and generate_prompt. specs: the cycle cards, as the app sends them,
    or None to leave them out."""
    system = client.generate_prompt()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    records, total = [], len(tasks) * repeat
    with open(out_path, "a", encoding="utf-8") as out:
        for n in range(total):
            task = tasks[n % len(tasks)]
            started = time.monotonic()
            outcome = verify(client, task.request, system, linter, snippet=task.snippet,
                             specs=specs)
            seconds = time.monotonic() - started
            record = dict({"when": datetime.now().isoformat(timespec="seconds"), "mode": mode,
                           "model": model, "task": task.name, "request": task.request,
                           "seconds": round(seconds, 1), "with_cards": specs is not None,
                           "cards": list(outcome.cards)}, **outcome_record(outcome))
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()
            records.append(record)
            if outcome.stop == FAILED and not outcome.attempts:
                verdict = "call failed"
            else:
                verdict = (f"draft {'clean' if record['draft_clean'] else 'not clean'}, "
                           f"result {'clean' if outcome.lint_clean else 'not clean'}, "
                           f"{outcome.corrections} correction(s), stop {outcome.stop}")
            print(f"[{n + 1}/{total}] {task.name}: {verdict} ({seconds:.0f} s)", flush=True)
            if outcome.stop == FAILED:
                print("  " + re.sub(r"<[^>]+>", "", outcome.error or ""))
                if not keep_going:
                    print("Stopped at the first failed call (--keep-going goes on).")
                    break
    return records


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m evals.generate_eval",
        description="How often does the verifier loop end lint clean? Without --run nothing is sent.")
    parser.add_argument("--run", action="store_true", help="ask the model; costs credits")
    parser.add_argument("--mode", choices=("claude", "ollama", "openai"), help="instead of ai.mode")
    parser.add_argument("--model", help="instead of the model in config.yaml")
    parser.add_argument("--tasks", help="comma-separated, from: " + ", ".join(TASKS_BY_NAME))
    parser.add_argument("--repeat", type=int, default=1, help="run each task this many times")
    parser.add_argument("--keep-going", action="store_true", help="go on after a failed call")
    parser.add_argument("--out", help="JSONL file for the outcomes (appended to)")
    parser.add_argument("--show-prompt", action="store_true",
                        help="print the first request as the model gets it")
    parser.add_argument("--summarize", metavar="JSONL", help="summarize saved outcomes, send nothing")
    parser.add_argument("--no-cards", action="store_true",
                        help="leave the cycle cards and the dialect sheet out, as before step C")
    args = parser.parse_args(argv)

    if args.summarize:
        with open(args.summarize, "r", encoding="utf-8") as f:
            print(summarize([json.loads(line) for line in f if line.strip()]))
        return 0

    tasks = TASKS
    if args.tasks:
        names = [n.strip() for n in args.tasks.split(",") if n.strip()]
        unknown = [n for n in names if n not in TASKS_BY_NAME]
        if unknown:
            parser.error(f"unknown task(s): {', '.join(unknown)}")
        tasks = [TASKS_BY_NAME[n] for n in names]

    from vibe_cnc.settings_manager import SettingsManager
    from evals.review_eval import model_of
    settings = SettingsManager(os.path.join(ROOT, "config.yaml"))
    ai = settings.data.setdefault("ai", {})
    if args.mode:
        ai["mode"] = args.mode          # in memory only; config.yaml is not written
    if args.model:
        from vibe_cnc.ai_client import SECTION_OF, backend_of
        if backend_of(ai) not in SECTION_OF:
            parser.error(f"ai.mode '{backend_of(ai)}' is not known; use --mode")
        ai.setdefault(SECTION_OF[backend_of(ai)], {})["model"] = args.model
    mode, model = model_of(settings)
    specs = None if args.no_cards else load_specs()

    if not args.run:
        print(f"{len(tasks)} task(s), {'without' if specs is None else 'with'} cycle cards:")
        for t in tasks:
            cards = "" if specs is None else \
                " [" + ", ".join(c.code for c in cards_for_request(t.request, specs)) + "]"
            print(f"  {t.name:<18} {t.request}{cards}")
        if args.show_prompt:
            chosen = [] if specs is None else cards_for_request(tasks[0].request, specs)
            first = generation_request(
                tasks[0].request, "" if specs is None else reference(chosen, specs.dialect))
            print(f"\n--- {tasks[0].name} as the model gets it ---\n{first}")
        most = len(tasks) * args.repeat * (1 + MAX_CORRECTIONS)
        print(f"\nNothing was sent. --run asks {model or '(no model set)'} ({mode}) between "
              f"{len(tasks) * args.repeat} and {most} time(s): one draft per task, and up to "
              f"{MAX_CORRECTIONS} corrections each.")
        return 0

    if not model:
        parser.error(f"no model for {mode}: set it in config.yaml (Settings → AI) or pass --model")
    out_path = args.out or os.path.join(
        OUT_DIR, f"{datetime.now():%Y%m%d-%H%M%S}-{re.sub(r'[^A-Za-z0-9.-]+', '_', model)}.jsonl")
    print(f"Asking {model} ({mode}) for {len(tasks) * args.repeat} task(s); outcomes go to {out_path}")
    from vibe_cnc.ai_client import AIClient
    records = run(tasks, AIClient(settings), LintEngine(settings), mode, model, args.repeat,
                  out_path, args.keep_going, specs)
    print()
    print(summarize(records))
    return 0


if __name__ == "__main__":
    sys.exit(main())
