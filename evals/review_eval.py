"""Review eval: does a model find the faults the linter knows about?

It takes your programs, puts one known fault into a copy of one of them, and
asks the model exactly what AI ANALYZE asks -- the prompt comes from
vibe_cnc/analysis_prompt.py, which the button uses too. Then it looks for the
fault in the answer: the right block (its N number or its line number) and
words that name the fault, in the same item of the answer.

The linter is the ground truth. A fault is used only where the linter, run on
the changed copy, reports something it did not report on the original; where
it does not, the case is left out and listed. So this measures a model on
faults the linter already finds, and nothing beyond them. Each program is also
sent once as it is, to see how much a model reports on a program the linter
finds clean.

    python3 -m evals.review_eval                  # plan: lists the cases, sends nothing
    python3 -m evals.review_eval --run            # asks the model config.yaml names
    python3 -m evals.review_eval --run --model claude-haiku-5-5
    python3 -m evals.review_eval --rescore _tmp/review_eval/<file>.jsonl

Every answer is written to _tmp/review_eval/ (gitignored) as soon as it
arrives, one JSON object per line, so a run that stops halfway keeps what it
has, and --rescore scores saved answers again after the scorer changed without
paying for new ones.

The scoring is a heuristic. Read the saved answers before trusting a number.
"""
import argparse
import glob
import html
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Callable, Dict, Iterator, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:            # run as a file rather than with -m
    sys.path.insert(0, ROOT)

from vibe_cnc.analysis_prompt import MAX_CODE_CHARS, build_analysis_prompt, read_policies
from vibe_cnc.lint_engine import LintEngine
from vibe_cnc.tool_data import load_tools_json

OUT_DIR = os.path.join(ROOT, "_tmp", "review_eval")

# ---- reading and changing a program ---------------------------------------------

NUMBER = r'[-+]?(?:\d+\.?\d*|\.\d+)'


def _word(pattern: str) -> str:
    """A G-code word: not the tail of another word, not the head of a longer number."""
    return rf'(?<![A-Z]){pattern}(?![\d.])'


def split_code(line: str):
    """The code of a line and the comment that follows it."""
    cuts = [k for k in (line.find('('), line.find(';')) if k != -1]
    cut = min(cuts) if cuts else len(line)
    return line[:cut], line[cut:]


def code_of(line: str) -> str:
    return split_code(line)[0]


def has(code: str, pattern: str) -> bool:
    return re.search(_word(pattern), code, re.IGNORECASE) is not None


def drop(code: str, pattern: str) -> str:
    """The code without its first word matching pattern, and the space before it."""
    return re.sub(r'[ \t]*' + _word(pattern), '', code, count=1, flags=re.IGNORECASE)


def block_numbers(lines: List[str]) -> Dict[int, int]:
    """N number -> 1-based line, for the blocks that have one."""
    numbers = {}
    for i, line in enumerate(lines):
        m = re.match(r'\s*N0*(\d+)', code_of(line), re.IGNORECASE)
        if m:
            numbers.setdefault(int(m.group(1)), i + 1)
    return numbers


def _join(code: str, comment: str, was: str = "") -> str:
    """Code and comment back on one line, the gap between them as it was."""
    gap = was[len(was.rstrip()):] or " "
    code = code.rstrip()
    if not comment:
        return code
    return f"{code}{gap}{comment}" if code else comment


@dataclass
class Change:
    lines: List[str]        # the whole program, as many lines as before
    changed: List[int]      # 1-based numbers of the lines that differ


def edit(lines: List[str], index: int, fn: Callable[[str], str]) -> Optional[Change]:
    """fn applied to the code of one line (0-based), the comment left alone."""
    code, comment = split_code(lines[index])
    new_code = fn(code)
    if new_code == code:
        return None
    new = list(lines)
    new[index] = _join(new_code, comment, code)
    return Change(new, [index + 1])


def _pointed(value: float) -> str:
    """A number as G-code writes it, always with a point: 1.5, 2., 0.2."""
    text = f"{value:g}"
    return text if '.' in text else text + '.'


def _mm(amount: int) -> str:
    """1500 (0.001 mm) as 1.5 -- what is meant, written the way the control refuses."""
    return _pointed(amount / 1000)


