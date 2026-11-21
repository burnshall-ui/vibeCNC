import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from vibe_cnc import tool_data
from vibe_cnc.diagnostics import Diagnostic, blocking, errors, is_lint_clean, summary, weight
from vibe_cnc.lint_engine import LintEngine, is_snippet

# Shared so a rename in the engine breaks the positive tests below loudly,
# instead of quietly emptying the filters and turning these tests green.
M_RULE = "PROTECTED_M_CODE"

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "reference.nc")

# What config.yaml actually asks for, so the header tests match the shop rules.
SHOP_HEADER = {
    "require_header_codes": ["G18", "G40", "G80", "G97"],
    "require_units": "G21",
    "require_origin": "G54",
}


def make_cfg(**policy_overrides):
    policies = {
        "require_header_codes": [],
        "require_units": None,
        "require_origin": None,
        "protected_m_codes": [],
    }
    policies.update(policy_overrides)
    return SimpleNamespace(data={"policies": policies})


def rules(findings, rule):
    """The findings of one rule; a name ending in "_" takes every rule it begins."""
    if rule.endswith("_"):
        return [f for f in findings if f.rule.startswith(rule)]
    return [f for f in findings if f.rule == rule]


class LintEngineProtectedMTests(unittest.TestCase):
    """VC-10: the rule is 'a protected M-code was commented out', nothing wider."""

    def test_two_m_codes_on_one_line_are_not_an_override(self):
        engine = LintEngine(make_cfg(protected_m_codes=[62]))

        findings = engine.run_all("M62 M08")

        self.assertEqual(rules(findings, M_RULE), [])

    def test_active_protected_m_code_is_not_reported(self):
        engine = LintEngine(make_cfg(protected_m_codes=[8]))

        self.assertEqual(rules(engine.run_all("M8"), M_RULE), [])

    def test_commented_out_protected_m_code_is_reported(self):
        engine = LintEngine(make_cfg(protected_m_codes=[8]))

        findings = engine.run_all("(M8)\n; M8\nG01 X1 ; M8")

        self.assertEqual([f.line for f in rules(findings, M_RULE)], [1, 2, 3])
        self.assertIn("commented out", findings[0].message)

    def test_unprotected_m_code_in_a_comment_is_ignored(self):
        engine = LintEngine(make_cfg(protected_m_codes=[8]))

        self.assertEqual(rules(engine.run_all("(M9)"), M_RULE), [])

    def test_no_finding_appears_twice_for_the_same_line_and_rule(self):
        engine = LintEngine(make_cfg(protected_m_codes=[62, 63, 64, 65]))

        findings = engine.run_all("(M62 M63)")

        seen = [(f.line, f.rule, f.message) for f in findings]
        self.assertEqual(len(seen), len(set(seen)))


class LintEngineHeaderTests(unittest.TestCase):
    """VC-10: whole words, on code, however far down the header starts."""

    def test_code_inside_a_comment_does_not_satisfy_the_header(self):
        engine = LintEngine(LintEngineHeaderTests._cfg())

        findings = engine.run_all("(G18 ist hier nicht noetig)\nG21 G40 G80 G97 G54")

        self.assertEqual([f.message for f in rules(findings, "HEADER_CODE")],
                         ["G18 expected in header."])

    def test_a_longer_code_does_not_satisfy_a_shorter_one(self):
        engine = LintEngine(LintEngineHeaderTests._cfg())

        findings = engine.run_all("G180 G21 G40 G80 G97 G54")

        self.assertEqual([f.message for f in rules(findings, "HEADER_CODE")],
                         ["G18 expected in header."])

    def test_comment_block_does_not_push_the_header_out_of_range(self):
        engine = LintEngine(LintEngineHeaderTests._cfg())

        with open(FIXTURE, "r", encoding="utf-8") as handle:
            findings = engine.run_all(handle.read())

        self.assertEqual(rules(findings, "HEADER_CODE"), [])
        self.assertEqual(rules(findings, "UNITS"), [])
        self.assertEqual(rules(findings, "ORIGIN"), [])

    def test_a_genuinely_missing_header_is_still_reported(self):
        engine = LintEngine(LintEngineHeaderTests._cfg())

        findings = engine.run_all("G00 X50. Z2.\nG01 Z0. F0.2")

        self.assertEqual(len(rules(findings, "HEADER_CODE")), 4)
        self.assertEqual(len(rules(findings, "UNITS")), 1)
        self.assertEqual(len(rules(findings, "ORIGIN")), 1)

    @staticmethod
    def _cfg():
        return make_cfg(**SHOP_HEADER)


class LintEngineCycleFeedTests(unittest.TestCase):
    """VC-10: F0.25 is a normal turning feed, not a zero feed."""

    def test_ordinary_turning_feed_is_not_read_as_zero(self):
        engine = LintEngine(make_cfg())

        for feed in ("F0.25", "F0.15", "F0.08"):
            with self.subTest(feed=feed):
                findings = engine.run_all(f"G71 P100 Q200 U0.4 W0.1 {feed}")
                self.assertEqual(rules(findings, "VALUE_RANGE"), [])

    def test_zero_and_negative_feed_are_still_reported(self):
        engine = LintEngine(make_cfg())

        for feed in ("F0", "F0.0", "F0.00", "F-0.2"):
            with self.subTest(feed=feed):
                findings = engine.run_all(f"G71 P100 Q200 {feed}")
                self.assertEqual(len(rules(findings, "VALUE_RANGE")), 1)

    def test_cycle_block_without_a_feed_is_not_reported(self):
        # G71 is two blocks: depth and retract first, P/Q/U/W/F second. F is
        # modal besides, so its absence on one block means nothing.
        engine = LintEngine(make_cfg())

        self.assertEqual(rules(engine.run_all("G71 U1.5 R0.5"), "VALUE_RANGE"), [])


