"""The review eval: the faults it puts in, and how it reads an answer.

GUI-free and offline. No model is asked; the answers below are written by hand,
and a fake client stands in for the AI backend.
"""
import contextlib
import io
import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from evals import review_eval as ev
from vibe_cnc.analysis_prompt import FORMAT_RULES, MAX_CODE_CHARS, build_analysis_prompt
from vibe_cnc.lint_engine import LintEngine

SHOP = {
    "require_header_codes": ["G18", "G40", "G80", "G97"],
    "require_units": "G21",
    "require_origin": "G54",
    "protected_m_codes": [62, 63, 64, 65],
}

# Clean for the linter. Line numbers below are counted from this text.
SHAFT = """(SHAFT - TEST)
(Material: C45, REV: A1)
N10 G18 G40 G80 G97 G99
N20 G21 G54
N30 G50 S2500
N40 G00 X100. Z50.
N50 T0101
N60 G96 S150 M03
N70 G00 X42. Z2.
N80 G71 U1.5 R0.5
N90 G71 P100 Q130 U0.4 W0.1 F0.25
N100 G00 X16.
N110 G01 X20. Z-2. F0.15
N120 Z-18.
N130 X40. Z-36.
N140 G70 P100 Q130
N150 M62
N160 G00 Z50.
N170 G00 X100. M05
N180 M30
"""

# Compensation, an arc outside any cycle, a tool change. Clean too.
COMP = """(COMP - TEST)
N10 G18 G40 G80 G97 G99
N20 G21 G54
N30 T0101
N40 G00 X50. Z2.
N50 G42
N60 G01 X40. Z0. F0.2
N70 G01 Z-20.
N80 G02 X44. Z-22. R2.
N90 G40
N100 G00 X100. Z50.
N110 T1111
N120 G00 X50. Z2.
N130 G00 Z50.
N140 G00 X100.
N150 M30
"""


def engine():
    return LintEngine(SimpleNamespace(data={"policies": dict(SHOP)}))


def cases_of(**programs):
    cases, left_out = ev.build_cases(programs, engine())
    return {(c.program, c.fault): c for c in cases}, left_out


class FaultTests(unittest.TestCase):
    """Each fault lands where it should, and the linter reports it there."""

    @classmethod
    def setUpClass(cls):
        cls.cases, cls.left_out = cases_of(SHAFT=SHAFT, COMP=COMP)

    def case(self, program, fault):
        self.assertIn((program, fault), self.cases, f"no {fault} case in {program}")
        return self.cases[(program, fault)]

    def expect(self, program, fault, line, rule, after, where):
        case = self.case(program, fault)
        self.assertIn((line, rule), [(f["line"], f["rule"]) for f in case.expected])
        self.assertEqual(case.after, after)
        self.assertEqual(case.where, where)

    def test_the_test_programs_are_clean(self):
        self.assertEqual(engine().run_all(SHAFT), [])
        self.assertEqual(engine().run_all(COMP), [])

    def test_every_fault_is_used_somewhere(self):
        used = {fault for _, fault in self.cases}
        self.assertEqual(used - {ev.CLEAN}, set(ev.FAULTS_BY_NAME))

    def test_header(self):
        # The linter puts header findings on line 1; the change is on line 3.
        self.expect("SHAFT", "header_g40", 1, "Header", ["N10 G18 G80 G97 G99"], [1, 3])

    def test_spindle_limit(self):
        # G50 S2500 goes from line 5; the G96 it guarded is line 8.
        self.expect("SHAFT", "css_g50", 8, "CSS", ["N30"], [5, 8])

    def test_retract(self):
        self.expect("SHAFT", "no_retract", 20, "Retract", ["N160", "N170 M05"], [18, 19, 20])

    def test_zero_feed(self):
        self.expect("SHAFT", "feed_zero", 11, "G7x", ["N90 G71 P100 Q130 U0.4 W0.1 F0"], [11])

    def test_decimal_point_in_a_block_number(self):
        self.expect("SHAFT", "pq_point", 11, "G7x", ["N90 G71 P100. Q130 U0.4 W0.1 F0.25"], [11])

    def test_zero_depth(self):
        # Changed on the first G71 block; the parser reports it on the second.
        self.expect("SHAFT", "depth_zero", 11, "Cycle", ["N80 G71 U0 R0.5"], [10, 11])

    def test_missing_block(self):
        # Q130 + 5; there is no N135.
        self.expect("SHAFT", "blocks_missing", 11, "Cycle",
                    ["N90 G71 P100 Q135 U0.4 W0.1 F0.25"], [11])

    def test_protected_m_code(self):
        self.expect("SHAFT", "protected_m", 17, "M-Invariant", ["N150 (M62)"], [17])

    def test_arc_radius(self):
        # Chord from (r20, z-20) to (r22, z-22) is 2.83, so R0.2 cannot reach.
        self.expect("COMP", "arc_radius", 9, "Arc R", ["N80 G02 X44. Z-22. R0.2"], [9])

    def test_rapid_lead_in(self):
        self.expect("COMP", "leadin_rapid", 7, "G41/G42", ["N60 G00 X40. Z0. F0.2"], [7])

    def test_tool_change_with_compensation_on(self):
        self.expect("COMP", "g40_toolchange", 12, "G41/G42", ["N90"], [10, 12, 16])

    def test_no_change_alters_the_number_of_lines(self):
        for (program, fault), case in self.cases.items():
            original = SHAFT if program == "SHAFT" else COMP
            self.assertEqual(len(case.code.splitlines()), len(original.splitlines()), fault)
            self.assertTrue(case.code.endswith("\n"), fault)

    def test_a_fault_with_no_place_is_listed_as_such(self):
        self.assertIn(("COMP", "css_g50", ev.NO_PLACE), self.left_out)
        self.assertIn(("SHAFT", "leadin_rapid", ev.NO_PLACE), self.left_out)


