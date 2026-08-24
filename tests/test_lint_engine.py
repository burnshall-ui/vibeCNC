import unittest
from types import SimpleNamespace

from vibe_cnc.lint_engine import LintEngine

# Shared so a rename in the engine breaks the positive test below loudly,
# instead of quietly emptying the filters and turning these tests green.
M_RULE = "M-Invariant"


def make_cfg(**policy_overrides):
    policies = {
        "require_header_codes": [],
        "require_units": None,
        "require_origin": None,
        "protected_m_codes": [],
    }
    policies.update(policy_overrides)
    return SimpleNamespace(data={"policies": policies})


class LintEngineProtectedMTests(unittest.TestCase):
    def test_commented_protected_m_codes_are_ignored(self):
        engine = LintEngine(make_cfg(protected_m_codes=[8]))

        findings = engine.run_all("(M8)\n; M8\nG01 X1 ; M8")

        self.assertEqual(
            [finding for finding in findings if finding["rule"] == M_RULE],
            [],
        )

    def test_protected_m_code_with_other_m_code_still_warns(self):
        engine = LintEngine(make_cfg(protected_m_codes=[8]))

        findings = engine.run_all("M8 M9")

        self.assertEqual(
            [finding["message"] for finding in findings if finding["rule"] == M_RULE],
            ["Do not override M8 with another M-code."],
        )


class LintEngineParserWarningTests(unittest.TestCase):
    """VC-16: geometry the parser rejects has to reach the operator."""

    def test_impossible_r_geometry_is_reported(self):
        engine = LintEngine(make_cfg())

        findings = engine.run_all("G00 X0. Z0.\nG02 X40. Z-15. R1.")

        arc = [f for f in findings if f["rule"] == "Arc R"]
        self.assertEqual(len(arc), 1)
        self.assertEqual(arc[0]["line"], 2)
        self.assertIn("half the chord", arc[0]["message"])

    def test_arc_without_a_centre_is_reported(self):
        engine = LintEngine(make_cfg())

        findings = engine.run_all("G00 X30. Z0.\nG02 X40. Z-5.")

        self.assertEqual([f["rule"] for f in findings if f["rule"] == "Arc"], ["Arc"])

    def test_valid_geometry_produces_no_arc_finding(self):
        engine = LintEngine(make_cfg())

        findings = engine.run_all("G00 X30. Z0.\nG02 X40. Z-5. R5.")

        self.assertEqual([f for f in findings if f["rule"].startswith("Arc")], [])

    def test_findings_come_back_in_program_order(self):
        engine = LintEngine(make_cfg(protected_m_codes=[8]))

        findings = engine.run_all("\n".join([
            "G00 X0. Z0.",
            "G02 X40. Z-15. R1.",   # parser: impossible radius
            "M8 M9",                # rule: protected M-code
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
