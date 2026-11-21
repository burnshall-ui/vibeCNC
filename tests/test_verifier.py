"""The verifier loop: draft, lint, correct.

The model is a script of answers; the linter is the real one. GUI-free, so it
runs in the core job on a bare interpreter.
"""
import unittest
from types import SimpleNamespace

from vibe_cnc import verifier
from vibe_cnc.diagnostics import Diagnostic
from vibe_cnc.lint_engine import LintEngine
from vibe_cnc.verifier import (
    CLEAN, FAILED, MAX_CORRECTIONS, NO_CODE, OUT_OF_ROUNDS, REPEATED, change, code_blocks,
    dropped, fingerprint, the_code, verify,
)

SHOP = {
    "require_header_codes": ["G18", "G40", "G80", "G97"],
    "require_units": "G21",
    "require_origin": "G54",
    "protected_m_codes": [62, 63],
}

# From the G76 card in docs/PLAN.md, with the minimum cut written wrong.
G76_POINT = "G00 X24. Z5.\nG76 P020060 Q0.4 R0.05\nG76 X16.93 Z-30. P1534 Q400 F2.5"
G76_RIGHT = "G00 X24. Z5.\nG76 P020060 Q100 R0.05\nG76 X16.93 Z-30. P1534 Q400 F2.5"

PROGRAM = """%
O1000
G18 G21 G40 G80 G97 G54
T0101
G50 S2000
G96 S200 M03
G00 X24. Z5.
G76 P020060 Q100 R0.05
G76 X16.93 Z-30. P1534 Q400 F2.5
G00 Z50.
X200.
M30
%"""


def answer(code, note="Thread M20x2.5."):
    return f"```gcode\n{code}\n```\n{note}"