# ---- the faults ------------------------------------------------------------------
#
# Each one yields the places it could go, in program order; the first place the
# linter reports is used. Every change keeps the number of lines, so a line
# number in the answer means the same line in the copy and in the original.

def _header_g40(lines, engine) -> Iterator[Change]:
    seen = 0
    for i, line in enumerate(lines):
        code = code_of(line).lstrip('%').strip()
        if not code:
            continue
        seen += 1
        if seen > engine.HEADER_LINES:
            return
        if has(code, r'G0?40'):
            yield edit(lines, i, lambda c: drop(c, r'G0?40'))
            return


def _css_g50(lines, engine) -> Iterator[Change]:
    for i, line in enumerate(lines):
        code = code_of(line)
        if has(code, r'G96'):
            return                      # G50 only counts before the first G96
        if has(code, r'G50') and has(code, 'S' + NUMBER):
            yield edit(lines, i, lambda c: drop(drop(c, r'G50'), 'S' + NUMBER))


def _no_retract(lines, engine) -> Iterator[Change]:
    start = max(len(lines) - 3, 0)      # the linter's end block
    if not any('M30' in line for line in lines[start:]):
        return
    new, changed = list(lines), []
    for i in range(start, len(lines)):
        code, comment = split_code(lines[i])
        bare = re.sub(r'[ \t]*' + _word(r'(?:G28|[XZUW]' + NUMBER + ')'), '', code,
                      flags=re.IGNORECASE)
        if not re.search(r'(?<![A-Z])[XZUW]', bare, re.IGNORECASE):
            bare = drop(bare, r'G0?[01]')   # a G00 with nothing left to move
        if bare != code:
            new[i] = _join(bare, comment, code)
            changed.append(i + 1)
    if changed:
        yield Change(new, changed)


def _feed_zero(lines, engine) -> Iterator[Change]:
    for i, line in enumerate(lines):
        code = code_of(line)
        if re.search(r'\bG7[012]\b', code) and has(code, 'F' + NUMBER):
            yield edit(lines, i, lambda c: re.sub(_word('F' + NUMBER), 'F0', c, count=1,
                                                  flags=re.IGNORECASE))


def _pq_point(lines, engine) -> Iterator[Change]:
    for i, line in enumerate(lines):
        code = code_of(line)
        cycle = re.search(_word(r'G7([0-6])'), code, re.IGNORECASE)
        if not cycle:
            continue
        number = int(cycle.group(1))
        if number == 6 and not re.search(r'(?<![A-Z])[XZUW]', code, re.IGNORECASE):
            continue                    # first G76 block: P is the code m r a, not an amount
        word = re.search(_word(r'([PQ])(\d+)'), code, re.IGNORECASE)
        if not word:
            continue
        letter, digits = word.group(1).upper(), word.group(2)
        pointed = f"{letter}{digits}." if number <= 3 else f"{letter}{_mm(int(digits))}"
        yield edit(lines, i, lambda c, w=word, p=pointed: c[:w.start()] + p + c[w.end():])


def _depth_zero(lines, engine) -> Iterator[Change]:
    for i, line in enumerate(lines):
        code = code_of(line)
        cycle = re.search(_word(r'G7([12])'), code, re.IGNORECASE)
        if not cycle or re.search(r'(?<![A-Z])[PQ]\d', code, re.IGNORECASE):
            continue                    # the second block: U/W are allowances there
        letter = 'U' if cycle.group(1) == '1' else 'W'
        if has(code, letter + NUMBER):
            yield edit(lines, i, lambda c, a=letter: re.sub(_word(a + NUMBER), a + '0', c,
                                                            count=1, flags=re.IGNORECASE))


def _blocks_missing(lines, engine) -> Iterator[Change]:
    numbers = block_numbers(lines)
    for i, line in enumerate(lines):
        code = code_of(line)
        if not has(code, r'G7[0-3]'):
            continue
        q = re.search(_word(r'Q(\d+)'), code, re.IGNORECASE)
        if not q:
            continue
        missing = int(q.group(1)) + 5
        while missing in numbers:
            missing += 5
        yield edit(lines, i, lambda c, w=q, n=missing: c[:w.start()] + f"Q{n}" + c[w.end():])


