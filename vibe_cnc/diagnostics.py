# diagnostics.py — one shape for everything the linter reports. GUI-free.
#
# The linter used to hand out dicts of line, rule and message, with rule a
# label for the operator ("G41/G42" for six different checks). The verifier
# loop needs more: how bad a finding is, so it knows what to correct, and a
# stable name, so it can tell a repeated finding from a new one.
from dataclasses import asdict, dataclass
from typing import Iterable, List, Literal, Optional

Severity = Literal["ERROR", "WARNING", "INFO"]

ERROR: Severity = "ERROR"
WARNING: Severity = "WARNING"
INFO: Severity = "INFO"

# Fewest first when attempts are compared: an ERROR outweighs any number of
# WARNINGs.
_RANK = {ERROR: 0, WARNING: 1, INFO: 2}


@dataclass(frozen=True)
class Diagnostic:
    """One finding. Severity follows the consequence, see docs/PLAN.md:

    ERROR    the control alarms, the machine can crash, or a shop safety rule
             breaks;
    WARNING  a shop rule about form;
    INFO     a hint without a finding.
    """
    line: int                           # 1-based line in the program
    severity: Severity
    rule: str                           # stable id, e.g. "DECIMAL_POINT"
    message: str                        # for the operator
    token: Optional[str] = None         # e.g. "Q0.4"; None where there is nothing to point at
    hint: Optional[str] = None          # a concrete fix, for the operator and the model
    related_code: Optional[str] = None  # e.g. "G76", to fetch its card

    def as_dict(self) -> dict:
        """The fields that are set, for JSON: a model is sent no nulls."""
        return {k: v for k, v in asdict(self).items() if v is not None}


def blocking(diagnostics: Iterable[Diagnostic]) -> List[Diagnostic]:
    """The findings that keep code from being lint clean: ERROR and WARNING."""
    return [d for d in diagnostics if d.severity in (ERROR, WARNING)]


def is_lint_clean(diagnostics: Iterable[Diagnostic]) -> bool:
    """No ERROR and no WARNING. It says the linter found nothing it knows
    about -- not that the program is right."""
    return not blocking(diagnostics)


def errors(diagnostics: Iterable[Diagnostic]) -> List[Diagnostic]:
    """The ERRORs alone: what the control alarms at or the machine crashes on."""
    return [d for d in diagnostics if d.severity == ERROR]


_NAMES = ((ERROR, "error", "errors"), (WARNING, "warning", "warnings"), (INFO, "note", "notes"))


def summary(diagnostics: Iterable[Diagnostic]) -> str:
    """'2 errors, 1 warning', worst first, for a status line or a question.

    The window counted every finding as an error -- three notes read as
    "3 errors found".
    """
    found = list(diagnostics)
    parts = []
    for severity, one, many in _NAMES:
        count = sum(1 for d in found if d.severity == severity)
        if count:
            parts.append(f"{count} {one if count == 1 else many}")
    return ", ".join(parts) or "no findings"


def weight(diagnostics: Iterable[Diagnostic]) -> tuple:
    """(errors, warnings): smaller is better, compared as a tuple."""
    counts = [0, 0, 0]
    for d in diagnostics:
        counts[_RANK[d.severity]] += 1
    return counts[0], counts[1]
