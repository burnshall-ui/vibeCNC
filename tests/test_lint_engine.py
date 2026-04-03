import unittest
from types import SimpleNamespace

from vibe_cnc.lint_engine import LintEngine


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
            [finding for finding in findings if finding["rule"] == "M-Invarianz"],
            [],
        )

    def test_protected_m_code_with_other_m_code_still_warns(self):
        engine = LintEngine(make_cfg(protected_m_codes=[8]))

        findings = engine.run_all("M8 M9")

        self.assertEqual(
            [finding["message"] for finding in findings if finding["rule"] == "M-Invarianz"],
            ["M8 nicht mit anderem M-Code überschreiben."],
        )