def _arc_radius(lines, engine) -> Iterator[Change]:
    for i, line in enumerate(lines):
        code = code_of(line)
        if not has(code, r'G0?[23]'):
            continue
        r = re.search(_word(r'R([-+]?)(\d+\.?\d*|\.\d+)'), code, re.IGNORECASE)
        if r:
            tenth = f"R{r.group(1)}{_pointed(float(r.group(2)) / 10)}"
            yield edit(lines, i, lambda c, w=r, t=tenth: c[:w.start()] + t + c[w.end():])


def _leadin_rapid(lines, engine) -> Iterator[Change]:
    for i, line in enumerate(lines):
        if not has(code_of(line), r'G0?4[12]'):
            continue
        for j in range(i + 1, min(i + 6, len(lines))):   # the linter's look-ahead
            code = code_of(lines[j])
            if not re.search(r'(?<![A-Z])[XZ][-+]?\d', code, re.IGNORECASE):
                continue
            if has(code, r'G0?1'):
                yield edit(lines, j, lambda c: re.sub(_word(r'G0?1'), 'G00', c, count=1,
                                                      flags=re.IGNORECASE))
            elif not has(code, r'G0?0'):
                yield edit(lines, j, lambda c: re.sub(r'^(\s*(?:N\d+\s*)?)', r'\1G00 ', c,
                                                      count=1, flags=re.IGNORECASE))
            break


def _g40_toolchange(lines, engine) -> Iterator[Change]:
    comp = False
    for i, line in enumerate(lines):
        code = code_of(line)
        if has(code, r'G0?4[12]'):
            comp = True
        elif comp and has(code, r'G0?40'):
            comp = False
            for k in range(i + 1, len(lines)):
                later = code_of(lines[k])
                if has(later, r'G0?4[12]'):
                    break
                if has(later, r'T\d+'):
                    yield edit(lines, i, lambda c: drop(c, r'G0?40'))
                    break


def _protected_m(lines, engine) -> Iterator[Change]:
    for i, line in enumerate(lines):
        code, comment = split_code(line)
        for m in sorted(engine.protected_m):
            word = re.search(_word(rf'M0?{m}'), code, re.IGNORECASE)
            if word:
                new = list(lines)
                commented = f"({word.group(0)})" + (f" {comment}" if comment else "")
                new[i] = _join(drop(code, rf'M0?{m}'), commented, code)
                yield Change(new, [i + 1])


# What an answer says when it is about the fault. Words alone are not enough:
# the item has to name the right place too (see find_fault).
_MISSING = (r'(?:missing|absent|lacks?|not (?:set|present|found|programmed|included)'
            r'|without|expected|required|add|insert|fehlt|ergänzen)')


@dataclass
class Fault:
    name: str
    summary: str
    about: str              # regex, case-insensitive
    yields: Callable[..., Iterator[Change]]
    places: str = ""        # regex: words that count as naming the place, besides a block


FAULTS = [
    Fault("header_g40", "G40 taken out of the header",
          rf'(?=.*G0?40)(?=.*{_MISSING})', _header_g40,
          places=r'header|program start|start block|safe(?:ty)? (?:start )?block|kopf'),
    Fault("css_g50", "the G50 spindle limit before G96 taken out",
          r'G50|spindle (?:speed )?(?:limit|clamp)|max(?:imum)? (?:spindle )?speed|drehzahlbegrenz',
          _css_g50),
    Fault("no_retract", "the retract before M30 taken out",
          r'retract|safe|clearance|withdraw|G28|home|reference|move (?:the tool )?away'
          r'|rückzug|freifahr', _no_retract,
          places=r'\bM30\b|end of (?:the )?program|program end|programmende'),
    Fault("feed_zero", "the feed of a G70/G71/G72 block set to F0",
          r'\bF0(?:\.0*)?(?![.\d])|\bzero\b|\bfeed|vorschub', _feed_zero),
    Fault("pq_point", "a decimal point put into P or Q of a cycle",
          r'decimal|\bpoint\b|dezimal|komma|punkt', _pq_point),
    Fault("depth_zero", "the depth of cut of G71/G72 set to 0",
          r'depth|\b[UW]0(?:\.0*)?(?![.\d])|\bzero\b|zustellung|schnitttiefe', _depth_zero),
    Fault("blocks_missing", "Q of a cycle pointed at a block that does not exist",
          r'not (?:be )?(?:found|exist|defined|present)|missing|does(?:n\'t| not) exist'
          r'|no (?:such )?block|non-?exist|undefined|fehlt|nicht vorhanden', _blocks_missing),
    Fault("arc_radius", "the R of an arc cut to a tenth, too small to reach the end point",
          r'radius|chord|too small|impossible|cannot|geometr|end ?point', _arc_radius),
    Fault("leadin_rapid", "the move after G41/G42 made a rapid",
          r'rapid|lead-?in|approach|\bG0?1\b|anfahr|eilgang', _leadin_rapid),
    Fault("g40_toolchange", "the G40 before a tool change taken out",
          r'G0?40|cancel|abwahl', _g40_toolchange),
    Fault("protected_m", "a protected M-code commented out",
          r'M0?6[2-5]|invariant|protected|comment|auskommentiert', _protected_m),
]
FAULTS_BY_NAME = {f.name: f for f in FAULTS}
CLEAN = "clean"
NO_PLACE = "no place for it in this program"


