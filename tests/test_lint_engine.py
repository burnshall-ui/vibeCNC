import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from vibe_cnc import tool_data
from vibe_cnc.lint_engine import LintEngine

# Shared so a rename in the engine breaks the positive tests below loudly,
# instead of quietly emptying the filters and turning these tests green.
M_RULE = "M-Invariant"

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
    return [f for f in findings if f["rule"] == rule]


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

        self.assertEqual([f["line"] for f in rules(findings, M_RULE)], [1, 2, 3])
        self.assertIn("commented out", findings[0]["message"])

    def test_unprotected_m_code_in_a_comment_is_ignored(self):
        engine = LintEngine(make_cfg(protected_m_codes=[8]))

        self.assertEqual(rules(engine.run_all("(M9)"), M_RULE), [])

    def test_no_finding_appears_twice_for_the_same_line_and_rule(self):
        engine = LintEngine(make_cfg(protected_m_codes=[62, 63, 64, 65]))

        findings = engine.run_all("(M62 M63)")

        seen = [(f["line"], f["rule"], f["message"]) for f in findings]
        self.assertEqual(len(seen), len(set(seen)))


class LintEngineHeaderTests(unittest.TestCase):
    """VC-10: whole words, on code, however far down the header starts."""

    def test_code_inside_a_comment_does_not_satisfy_the_header(self):
        engine = LintEngine(LintEngineHeaderTests._cfg())

        findings = engine.run_all("(G18 ist hier nicht noetig)\nG21 G40 G80 G97 G54")

        self.assertEqual([f["message"] for f in rules(findings, "Header")],
                         ["G18 expected in header."])

    def test_a_longer_code_does_not_satisfy_a_shorter_one(self):
        engine = LintEngine(LintEngineHeaderTests._cfg())

        findings = engine.run_all("G180 G21 G40 G80 G97 G54")

        self.assertEqual([f["message"] for f in rules(findings, "Header")],
                         ["G18 expected in header."])

    def test_comment_block_does_not_push_the_header_out_of_range(self):
        engine = LintEngine(LintEngineHeaderTests._cfg())

        with open(FIXTURE, "r", encoding="utf-8") as handle:
            findings = engine.run_all(handle.read())

        self.assertEqual(rules(findings, "Header"), [])
        self.assertEqual(rules(findings, "Units"), [])
        self.assertEqual(rules(findings, "Origin"), [])

    def test_a_genuinely_missing_header_is_still_reported(self):
        engine = LintEngine(LintEngineHeaderTests._cfg())

        findings = engine.run_all("G00 X50. Z2.\nG01 Z0. F0.2")

        self.assertEqual(len(rules(findings, "Header")), 4)
        self.assertEqual(len(rules(findings, "Units")), 1)
        self.assertEqual(len(rules(findings, "Origin")), 1)

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
                self.assertEqual(rules(findings, "G7x"), [])

    def test_zero_and_negative_feed_are_still_reported(self):
        engine = LintEngine(make_cfg())

        for feed in ("F0", "F0.0", "F0.00", "F-0.2"):
            with self.subTest(feed=feed):
                findings = engine.run_all(f"G71 P100 Q200 {feed}")
                self.assertEqual(len(rules(findings, "G7x")), 1)

    def test_cycle_block_without_a_feed_is_not_reported(self):
        # G71 is two blocks: depth and retract first, P/Q/U/W/F second. F is
        # modal besides, so its absence on one block means nothing.
        engine = LintEngine(make_cfg())

        self.assertEqual(rules(engine.run_all("G71 U1.5 R0.5"), "G7x"), [])


class LintEngineRetractTests(unittest.TestCase):
    """VC-10: G28 and the U/W words are how a Fanuc lathe retracts."""

    def test_g28_counts_as_a_retract(self):
        engine = LintEngine(make_cfg())

        self.assertEqual(rules(engine.run_all("G01 X10. Z-5.\nG28 U0 W0\nM30"),
                               "Retract"), [])

    def test_incremental_retract_counts(self):
        engine = LintEngine(make_cfg())

        self.assertEqual(rules(engine.run_all("G00 W50.\nG00 U150.\nM30"),
                               "Retract"), [])

    def test_program_that_never_pulls_z_clear_is_still_reported(self):
        engine = LintEngine(make_cfg())

        findings = engine.run_all("G01 X-5.\nG00 X200.\nM30")

        self.assertEqual(len(rules(findings, "Retract")), 1)