class GroundTruthTests(unittest.TestCase):

    def test_a_fault_the_linter_does_not_see_is_left_out(self):
        # No Z retract to begin with: taking the X retract out as well changes
        # the program, but the linter already said "no retract" and says no more.
        program = SHAFT.replace("N160 G00 Z50.", "N160 M09")
        cases, left_out = cases_of(P=program)
        self.assertNotIn(("P", "no_retract"), cases)
        self.assertEqual([why for _, fault, why in left_out if fault == "no_retract"],
                         ["the linter reports nothing new at any of 1 place(s)"])

    def test_an_impossible_arc_in_a_cycle_contour_is_used(self):
        # Left out until the parser passed on the warnings of its contour reader.
        program = SHAFT.replace("N120 Z-18.", "N120 G02 X24. Z-20. R2.")
        cases, _ = cases_of(P=program)
        self.assertEqual(cases[("P", "arc_radius")].after, ["N120 G02 X24. Z-20. R0.2"])
        self.assertEqual([(f["line"], f["rule"]) for f in cases[("P", "arc_radius")].expected],
                         [(14, "Arc R")])

    def test_only_what_is_new_is_expected(self):
        # No retract to begin with: every copy still has that finding, and no
        # case expects it, because the fault put in is something else.
        program = SHAFT.replace("N160 G00 Z50.", "N160").replace("N170 G00 X100. M05", "N170 M05")
        cases, left_out = cases_of(P=program)
        self.assertEqual([f["rule"] for f in cases[("P", "feed_zero")].expected], ["G7x"])
        self.assertIn(20, cases[("P", "feed_zero")].known)
        self.assertNotIn(("P", "no_retract"), cases)
        self.assertEqual(cases[("P", ev.CLEAN)].known, [20])

    def test_comment_and_its_spacing_are_kept(self):
        program = SHAFT.replace("N90 G71 P100 Q130 U0.4 W0.1 F0.25",
                                "N90 G71 P100 Q130 U0.4 W0.1 F0.25   (rough)")
        cases, _ = cases_of(P=program)
        self.assertEqual(cases[("P", "feed_zero")].after,
                         ["N90 G71 P100 Q130 U0.4 W0.1 F0   (rough)"])

    def test_g76_amounts_are_pointed_in_millimetres(self):
        program = ("(THREAD - TEST)\n"
                   "N10 G18 G40 G80 G97 G99\n"
                   "N20 G21 G54\n"
                   "N30 T0101\n"
                   "N40 G97 S800 M03\n"
                   "N50 G00 X24. Z4.\n"
                   "N60 G76 P020060 Q100 R0.05\n"
                   "N70 G76 X17.4 Z-30. P1300 Q400 F2.\n"
                   "N80 G00 Z50.\n"
                   "N90 G00 X100. M05\n"
                   "N100 M30\n")
        cases, _ = cases_of(P=program)
        # Not the first block, whose P is the code m r a. P1300 is 1.3 mm.
        self.assertEqual(cases[("P", "pq_point")].after, ["N70 G76 X17.4 Z-30. P1.3 Q400 F2."])

    def test_the_prompt_is_the_one_the_app_sends(self):
        cases, _ = cases_of(SHAFT=SHAFT)
        case = cases[("SHAFT", "feed_zero")]
        prompt = ev.case_prompt(case, "POLICIES", {"tool_table": []})
        self.assertEqual(prompt, build_analysis_prompt(case.code[:MAX_CODE_CHARS], "POLICIES",
                                                       {"tool_table": []}))
        self.assertIn("N90 G71 P100 Q130 U0.4 W0.1 F0\n", prompt)
        self.assertIn("POLICIES", prompt)
        self.assertIn(FORMAT_RULES, prompt)

    def test_the_prompt_names_no_material_of_its_own(self):
        # The program's header names it. A fixed one in the prompt contradicted
        # a C45 program, and the model proposed to rewrite the header to match.
        prompt = build_analysis_prompt("(Material: C45, Stock D40, REV: A1)\nN10 G21\n",
                                       "POLICIES", {"tool_table": []})

        self.assertNotIn("42CrMo4", prompt)
        self.assertEqual(prompt.count("Material:"), 1)    # the program's own

    def test_a_fault_past_what_is_sent_is_not_used(self):
        padding = "(" + "x" * 98 + ")\n"
        program = SHAFT.replace("N10 G18", padding * (MAX_CODE_CHARS // 100 + 1) + "N10 G18")
        cases, _ = cases_of(P=program)
        self.assertIn(("P", ev.CLEAN), cases)
        self.assertNotIn(("P", "feed_zero"), cases)


class ScoringTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cases, _ = cases_of(SHAFT=SHAFT)
        cls.feed = cases[("SHAFT", "feed_zero")]
        cls.header = cases[("SHAFT", "header_g40")]
        cls.retract = cases[("SHAFT", "no_retract")]
        cls.clean = cases[("SHAFT", ev.CLEAN)]

    def found(self, answer, case=None):
        return ev.find_fault(answer, case or self.feed)

    def test_the_block_number_and_the_fault_in_one_item(self):
        self.assertTrue(self.found("<b>Analysis Result:</b><br><br>"
                                   "• N90: Feed F0 is not allowed — use F0.25<br>"))

    def test_the_line_number_counts_like_the_block(self):
        self.assertTrue(self.found("• Line 11: the feed must not be zero<br>"))

    def test_the_wrong_block_does_not_count(self):
        self.assertFalse(self.found("• N110: feed is zero<br>"))
        self.assertFalse(self.found("• Line 12: feed is zero<br>"))

    def test_the_right_block_for_something_else_does_not_count(self):
        self.assertFalse(self.found("• N90: P and Q look right<br>"))

    def test_the_reason_may_follow_on_the_next_line(self):
        self.assertTrue(self.found("<b>N90</b><br>The feed is zero."))

    def test_but_not_when_the_next_line_is_about_another_block(self):
        self.assertFalse(self.found("<b>N90</b><br>N110: the feed is zero."))

    def test_markdown_and_entities_are_read_too(self):
        self.assertTrue(self.found("- **Line 11** &mdash; F0 means no feed"))

    def test_a_range_of_lines(self):
        self.assertTrue(self.found("Lines 10-11: the feed of the G71 is zero"))

    def test_a_block_with_leading_zeros(self):
        self.assertTrue(self.found("N090: zero feed"))

    def test_the_header_may_be_named_by_word(self):
        self.assertTrue(self.found("The header lacks G40.", self.header))
        self.assertTrue(self.found("• N10: add G40 to the safe start block", self.header))

    def test_a_header_said_to_be_complete_is_no_finding(self):
        self.assertFalse(self.found("Header: G18 G40 G80 G97 present, OK.", self.header))
        self.assertFalse(self.found("N10: G18 G40 G80 G97 — fine", self.header))

    def test_a_retract_named_at_m30(self):
        self.assertTrue(self.found("• Before M30 there is no retract to a safe Z", self.retract))
        self.assertTrue(self.found("• N170: X retract missing", self.retract))

    def test_a_program_as_it_is_is_never_found(self):
        self.assertFalse(ev.score("• N90: feed is zero", self.clean)["found"])

    def test_other_places_named(self):
        # N110 and line 4 are lines the linter has nothing on; N90 is the fault.
        answer = "• N90: F0<br>• N110: consider G42<br>• Line 4: G21 fine<br>• N999: ?"
        self.assertEqual(ev.score(answer, self.feed), {"found": True, "other_places": 2})


class FakeClient:
    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def review_prompt(self):
        return "SYSTEM"

    def ask(self, prompt, system=None):
        self.prompts.append((prompt, system))
        return self.answers.pop(0)


class RunTests(unittest.TestCase):

    def setUp(self):
        cases, _ = cases_of(SHAFT=SHAFT)
        self.cases = [cases[("SHAFT", "feed_zero")], cases[("SHAFT", "pq_point")]]
        self.dir = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.dir.name, "sub", "run.jsonl")

    def tearDown(self):
        self.dir.cleanup()

    def run_eval(self, answers, keep_going=False):
        client = FakeClient(answers)
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            records = ev.run(self.cases, client, "claude", "test-model", 1, self.out, keep_going)
        return client, records, printed.getvalue()

    def test_every_answer_is_saved_and_scored(self):
        client, records, _ = self.run_eval([(True, "• N90: F0 is no feed"),
                                            (True, "• N90: looks fine")])
        self.assertEqual([r["score"]["found"] for r in records], [True, False])
        self.assertEqual(client.prompts[0][1], "SYSTEM")
        with open(self.out, encoding="utf-8") as f:
            saved = [json.loads(line) for line in f]
        self.assertEqual([r["case"]["fault"] for r in saved], ["feed_zero", "pq_point"])
        self.assertEqual(saved[0]["model"], "test-model")

    def test_a_failed_call_stops_the_run(self):
        client, records, printed = self.run_eval([(False, "❌ Claude API rejected the request (400)"),
                                                  (True, "never asked")])
        self.assertEqual(len(records), 1)
        self.assertIsNone(records[0]["score"])
        self.assertIn("rejected the request", printed)
        self.assertEqual(len(client.prompts), 1)

    def test_keep_going_goes_on(self):
        _, records, _ = self.run_eval([(False, "❌ timeout"), (True, "• N90: decimal point")],
                                      keep_going=True)
        self.assertEqual([r["ok"] for r in records], [False, True])
        self.assertTrue(records[1]["score"]["found"])

    def test_saved_answers_can_be_scored_again(self):
        self.run_eval([(True, "• N90: F0 is no feed"), (True, "• N90: decimal point in P")])
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(ev.main(["--rescore", self.out]), 0)
        report = printed.getvalue()
        self.assertIn("test-model (claude): 2 answer(s), 0 failed call(s)", report)
        self.assertIn("all              2/2 (100 %)", report)

    def test_a_miss_is_named_with_its_block(self):
        _, records, _ = self.run_eval([(True, "nothing"), (True, "nothing")])
        report = ev.summarize(records)
        self.assertIn("missed: SHAFT feed_zero (line 11 (N90))", report)


if __name__ == "__main__":
    unittest.main()