class LintEngineThreadingTests(unittest.TestCase):
    """What the parser works out about a G76 reaches the operator as a finding."""

    SETUP = "G00 X24. Z2.\nG76 P010060 Q100 R0.05\n"

    def test_a_sensible_thread_has_no_cycle_finding(self):
        engine = LintEngine(make_cfg())

        findings = engine.run_all(self.SETUP + "G76 X17.4 Z-30. P1300 Q400 F2.")

        self.assertEqual(rules(findings, "CYCLE_"), [])

    def test_a_runaway_pass_count_is_reported(self):
        # A first cut of one micron would be hundreds of thousands of passes --
        # unless the minimum cut in the first block (Q100 in SETUP, 0.1 mm)
        # keeps the count down, so this one sets none.
        engine = LintEngine(make_cfg())

        findings = engine.run_all("G00 X24. Z2.\nG76 P010060 Q0 R0.05\n"
                                  "G76 X17.4 Z-30. P1300 Q1 F2.")

        self.assertEqual(len(rules(findings, "CYCLE_")), 1)
        self.assertIn("passes", rules(findings, "CYCLE_")[0].message)

    def test_a_thread_with_no_height_is_reported(self):
        engine = LintEngine(make_cfg())

        findings = engine.run_all(self.SETUP + "G76 X17.4 Z-30. Q400 F2.")

        self.assertEqual(len(rules(findings, "CYCLE_")), 1)


class LintEngineRetractTests(unittest.TestCase):
    """VC-10: G28 and the U/W words are how a Fanuc lathe retracts."""

    def test_g28_counts_as_a_retract(self):
        engine = LintEngine(make_cfg())

        self.assertEqual(rules(engine.run_all("G01 X10. Z-5.\nG28 U0 W0\nM30"),
                               "RETRACT_BEFORE_M30"), [])

    def test_incremental_retract_counts(self):
        engine = LintEngine(make_cfg())

        self.assertEqual(rules(engine.run_all("G00 W50.\nG00 U150.\nM30"),
                               "RETRACT_BEFORE_M30"), [])

    def test_program_that_never_pulls_z_clear_is_still_reported(self):
        engine = LintEngine(make_cfg())

        findings = engine.run_all("G01 X-5.\nG00 X200.\nM30")

        self.assertEqual(len(rules(findings, "RETRACT_BEFORE_M30")), 1)

    def test_what_follows_m30_does_not_push_the_retract_out_of_sight(self):
        # The tape end '%' after M30 took the place of the Z retract, so this
        # ordinary ending was reported. Blank lines and comments did the same.
        engine = LintEngine(make_cfg())
        for ending in ("M30\n%", "M30\n\n", "M30\n(END)", "M30\n%\n"):
            with self.subTest(ending=ending):
                code = "G01 X-5. F0.2\nG00 Z50.\nX200.\n" + ending

                self.assertEqual(rules(engine.run_all(code), "RETRACT_BEFORE_M30"), [])

    def test_the_finding_points_at_m30_not_at_what_follows(self):
        engine = LintEngine(make_cfg())

        findings = rules(engine.run_all("G01 X-5.\nG00 X200.\nN100M30\n%"), "RETRACT_BEFORE_M30")

        self.assertEqual([f.line for f in findings], [3])

    def test_m30_in_a_comment_ends_nothing(self):
        engine = LintEngine(make_cfg())

        findings = engine.run_all("G01 X-5.\nG00 X200.\n(no M30 here, the program goes on)")

        self.assertEqual(rules(findings, "RETRACT_BEFORE_M30"), [])