# ---- cases -----------------------------------------------------------------------

@dataclass
class Case:
    program: str
    fault: str              # a Fault name, or CLEAN
    summary: str
    code: str               # the program as sent
    expected: List[dict]    # what the linter reports that it did not before
    where: List[int]        # the lines an answer may name: the findings and the change
    known: List[int]        # every line the linter reports, baseline included
    about: str
    places: str = ""
    before: List[str] = field(default_factory=list)   # the changed lines, as they were
    after: List[str] = field(default_factory=list)    # and as they are now


def _key(finding):
    return (finding["line"], finding["rule"], finding["message"])


def build_cases(programs: Dict[str, str], engine, faults=None):
    """(cases, left_out). left_out lists (program, fault, why) for the faults not used."""
    faults = FAULTS if faults is None else faults
    cases, left_out = [], []
    for label, code in programs.items():
        lines = code.splitlines()
        tail = "\n" if code.endswith("\n") else ""
        baseline = engine.run_all(code)
        seen = {_key(f) for f in baseline}
        base_lines = sorted({f["line"] for f in baseline})
        said = "the linter reports nothing" if not baseline else \
            f"the linter reports {len(baseline)} thing(s) already"
        cases.append(Case(label, CLEAN, f"as it is ({said})", code, [], base_lines,
                          base_lines, about=r'(?!)'))

        for fault in faults:
            tried = 0
            for change in fault.yields(lines, engine):
                if change is None:
                    continue
                if len(change.lines) != len(lines):
                    raise AssertionError(f"{fault.name} changed the number of lines")
                tried += 1
                if len("\n".join(change.lines[:max(change.changed)])) > MAX_CODE_CHARS:
                    continue            # past what the analysis sends
                new_code = "\n".join(change.lines) + tail
                new = [f for f in engine.run_all(new_code) if _key(f) not in seen]
                if not new:
                    continue
                where = sorted({f["line"] for f in new} | set(change.changed))
                cases.append(Case(
                    label, fault.name, fault.summary, new_code, new, where,
                    sorted(set(where) | set(base_lines)), fault.about, fault.places,
                    before=[lines[n - 1] for n in change.changed],
                    after=[change.lines[n - 1] for n in change.changed]))
                break
            else:
                why = (NO_PLACE if not tried else
                       f"the linter reports nothing new at any of {tried} place(s)")
                left_out.append((label, fault.name, why))
    return cases, left_out


def case_prompt(case: Case, policies: str, tools_json: dict) -> str:
    """What AI ANALYZE would send with this program in the editor and nothing selected."""
    return build_analysis_prompt(case.code[:MAX_CODE_CHARS], policies, tools_json)


# ---- scoring ---------------------------------------------------------------------

_BREAKS = re.compile(r'<\s*(?:br|/?p|/?li|/?div|/?tr|/?pre|/?h\d|/?ul|/?ol)\b[^>]*>', re.IGNORECASE)
_LINE_REF = re.compile(r'\b(?:lines?|zeilen?|ln\.?)\s*[:#]?\s*(\d+)'
                       r'(?:\s*(?:-|–|to|bis|and|und)\s*(\d+))?', re.IGNORECASE)
