import unittest

from vibe_cnc.gcode_text import uppercase_code


class UppercaseCodeTests(unittest.TestCase):
    def test_code_is_upper_cased(self):
        self.assertEqual(uppercase_code("g01 x10. z-5. f0.2"), "G01 X10. Z-5. F0.2")

    def test_a_parenthesised_comment_keeps_its_case(self):
        self.assertEqual(uppercase_code("g00 x10. (Rapid to 10 mm)"),
                         "G00 X10. (Rapid to 10 mm)")

    def test_code_after_a_comment_is_upper_cased_again(self):
        self.assertEqual(uppercase_code("(tool one) t0101 (next) g00"),
                         "(tool one) T0101 (next) G00")

    def test_a_comment_that_is_still_being_typed_keeps_its_case(self):
        # No closing parenthesis yet: everything after the opening one is prose.
        self.assertEqual(uppercase_code("g00 (rapid to the stock"),
                         "G00 (rapid to the stock")

    def test_everything_after_a_semicolon_keeps_its_case(self):
        self.assertEqual(uppercase_code("g00 x10. ; retract to 10 mm"),
                         "G00 X10. ; retract to 10 mm")

    def test_a_semicolon_inside_a_comment_does_not_end_the_comment_early(self):
        self.assertEqual(uppercase_code("(a; b) g00"), "(a; b) G00")

    def test_a_stock_comment_keeps_its_letters(self):
        self.assertEqual(uppercase_code("(stock d40 l50)"), "(stock d40 l50)")

    def test_already_upper_case_text_is_unchanged(self):
        self.assertEqual(uppercase_code("G01 X10. (OK)"), "G01 X10. (OK)")

    def test_the_length_never_changes(self):
        # 'ß'.upper() is 'SS'. A caller applying the result character by
        # character relies on the two strings being the same length.
        for text in ("g01 straße", "(straße) g01", "x10 ; straße", ""):
            with self.subTest(text):
                self.assertEqual(len(uppercase_code(text)), len(text))


if __name__ == "__main__":
    unittest.main()
