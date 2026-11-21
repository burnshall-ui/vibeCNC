# analysis_prompt.py — what AI ANALYZE sends to the model. GUI-free.
#
# It lived inside the main window. It is here so the review eval
# (evals/review_eval.py) sends the very text the app sends: an eval that
# measured a prompt of its own would say nothing about the button.
import json
import os

from .spec_context import review_reference

# The most of the program a review is sent; a longer one is cut here.
MAX_CODE_CHARS = 8000

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


def build_analysis_prompt(snippet: str, policies: str, tools_json: dict, specs=None) -> str:
    """The user message of a review: machine, shop rules, tools, the code.

    specs: the spec files (specs.load_specs()), or None. With them the
    review carries the dialect sheet and the cards of the cycles the code
    runs, found by their code words. The app always passes them; None is
    for the eval, to measure what they change.

    No material is named. The program says it in its own header, as the
    policies ask; a fixed "Material: 42CrMo4" here contradicted every program
    cut from anything else, and a model then proposed to change the program's
    material comment to match the prompt.
    """
    reference = review_reference(snippet, specs)
    return (f"Machine: FANUC 0i-TF\nPolicies:\n{policies}\n"
            f"Tools(JSON):\n{json.dumps(tools_json, ensure_ascii=False)}\n\n"
            + (f"{reference}\n\n" if reference else "") +
            f"Code:\n```\n{snippet}\n```\n\n"
            "Task: List rule violations (line/rule/fix). Optional unified diff. "
            f"No cosmetic changes.\n\n{FORMAT_RULES}")
