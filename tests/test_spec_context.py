"""Which cycle cards go into a prompt, and how they read. GUI-free.

The choice is a plain search over the request or the program, so it must give
the same cards every time and for every backend.
"""
import unittest

from vibe_cnc.diagnostics import ERROR, INFO, WARNING, Diagnostic
from vibe_cnc.spec_context import (
    HEADER, MAX_CARDS, card_text, cards_for_findings, cards_for_program, cards_for_request,
    dialect_text, reference, review_reference,
)
from vibe_cnc.specs import load_specs

SPECS = load_specs()


def request_codes(text):
    return [c.code for c in cards_for_request(text, SPECS)]


def program_codes(text):
    return [c.code for c in cards_for_program(text, SPECS)]


class RequestTests(unittest.TestCase):
    def test_a_thread_brings_in_the_g76_card_every_time(self):
        # The done criterion of step C in docs/PLAN.md.
        first = request_codes("Cut an M20x2.5 thread, 30 mm long.")
        self.assertEqual(first, ["G76"])
        for _ in range(5):
            self.assertEqual(request_codes("Cut an M20x2.5 thread, 30 mm long."), first)

    def test_german_requests_find_their_cards(self):
        self.assertEqual(request_codes("Schneide ein Außengewinde M24x3, 25 lang"), ["G76"])
        self.assertEqual(request_codes("Einstich auf Ø25 bei Z-20"), ["G75"])
        self.assertEqual(request_codes("Welle längsschruppen, dann schlichten"), ["G71", "G70"])

    def test_a_request_that_names_cycles_gets_those_and_no_others(self):
        # "the face at Z0" and "groove" would bring G72 and G75 by keyword.
        self.assertEqual(
            request_codes("Rough with G71, then finish with G70, from the face at Z0; a groove later"),
            ["G71", "G70"])

    def test_cycles_come_in_the_order_the_request_names_them(self):
        self.assertEqual(request_codes("Finish with G70 after the G73"), ["G70", "G73"])
        self.assertEqual(request_codes("Thread first, then a groove"), ["G76", "G75"])

    def test_a_code_is_read_as_the_control_reads_it(self):
        self.assertEqual(request_codes("N10G76P020060Q100R0.05"), ["G76"])
        self.assertEqual(request_codes("use g076 here"), ["G76"])
        self.assertEqual(request_codes("go to X71. Z-76."), [])
        self.assertEqual(request_codes("G760 is not a code"), [])

    def test_the_longer_keyword_decides(self):
        # "face groove" is G74's; the "groove" in it is not G75.
        self.assertEqual(request_codes("Cut a face groove 5 mm deep"), ["G74"])

    def test_keywords_are_whole_words(self):
        # "od" is a G71 keyword, "rough" another, "lead" a G76 one.
        self.assertEqual(request_codes("Go through, then the method of a leader"), [])
        self.assertEqual(request_codes("Plan oder längs?"), [])

    def test_a_plain_turning_request_gets_no_card(self):
        # Facing and turning with G01 need no cycle; the dialect sheet still goes.
        self.assertEqual(request_codes(
            "Face a Ø60 bar at Z0 and turn it to Ø55 for 40 mm, constant surface speed 180"), [])

    def test_at_most_three_cards(self):
        self.assertEqual(len(cards_for_request("G71 G70 G76 G75 G74", SPECS)), MAX_CARDS)
        self.assertEqual(request_codes("G71 G70 G76 G75 G74"), ["G71", "G70", "G76"])


class ProgramTests(unittest.TestCase):
    def test_the_cycles_a_program_runs(self):
        program = ("G00 X52. Z2.\nG71 U2. R0.5\nG71 P10 Q20 U0.4 W0.1 F0.25\n"
                   "N10 G00 X30.\nN20 X52.\nG70 P10 Q20\n")
        self.assertEqual(program_codes(program), ["G71", "G70"])

    def test_comments_do_not_count(self):
        program = "(THREAD M20 WITH G76)\nG92 X19. Z-30. F2.5 ; G76 would be better\n"
        self.assertEqual(program_codes(program), [])

    def test_the_review_reference_is_empty_without_specs(self):
        self.assertEqual(review_reference("G76 P020060 Q100 R0.05", None), "")
        self.assertIn("G76 -- ", review_reference("G76 P020060 Q100 R0.05", SPECS))


class FindingTests(unittest.TestCase):
    def finding(self, line, severity, code):
        return Diagnostic(line, severity, "VALUE_RANGE", "a finding", related_code=code)

    def test_the_cards_a_correction_still_needs(self):
        findings = [self.finding(9, ERROR, "G76"), self.finding(3, WARNING, "G75"),
                    self.finding(4, ERROR, "G76"), self.finding(5, ERROR, "G96"),
                    self.finding(6, INFO, "G74"), self.finding(7, ERROR, None)]
        # By line, each once; G96 has no card; INFO is never sent to the model.
        self.assertEqual([c.code for c in cards_for_findings(findings, SPECS)], ["G75", "G76"])
        self.assertEqual([c.code for c in cards_for_findings(findings, SPECS, sent=["G76"])],
                         ["G75"])
        self.assertEqual(cards_for_findings(findings, SPECS, sent=["G75", "G76"]), [])


class TextTests(unittest.TestCase):
    def test_a_card_says_every_word_and_the_decimal_point_rule(self):
        text = card_text(SPECS.cycles["G76"])
        self.assertTrue(text.startswith("G76 -- Multiple threading cycle (not verified)"))
        self.assertIn("Block 1: G76 P Q R", text)
        self.assertIn("Block 2, the one with X, U, Z or W: G76 X U Z W R P Q F", text)
        self.assertIn("  Q: minimum depth of cut, a radius (0.001 mm, at least 0, no decimal point)",
                      text)
        self.assertIn("  F: lead (mm, above 0)", text)
        self.assertIn("How this simulator reads it (not verified):", text)
        self.assertIn("G76 X16.93 Z-30. P1534 Q400 F2.5", text)

    def test_every_card_reads_without_a_gap(self):
        for code, card in SPECS.cycles.items():
            text = card_text(card)
            for block in card.blocks:
                for letter in block.words:
                    self.assertIn(f"  {letter}: ", text, f"{code} {letter}")
            for example in card.examples:
                self.assertIn(example.code, text, code)

    def test_the_dialect_sheet(self):
        text = dialect_text(SPECS.dialect)
        self.assertTrue(text.startswith("Control: FANUC 0i-TF (Plus), G-code system A"))
        for note in SPECS.dialect.notes:
            self.assertIn(f"- {note}", text)
        self.assertIn("There is no G91", text)
        for code in list(SPECS.dialect.g_codes) + list(SPECS.dialect.m_codes):
            self.assertIn(f"  {code} ", text)
        self.assertIn("M06, M10", text)

    def test_the_reference(self):
        self.assertEqual(reference([]), "")
        text = reference([SPECS.cycles["G76"]], SPECS.dialect)
        self.assertTrue(text.startswith(HEADER))
        self.assertLess(text.index("Control: "), text.index("G76 -- "))
        self.assertNotIn("Control: ", reference([SPECS.cycles["G76"]]))


if __name__ == "__main__":
    unittest.main()