class LintEngineParserWarningTests(unittest.TestCase):
    """VC-16: geometry the parser rejects has to reach the operator."""

    def test_impossible_r_geometry_is_reported(self):
        engine = LintEngine(make_cfg())

        findings = engine.run_all("G00 X0. Z0.\nG02 X40. Z-15. R1.")

        arc = rules(findings, "Arc R")
        self.assertEqual(len(arc), 1)
        self.assertEqual(arc[0]["line"], 2)
        self.assertIn("half the chord", arc[0]["message"])

    def test_arc_without_a_centre_is_reported(self):
        engine = LintEngine(make_cfg())

        findings = engine.run_all("G00 X30. Z0.\nG02 X40. Z-5.")

        self.assertEqual(len(rules(findings, "Arc")), 1)

    def test_valid_geometry_produces_no_arc_finding(self):
        engine = LintEngine(make_cfg())

        findings = engine.run_all("G00 X30. Z0.\nG02 X40. Z-5. R5.")

        self.assertEqual([f for f in findings if f["rule"].startswith("Arc")], [])

    def test_findings_come_back_in_program_order(self):
        engine = LintEngine(make_cfg(protected_m_codes=[8]))

        findings = engine.run_all("\n".join([
            "G00 X0. Z0.",
            "G02 X40. Z-15. R1.",   # parser: impossible radius
            "(M8)",                 # rule: protected M-code commented out
            "G00 X30. Z0.",
            "G02 X40. Z-5.",        # parser: no centre
        ]))

        lines = [f["line"] for f in findings]
        self.assertEqual(lines, sorted(lines))
        self.assertIn("Arc R", [f["rule"] for f in findings])
        self.assertIn(M_RULE, [f["rule"] for f in findings])

    def test_every_finding_carries_the_keys_the_ui_reads(self):
        engine = LintEngine(make_cfg())

        findings = engine.run_all("G00 X0. Z0.\nG02 X40. Z-15. R1.")

        self.assertTrue(findings)
        for finding in findings:
            self.assertIsInstance(finding["line"], int)
            self.assertIsInstance(finding["rule"], str)
            self.assertIsInstance(finding["message"], str)


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
        return rules(findings, "G41/G42")

    def test_a_tool_without_a_tip_number_is_reported(self):
        findings = self._compensation_findings({"t": 1, "insert_radius_mm": 0.8})

        self.assertEqual(len(findings), 1)
        self.assertIn("nose_direction", findings[0]["message"])
        self.assertEqual(findings[0]["line"], 2)   # the G42 block

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
        self.assertIn("nose_direction", findings[0]["message"])

    def test_a_missing_radius_is_reported_instead_not_as_well(self):
        # Without a radius nothing is offset at all, so the tip number makes no
        # difference and saying both would just be noise.
        findings = self._compensation_findings({"t": 1, "name": "no radius"})

        self.assertEqual(len(findings), 1)
        self.assertIn("insert_radius_mm", findings[0]["message"])


class LintEngineCycleTests(unittest.TestCase):
    """VC-13/VC-15: a cycle that cannot be expanded says why."""

    def _cycle_findings(self, code):
        return rules(LintEngine(make_cfg()).run_all(code), "Cycle")

    def test_a_roughing_cycle_without_a_depth_is_reported(self):
        findings = self._cycle_findings("\n".join([
            "G00 X52. Z2.",
            "G71 P100 Q200 U0.4 W0.1 F0.25",
            "N100 G01 X20. F0.15",
            "N200 Z-10.",
        ]))

        self.assertEqual(len(findings), 1)
        self.assertIn("depth of cut", findings[0]["message"])

    def test_block_numbers_that_name_nothing_are_reported(self):
        findings = self._cycle_findings("\n".join([
            "G00 X52. Z2.",
            "G71 U1.5 R0.5",
            "G71 P900 Q950 U0.4 W0.1 F0.25",
            "N100 G01 X20. F0.15",
        ]))

        self.assertEqual(len(findings), 1)
        self.assertIn("P900/Q950", findings[0]["message"])

    def test_a_thread_block_with_nowhere_to_go_is_reported(self):
        findings = self._cycle_findings("G00 X22. Z2.\nG92 X22. Z2. F1.5")

        self.assertEqual(len(findings), 1)
        self.assertIn("nothing to cut", findings[0]["message"])

    def test_a_cycle_that_expands_says_nothing(self):
        findings = self._cycle_findings("\n".join([
            "G00 X52. Z2.",
            "G71 U1.5 R0.5",
            "G71 P100 Q200 U0.4 W0.1 F0.25",
            "N100 G01 X20. F0.15",
            "N200 Z-10.",
        ]))

        self.assertEqual(findings, [])