class SpindleTests(unittest.TestCase):
    """The spindle runs before the tool cuts. Three of six programs a model
    generated on 2026-11-21 never started it and came through lint clean."""

    RULE = "SPINDLE_NOT_STARTED"

    def found(self, code, snippet=False):
        return [(f.line, f.severity) for f in
                rules(LintEngine(make_cfg()).run_all(code, snippet=snippet), self.RULE)]

    def test_a_cut_without_m03_is_an_error_at_the_first_cut(self):
        code = "T0101\nG00 X52. Z2.\nG01 Z-15. F0.2\nX60.\nG00 Z50.\nX200.\nM30"

        self.assertEqual(self.found(code), [(3, "ERROR")])

    def test_a_started_spindle_is_no_finding(self):
        for start in ("G97 S800 M03", "G96 S180 M3", "G97 S500 M04", "G97 S500 M003"):
            with self.subTest(start=start):
                code = f"T0101\n{start}\nG00 X52. Z2.\nG01 Z-15. F0.2\nG00 Z50.\nX200.\nM30"
                self.assertEqual(self.found(code), [])

    def test_m03_on_the_block_of_the_cut_counts(self):
        self.assertEqual(self.found("G00 X52. Z2.\nG01 Z-15. F0.2 S500 M03\nM30"), [])

    def test_rapids_alone_need_no_spindle(self):
        self.assertEqual(self.found("T0101\nG00 X52. Z2.\nG00 X200. Z50.\nM30"), [])

    def test_a_cycle_without_the_spindle_is_found_at_the_cycle(self):
        code = ("T0101\nG00 X52. Z2.\nG71 U2. R0.5\nG71 P10 Q20 U0.4 W0.1 F0.25\n"
                "N10 G00 X30.\nN20 G01 Z-15. F0.1\nG00 Z50.\nX200.\nM30")

        self.assertEqual(self.found(code), [(3, "ERROR")])

    def test_a_cut_after_m05_is_found_again(self):
        code = ("G97 S800 M03\nG00 X52. Z2.\nG01 Z-15. F0.2\nM05\nG00 X52. Z2.\n"
                "G01 Z-10.\nX60.\nG00 Z50.\nM30")

        self.assertEqual(self.found(code), [(6, "ERROR")])    # modal G01, once

    def test_the_usual_end_after_m05_is_no_cut(self):
        # G28 U0 W0 has axis words with G01 still active.
        code = "G97 S800 M03\nG00 X52. Z2.\nG01 Z-15. F0.2\nG00 X60.\nM05\nG28 U0 W0\nM30"

        self.assertEqual(self.found(code), [])

    def test_a_dwell_and_g50_are_no_cuts(self):
        code = "G50 X200. Z100.\nG01 F0.2\nG04 X1.\nG00 X52. Z2.\nM30"

        self.assertEqual(self.found(code), [])

    def test_m30_is_not_m3_and_m50_is_not_m5(self):
        self.assertEqual(self.found("M30\nG01 X10. Z-5. F0.2"), [(2, "ERROR")])
        self.assertEqual(self.found("G97 S800 M03\nM50\nG01 X10. Z-5. F0.2\nM30"), [])

    def test_a_snippet_gets_its_spindle_from_the_program_it_goes_into(self):
        self.assertEqual(self.found("G00 X52. Z2.\nG01 Z-15. F0.2", snippet=True), [])

    def test_the_sample_programs_start_their_spindles(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in sorted(os.listdir(os.path.join(root, "programs"))):
            with self.subTest(program=name), \
                    open(os.path.join(root, "programs", name), encoding="utf-8") as handle:
                self.assertEqual(self.found(handle.read()), [])


class LintEngineParserWarningTests(unittest.TestCase):
    """VC-16: geometry the parser rejects has to reach the operator."""

    def test_impossible_r_geometry_is_reported(self):
        engine = LintEngine(make_cfg())

        findings = engine.run_all("G00 X0. Z0.\nG02 X40. Z-15. R1.")

        arc = rules(findings, "ARC_R_")
        self.assertEqual(len(arc), 1)
        self.assertEqual(arc[0].line, 2)
        self.assertIn("half the chord", arc[0].message)

    def test_impossible_r_in_a_cycle_contour_is_reported(self):
        # The contour is read by a parser of its own, whose warnings used to
        # go nowhere: the plot drew a straight line and the linter said nothing.
        engine = LintEngine(make_cfg())

        findings = engine.run_all("\n".join([
            "G00 X52. Z2.",
            "G71 U1.5 R0.5",
            "G71 P100 Q200 U0.4 W0.1 F0.25",
            "N100 G01 X20. F0.15",
            "N110 Z-10.",
            "N120 G02 X30. Z-15. R1.",
            "N200 G01 Z-30.",
            "G70 P100 Q200",
        ]))

        arc = rules(findings, "ARC_R_")
        self.assertEqual([f.line for f in arc], [6])

    def test_arc_without_a_centre_is_reported(self):
        engine = LintEngine(make_cfg())

        findings = engine.run_all("G00 X30. Z0.\nG02 X40. Z-5.")

        self.assertEqual(len(rules(findings, "ARC_NO_CENTER")), 1)

    def test_valid_geometry_produces_no_arc_finding(self):
        engine = LintEngine(make_cfg())

        findings = engine.run_all("G00 X30. Z0.\nG02 X40. Z-5. R5.")

        self.assertEqual([f for f in findings if f.rule.startswith("ARC")], [])

    def test_findings_come_back_in_program_order(self):
        engine = LintEngine(make_cfg(protected_m_codes=[8]))

        findings = engine.run_all("\n".join([
            "G00 X0. Z0.",
            "G02 X40. Z-15. R1.",   # parser: impossible radius
            "(M8)",                 # rule: protected M-code commented out
            "G00 X30. Z0.",
            "G02 X40. Z-5.",        # parser: no centre
        ]))

        lines = [f.line for f in findings]
        self.assertEqual(lines, sorted(lines))
        self.assertTrue(any(f.rule.startswith("ARC_R_") for f in findings))
        self.assertIn(M_RULE, [f.rule for f in findings])

    def test_a_parser_that_chokes_keeps_the_program_from_lint_clean(self):
        # It was caught and dropped: the geometry, chuck and retract checks
        # were skipped without a word, and a program that only passed the text
        # rules came out "lint clean -- no known faults".
        engine = LintEngine(make_cfg())
        with open(FIXTURE, encoding="utf-8") as f:
            program = f.read()
        self.assertTrue(is_lint_clean(engine.run_all(program)))

        with patch("vibe_cnc.gcode_parser.GCodeParser.parse",
                   side_effect=ValueError("bad arc")):
            findings = engine.run_all(program)
            piece = engine.run_all("G01 X30. Z-5. F0.2", snippet=True)

        failed = rules(findings, "PARSER_FAILED")
        self.assertEqual([(f.line, f.severity) for f in failed], [(1, "ERROR")])
        self.assertIn("ValueError: bad arc", failed[0].message)
        self.assertIn("did not run", failed[0].message)
        self.assertFalse(is_lint_clean(findings))
        # A piece of a program is drawn by the parser too.
        self.assertEqual(len(rules(piece, "PARSER_FAILED")), 1)

    def test_a_parser_that_reads_the_program_adds_no_failure(self):
        engine = LintEngine(make_cfg())

        findings = engine.run_all("G00 X30. Z0.\nG02 X40. Z-5.")

        self.assertEqual(rules(findings, "PARSER_FAILED"), [])

    def test_every_finding_carries_the_keys_the_ui_reads(self):
        engine = LintEngine(make_cfg())

        findings = engine.run_all("G00 X0. Z0.\nG02 X40. Z-15. R1.")

        self.assertTrue(findings)
        for finding in findings:
            self.assertIsInstance(finding.line, int)
            self.assertIsInstance(finding.rule, str)
            self.assertIsInstance(finding.message, str)


class LintEngineCleanProgramTests(unittest.TestCase):
    """The reference program must lint clean, or it is not a reference."""

    def test_reference_program_has_no_findings_at_all(self):
        engine = LintEngine(make_cfg(protected_m_codes=[62, 63, 64, 65], **SHOP_HEADER))

        with open(FIXTURE, "r", encoding="utf-8") as handle:
            findings = engine.run_all(handle.read())

        self.assertEqual(findings, [])


class LintEngineNoseDirectionTests(unittest.TestCase):
    """VC-08: an unset tip number is said out loud, not silently assumed."""

    PROGRAM = "T0101\nG42\nG01 X20. Z-5. F0.2\nG40\n"

    def _compensation_findings(self, tool):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tools.json")
            with patch.object(tool_data, "TOOLS_JSON", path):
                tool_data.save_tools_json({"tool_table": [tool]})
                findings = LintEngine(make_cfg()).run_all(self.PROGRAM)
        return rules(findings, "TNR_")

    def test_a_tool_without_a_tip_number_is_reported(self):
        findings = self._compensation_findings({"t": 1, "insert_radius_mm": 0.8})

        self.assertEqual(len(findings), 1)
        self.assertIn("nose_direction", findings[0].message)
        self.assertEqual(findings[0].line, 2)   # the G42 block

    def test_a_tool_with_a_tip_number_is_not(self):
        self.assertEqual(
            self._compensation_findings({"t": 1, "insert_radius_mm": 0.8,
                                         "nose_direction": 3}), [])

    def test_an_explicit_zero_counts_as_answered(self):
        # 0 means "nose point is the centre", which is a choice like any other.
        self.assertEqual(
            self._compensation_findings({"t": 1, "insert_radius_mm": 0.8,
                                         "nose_direction": 0}), [])

    def test_a_tip_number_outside_the_table_is_reported(self):
        findings = self._compensation_findings({"t": 1, "insert_radius_mm": 0.8,
                                                "nose_direction": 12})

        self.assertEqual(len(findings), 1)
        self.assertIn("nose_direction", findings[0].message)

    def test_a_missing_radius_is_reported_instead_not_as_well(self):
        # Without a radius nothing is offset at all, so the tip number makes no
        # difference and saying both would just be noise.
        findings = self._compensation_findings({"t": 1, "name": "no radius"})

        self.assertEqual(len(findings), 1)
        self.assertIn("insert_radius_mm", findings[0].message)


class LintEngineCycleDecimalPointTests(unittest.TestCase):
    """P and Q of G70-G76 take no decimal point; the parser would draw them anyway."""

    def _findings(self, code):
        return [f for f in rules(LintEngine(make_cfg()).run_all(code), "DECIMAL_POINT")
                if "decimal point" in f.message]

    def test_a_pointed_peck_depth_in_a_groove_is_reported(self):
        findings = self._findings("G00 X32. Z-10.\nG75 R0.5\nG75 X26. P1.5 F0.05")

        self.assertEqual([f.line for f in findings], [3])
        self.assertIn("G75: P1.5", findings[0].message)
        self.assertIn("P1500 = 1.5 mm", findings[0].message)

    def test_the_same_groove_without_points_is_not(self):
        self.assertEqual(self._findings("G00 X32. Z-20.\nG75 R0.5\nG75 X26. Z-27. P1500 Q3000 F0.05"), [])

    def test_a_point_on_r_is_fine(self):
        # R is the return, the relief or the allowance: millimetres with a point.
        self.assertEqual(self._findings("G76 P010060 Q100 R0.05\nG76 X17.4 Z-30. R1.5 P1300 Q400 F2."), [])

    def test_every_pointed_word_of_a_thread_is_reported(self):
        # Q. at the end and .4 without a leading digit both count.
        findings = self._findings("G76 P010060 Q100. R0.05\nG76 X17.4 Z-30. P1300 Q.4 F2.")

        self.assertEqual([(f.line, f.message.split(" has")[0]) for f in findings],
                         [(1, "G76: Q100."), (2, "G76: Q.4")])

    def test_block_numbers_of_a_roughing_cycle_are_reported_as_block_numbers(self):
        findings = self._findings("G71 U1.5 R0.5\nG71 P100. Q200 U0.4 W0.1 F0.25")

        self.assertEqual(len(findings), 1)
        self.assertIn("G71: P100.", findings[0].message)
        self.assertIn("block numbers", findings[0].message)

    def test_a_block_written_without_spaces_is_read(self):
        findings = self._findings("N10G75X26.P1.5F0.05")

        self.assertEqual(len(findings), 1)
        self.assertIn("G75: P1.5", findings[0].message)

    def test_a_point_inside_a_comment_is_not_code(self):
        self.assertEqual(self._findings("G75 X26. P1500 F0.05 (NOT P1.5)"), [])

    def test_other_g_codes_are_left_alone(self):
        # G7 with a further digit is not a cycle, and a non-cycle block is not
        # this rule's business.
        self.assertEqual(self._findings("G700 P1.5\nG01 X20. Q1.5"), [])

    def test_a_packed_word_gets_no_guessed_fix(self):
        # P of the first G76 block is digits, mmrraa; 01.0060 means nothing.
        findings = self._findings("G76 P01.0060 Q100 R0.05")

        self.assertEqual(len(findings), 1)
        self.assertIn("packed digits (mmrraa)", findings[0].message)
        self.assertIsNone(findings[0].hint)


class CardRuleTests(unittest.TestCase):
    """The decimal-point and range rules come from the cycle cards in specs/."""

    def found(self, code, rule="VALUE_RANGE", engine=None):
        engine = engine or LintEngine(make_cfg())
        return [(f.line, f.token) for f in rules(engine.run_all(code, snippet=True), rule)]

    def test_a_feed_of_zero_or_below_in_any_cycle(self):
        # The hand-written rule knew G70-G72; the cards give every cycle its F.
        for code in ("G71 P10 Q20 U0.4 W0.1 F0", "G73 P10 Q20 F0.0", "G74 Z-20. Q2000 F-0.1",
                     "G75 X25. P1000 F0", "G76 X16.93 Z-30. P1534 Q400 F0"):
            with self.subTest(code=code):
                self.assertEqual(len(self.found(code)), 1)

    def test_a_negative_depth_of_cut_is_found_the_parser_reads_it_as_positive(self):
        self.assertEqual(self.found("G71 U-2. R0.5"), [(1, "U-2.")])

    def test_a_thread_without_height_is_one_finding_not_two(self):
        # The parser says "nothing to cut" on the same line; the card names P.
        engine = LintEngine(make_cfg())
        findings = engine.run_all("G00 X24. Z5.\nG76 P020060 Q100 R0.05\n"
                                  "G76 X16.93 Z-30. P0 Q400 F2.5", snippet=True)

        self.assertEqual([(f.line, f.rule, f.token) for f in findings], [(3, "VALUE_RANGE", "P0")])
        self.assertEqual(findings[0].hint, "Give P a value above 0.")
        self.assertIn("thread height", findings[0].message)

    def test_a_step_of_zero_is_fine_a_peck_of_zero_is_not(self):
        # G75: P is the peck (above 0), Q the step along Z (0 for one plunge).
        self.assertEqual(self.found("G00 X42. Z-20.\nG75 X25. Z-20. P1000 Q0 F0.05"), [])
        self.assertEqual(self.found("G00 X42. Z-20.\nG75 X25. P0 F0.05"), [(2, "P0")])

    def test_the_same_letter_means_what_its_block_says(self):
        # U of the first G71 block is the depth, U of the second the allowance,
        # negative when boring.
        self.assertEqual(self.found("G71 P10 Q20 U-0.4 W0.1 F0.2"), [])

    def test_the_linter_follows_the_card(self):
        # Change the card, and the finding changes with it: nothing about
        # G75's P is written in the linter.
        from dataclasses import replace
        engine = LintEngine(make_cfg())
        card = engine.cards["G75"]
        second = card.blocks[1]
        lenient = replace(second.words["P"], decimal_point=True, min_exclusive=None)
        engine.cards = dict(engine.cards, G75=replace(
            card, blocks=(card.blocks[0], replace(second, words=dict(second.words, P=lenient)))))

        self.assertEqual(self.found("G75 X26. P1.5 F0.05", "DECIMAL_POINT", engine), [])
        self.assertEqual(self.found("G75 X26. P0 F0.05", "VALUE_RANGE", engine), [])
        self.assertEqual(len(self.found("G75 X26. P1.5 F0.05", "DECIMAL_POINT")), 1)


class LintEngineCycleTests(unittest.TestCase):
    """VC-13/VC-15: a cycle that cannot be expanded says why."""

    def _cycle_findings(self, code):
        return rules(LintEngine(make_cfg()).run_all(code), "CYCLE_")

    def test_a_roughing_cycle_without_a_depth_is_reported(self):
        findings = self._cycle_findings("\n".join([
            "G00 X52. Z2.",
            "G71 P100 Q200 U0.4 W0.1 F0.25",
            "N100 G01 X20. F0.15",
            "N200 Z-10.",
        ]))

        self.assertEqual(len(findings), 1)
        self.assertIn("depth of cut", findings[0].message)

    def test_block_numbers_that_name_nothing_are_reported(self):
        findings = self._cycle_findings("\n".join([
            "G00 X52. Z2.",
            "G71 U1.5 R0.5",
            "G71 P900 Q950 U0.4 W0.1 F0.25",
            "N100 G01 X20. F0.15",
        ]))

        self.assertEqual(len(findings), 1)
        self.assertIn("P900/Q950", findings[0].message)

    def test_a_thread_block_with_nowhere_to_go_is_reported(self):
        findings = self._cycle_findings("G00 X22. Z2.\nG92 X22. Z2. F1.5")

        self.assertEqual(len(findings), 1)
        self.assertIn("nothing to cut", findings[0].message)

    def test_a_cycle_that_expands_says_nothing(self):
        findings = self._cycle_findings("\n".join([
            "G00 X52. Z2.",
            "G71 U1.5 R0.5",
            "G71 P100 Q200 U0.4 W0.1 F0.25",
            "N100 G01 X20. F0.15",
            "N200 Z-10.",
        ]))

        self.assertEqual(findings, [])


def machine_cfg(chuck_z=-50.0, chuck_diameter=60.0, **policy_overrides):
    cfg = make_cfg(**policy_overrides)
    cfg.data["machine"] = {"chuck_z_limit": chuck_z, "chuck_diameter": chuck_diameter}
    return cfg


class DiagnosticShapeTests(unittest.TestCase):
    """Every finding is a Diagnostic with a stable id and a severity."""

    def test_every_finding_is_a_diagnostic(self):
        findings = LintEngine(make_cfg(**SHOP_HEADER)).run_all(
            "G96 S150 M03\nG71 U1. R0.5\nG71 P10 Q20 U0.4 W0.1 F0\nG76 X17. Z-30.\nM30")

        self.assertTrue(findings)
        for f in findings:
            self.assertIsInstance(f, Diagnostic)
            self.assertIn(f.severity, ("ERROR", "WARNING", "INFO"))
            self.assertRegex(f.rule, r"^[A-Z0-9_]+$")

    def test_severity_follows_the_consequence(self):
        engine = LintEngine(make_cfg(require_header_codes=["G18", "G40"], require_units="G21",
                                     require_origin="G54"))
        findings = engine.run_all("G96 S150 M03\nG00 X30. Z2.\nG76 X17. Z-30.\nM30")
        severity = {(f.rule, f.token): f.severity for f in findings}

        # Compensation or inch mode left over from another program can crash.
        self.assertEqual(severity[("HEADER_CODE", "G40")], "ERROR")
        self.assertEqual(severity[("UNITS", "G21")], "ERROR")
        # A rule about form.
        self.assertEqual(severity[("HEADER_CODE", "G18")], "WARNING")
        self.assertEqual(severity[("ORIGIN", "G54")], "WARNING")
        # Overspeed near the centre.
        self.assertEqual(severity[("G50_BEFORE_G96", "G96")], "ERROR")
        # A hint, not a finding.
        self.assertEqual([f.severity for f in rules(findings, "G76_CHECK")], ["INFO"])

    def test_what_cannot_be_cut_is_an_error_and_a_short_drawing_a_warning(self):
        engine = LintEngine(make_cfg())
        impossible = engine.run_all("G00 X0. Z0.\nG02 X40. Z-15. R1.")
        many = engine.run_all("G00 X24. Z2.\nG76 P010060 Q0 R0.05\nG76 X17.4 Z-30. P1300 Q1 F2.")

        self.assertEqual({f.severity for f in rules(impossible, "ARC_R_")}, {"ERROR"})
        self.assertEqual([f.severity for f in rules(many, "CYCLE_TOO_MANY_PASSES")], ["WARNING"])

    def test_a_pointed_p_or_q_says_what_to_write_instead(self):
        engine = LintEngine(make_cfg())
        groove = rules(engine.run_all("G75 R0.3\nG75 X26. Z-25. P1.5 Q2500 F0.05"), "DECIMAL_POINT")
        rough = rules(engine.run_all("G71 U1. R0.5\nG71 P100. Q200 U0.4 W0.1 F0.25"), "DECIMAL_POINT")

        self.assertEqual([(f.token, f.hint, f.related_code) for f in groove],
                         [("P1.5", "Write P1500.", "G75")])
        self.assertEqual([(f.token, f.hint) for f in rough], [("P100.", "Write P100.")])

    def test_a_finding_for_a_model_carries_no_empty_fields(self):
        finding = Diagnostic(line=3, severity="ERROR", rule="X", message="m")

        self.assertEqual(finding.as_dict(), {"line": 3, "severity": "ERROR", "rule": "X", "message": "m"})

    def test_lint_clean_means_no_error_and_no_warning(self):
        info = Diagnostic(1, "INFO", "G76_CHECK", "hint")
        warning = Diagnostic(1, "WARNING", "ORIGIN", "G54 expected.")
        error = Diagnostic(2, "ERROR", "UNITS", "G21 (mm) expected.")

        self.assertTrue(is_lint_clean([]))
        self.assertTrue(is_lint_clean([info]))
        self.assertFalse(is_lint_clean([info, warning]))
        self.assertEqual(blocking([info, warning, error]), [warning, error])
        # An ERROR outweighs any number of WARNINGs.
        self.assertLess(weight([warning, warning, warning]), weight([error]))

    def test_the_count_names_each_severity_worst_first(self):
        # The window called every finding an error: three notes read as
        # "3 errors found", and the simulation asked about them.
        info = Diagnostic(1, "INFO", "G76_CHECK", "hint")
        warning = Diagnostic(1, "WARNING", "ORIGIN", "G54 expected.")
        error = Diagnostic(2, "ERROR", "UNITS", "G21 (mm) expected.")

        self.assertEqual(summary([info, warning, error, warning]), "1 error, 2 warnings, 1 note")
        self.assertEqual(summary([info, info, info]), "3 notes")
        self.assertEqual(summary([]), "no findings")
        self.assertEqual(errors([info, warning, error]), [error])
        self.assertEqual(errors([info, warning, warning]), [])


class ChuckCollisionTests(unittest.TestCase):
    """A move into the chuck was drawn red and reported nowhere else."""

    def test_a_move_into_the_jaws_is_an_error_on_its_line(self):
        findings = LintEngine(machine_cfg()).run_all("G00 X40. Z2.\nG01 Z-60. F0.2\nG00 X100.")

        hits = rules(findings, "CHUCK_COLLISION")
        # The retract on line 3 starts at Z-60 inside the jaws and runs out
        # through them: a second collision, not a repeat of the first.
        self.assertEqual([(f.line, f.severity) for f in hits], [(2, "ERROR"), (3, "ERROR")])
        self.assertIn("Z-50", hits[0].message)
        self.assertIn("Ø60", hits[0].message)

    def test_a_move_outside_the_jaws_is_not(self):
        findings = LintEngine(machine_cfg()).run_all("G00 X80. Z2.\nG01 Z-60. F0.2\nG00 X100.")

        self.assertEqual(rules(findings, "CHUCK_COLLISION"), [])

    def test_without_a_chuck_diameter_every_diameter_behind_the_face_counts(self):
        findings = LintEngine(machine_cfg(chuck_diameter=None)).run_all("G00 X80. Z2.\nG01 Z-60. F0.2")

        hits = rules(findings, "CHUCK_COLLISION")
        self.assertEqual([f.line for f in hits], [2])
        self.assertIn("no chuck diameter is set", hits[0].message)

    def test_without_a_configured_chuck_nothing_is_guessed(self):
        # The parser's own default face is -5; reporting against it would turn
        # every ordinary turning pass into a collision.
        findings = LintEngine(make_cfg()).run_all("G00 X40. Z2.\nG01 Z-60. F0.2")

        self.assertEqual(rules(findings, "CHUCK_COLLISION"), [])


class SnippetTests(unittest.TestCase):
    """A generated piece of a program is not held to the rules of a whole one."""

    G71 = "G00 X42. Z2.\nG71 U1. R0.5\nG71 P10 Q20 U0.4 W0.1 F0.25\nN10 G00 X30.\nN20 G01 Z-20."

    def test_code_without_an_end_is_a_snippet(self):
        self.assertTrue(is_snippet(self.G71))
        self.assertTrue(is_snippet("G00 X30. Z2. (then M30)"))      # a comment ends nothing
        self.assertTrue(is_snippet("G01 Z-5. F0.1\nM300"))
        self.assertFalse(is_snippet(self.G71 + "\nM30"))
        self.assertFalse(is_snippet("G00 X30.\nM02"))
        self.assertFalse(is_snippet("G00 X30.\nM2"))
        self.assertFalse(is_snippet("G00 X30.\nM99"))

    def test_a_snippet_has_no_header_g50_retract_or_open_compensation_findings(self):
        engine = LintEngine(make_cfg(**SHOP_HEADER))
        code = "G96 S150 M03\nG42\nG01 X30. Z-5. F0.2\n" + self.G71

        as_program = {f.rule for f in engine.run_all(code)}
        as_snippet = {f.rule for f in engine.run_all(code, snippet=True)}

        self.assertTrue({"HEADER_CODE", "UNITS", "G50_BEFORE_G96", "TNR_OPEN_AT_END"} <= as_program)
        self.assertEqual(as_snippet & {"HEADER_CODE", "UNITS", "ORIGIN", "G50_BEFORE_G96",
                                       "RETRACT_BEFORE_M30", "TNR_OPEN_AT_END"}, set())

    def test_the_rules_of_a_cycle_and_a_collision_still_apply_to_a_snippet(self):
        engine = LintEngine(machine_cfg(protected_m_codes=[62]))
        code = ("G75 R0.3\nG75 X26. Z-25. P1.5 Q2500 F0.05\n"
                "G71 U1. R0.5\nG71 P10 Q20 U0.4 W0.1 F0\n(M62)\nG01 X40. Z-60. F0.2")

        found = {f.rule for f in engine.run_all(code, snippet=True)}

        self.assertTrue({"DECIMAL_POINT", "VALUE_RANGE", "PROTECTED_M_CODE",
                         "CHUCK_COLLISION"} <= found, found)


class RunTogetherWordTests(unittest.TestCase):
    """N10G96S180M03 is a legal block. The word boundaries the older rules
    searched for are not there between a digit and a letter, so the block
    was not seen; and a G50 in a comment was taken for the limit."""

    G96 = "G96 S180 M03\nG00 X40. Z5.\nG01 Z-20. F0.2\nG00 X60.\nZ50.\nM30"

    def found(self, code, rule):
        return [f.line for f in rules(LintEngine(make_cfg()).run_all(code), rule)]

    def test_a_g50_in_a_comment_limits_nothing(self):
        self.assertEqual(self.found("(G50)\n" + self.G96, "G50_BEFORE_G96"), [2])
        self.assertEqual(self.found("G00 X60. (G50 S2500 is in the setup sheet)\n" + self.G96,
                                    "G50_BEFORE_G96"), [2])

    def test_a_run_together_g96_is_seen(self):
        self.assertEqual(self.found(self.G96.replace("G96 S180 M03", "N10G96S180M03"),
                                    "G50_BEFORE_G96"), [1])

    def test_only_a_g50_with_an_s_is_the_limit(self):
        # G50 X Z sets the coordinate system; it limits nothing.
        self.assertEqual(self.found("G50 X0 Z0\n" + self.G96, "G50_BEFORE_G96"), [2])
        self.assertEqual(self.found("N5G50S2500\n" + self.G96, "G50_BEFORE_G96"), [])
        self.assertEqual(self.found("G50 S2500\n" + self.G96, "G50_BEFORE_G96"), [])

    def test_a_run_together_header_is_a_header(self):
        engine = LintEngine(make_cfg(**SHOP_HEADER))

        found = engine.run_all("%\nO0001\nG18G21G40G80G97\nG54\nM30\n%")

        self.assertEqual(rules(found, "HEADER_CODE") + rules(found, "UNITS")
                         + rules(found, "ORIGIN"), [])

    def test_a_run_together_reference_return_is_a_retract(self):
        self.assertEqual(self.found("G01 X10. Z-5. F0.2\nN100G28U0W0\nM30", "RETRACT_BEFORE_M30"),
                         [])

    def test_run_together_compensation_is_seen(self):
        code = "G00 X40. Z5.\nN20G42G01X30.Z0F0.2\nG01 Z-20.\nG00 X60.\nZ50.\nM30"
        self.assertEqual(self.found(code, "TNR_OPEN_AT_END"), [6])
        self.assertEqual(self.found(code.replace("G00 X60.", "G40 G00 X60."), "TNR_OPEN_AT_END"),
                         [])

    def test_g960_and_a_value_of_96_are_no_g96(self):
        self.assertEqual(self.found("G960 S180\nG00 X96.\nM30", "G50_BEFORE_G96"), [])


class WhereTheToolEndsTests(unittest.TestCase):
    """Before M30 the tool stands clear of the part. The rule read the text
    before: an X and a Z word in the last three lines were a retract, so a
    G01 into the part right before M30 passed."""

    def found(self, code):
        return rules(LintEngine(make_cfg()).run_all(code), "RETRACT_BEFORE_M30")

    def test_a_cut_into_the_part_is_no_retract(self):
        findings = self.found("G00 X40. Z5.\nG01 X30. Z0 F0.2\nM30")

        self.assertEqual([f.line for f in findings], [3])
        self.assertIn("Z0", findings[0].message)
        self.assertIn("Z5", findings[0].message)

    def test_in_front_of_everything_cut_is_clear_whichever_axis_went_first(self):
        for ending in ("G00 Z50.\nX200.", "G00 X200.\nZ50.", "G00 X200. Z50."):
            with self.subTest(ending=ending):
                self.assertEqual(self.found(f"G00 X40. Z2.\nG01 Z-20. F0.2\n{ending}\nM30"), [])

    def test_a_reference_return_after_the_last_move_is_clear(self):
        self.assertEqual(self.found("G00 X40. Z2.\nG01 Z-20. F0.2\nG28 U0 W0\nM30"), [])
        self.assertEqual(self.found("G00 X40. Z2.\nG01 Z-20. F0.2\nG30 U0 W0\nM05\nM30"), [])

    def test_a_move_back_after_the_reference_return_is_judged(self):
        found = self.found("G00 X40. Z2.\nG01 Z-20. F0.2\nG28 U0 W0\nG00 X30. Z-10.\nM30")

        self.assertEqual([f.line for f in found], [5])

    def test_the_face_of_the_stock_counts(self):
        # Nothing was cut in front of Z0, but the blank stands to Z3.
        code = "(STOCK D50 L80 Z3)\nG00 X52. Z0\nG01 X-1. F0.2\nG00 Z2.\nX100.\nM30"
        self.assertEqual(len(self.found(code)), 1)
        self.assertEqual(self.found(code.replace("G00 Z2.", "G00 Z5.")), [])

    def test_an_arc_that_bulges_forward_counts(self):
        # The arc runs from Z-2 back to Z-2 over a half circle that reaches
        # Z0, so Z-1 is still inside what it cut; its two ends say Z-2.
        code = "G00 X40. Z-2.\nG02 X32. Z-2. I-2. K0 F0.1\nG00 Z-1.\nX100.\nM30"
        self.assertEqual(len(self.found(code)), 1)
        self.assertEqual(self.found(code.replace("G00 Z-1.", "G00 Z1.")), [])

    def test_m02_ends_a_program_too_and_m99_hands_back_to_its_caller(self):
        cut = "G00 X40. Z5.\nG01 X30. Z0 F0.2\n"
        self.assertEqual([f.message.split(" ")[1] for f in self.found(cut + "M02")], ["M02"])
        self.assertEqual(self.found(cut + "M99"), [])

    def test_without_a_cut_there_is_nothing_to_stand_clear_of(self):
        self.assertEqual(self.found("G00 X40. Z5.\nM30"), [])


class ProgramEndTests(unittest.TestCase):
    """A whole program ends; a piece of one need not. What follows the end is
    never run, so it is no fault of the program -- but it is said."""

    def test_a_program_without_m30_is_reported(self):
        engine = LintEngine(make_cfg())

        findings = rules(engine.run_all("G00 X40. Z5.\nG01 Z-20. F0.2\nG00 X60."), "PROGRAM_END")

        self.assertEqual([(f.line, f.severity) for f in findings], [(3, "ERROR")])

    def test_a_piece_of_a_program_need_not_end(self):
        engine = LintEngine(make_cfg())

        self.assertEqual(rules(engine.run_all("G00 X40. Z5.", snippet=True), "PROGRAM_END"), [])

    def test_m02_and_m99_are_ends_as_well(self):
        engine = LintEngine(make_cfg())
        for end in ("M02", "M99", "N200M30"):
            with self.subTest(end=end):
                self.assertEqual(rules(engine.run_all(f"G00 X40. Z5.\n{end}"), "PROGRAM_END"), [])

    def test_what_follows_m30_is_said_and_not_checked_for_a_collision(self):
        engine = LintEngine(machine_cfg(chuck_z=-100.0, chuck_diameter=210.0))
        code = ("G97 S1000 M03\nG00 X40. Z5.\nG01 Z-20. F0.2\nG00 X60.\nZ50.\nM30\n"
                "G01 X20. Z-150. F0.2\n%")

        findings = engine.run_all(code)

        self.assertEqual([(f.line, f.rule, f.severity) for f in findings],
                         [(7, "CODE_AFTER_END", "WARNING")])


# A G71 whose contour the control takes: G00 and X in the first block, and
# every block number once. The tests below each break one thing in it.
ROUGHING = ("G00 X52. Z2.\nG71 U2. R0.5\nG71 P10 Q20 U0.4 W0.1 F0.25\n"
            "N10 G00 X30.\nG01 Z-15. F0.1\nX40.\nZ-30.\nN20 X52.\nG70 P10 Q20\n")
THREAD = "G00 X24. Z5.\nG76 P020060 Q100 R0.05\nG76 X16.93 Z-30. P1534 Q400 F2.5\n"


class ManualRuleTests(unittest.TestCase):
    """What the control refuses, from the 0i-F lathe manual (B-64604EN-1/01,
    section 4.2); not checked on the machine."""

    def found(self, code):
        return [(f.line, f.rule, f.severity)
                for f in LintEngine(make_cfg()).run_all(code, snippet=True)]

    def test_the_programs_these_rules_start_from_are_clean(self):
        self.assertEqual(self.found(ROUGHING), [])
        self.assertEqual(self.found(THREAD), [])

    def test_the_first_contour_block_writes_g00_or_g01_itself(self):
        # A G01 that is still modal from before does not count: PS0065.
        code = ROUGHING.replace("N10 G00 X30.", "N10 X30.")
        self.assertEqual(self.found(code), [(4, "CONTOUR_FIRST_BLOCK", "ERROR")])
        self.assertEqual(self.found(code.replace("N10 X30.", "N10 G1 X30.")), [])

    def test_g70_alone_does_not_ask_for_it(self):
        # The manual says it of the block P names in G71, G72 and G73.
        code = "G00 X52. Z2.\nG70 P10 Q20\nN10 X30.\nG01 Z-15. F0.1\nN20 X52.\n"
        self.assertEqual(rules(LintEngine(make_cfg()).run_all(code, snippet=True),
                               "CONTOUR_FIRST_BLOCK"), [])

    def test_the_first_contour_block_of_g71_moves_x(self):
        # PS0325. G72 the same way round: its first block moves Z.
        code = ROUGHING.replace("N10 G00 X30.\nG01 Z-15. F0.1", "N10 G00 Z0.\nG01 X30. F0.1\nZ-15.")
        self.assertIn((4, "CONTOUR_START_AXIS", "ERROR"), self.found(code))
        facing = ("G00 X82. Z2.\nG72 W1.5 R0.5\nG72 P10 Q20 U0.2 W0.1 F0.2\n"
                  "N10 G00 X40.\nG01 Z-10. F0.1\nN20 X0.\n")
        self.assertIn((4, "CONTOUR_START_AXIS", "ERROR"), self.found(facing))

    def test_compensation_inside_the_contour(self):
        # Every G40, G41 or G42 between P and Q: PS0325.
        code = ROUGHING.replace("G01 Z-15. F0.1", "G42 G01 Z-15. F0.1").replace(
            "N20 X52.", "N20 G40 X52.")
        self.assertEqual([f[0] for f in self.found(code) if f[1] == "CONTOUR_COMPENSATION"], [5, 8])

    def test_compensation_before_the_cycle_is_how_it_is_done(self):
        code = "G42 G01 X52. Z2. F0.2\n" + ROUGHING.split("\n", 1)[1] + "G40 G00 X100.\n"
        self.assertEqual(rules(LintEngine(make_cfg()).run_all(code, snippet=True),
                               "CONTOUR_COMPENSATION"), [])

    def test_a_block_number_a_cycle_names_twice(self):
        code = ROUGHING + "N10 G00 X100.\n"
        self.assertEqual(self.found(code), [(10, "BLOCK_NUMBER_TWICE", "ERROR")])
        # A number no cycle names may repeat; the manual says it of P and Q.
        self.assertEqual(self.found(ROUGHING + "N30 G00 X100.\nN30 Z50.\n"), [])

    def test_the_fields_of_g76_p(self):
        # m 01 to 99, r 00 to 99, a one of six angles. P20060 is P020060.
        for p, field in (("P000060", "mm"), ("P020045", "aa"), ("P1020060", None)):
            with self.subTest(p=p):
                findings = rules(LintEngine(make_cfg()).run_all(
                    THREAD.replace("P020060", p), snippet=True), "VALUE_RANGE")
                self.assertEqual([(f.line, f.token) for f in findings], [(2, p)])
                if field:
                    self.assertIn(f"{field} is the", findings[0].message)
                else:
                    self.assertIn("more digits than mmrraa", findings[0].message)
        self.assertEqual(self.found(THREAD.replace("P020060", "P20060")), [])
        self.assertEqual(self.found(THREAD.replace("P020060", "P019929")), [])

    def test_the_finishing_allowance_is_smaller_than_the_thread_height(self):
        # R of the first block in mm, P of the second in 0.001 mm: 1.534 here.
        self.assertEqual(self.found(THREAD.replace("R0.05", "R1.6")),
                         [(3, "THREAD_ALLOWANCE", "ERROR")])
        self.assertEqual(self.found(THREAD.replace("R0.05", "R1.5")), [])

    def test_no_nose_compensation_in_g74_g75_g76(self):
        code = "G42 G01 X24. Z5. F0.2\n" + THREAD.split("\n", 1)[1]
        self.assertEqual([f for f in self.found(code) if f[1] == "NOSE_COMP_IN_CYCLE"],
                         [(2, "NOSE_COMP_IN_CYCLE", "WARNING"),
                          (3, "NOSE_COMP_IN_CYCLE", "WARNING")])
        cancelled = "G42 G01 X24. Z6. F0.2\nG40 G00 X24. Z5.\n" + THREAD.split("\n", 1)[1]
        self.assertEqual([f for f in self.found(cancelled) if f[1] == "NOSE_COMP_IN_CYCLE"], [])
