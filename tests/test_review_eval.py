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
from vibe_cnc.analysis_prompt import (FORMAT_RULES, MAX_CODE_CHARS, build_analysis_prompt,
                                       code_for_review, cut_note)
from vibe_cnc.specs import load_specs
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

# Compensation, an arc outside any cycle, a tool change. Clean too. The
# spindle starts on the tool's own block, so no line moves. The second tool
# goes to Z-10, beside the part: taken out, its retract leaves it standing
# there. SHAFT ends at the start point of G70, in front of the face, which is
# a retract already.
COMP = """(COMP - TEST)
N10 G18 G40 G80 G97 G99
N20 G21 G54
N30 T0101 S1000 M03
N40 G00 X50. Z2.
N50 G42
N60 G01 X40. Z0. F0.2
N70 G01 Z-20.
N80 G02 X44. Z-22. R2.
N90 G40
N100 G00 X100. Z50.
N110 T1111
N120 G00 X50. Z-10.
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
        self.assertIn((line, rule), [(f.line, f.rule) for f in case.expected])
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
        self.expect("SHAFT", "header_g40", 1, "HEADER_CODE", ["N10 G18 G80 G97 G99"], [1, 3])

    def test_spindle_limit(self):
        # G50 S2500 goes from line 5; the G96 it guarded is line 8.
        self.expect("SHAFT", "css_g50", 8, "G50_BEFORE_G96", ["N30"], [5, 8])

    def test_retract(self):
        self.expect("COMP", "no_retract", 16, "RETRACT_BEFORE_M30", ["N130", "N140"], [14, 15, 16])

    def test_a_tool_left_at_a_cycle_start_in_front_of_the_face_is_no_fault(self):
        # G70 hands the tool back at its start point, X42 Z2, in front of the
        # face. That is a retract (the operator's decision of 2026-11-21); the
        # rule used to count the shifted G71 pass at Z2.1 as a cut, and found
        # the tool standing in front of it.
        self.assertNotIn(("SHAFT", "no_retract"), self.cases)
        self.assertIn(("SHAFT", "no_retract", "the linter reports nothing new at any of 1 place(s)"),
                      self.left_out)

    def test_zero_feed(self):
        self.expect("SHAFT", "feed_zero", 11, "VALUE_RANGE", ["N90 G71 P100 Q130 U0.4 W0.1 F0"], [11])

    def test_decimal_point_in_a_block_number(self):
        self.expect("SHAFT", "pq_point", 11, "DECIMAL_POINT", ["N90 G71 P100. Q130 U0.4 W0.1 F0.25"], [11])

    def test_zero_depth(self):
        # Changed on the first G71 block; the parser reports it on the second.
        self.expect("SHAFT", "depth_zero", 11, "CYCLE_NO_DEPTH", ["N80 G71 U0 R0.5"], [10, 11])

    def test_missing_block(self):
        # Q130 + 5; there is no N135.
        self.expect("SHAFT", "blocks_missing", 11, "CYCLE_BLOCKS_MISSING",
                    ["N90 G71 P100 Q135 U0.4 W0.1 F0.25"], [11])

    def test_protected_m_code(self):
        self.expect("SHAFT", "protected_m", 17, "PROTECTED_M_CODE", ["N150 (M62)"], [17])

    def test_arc_radius(self):
        # Chord from (r20, z-20) to (r22, z-22) is 2.83, so R0.2 cannot reach.
        self.expect("COMP", "arc_radius", 9, "ARC_R_TOO_SMALL", ["N80 G02 X44. Z-22. R0.2"], [9])

    def test_rapid_lead_in(self):
        self.expect("COMP", "leadin_rapid", 7, "TNR_LEADIN_RAPID", ["N60 G00 X40. Z0. F0.2"], [7])

    def test_tool_change_with_compensation_on(self):
        self.expect("COMP", "g40_toolchange", 12, "TNR_TOOL_CHANGE", ["N90"], [10, 12, 16])

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
        # The tool goes back along the part instead of away from it, so the
        # linter says "no retract" to begin with. Taking the retract out
        # changes the program, but says nothing new.
        program = SHAFT.replace("N160 G00 Z50.", "N160 G00 Z-30.")
        cases, left_out = cases_of(P=program)
        self.assertNotIn(("P", "no_retract"), cases)
        self.assertEqual([why for _, fault, why in left_out if fault == "no_retract"],
                         ["the linter reports nothing new at any of 1 place(s)"])

    def test_an_impossible_arc_in_a_cycle_contour_is_used(self):
        # Left out until the parser passed on the warnings of its contour reader.
        program = SHAFT.replace("N120 Z-18.", "N120 G02 X24. Z-20. R2.")
        cases, _ = cases_of(P=program)
        self.assertEqual(cases[("P", "arc_radius")].after, ["N120 G02 X24. Z-20. R0.2"])
        self.assertEqual([(f.line, f.rule) for f in cases[("P", "arc_radius")].expected],
                         [(14, "ARC_R_TOO_SMALL")])

    def test_only_what_is_new_is_expected(self):
        # No retract to begin with: every copy still has that finding, and no
        # case expects it, because the fault put in is something else.
        program = SHAFT.replace("N160 G00 Z50.", "N160 G00 Z-30.")
        cases, left_out = cases_of(P=program)
        self.assertEqual([f.rule for f in cases[("P", "feed_zero")].expected], ["VALUE_RANGE"])
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
        shown = code_for_review(case.code)
        self.assertEqual(prompt, build_analysis_prompt(shown.code, "POLICIES",
                                                       {"tool_table": []}, None, shown.total))
        self.assertIn("N90 G71 P100 Q130 U0.4 W0.1 F0\n", prompt)
        self.assertIn("POLICIES", prompt)
        self.assertIn(FORMAT_RULES, prompt)
        self.assertNotIn("G71 -- ", prompt)                 # no specs, no cards (--no-cards)

    def test_with_the_cards_the_prompt_is_still_the_one_the_app_sends(self):
        cases, _ = cases_of(SHAFT=SHAFT)
        case = cases[("SHAFT", "feed_zero")]
        specs = load_specs()
        prompt = ev.case_prompt(case, "POLICIES", {"tool_table": []}, specs)
        shown = code_for_review(case.code)
        self.assertEqual(prompt, build_analysis_prompt(shown.code, "POLICIES",
                                                       {"tool_table": []}, specs, shown.total))
        # The cards of the cycles the program runs, after the tools and before the code.
        self.assertEqual(ev.case_cards(case, specs), ["G71", "G70"])
        self.assertIn("Control: FANUC 0i-TF (Plus)", prompt)
        self.assertLess(prompt.index("Tools(JSON):"), prompt.index("G71 -- "))
        self.assertLess(prompt.index("G70 -- "), prompt.index("Code:\n```"))
        self.assertEqual(ev.case_cards(case, None), [])

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


class ReviewCutTests(unittest.TestCase):
    """A long program goes to the model cut short, in whole lines, and both
    the operator and the model are told. It was code[:8000], mid-block and
    silent."""

    LINE = "N{:04d} G01 X40. Z-{}. F0.2"

    def program(self, count):
        return "\n".join(self.LINE.format(n, n % 90) for n in range(count))

    def test_a_short_program_is_sent_whole(self):
        shown = code_for_review(SHAFT)

        self.assertEqual(shown.code, SHAFT)
        self.assertFalse(shown.cut)
        self.assertEqual(shown.lines, shown.total)
        self.assertEqual(cut_note(shown), "")

    def test_a_long_program_is_cut_between_lines_not_inside_one(self):
        program = self.program(1000)
        lines = program.splitlines()

        shown = code_for_review(program)

        self.assertTrue(shown.cut)
        self.assertEqual(shown.total, 1000)
        self.assertEqual(shown.code.splitlines(), lines[:shown.lines])
        self.assertLessEqual(len(shown.code), MAX_CODE_CHARS)
        # One more line would not have fitted.
        self.assertGreater(len("\n".join(lines[:shown.lines + 1])), MAX_CODE_CHARS)

    def test_a_program_of_exactly_the_limit_is_not_cut(self):
        line = "x" * 99
        program = "\n".join([line] * (MAX_CODE_CHARS // 100))      # 80 x 100 - 1 chars
        program += "\n" + "y" * (MAX_CODE_CHARS - len(program) - 1)

        self.assertEqual(len(program), MAX_CODE_CHARS)
        self.assertFalse(code_for_review(program).cut)
        self.assertTrue(code_for_review(program + "z").cut)

    def test_the_operator_is_told_which_lines_the_ai_saw(self):
        shown = code_for_review(self.program(1000))

        note = cut_note(shown)

        self.assertIn(f"lines 1–{shown.lines} of 1000", note)
        self.assertIn("Select", note)
        self.assertEqual(cut_note(code_for_review(SHAFT)), "")

    def test_the_model_is_told_the_rest_follows(self):
        shown = code_for_review(self.program(1000))

        cut = build_analysis_prompt(shown.code, "P", {}, None, shown.total)
        whole = build_analysis_prompt(SHAFT, "P", {}, None, len(SHAFT.splitlines()))

        self.assertIn(f"Only lines 1-{shown.lines} of the program's 1000 are shown", cut)
        self.assertLess(cut.index("```\n\nOnly lines"), cut.index("Task:"))
        self.assertNotIn("Only lines", whole)
        self.assertNotIn("Only lines", build_analysis_prompt(SHAFT, "P", {}))

    def test_the_eval_sends_what_the_app_sends_of_a_long_program(self):
        padding = "(" + "x" * 98 + ")\n"
        program = SHAFT.replace("N10 G18", padding * (MAX_CODE_CHARS // 100 + 1) + "N10 G18")
        cases, _ = cases_of(P=program)

        prompt = ev.case_prompt(cases[("P", ev.CLEAN)], "P", {})

        self.assertIn("Only lines 1-", prompt)


class ScoringTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cases, _ = cases_of(SHAFT=SHAFT, COMP=COMP)
        cls.feed = cases[("SHAFT", "feed_zero")]
        cls.header = cases[("SHAFT", "header_g40")]
        cls.retract = cases[("COMP", "no_retract")]
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

    def test_a_block_at_the_end_of_a_sentence(self):
        # "... before N40." was read as no block at all.
        self.assertTrue(self.found("• The feed is zero; correct it at N90."))

    def test_a_number_with_decimals_after_n_is_no_block(self):
        self.assertFalse(self.found("• N90.5: the feed is zero"))

    def test_a_line_past_the_end_is_the_block_of_that_number(self):
        # The program has 20 lines; a model writing "Line 90" means N90.
        self.assertTrue(self.found("• Line 90 — the feed is zero"))
        self.assertTrue(self.found("• Lines 90-100: the feed is zero"))

    def test_a_line_inside_the_program_is_still_that_line(self):
        # Line 11 is N90; N11 does not exist, but line 11 does.
        self.assertTrue(self.found("• Line 11: the feed must not be zero"))
        self.assertFalse(self.found("• Line 12: the feed is zero"))

    def test_a_missing_block_said_as_no_matching_block(self):
        cases, _ = cases_of(SHAFT=SHAFT)
        missing = cases[("SHAFT", "blocks_missing")]

        self.assertTrue(self.found("• Line 11 (N90): Q135 has no matching N135 block", missing))
        self.assertTrue(self.found("• N90: there is no such block for Q135", missing))
        self.assertFalse(self.found("• N90: P100 and Q135 are fine", missing))

    def test_the_header_may_be_named_by_word(self):
        self.assertTrue(self.found("The header lacks G40.", self.header))
        self.assertTrue(self.found("• N10: add G40 to the safe start block", self.header))

    def test_a_header_said_to_be_complete_is_no_finding(self):
        self.assertFalse(self.found("Header: G18 G40 G80 G97 present, OK.", self.header))
        self.assertFalse(self.found("N10: G18 G40 G80 G97 — fine", self.header))

    def test_a_retract_named_at_m30(self):
        self.assertTrue(self.found("• Before M30 there is no retract to a safe Z", self.retract))
        self.assertTrue(self.found("• N140: X retract missing", self.retract))

    def test_a_program_as_it_is_is_never_found(self):
        self.assertFalse(ev.score("• N90: feed is zero", self.clean)["found"])

    def test_other_places_named(self):
        # N110 and line 4 are lines the linter has nothing on; N90 is the fault.
        answer = "• N90: F0<br>• N110: consider G42<br>• Line 4: G21 fine<br>• N999: ?"
        self.assertEqual(ev.score(answer, self.feed), {"found": True, "other_places": 2})

    def test_a_line_that_is_neither_a_line_nor_a_block_is_no_place(self):
        # The program has 20 lines and no N400: line 400 was counted as a place.
        self.assertEqual(ev.score("• N90: F0<br>• Line 400: odd", self.feed)["other_places"], 0)


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
        # run_eval sends no specs, so the answers are labelled as without the cards.
        self.assertIn("test-model (claude, no cards): 2 answer(s), 0 failed call(s)", report)
        self.assertIn("all              2/2 (100 %)", report)

    def test_answers_saved_before_the_cards_keep_their_label(self):
        self.run_eval([(True, "• N90: F0 is no feed"), (True, "• N90: decimal point in P")])
        with open(self.out, encoding="utf-8") as f:
            records = [json.loads(line) for line in f]
        for r in records:
            del r["with_cards"], r["cards"]
        self.assertIn("test-model (claude): 2 answer(s)", ev.summarize(records))

    def test_with_specs_the_cards_are_sent_and_saved(self):
        client = FakeClient([(True, "• N90: F0 is no feed"), (True, "• N90: decimal point in P")])
        with contextlib.redirect_stdout(io.StringIO()):
            records = ev.run(self.cases, client, "claude", "test-model", 1, self.out, False,
                             load_specs())
        self.assertIn("G71 -- ", client.prompts[0][0])
        self.assertEqual([(r["with_cards"], r["cards"]) for r in records],
                         [(True, ["G71", "G70"])] * 2)
        self.assertIn("test-model (claude, cards): 2 answer(s)", ev.summarize(records))

    def test_scoring_again_uses_the_words_as_they_are_now(self):
        # The record keeps the words of its run; a corrected list must count.
        self.run_eval([(True, "• N90: F0 is no feed"), (True, "• N90: decimal point in P")])
        with open(self.out, encoding="utf-8") as handle:
            records = [json.loads(line) for line in handle]
        for record in records:
            record["case"]["about"] = r"(?!)"           # words that match nothing
        with open(self.out, "w", encoding="utf-8") as handle:
            handle.writelines(json.dumps(r) + "\n" for r in records)

        with contextlib.redirect_stdout(io.StringIO()) as printed:
            ev.main(["--rescore", self.out])

        self.assertIn("all              2/2 (100 %)", printed.getvalue())

    def test_a_miss_is_named_with_its_block(self):
        _, records, _ = self.run_eval([(True, "nothing"), (True, "nothing")])
        report = ev.summarize(records)
        self.assertIn("missed: SHAFT feed_zero (line 11 (N90))", report)


if __name__ == "__main__":
    unittest.main()
