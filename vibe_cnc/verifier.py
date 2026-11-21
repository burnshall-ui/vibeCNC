# verifier.py — a model drafts G-code, the linter checks it, and the model is
# sent what the linter found until nothing is left or the rounds run out.
# GUI-free: the client and the linter are passed in.
#
# The best result is "lint clean": the linter finds nothing it knows about.
# That is not "verified". The linter knows a handful of rule families and
# does not see everything a person must; a person still reads every line
# before it reaches a machine. See docs/PLAN.md, step A.
import difflib
import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

from . import spec_context
from .diagnostics import Diagnostic, blocking, is_lint_clean, weight
from .lint_engine import code_only, is_snippet


# The turns of a conversation as AIClient.chat takes them. Written out here
# rather than imported: ai_client needs requests, and this module runs on a
# bare interpreter with the linter.
def user(content: str) -> dict:
    return {"role": "user", "content": content}


def assistant(content: str) -> dict:
    return {"role": "assistant", "content": content}

# Corrections after the draft. Each is one more request to the model.
MAX_CORRECTIONS = 3

# Why the loop ended.
CLEAN = "lint_clean"            # the last accepted attempt has no ERROR and no WARNING
NO_CODE = "no_code_block"       # an answer had no code block, or more than one
REPEATED = "repeated"           # a correction came back with findings an earlier attempt had
OUT_OF_ROUNDS = "out_of_rounds"
FAILED = "backend_failed"       # the backend answered with an error

ANSWER_FORMAT = """

Answer format -- the code is read out of your answer and checked by a linter:
- Exactly one code block holding all the G-code, with a line of three backticks before and after it.
- Inside it nothing but G-code, and comments in parentheses.
- After it a short explanation in plain text: what the blocks do, and what you assumed.
- No second code block, not even for one line in the explanation; name the word instead (for example Q400).

Example:
```
G71 U1.0 R0.5
G71 P10 Q20 U0.4 W0.1 F0.25
```
U1.0 R0.5: depth of cut per pass (radius) and retract.
P10 Q20: first and last block of the contour.
U0.4 W0.1: finishing allowance in X (diameter) and Z.
F0.25: feed.
"""

_SAME_FORMAT = ("Answer in the same format: the whole code again in one code block, then a short "
                "note of what you changed. Line numbers count the lines of your code block from 1.")

# A fenced block: a line of three backticks, perhaps with a language name,
# then everything up to the next line of three backticks.
FENCE = re.compile(r"^[ \t]*```[^\n`]*\n(.*?)^[ \t]*```[ \t]*$", re.MULTILINE | re.DOTALL)

# What the delete guard counts. The cycles of G-code system A: G70-G76, and
# G90, G92 and G94 (turning, threading and facing in one block). A digit may
# come before the G -- N10G71 is a legal block.
_CYCLE = re.compile(r"(?<![A-Z])G(7[0-6]|9[024])(?!\d)")
# G90, G92 and G94 are a cycle only on a block with a target. Without one
# they are another G-code system's words a model brings along -- "G92 S2500"
# for the speed limit, "G90" for absolute -- and replacing "G92 S2500" with
# the G50 S2500 the linter asks for must not count as dropping a thread.
_TARGET = re.compile(r"(?<![A-Z])[XZUW][-+]?[\d.]")
_TOOL = re.compile(r"(?<![A-Z])T(\d+)")
_M_CODE = re.compile(r"(?<![A-Z])M0*(\d+)(?!\d)")


def generation_request(text: str, reference: str = "") -> str:
    """The operator's request with the answer format the code is read out by,
    after the reference from the spec files when there is one."""
    if not reference:
        return text.strip() + ANSWER_FORMAT
    return f"{reference}\n\nRequest: {text.strip()}{ANSWER_FORMAT}"


def code_blocks(answer: str) -> List[str]:
    """Every fenced code block of an answer, without the fences."""
    return [block.strip("\n") for block in FENCE.findall(answer or "")]


def the_code(answer: str) -> Tuple[Optional[str], int]:
    """(the code, how many blocks there were). The code is None unless there
    is exactly one block with something in it: which of two blocks is the
    program cannot be told, and an empty one is not code."""
    blocks = code_blocks(answer)
    if len(blocks) != 1 or not blocks[0].strip():
        return None, len(blocks)
    return blocks[0], 1