_BLOCK_REF = re.compile(r'(?<![A-Za-z0-9])N0*(\d+)(?![\d.])')


def answer_items(answer: str) -> List[str]:
    """The answer as plain text, one item per line or bullet."""
    text = _BREAKS.sub('\n', answer)
    text = html.unescape(re.sub(r'<[^>]+>', '', text))
    return [part.strip() for part in re.split(r'\n|•', text) if part.strip()]


def named_lines(text: str, blocks: Dict[int, int]) -> set:
    """The program lines a piece of text names, by line number or by N number."""
    found = set()
    for m in _LINE_REF.finditer(text):
        first = int(m.group(1))
        last = int(m.group(2)) if m.group(2) else first
        if 0 <= last - first <= 20:
            found.update(range(first, last + 1))
        else:
            found.add(first)
    for m in _BLOCK_REF.finditer(text):
        line = blocks.get(int(m.group(1)))
        if line is not None:
            found.add(line)
    return found


def find_fault(answer: str, case: Case) -> bool:
    """One item names the right place and talks about the fault.

    The item after it counts too, when it names no place of its own: some
    answers put "N150:" on one line and the reason on the next.
    """
    blocks = block_numbers(case.code.splitlines())
    where = set(case.where)
    about = re.compile(case.about, re.IGNORECASE)
    places = re.compile(case.places, re.IGNORECASE) if case.places else None
    items = answer_items(answer)
    for i, item in enumerate(items):
        if not (named_lines(item, blocks) & where or (places and places.search(item))):
            continue
        if about.search(item):
            return True
        nxt = items[i + 1] if i + 1 < len(items) else ""
        if nxt and not named_lines(nxt, blocks) and about.search(nxt):
            return True
    return False


def other_places(answer: str, case: Case) -> int:
    """How many lines the answer names that the linter has nothing on.

    Rough: advice ("N10: add G99") counts the same as a false alarm. Read them.
    """
    blocks = block_numbers(case.code.splitlines())
    return len(named_lines(" \n ".join(answer_items(answer)), blocks) - set(case.known))


def score(answer: str, case: Case) -> dict:
    return {"found": case.fault != CLEAN and find_fault(answer, case),
            "other_places": other_places(answer, case)}


# ---- reports ---------------------------------------------------------------------

def plain(text: str) -> str:
    return html.unescape(re.sub(r'<[^>]+>', '', text))


def describe_plan(cases, left_out, prompt_chars: List[int]) -> str:
    out = []
    faulted = [c for c in cases if c.fault != CLEAN]
    programs = sorted({c.program for c in cases})
    out.append(f"{len(programs)} program(s), {len(cases)} case(s): "
               f"{len(cases) - len(faulted)} as they are, {len(faulted)} with one fault each.")
    for program in programs:
        out.append(f"\n{program}")
        for c in (c for c in cases if c.program == program):
            if c.fault == CLEAN:
                out.append(f"  {CLEAN:<15} {c.summary}")
                continue
            out.append(f"  {c.fault:<15} {c.summary}")
            for before, after in zip(c.before, c.after):
                out.append(f"  {'':<15}   {before.strip()!r} -> {after.strip()!r}")
            for f in c.expected:
                out.append(f"  {'':<15}   linter: line {f['line']} {f['rule']}: {f['message']}")
    unseen = [(p, f, why) for p, f, why in left_out if why != NO_PLACE]
    if unseen:
        out.append("\nLeft out, the linter does not see them there:")
        for program, fault, why in unseen:
            out.append(f"  {program:<20} {fault:<15} {why}")
    no_place: Dict[str, int] = {}
    for _, fault, why in left_out:
        if why == NO_PLACE:
            no_place[fault] = no_place.get(fault, 0) + 1
    if no_place:
        out.append("\nNo place for it (programs without one):")
        out.append("  " + ", ".join(f"{fault} {n}" for fault, n in no_place.items()))
    if prompt_chars:
        average = sum(prompt_chars) // len(prompt_chars)
        out.append(f"\nPrompt: {average} characters on average, roughly {average // 4} tokens "
                   "each (characters / 4), plus the system prompt and the answer.")
    return "\n".join(out)


