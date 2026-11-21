# analysis_prompt.py — what AI ANALYZE sends to the model. GUI-free.
#
# It lived inside the main window. It is here so the review eval
# (evals/review_eval.py) sends the very text the app sends: an eval that
# measured a prompt of its own would say nothing about the button.
import json
import os
from dataclasses import dataclass

from .spec_context import review_reference

# The most of the program a review is sent; a longer one is cut here.
MAX_CODE_CHARS = 8000


@dataclass(frozen=True)
class Shown:
    """The part of a program a review is sent: whole lines from the top."""
    code: str
    lines: int              # how many lines of it are sent
    total: int              # how many the program has

    @property
    def cut(self) -> bool:
        return self.lines < self.total


def code_for_review(code: str) -> Shown:
    """As many whole lines from the top as fit into MAX_CODE_CHARS.

    It was code[:MAX_CODE_CHARS], cut wherever the count ran out -- in the
    middle of a block, and without a word: the linter read the whole program,
    the model only its start, and neither the operator nor the model knew.
    """
    lines = code.splitlines()
    sent, size = 0, -1
    for line in lines:
        size += len(line) + 1            # the newline before it, but the first
        if size > MAX_CODE_CHARS:
            break
        sent += 1
    if sent == len(lines):
        return Shown(code, sent, len(lines))
    return Shown("\n".join(lines[:sent]), sent, len(lines))


def cut_note(shown: Shown) -> str:
    """For the operator: what part of the program the AI was sent. Empty when
    it was sent all of it."""
    if not shown.cut:
        return ""
    return (f"The AI sees lines 1–{shown.lines} of {shown.total} only (the first "
            f"{MAX_CODE_CHARS} characters); the linter checked all of them. Select "
            "a part of the program to have the AI read that instead.")


FORMAT_RULES = """
IMPORTANT - Formatting:
- Use HTML tags for structure (displayed in HTML widget)
- Headings: <b>Heading</b>
- Paragraphs: Double line breaks <br><br>
- Lists: Bullet points with • or numbered (1., 2., ...)
- Code blocks: <pre style='color:#6CFF6C; background:#0A0A0A; padding:4px;'>code</pre>
- NO Markdown (**, ##, etc.) - HTML only!

Example good response:
<b>Analysis Result:</b><br><br>
• Line 5: G21 missing in header<br>
• Line 12: Set G40 before tool change<br><br>
<b>Recommended Fixes:</b><br>
1. Extend header with G21<br>
2. Insert G40 before T-code
"""


def read_policies(path: str) -> str:
    """policies.md as text, or a placeholder when it is not there."""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return "No policies found."


def build_analysis_prompt(snippet: str, policies: str, tools_json: dict, specs=None,
                          total_lines: int = 0) -> str:
    """The user message of a review: machine, shop rules, tools, the code.

    specs: the spec files (specs.load_specs()), or None. With them the
    review carries the dialect sheet and the cards of the cycles the code
    runs, found by their code words. The app always passes them; None is
    for the eval, to measure what they change.

    total_lines: how many lines the program has, when snippet is only its
    start (code_for_review). The model is told, or it reports the M30 and
    the retract that are merely past the cut as missing.

    No material is named. The program says it in its own header, as the
    policies ask; a fixed "Material: 42CrMo4" here contradicted every program
    cut from anything else, and a model then proposed to change the program's
    material comment to match the prompt.
    """
    reference = review_reference(snippet, specs)
    shown = len(snippet.splitlines())
    cut = (f"Only lines 1-{shown} of the program's {total_lines} are shown; the rest "
           "follows after them. Do not report as missing what the rest may hold "
           "(the retract, M30).\n\n" if total_lines > shown else "")
    return (f"Machine: FANUC 0i-TF\nPolicies:\n{policies}\n"
            f"Tools(JSON):\n{json.dumps(tools_json, ensure_ascii=False)}\n\n"
            + (f"{reference}\n\n" if reference else "") +
            f"Code:\n```\n{snippet}\n```\n\n{cut}"
            "Task: List rule violations (line/rule/fix). Optional unified diff. "
            f"No cosmetic changes.\n\n{FORMAT_RULES}")