def fingerprint(findings: Iterable[Diagnostic]) -> tuple:
    """What makes two attempts' findings the same: rule, token and message of
    each ERROR and WARNING, but not the line, which moves when a line is added
    above. The messages carry the values (R0.5, Q0.4), so a changed value is a
    changed finding."""
    return tuple(sorted((d.rule, d.token or "", d.message) for d in blocking(findings)))


def _inventory(code: str) -> Tuple[Counter, dict, Counter]:
    """Cycle blocks by code, tools by number (with the word as written), and
    M-codes by number, of the code words only."""
    cycles, tools, m_codes = Counter(), {}, Counter()
    for line in code.splitlines():
        words = code_only(line).upper()
        cycles.update(f"G{number}" for number in _CYCLE.findall(words)
                      if number.startswith("7") or _TARGET.search(words))
        for digits in _TOOL.findall(words):
            tools.setdefault(int(digits), f"T{digits}")
        m_codes.update(int(number) for number in _M_CODE.findall(words))
    return cycles, tools, m_codes


def dropped(draft: str, correction: str, snippet: bool, protected=()) -> Tuple[str, ...]:
    """What the draft had and the correction no longer has: cycle blocks,
    tools, protected M-codes, and the program end of a program. A correction
    that fixes a finding by deleting the cycle it is in has fixed nothing.
    A draft that never had an M30 cannot lose it; the linter asks for one."""
    had_cycles, had_tools, had_m = _inventory(draft)
    has_cycles, has_tools, has_m = _inventory(correction)
    gone = []
    for code in sorted(had_cycles):
        need, have = had_cycles[code], has_cycles[code]
        if have == 0:
            gone.append(code)
        elif have < need:
            gone.append(f"{need - have} of its {need} {code} blocks")
    gone.extend(word for number, word in sorted(had_tools.items()) if number not in has_tools)
    for m in sorted({int(code) for code in protected}):
        if has_m[m] < had_m[m]:
            gone.append(f"M{m}")
    if not snippet and not is_snippet(draft) and is_snippet(correction):
        gone.append("the program end (M30)")
    return tuple(gone)


def _listed(findings: Iterable[Diagnostic]) -> str:
    rows = []
    for d in blocking(findings):
        row = f"- Line {d.line}, {d.severity} {d.rule}: {d.message}"
        if d.hint:
            row += f" Fix: {d.hint}"
        rows.append(row)
    return "\n".join(rows)


def correction_request(findings: Iterable[Diagnostic], reference: str = "") -> str:
    """The findings to correct; reference: the cards of cycles they name
    that the model was not given yet."""
    return ("The linter found this in your code:\n" + _listed(findings) + "\n\n"
            + (reference + "\n\n" if reference else "") +
            "Correct exactly these findings. Keep everything else as it is: every cycle, tool, "
            "offset and value the findings do not name. " + _SAME_FORMAT)


def rejection_request(gone: Iterable[str], findings: Iterable[Diagnostic]) -> str:
    """Why a correction was not taken, and what is still to be corrected:
    the findings of the last attempt that was."""
    text = (f"This correction is not taken: it no longer has {', '.join(gone)}, which your first "
            "draft had. A correction must keep every cycle and every tool of the draft. Write the "
            "whole code again with them")
    listed = _listed(findings)
    text += (", and correct only these findings:\n" + listed + "\n\n") if listed else ".\n\n"
    return text + _SAME_FORMAT


@dataclass(frozen=True)
class Attempt:
    answer: str                             # the model's whole answer, as it came
    code: Optional[str]                     # None: not exactly one code block
    blocks: int                             # how many code blocks the answer had
    findings: Tuple[Diagnostic, ...] = ()
    dropped: Tuple[str, ...] = ()           # set: the delete guard rejected it

    @property
    def rejected(self) -> bool:
        return bool(self.dropped)


@dataclass(frozen=True)
class Outcome:
    attempts: Tuple[Attempt, ...]           # the draft first, then every correction
    best: Optional[Attempt]                 # fewest ERRORs, then fewest WARNINGs; the earlier on a tie
    snippet: bool                           # linted as a piece of a program, as the caller asked
    stop: str                               # why the loop ended, one of the names above
    error: Optional[str] = None             # the backend's message when stop is FAILED
    cards: Tuple[str, ...] = ()             # the cycle cards the model was given, in order

    @property
    def lint_clean(self) -> bool:
        return self.best is not None and is_lint_clean(self.best.findings)

    @property
    def draft(self) -> Optional[Attempt]:
        return self.attempts[0] if self.attempts else None

    @property
    def corrections(self) -> int:
        """How many corrections the model was asked for."""
        return max(len(self.attempts) - 1, 0)

    @property
    def rejections(self) -> int:
        return sum(a.rejected for a in self.attempts)