def _places(case: dict) -> str:
    """"line 21 (N150), line 34 (N270)" for the lines a case is about."""
    lines = case["code"].splitlines()
    shown = []
    for n in case["where"]:
        block = re.match(r'\s*(N\d+)', code_of(lines[n - 1])) if n <= len(lines) else None
        shown.append(f"line {n} ({block.group(1)})" if block else f"line {n}")
    return ", ".join(shown)


def summarize(records: List[dict]) -> str:
    out = []
    by_model: Dict[str, List[dict]] = {}
    for r in records:
        by_model.setdefault(f"{r['model']} ({r['mode']})", []).append(r)
    for model, rows in by_model.items():
        answered = [r for r in rows if r["ok"]]
        failed = len(rows) - len(answered)
        faulted = [r for r in answered if r["case"]["fault"] != CLEAN]
        clean = [r for r in answered if r["case"]["fault"] == CLEAN]
        out.append(f"{model}: {len(answered)} answer(s), {failed} failed call(s)")
        if faulted:
            out.append(f"  {'fault':<16} found")
            for name in [f.name for f in FAULTS]:
                rows_f = [r for r in faulted if r["case"]["fault"] == name]
                if rows_f:
                    hits = sum(r["score"]["found"] for r in rows_f)
                    out.append(f"  {name:<16} {hits}/{len(rows_f)}")
            hits = sum(r["score"]["found"] for r in faulted)
            out.append(f"  {'all':<16} {hits}/{len(faulted)} ({100 * hits // len(faulted)} %)")
            missed = [r for r in faulted if not r["score"]["found"]]
            for r in missed:
                c = r["case"]
                out.append(f"  missed: {c['program']} {c['fault']} ({_places(c)})")
            noise = sum(r["score"]["other_places"] for r in faulted) / len(faulted)
            out.append(f"  other lines named per answer: {noise:.1f} on average")
        if clean:
            noise = sum(r["score"]["other_places"] for r in clean) / len(clean)
            out.append(f"  programs as they are: {noise:.1f} lines named the linter has "
                       "nothing on, on average")
        if faulted or clean:
            out.append("  (\"lines named\" counts advice and false alarms alike: read the answers.)")
    return "\n".join(out)


# ---- running ---------------------------------------------------------------------

def load_programs(paths: List[str]) -> Dict[str, str]:
    programs = {}
    for path in paths:
        with open(path, "r", encoding="utf-8") as f:
            programs[os.path.basename(path)] = f.read()
    return programs


def _model_of(settings) -> Tuple[str, str]:
    from vibe_cnc.claude_client import (DEFAULT_CLAUDE_MODEL, DEFAULT_OLLAMA_MODEL, SECTION_OF,
                                        backend_of)
    ai = settings.data.get("ai") or {}
    mode = backend_of(ai)
    # The OpenAI-compatible backend has no default model; an unknown mode none either.
    default = {"claude": DEFAULT_CLAUDE_MODEL, "ollama": DEFAULT_OLLAMA_MODEL}.get(mode, "")
    section = ai.get(SECTION_OF.get(mode, "")) or {}
    return mode, str(section.get("model") or default).strip()