class Model:
    """Answers from a script, and records every conversation it was sent."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.sent = []

    def chat(self, messages, system=None):
        self.sent.append(([dict(m) for m in messages], system))
        reply = self.answers.pop(0)
        return reply if isinstance(reply, tuple) else (True, reply)


def linter(**machine):
    return LintEngine(SimpleNamespace(data={"policies": SHOP, "machine": machine}))


class TheLoopTests(unittest.TestCase):
    def test_a_decimal_point_in_g76_q_is_corrected_in_the_second_round(self):
        model = Model(answer(G76_POINT), answer(G76_RIGHT))

        outcome = verify(model, "Cut an M20x2.5 thread, 30 long.", "Be a CNC programmer.", linter())

        self.assertTrue(outcome.lint_clean)
        self.assertEqual(outcome.stop, CLEAN)
        self.assertEqual(outcome.corrections, 1)
        self.assertIs(outcome.best, outcome.attempts[1])
        self.assertEqual([d.rule for d in outcome.draft.findings], ["PQ_DECIMAL_POINT"])

    def test_the_correction_is_asked_for_in_the_same_conversation(self):
        model = Model(answer(G76_POINT), answer(G76_RIGHT))

        verify(model, "Cut an M20x2.5 thread.", "Be a CNC programmer.", linter())

        (first, system1), (second, system2) = model.sent
        self.assertEqual(system1, "Be a CNC programmer.")
        self.assertEqual(system2, "Be a CNC programmer.")
        self.assertEqual([m["role"] for m in first], ["user"])
        self.assertTrue(first[0]["content"].startswith("Cut an M20x2.5 thread."))
        self.assertIn("Exactly one code block", first[0]["content"])
        self.assertEqual([m["role"] for m in second], ["user", "assistant", "user"])
        self.assertEqual(second[1]["content"], answer(G76_POINT))
        correction = second[2]["content"]
        self.assertIn("Line 2, ERROR PQ_DECIMAL_POINT", correction)
        self.assertIn("Q0.4", correction)
        self.assertIn("Fix: Write Q400.", correction)

    def test_a_clean_draft_is_not_sent_back(self):
        model = Model(answer(G76_RIGHT))

        outcome = verify(model, "Thread", None, linter())

        self.assertTrue(outcome.lint_clean)
        self.assertEqual(outcome.corrections, 0)
        self.assertEqual(len(model.sent), 1)
        self.assertEqual(change(outcome), "")

    def test_a_hint_alone_is_no_reason_for_a_correction(self):
        # G76_CHECK is INFO: no F, S or R on a G76 block.
        code = "G00 X24. Z5.\nG76 P020060 Q100\nG76 X16.93 Z-30. P1534 Q400 F2.5"
        model = Model(answer(code))

        outcome = verify(model, "Thread", None, linter())

        self.assertEqual([d.severity for d in outcome.best.findings], ["INFO"])
        self.assertTrue(outcome.lint_clean)
        self.assertEqual(len(model.sent), 1)

    def test_a_correction_with_the_same_findings_stops_the_loop(self):
        # One line more above it: the finding moved, it did not change.
        model = Model(answer(G76_POINT), answer("(M20x2.5)\n" + G76_POINT))

        outcome = verify(model, "Thread", None, linter())

        self.assertEqual(outcome.stop, REPEATED)
        self.assertEqual(len(model.sent), 2)
        self.assertFalse(outcome.lint_clean)
        self.assertIs(outcome.best, outcome.draft)      # no better, so not taken

    def test_a_changed_value_is_a_changed_finding(self):
        model = Model(answer(G76_POINT), answer(G76_POINT.replace("Q0.4", "Q0.5")),
                      answer(G76_RIGHT))

        outcome = verify(model, "Thread", None, linter())

        self.assertEqual(outcome.stop, CLEAN)
        self.assertEqual(outcome.corrections, 2)

    def test_it_stops_after_three_corrections(self):
        model = Model(*(answer(G76_POINT.replace("Q0.4", f"Q0.{n}")) for n in range(4, 9)))

        outcome = verify(model, "Thread", None, linter())

        self.assertEqual(MAX_CORRECTIONS, 3)
        self.assertEqual(outcome.stop, OUT_OF_ROUNDS)
        self.assertEqual(outcome.corrections, 3)
        self.assertEqual(len(model.sent), 4)
        self.assertFalse(outcome.lint_clean)

    def test_the_best_attempt_has_the_fewest_errors_then_the_fewest_warnings(self):
        # Draft: no header at all. First correction: G40 and G21 added
        # (two ERRORs fewer), G80 forgotten. Second: worse again.
        bare = PROGRAM.replace("G18 G21 G40 G80 G97 G54", "G97")
        better = PROGRAM.replace("G18 G21 G40 G80 G97 G54", "G21 G40 G97")
        worse = PROGRAM.replace("G18 G21 G40 G80 G97 G54", "G40 G97")
        model = Model(answer(bare), answer(better), answer(worse), answer(worse + "\n(again)"))

        outcome = verify(model, "Program", None, linter())

        self.assertIs(outcome.best, outcome.attempts[1])
        self.assertEqual(outcome.best.code, better)
        self.assertFalse(outcome.lint_clean)

    def test_the_diff_runs_from_the_draft_to_the_result(self):
        model = Model(answer(G76_POINT), answer(G76_RIGHT))

        diff = change(verify(model, "Thread", None, linter()))

        self.assertIn("--- draft", diff)
        self.assertIn("+++ result", diff)
        self.assertIn("-G76 P020060 Q0.4 R0.05", diff)
        self.assertIn("+G76 P020060 Q100 R0.05", diff)


class DeleteGuardTests(unittest.TestCase):
    def test_a_correction_that_deletes_the_cycle_is_rejected_and_the_model_told_why(self):
        model = Model(answer(G76_POINT), answer("G00 X24. Z5."), answer(G76_RIGHT))

        outcome = verify(model, "Thread", None, linter())

        self.assertTrue(outcome.attempts[1].rejected)
        self.assertEqual(outcome.attempts[1].dropped, ("G76",))
        self.assertEqual(outcome.rejections, 1)
        rejection = model.sent[2][0][-1]["content"]
        self.assertIn("not taken", rejection)
        self.assertIn("G76", rejection)
        self.assertIn("Q0.4", rejection)        # what is still to be corrected
        self.assertEqual(outcome.stop, CLEAN)
        self.assertIs(outcome.best, outcome.attempts[2])

    def test_a_rejected_correction_is_never_the_result(self):
        # Lint clean, because the cycle with the fault is gone.
        model = Model(answer(G76_POINT), *[answer("G00 X24. Z5.")] * 3)

        outcome = verify(model, "Thread", None, linter())

        self.assertEqual(outcome.stop, OUT_OF_ROUNDS)
        self.assertEqual(outcome.rejections, 3)
        self.assertIs(outcome.best, outcome.draft)
        self.assertFalse(outcome.lint_clean)

    def test_one_block_of_a_two_block_cycle_counts(self):
        self.assertEqual(dropped(G76_RIGHT, "G00 X24. Z5.\nG76 X16.93 Z-30. P1534 Q400 F2.5", True),
                         ("1 of its 2 G76 blocks",))

    def test_tools_protected_m_codes_and_the_program_end_count(self):
        draft = "T0101\nG00 X30.\nM62\nT0303\nG00 X40.\nM30"
        self.assertEqual(dropped(draft, "T0101\nG00 X30.\nG00 X40.\n", False, protected=[62, 63]),
                         ("T0303", "M62", "the program end (M30)"))

    def test_what_is_only_in_a_comment_is_not_kept(self):
        self.assertEqual(dropped("G00 X30. (then G71, T0505)\nG01 Z-5.", "G00 X30.\nG01 Z-5.", True),
                         ())

    def test_a_snippet_need_not_end_the_program(self):
        self.assertEqual(dropped(G76_RIGHT, G76_RIGHT, True), ())

    def test_g90_g92_and_g94_are_cycles_too(self):
        # G-code system A: turning, threading and facing in one block.
        draft = "G90 X30. Z-20. F0.2\nG92 X19.2 Z-30. F2.5\nG94 X0 Z-1. F0.1"
        self.assertEqual(dropped(draft, "G01 X30. Z-20. F0.2", True), ("G90", "G92", "G94"))

    def test_g92_as_another_systems_speed_limit_may_become_g50(self):
        # What the linter's G50_BEFORE_G96 hint asks for; a bare G90 in the
        # header is no cycle either.
        draft = "G90 G21 G40\nG92 S2500\nG96 S180 M03\nG00 X60. Z2."
        self.assertEqual(dropped(draft, "G21 G40\nG50 S2500\nG96 S180 M03\nG00 X60. Z2.", True), ())

    def test_the_protected_m_codes_come_from_the_linter(self):
        draft = "G00 X30. Z2.\nM62\n" + G76_POINT
        model = Model(answer(draft), answer(G76_RIGHT), answer("G00 X30. Z2.\nM62\n" + G76_RIGHT))

        outcome = verify(model, "Thread", None, linter())

        self.assertEqual(outcome.attempts[1].dropped, ("M62",))
        self.assertEqual(outcome.stop, CLEAN)


class SnippetTests(unittest.TestCase):
    def test_code_without_m30_is_linted_as_a_snippet(self):
        outcome = verify(Model(answer(G76_RIGHT)), "Thread", None, linter())

        self.assertTrue(outcome.snippet)
        self.assertTrue(outcome.lint_clean)      # no header asked of it

    def test_a_program_is_linted_as_one(self):
        bare = PROGRAM.replace("G18 G21 G40 G80 G97 G54", "G97")
        outcome = verify(Model(answer(bare), answer(PROGRAM)), "Program", None, linter())

        self.assertFalse(outcome.snippet)
        self.assertIn("HEADER_CODE", {d.rule for d in outcome.draft.findings})
        self.assertTrue(outcome.lint_clean)

    def test_a_program_stays_a_program_when_a_correction_drops_m30(self):
        # Without M30 the retract rule has nothing to check; dropping it is
        # not a way to get rid of the finding.
        no_retract = PROGRAM.replace("G00 Z50.\nX200.\n", "G00 X30.\nX200.\nM05\n")
        model = Model(answer(no_retract), answer(no_retract.replace("M30\n", "")), answer(PROGRAM))

        outcome = verify(model, "Program", None, linter())

        self.assertEqual(outcome.attempts[1].dropped, ("the program end (M30)",))
        self.assertEqual(outcome.stop, CLEAN)


class CollisionTests(unittest.TestCase):
    def test_a_move_into_the_chuck_is_sent_back_as_a_finding(self):
        into = "G00 X40. Z2.\nG01 Z-120. F0.2\nG00 X250.\nZ2."
        clear = "G00 X40. Z2.\nG01 Z-90. F0.2\nG00 X250.\nZ2."
        model = Model(answer(into), answer(clear))

        outcome = verify(model, "Turn Ø40", None,
                         linter(chuck_z_limit=-100.0, chuck_diameter=210.0))

        self.assertIn("CHUCK_COLLISION", model.sent[1][0][-1]["content"])
        self.assertTrue(outcome.lint_clean)


class AnswerTests(unittest.TestCase):
    def test_an_answer_without_a_code_block_is_shown_as_it_came(self):
        model = Model("G00 X30.\nG01 Z-5. F0.2\nThat should do.")

        outcome = verify(model, "Turn", None, linter())

        self.assertEqual(outcome.stop, NO_CODE)
        self.assertIsNone(outcome.best)
        self.assertFalse(outcome.lint_clean)
        self.assertEqual(outcome.draft.answer, "G00 X30.\nG01 Z-5. F0.2\nThat should do.")
        self.assertEqual(len(model.sent), 1)

    def test_two_code_blocks_are_not_guessed_between(self):
        model = Model(answer(G76_RIGHT) + "\nOr:\n" + answer(G76_RIGHT.replace("Q100", "Q150")))

        outcome = verify(model, "Thread", None, linter())

        self.assertEqual(outcome.stop, NO_CODE)
        self.assertEqual(outcome.draft.blocks, 2)
        self.assertFalse(outcome.lint_clean)

    def test_a_correction_without_a_code_block_ends_with_the_best_so_far(self):
        model = Model(answer(G76_POINT), "I cannot fix that.")

        outcome = verify(model, "Thread", None, linter())

        self.assertEqual(outcome.stop, NO_CODE)
        self.assertIs(outcome.best, outcome.draft)

    def test_a_backend_failure_on_the_draft_is_passed_on(self):
        outcome = verify(Model((False, "❌ no credit")), "Thread", None, linter())

        self.assertEqual(outcome.stop, FAILED)
        self.assertEqual(outcome.error, "❌ no credit")
        self.assertEqual(outcome.attempts, ())
        self.assertIsNone(outcome.best)
        self.assertFalse(outcome.lint_clean)

    def test_a_backend_failure_later_keeps_what_there_is(self):
        outcome = verify(Model(answer(G76_POINT), (False, "❌ rate limit")), "Thread", None, linter())

        self.assertEqual(outcome.stop, FAILED)
        self.assertEqual(outcome.error, "❌ rate limit")
        self.assertIs(outcome.best, outcome.draft)


class CodeBlockTests(unittest.TestCase):
    def test_a_block_with_or_without_a_language_name(self):
        for fence in ("```", "```gcode", "```nc", "  ```"):
            with self.subTest(fence=fence):
                self.assertEqual(code_blocks(f"Here:\n{fence}\nG00 X30.\nM30\n```\nDone."),
                                 ["G00 X30.\nM30"])

    def test_an_unclosed_block_is_no_block(self):
        # What an answer cut off at the output limit looks like.
        self.assertEqual(the_code("```\nG00 X30.\nG01 Z-"), (None, 0))

    def test_an_empty_block_is_no_code(self):
        self.assertEqual(the_code("```\n\n```"), (None, 1))

    def test_backticks_inside_a_line_open_no_block(self):
        self.assertEqual(code_blocks("Write `Q400`, not ```Q0.4``` -- see above."), [])


class FingerprintTests(unittest.TestCase):
    def finding(self, line, rule="PQ_DECIMAL_POINT", severity="ERROR", token="Q0.4"):
        return Diagnostic(line, severity, rule, f"{token} has a decimal point.", token=token)

    def test_the_line_does_not_count(self):
        self.assertEqual(fingerprint([self.finding(2)]), fingerprint([self.finding(3)]))

    def test_the_token_does(self):
        self.assertNotEqual(fingerprint([self.finding(2)]), fingerprint([self.finding(2, token="Q0.5")]))

    def test_how_many_there_are_does(self):
        self.assertNotEqual(fingerprint([self.finding(2)]), fingerprint([self.finding(2)] * 2))

    def test_a_hint_does_not(self):
        self.assertEqual(fingerprint([self.finding(2)]),
                         fingerprint([self.finding(2), self.finding(5, "G76_CHECK", "INFO", None)]))


class PromptTests(unittest.TestCase):
    def test_the_example_in_the_answer_format_has_one_block_the_reader_finds(self):
        # The example is what the model copies; the reader must take it.
        code, blocks = the_code(verifier.ANSWER_FORMAT)

        self.assertEqual(blocks, 1)
        self.assertEqual(code, "G71 U1.0 R0.5\nG71 P10 Q20 U0.4 W0.1 F0.25")

    def test_the_request_comes_first(self):
        self.assertTrue(verifier.generation_request("  Cut a thread.  ").startswith("Cut a thread.\n"))


if __name__ == "__main__":
    unittest.main()
