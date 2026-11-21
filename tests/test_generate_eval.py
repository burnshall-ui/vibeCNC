"""The generate eval: what it records of a run and how it adds it up.

The model is a script; the linter is the real one. GUI-free.
"""
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace

from evals import generate_eval
from vibe_cnc.lint_engine import LintEngine

G76_POINT = "G00 X24. Z5.\nG76 P020060 Q0.4 R0.05\nG76 X16.93 Z-30. P1534 Q400 F2.5"
G76_RIGHT = "G00 X24. Z5.\nG76 P020060 Q100 R0.05\nG76 X16.93 Z-30. P1534 Q400 F2.5"


class Model:
    def __init__(self, *answers):
        self.answers = list(answers)

    def generate_prompt(self):
        return "Write G-code."

    def chat(self, messages, system=None):
        reply = self.answers.pop(0)
        return reply if isinstance(reply, tuple) else (True, reply)


def fenced(code):
    return f"```\n{code}\n```\nDone."


class GenerateEvalTests(unittest.TestCase):
    def run_tasks(self, model, *names):
        tasks = [generate_eval.TASKS_BY_NAME[n] for n in names]
        linter = LintEngine(SimpleNamespace(data={"policies": {}}))
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "out.jsonl")
            with redirect_stdout(StringIO()):
                records = generate_eval.run(tasks, model, linter, "openai", "m", 1, path,
                                            keep_going=True)
            with open(path, encoding="utf-8") as saved:
                lines = [json.loads(line) for line in saved]
        return records, lines

    def test_a_corrected_draft_is_recorded_as_such(self):
        records, saved = self.run_tasks(Model(fenced(G76_POINT), fenced(G76_RIGHT)), "thread_m20")

        record = records[0]
        self.assertEqual(saved, records)            # what is printed is what is kept
        self.assertFalse(record["draft_clean"])
        self.assertTrue(record["result_clean"])
        self.assertEqual(record["corrections"], 1)
        self.assertEqual(record["best"], 1)
        self.assertEqual(record["attempts"][0]["findings"][0]["rule"], "DECIMAL_POINT")
        self.assertEqual(record["stop"], "lint_clean")

    def test_the_summary_counts_drafts_results_rounds_and_the_guard(self):
        model = Model(fenced(G76_RIGHT),                                    # clean at once
                      fenced(G76_POINT), fenced("G00 X24. Z5."), fenced(G76_RIGHT),   # guard, then clean
                      (False, "❌ rate limit"))                              # fails at once
        records, _saved = self.run_tasks(model, "thread_m20", "thread_m20", "groove_g75")

        summary = generate_eval.summarize(records)

        self.assertIn("3 request(s), 1 failed before a draft.", summary)
        self.assertIn("First draft lint clean: 1/2", summary)
        self.assertIn("Result lint clean:      2/2", summary)
        self.assertIn("Delete guard fired:     in 1 request(s), 1 correction(s) rejected", summary)
        self.assertIn("Lint clean is not correct", summary)

    def test_the_findings_left_are_named(self):
        records, _saved = self.run_tasks(Model(fenced(G76_POINT), fenced(G76_POINT)), "thread_m20")

        self.assertIn("Findings left in the results: DECIMAL_POINT 1",
                      generate_eval.summarize(records))

    def test_every_task_has_a_name_of_its_own_and_a_request(self):
        names = [t.name for t in generate_eval.TASKS]
        self.assertEqual(len(names), len(set(names)))
        for task in generate_eval.TASKS:
            self.assertTrue(task.request.strip(), task.name)


if __name__ == "__main__":
    unittest.main()