def run(cases, client, mode: str, model: str, repeat: int, out_path: str,
        keep_going: bool) -> List[dict]:
    """Ask client (an AIClient, or anything with ask and review_prompt) about each case."""
    system = client.review_prompt()
    policies = read_policies(os.path.join(ROOT, "policies.md"))
    tools_json = load_tools_json()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    records, total = [], len(cases) * repeat
    with open(out_path, "a", encoding="utf-8") as out:
        for n in range(total):
            case = cases[n % len(cases)]
            started = time.monotonic()
            ok, answer = client.ask(case_prompt(case, policies, tools_json), system)
            seconds = time.monotonic() - started
            record = {"when": datetime.now().isoformat(timespec="seconds"), "mode": mode,
                      "model": model, "ok": ok, "seconds": round(seconds, 1),
                      "answer": answer, "case": asdict(case),
                      "score": score(answer, case) if ok else None}
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()
            records.append(record)
            if ok:
                verdict = ("found" if record["score"]["found"] else "missed") \
                    if case.fault != CLEAN else f"{record['score']['other_places']} other line(s)"
            else:
                verdict = "call failed"
            print(f"[{n + 1}/{total}] {case.program} {case.fault}: {verdict} ({seconds:.0f} s)",
                  flush=True)
            if not ok:
                print("  " + plain(answer))
                if not keep_going:
                    print("Stopped at the first failed call (--keep-going goes on).")
                    break
    return records


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m evals.review_eval",
        description="Does a model find the faults the linter knows about? "
                    "Without --run nothing is sent.")
    parser.add_argument("programs", nargs="*",
                        help="G-code files (default: programs/*.nc)")
    parser.add_argument("--run", action="store_true", help="ask the model; costs credits")
    parser.add_argument("--mode", choices=("claude", "ollama", "openai"), help="instead of ai.mode")
    parser.add_argument("--model", help="instead of the model in config.yaml")
    parser.add_argument("--faults", help="comma-separated, from: " + ", ".join(FAULTS_BY_NAME))
    parser.add_argument("--no-clean", action="store_true",
                        help="do not send the programs as they are")
    parser.add_argument("--repeat", type=int, default=1, help="ask each case this many times")
    parser.add_argument("--keep-going", action="store_true", help="go on after a failed call")
    parser.add_argument("--out", help="JSONL file for the answers (appended to)")
    parser.add_argument("--show-prompt", action="store_true",
                        help="print the prompt of the first case")
    parser.add_argument("--rescore", metavar="JSONL", help="score saved answers again, send nothing")
    args = parser.parse_args(argv)

    if args.rescore:
        with open(args.rescore, "r", encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]
        for r in records:
            if r["ok"]:
                r["score"] = score(r["answer"], Case(**r["case"]))
        print(summarize(records))
        return 0

    faults = FAULTS
    if args.faults:
        names = [n.strip() for n in args.faults.split(",") if n.strip()]
        unknown = [n for n in names if n not in FAULTS_BY_NAME]
        if unknown:
            parser.error(f"unknown fault(s): {', '.join(unknown)}")
        faults = [FAULTS_BY_NAME[n] for n in names]

    paths = args.programs or sorted(glob.glob(os.path.join(ROOT, "programs", "*.nc")))
    if not paths:
        parser.error("no programs: none given and none in programs/")

    from vibe_cnc.settings_manager import SettingsManager
    settings = SettingsManager(os.path.join(ROOT, "config.yaml"))
    ai = settings.data.setdefault("ai", {})
    if args.mode:
        ai["mode"] = args.mode          # in memory only; config.yaml is not written
    if args.model:
        from vibe_cnc.claude_client import SECTION_OF, backend_of
        if backend_of(ai) not in SECTION_OF:
            parser.error(f"ai.mode '{backend_of(ai)}' is not known; use --mode")
        ai.setdefault(SECTION_OF[backend_of(ai)], {})["model"] = args.model
    mode, model = _model_of(settings)

    cases, left_out = build_cases(load_programs(paths), LintEngine(settings), faults)
    if args.no_clean:
        cases = [c for c in cases if c.fault != CLEAN]
    if not cases:
        print(describe_plan(cases, left_out, []))
        return 1

    policies = read_policies(os.path.join(ROOT, "policies.md"))
    tools_json = load_tools_json()
    prompts = [case_prompt(c, policies, tools_json) for c in cases]

    if not args.run:
        print(describe_plan(cases, left_out, [len(p) for p in prompts]))
        if args.show_prompt:
            print(f"\n--- prompt of {cases[0].program} {cases[0].fault} ---\n{prompts[0]}")
        calls = len(cases) * args.repeat
        print(f"\nNothing was sent. --run asks {model or '(no model set)'} ({mode}) {calls} time(s).")
        return 0

    if not model:
        parser.error(f"no model for {mode}: set it in config.yaml (Settings → AI) or pass --model")
    out_path = args.out or os.path.join(
        OUT_DIR, f"{datetime.now():%Y%m%d-%H%M%S}-{re.sub(r'[^A-Za-z0-9.-]+', '_', model)}.jsonl")
    print(f"Asking {model} ({mode}) {len(cases) * args.repeat} time(s); answers go to {out_path}")
    from vibe_cnc.claude_client import AIClient
    records = run(cases, AIClient(settings), mode, model, args.repeat, out_path,
                  args.keep_going)
    print()
    print(summarize(records))
    return 0


if __name__ == "__main__":
    sys.exit(main())
