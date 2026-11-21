import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from vibe_cnc import tool_data
from vibe_cnc.diagnostics import Diagnostic, blocking, is_lint_clean, weight
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