def verify(client, request: str, system: Optional[str], linter,
           corrections: int = MAX_CORRECTIONS, *, snippet: bool, specs=None) -> Outcome:
    """Draft, lint, correct.

    client: anything with chat(messages, system) -> (ok, text), as AIClient.
    linter: anything with run_all(code, snippet=...), as LintEngine; its
    protected_m, if it has one, is kept by the delete guard as well.
    snippet: what was asked for -- a piece that goes into a program, so the
    rules about a whole program (header, spindle start, retract, program
    end) do not apply, or a whole program. The caller says it, not the
    draft: it was read off the draft before, so a "complete program" that
    came back without its M30 was linted as a piece and passed clean.

    Every correction is asked for in the same conversation, so the model
    sees its own earlier answers. A correction is rejected when it drops
    what the draft had (see dropped); the model is told why, and the
    rejection counts as a round. The loop stops when an attempt is lint
    clean, when a correction's findings repeat an earlier attempt's, when
    an answer has no single code block, when the backend fails, or after
    `corrections` rounds.

    specs: the spec files (specs.load_specs()), or None. With them the
    request carries the dialect sheet and the cards of the cycles it is
    about, and a correction the cards of cycles its findings name that the
    model has not had yet. None sends the request as the operator wrote it,
    which the evals use to measure what the cards change.
    """
    cards: List[str] = []
    reference = ""
    if specs is not None:
        chosen = spec_context.cards_for_request(request, specs)
        cards = [c.code for c in chosen]
        reference = spec_context.reference(chosen, specs.dialect)
    conversation = [user(generation_request(request, reference))]
    ok, answer = client.chat(conversation, system)
    if not ok:
        return Outcome((), None, snippet, FAILED, answer, tuple(cards))
    code, blocks = the_code(answer)
    if code is None:
        return Outcome((Attempt(answer, None, blocks),), None, snippet, NO_CODE, cards=tuple(cards))

    protected = getattr(linter, "protected_m", ())
    draft = Attempt(answer, code, blocks, tuple(linter.run_all(code, snippet=snippet)))
    attempts = [draft]
    accepted, last = draft, draft
    seen = {fingerprint(draft.findings)}
    stop, error = None, None

    while stop is None:
        if is_lint_clean(accepted.findings):
            stop = CLEAN
            break
        if len(attempts) - 1 >= corrections:
            stop = OUT_OF_ROUNDS
            break
        if last.rejected:
            feedback = rejection_request(last.dropped, accepted.findings)
        else:
            more = (spec_context.cards_for_findings(accepted.findings, specs, cards)
                    if specs is not None else [])
            cards += [c.code for c in more]
            feedback = correction_request(accepted.findings, spec_context.reference(more))
        conversation += [assistant(last.answer), user(feedback)]
        ok, answer = client.chat(conversation, system)
        if not ok:
            stop, error = FAILED, answer
            break
        code, blocks = the_code(answer)
        if code is None:
            attempts.append(Attempt(answer, None, blocks))
            stop = NO_CODE
            break
        findings = tuple(linter.run_all(code, snippet=snippet))
        last = Attempt(answer, code, blocks, findings, dropped(draft.code, code, snippet, protected))
        attempts.append(last)
        if last.rejected:
            continue
        key = fingerprint(findings)
        if key in seen:
            stop = REPEATED
            break
        seen.add(key)
        accepted = last

    candidates = [a for a in attempts if a.code is not None and not a.rejected]
    best = min(candidates, key=lambda a: weight(a.findings))
    return Outcome(tuple(attempts), best, snippet, stop, error, tuple(cards))


def change(outcome: Outcome) -> str:
    """The unified diff from the draft's code to the best attempt's; empty
    when the best is the draft."""
    if outcome.best is None or outcome.draft is None or outcome.best is outcome.draft:
        return ""
    return "\n".join(difflib.unified_diff(
        outcome.draft.code.splitlines(), outcome.best.code.splitlines(),
        "draft", "result", lineterm=""))
