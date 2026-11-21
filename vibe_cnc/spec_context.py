# spec_context.py — the cycle cards and the dialect, as text for a prompt.
# GUI-free, standard library only.
#
# A model is shown the cards of the cycles a request or a program is about,
# and a short sheet of the control's codes. The linter checks against the
# same cards, so what a model is told and what its code is checked against
# come from one place. Which cards is decided here, by a plain search, the
# same for every backend; no tool calling. See docs/PLAN.md, step C.
import re
from typing import Iterable, List, Optional, Sequence

from .diagnostics import Diagnostic, blocking
from .lint_engine import code_only
from .specs import CycleCard, Dialect, Specs, Word

# At most this many cards go into one message.
MAX_CARDS = 3

# A G-code word, also as G071 and run together as in N10G71; not the 71 of X71.
_G_WORD = re.compile(r"(?<![A-Z])G0*(\d+)(?!\d)", re.IGNORECASE)

HEADER = ("Reference from this simulator's spec files: the control's codes, and the cards of "
          "the cycles concerned. They were checked against the FANUC 0i-F lathe operator's "
          "manual (B-64604EN-1/01); [manual ...] names the section that says so. What is "
          "marked not verified was not checked on the machine.")


# ---- which cards -------------------------------------------------------------------

def _codes(text: str, specs: Specs) -> List[str]:
    """The cycles with a card that the text names, in the order it first names them."""
    found = []
    for match in _G_WORD.finditer(text):
        code = f"G{int(match.group(1)):02d}"
        if code in specs.cycles and code not in found:
            found.append(code)
    return found


def _keyword_pattern(keyword: str) -> re.Pattern:
    """A keyword as a whole word, or whole words with any space between them."""
    words = r"\s+".join(re.escape(w) for w in keyword.split())
    return re.compile(rf"(?<!\w){words}(?!\w)", re.IGNORECASE)


def _by_keywords(text: str, specs: Specs) -> List[str]:
    """The cycles whose keywords are in the text, in the order they come.

    Where keywords of two cards overlap, the longer one decides: "face
    groove" is G74's, and the "groove" in it does not bring G75 along.
    """
    hits = []
    for card in specs.cycles.values():
        for keyword in card.keywords:
            for match in _keyword_pattern(keyword).finditer(text):
                hits.append((match.start(), -(match.end() - match.start()), match.end(), card.code))
    hits.sort()
    found, covered = [], -1
    for start, _, end, code in hits:
        if start < covered:
            continue
        covered = end
        if code not in found:
            found.append(code)
    return found


def cards_for_request(request: str, specs: Specs, limit: int = MAX_CARDS) -> List[CycleCard]:
    """The cards a request asks about. A request that names a cycle gets the
    cycles it names; one that names none gets the cycles its keywords point
    to. An operator who writes G71 has chosen it, and "the face at Z0" in the
    same request does not make it about facing."""
    codes = _codes(request, specs) or _by_keywords(request, specs)
    return [specs.cycles[code] for code in codes[:limit]]


def cards_for_program(program: str, specs: Specs, limit: int = MAX_CARDS) -> List[CycleCard]:
    """The cards of the cycles a program runs, by their code words. Comments
    do not count: "(THREAD M20)" over a G92 is no G76."""
    code = "\n".join(code_only(line) for line in program.splitlines())
    return [specs.cycles[c] for c in _codes(code, specs)[:limit]]


def cards_for_findings(findings: Iterable[Diagnostic], specs: Specs,
                       sent: Iterable[str] = (), limit: int = MAX_CARDS) -> List[CycleCard]:
    """The cards of the cycles the blocking findings name that were not sent yet."""
    sent = set(sent)
    found = []
    for d in sorted(blocking(findings), key=lambda d: d.line):
        code = d.related_code
        if code in specs.cycles and code not in sent and code not in found:
            found.append(code)
    return [specs.cycles[code] for code in found[:limit]]


# ---- as text -----------------------------------------------------------------------

def _checked(verified: bool, verified_on: str = "") -> str:
    return f"verified: {verified_on}" if verified else "not verified"


def _range(word: Word) -> List[str]:
    out = []
    if word.min_exclusive is not None:
        out.append(f"above {word.min_exclusive:g}")
    if word.min is not None:
        out.append(f"at least {word.min:g}")
    if word.max is not None:
        out.append(f"at most {word.max:g}")
    return out


def _either(letters: Sequence[str]) -> str:
    """X, U, Z or W."""
    return letters[0] if len(letters) == 1 else f"{', '.join(letters[:-1])} or {letters[-1]}"


def _word_line(word: Word) -> str:
    details = ([word.unit] if word.unit else []) + _range(word)
    if word.form:
        details.append(f"packed as {word.form}")
    if not word.decimal_point:
        details.append("no decimal point")
    line = f"  {word.letter}: {word.meaning}" + (f" ({', '.join(details)})" if details else "")
    for part in word.parts:
        allowed = part.allowed()
        line += (f"\n    {part.letter * word.form.count(part.letter)}: {part.meaning}"
                 + (f" ({allowed})" if allowed else ""))
    return line


def card_text(card: CycleCard) -> str:
    """One card as a model reads it: the blocks and their words, the
    simulator's assumptions, the examples."""
    lines = [f"{card.code} -- {card.name} ({_checked(card.source.verified, card.source.verified_on)})"]
    for index, block in enumerate(card.blocks):
        title = f"Block {index + 1}" if len(card.blocks) > 1 else "Block"
        if block.recognized_by:
            title += f", the one with {_either(block.recognized_by)}"
        lines.append(f"{title}: {card.code} {' '.join(block.words)}")
        lines += [_word_line(word) for word in block.words.values()]
    if card.assumptions:
        none_verified = not any(a.verified for a in card.assumptions)
        lines.append("How this simulator reads it" + (" (not verified):" if none_verified else ":"))
        lines += [f"  - {a.text}"
                  + (f" [manual {a.manual}]" if a.manual else " [not in the manual]")
                  + ("" if a.verified or none_verified else " (not verified)")
                  for a in card.assumptions]
    for example in card.examples:
        lines.append(f"Example, {example.title}:")
        lines.append(example.code)
    return "\n".join(lines)


def dialect_text(dialect: Dialect) -> str:
    """The control's codes, one line each, and the notes on what models get wrong."""
    lines = [f"Control: {dialect.name} ({_checked(dialect.source.verified, dialect.source.verified_on)})"]
    lines += [f"- {note}" for note in dialect.notes]
    lines.append("G-codes:")
    lines += [f"  {code} {text}" for code, text in dialect.g_codes.items()]
    lines.append("M-codes:")
    lines += [f"  {code} {text}" for code, text in dialect.m_codes.items()]
    if dialect.machine_specific:
        lines.append(f"What {', '.join(dialect.machine_specific)} do is up to the machine builder.")
    return "\n".join(lines)


def reference(cards: Sequence[CycleCard], dialect: Optional[Dialect] = None) -> str:
    """The text that goes into a prompt: the header, the dialect sheet when
    given, the cards. Empty when there is neither."""
    parts = ([dialect_text(dialect)] if dialect is not None else []) + [card_text(c) for c in cards]
    if not parts:
        return ""
    return "\n\n".join([HEADER] + parts)


def review_reference(program: str, specs: Optional[Specs]) -> str:
    """What a review of this code is given: the sheet and the cards of its cycles."""
    if specs is None:
        return ""
    return reference(cards_for_program(program, specs), specs.dialect)
