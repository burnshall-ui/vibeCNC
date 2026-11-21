"""The spec files: cycle cards and the dialect.

A card and the tools can never disagree in silence: every example in a card
must come through the linter without a finding and be drawn by the parser. A
broken card must name its file and the key that is wrong. GUI-free.
"""
import os
import tempfile
import textwrap
import unittest
from types import SimpleNamespace

from vibe_cnc.gcode_parser import GCodeParser
from vibe_cnc.lint_engine import LintEngine, is_snippet
from vibe_cnc.specs import SPEC_DIR, SpecError, Word, load_card, load_dialect, load_specs

GOOD_CARD = '''
code = "G99"
name = "A test cycle"
keywords = ["test"]

[source]
text = "written for a test"
verified = false

[[block]]
[block.words.U]
meaning = "depth"
min_exclusive = 0

[[block]]
recognized_by = ["P", "Q"]
[block.words.P]
meaning = "first block"
unit = "block number"
decimal_point = false
'''


class CardsTests(unittest.TestCase):
    def test_there_is_a_card_for_every_cycle_g70_to_g76(self):
        self.assertEqual(sorted(load_specs().cycles), [f"G7{n}" for n in range(7)])

    def test_every_example_is_lint_clean_and_drawn(self):
        engine = LintEngine(SimpleNamespace(data={"policies": {}}))
        for code, card in load_specs().cycles.items():
            self.assertTrue(card.examples, f"{code} has no example")
            for example in card.examples:
                with self.subTest(card=code, example=example.title):
                    findings = engine.run_all(example.code, snippet=is_snippet(example.code))
                    self.assertEqual([(f.line, f.rule) for f in findings], [])
                    parser = GCodeParser()
                    paths = parser.parse(example.code)
                    self.assertEqual(parser.warnings, [])
                    drawn = sum(len(paths.get(kind) or []) for kind in ("cut", "finish", "thread"))
                    self.assertGreater(drawn, 0, "the parser drew no cut")

    def test_every_card_says_where_it_comes_from_and_what_it_assumes(self):
        for code, card in load_specs().cycles.items():
            with self.subTest(card=code):
                self.assertTrue(card.source.text.strip())
                self.assertTrue(card.assumptions, "the parser's assumptions belong in the card")
                self.assertIn(code.lower(), card.keywords)

    def test_the_shop_asks_in_german_too(self):
        keywords = {code: card.keywords for code, card in load_specs().cycles.items()}
        self.assertIn("gewinde", keywords["G76"])
        self.assertIn("einstich", keywords["G75"])
        self.assertIn("schruppen", keywords["G71"])

    def test_p_and_q_of_every_cycle_take_no_decimal_point(self):
        # What policies.md says, now read from the cards.
        for code, card in load_specs().cycles.items():
            for block in card.blocks:
                for letter in "PQ":
                    if letter in block.words:
                        with self.subTest(card=code, word=letter):
                            self.assertFalse(block.words[letter].decimal_point)

    def test_a_line_is_the_block_its_letters_say(self):
        g71 = load_specs().cycles["G71"]
        self.assertIs(g71.block_of({"P", "Q", "U", "W", "F"}), g71.blocks[1])
        self.assertIs(g71.block_of({"U", "R"}), g71.blocks[0])
        g76 = load_specs().cycles["G76"]
        self.assertIs(g76.block_of({"P", "Q", "R"}), g76.blocks[0])
        self.assertIs(g76.block_of({"X", "Z", "P", "Q", "F"}), g76.blocks[1])


class WordTests(unittest.TestCase):
    def test_a_range_says_what_it_wants(self):
        word = Word("U", "depth", min_exclusive=0)
        self.assertEqual(word.out_of_range(0), "above 0")
        self.assertEqual(word.out_of_range(-1.5), "above 0")
        self.assertIsNone(word.out_of_range(0.5))
        self.assertEqual(Word("P", "step", min=0).out_of_range(-1), "at least 0")
        self.assertIsNone(Word("P", "step", min=0).out_of_range(0))
        self.assertEqual(Word("R", "count", max=10).out_of_range(11), "at most 10")
        self.assertIsNone(Word("R", "anything").out_of_range(-1e9))


class DialectTests(unittest.TestCase):
    def test_it_is_g_code_system_a(self):
        dialect = load_specs().dialect
        self.assertIn("system A", dialect.name)
        for cycle in ("G90", "G92", "G94"):
            self.assertIn("cycle", dialect.g_codes[cycle].lower(), cycle)
        # U and W are the incremental words; the feed modes are G98 and G99.
        self.assertNotIn("G91", dialect.g_codes)
        self.assertNotIn("G95", dialect.g_codes)
        self.assertIn("per minute", dialect.g_codes["G98"])
        self.assertIn("per revolution", dialect.g_codes["G99"])
        self.assertIn("speed limit", dialect.g_codes["G50"])

    def test_every_shipped_card_names_the_manual(self):
        for code, card in load_specs().cycles.items():
            with self.subTest(code=code):
                self.assertIn("B-64604EN-1/01", card.source.text)
                self.assertFalse(card.source.verified)      # the machine, not the manual
                self.assertTrue(any(a.manual for a in card.assumptions))
        self.assertIn("B-64604EN-1/01", load_specs().dialect.source.text)

    def test_the_g76_p_fields(self):
        word = load_specs().cycles["G76"].blocks[0].words["P"]
        self.assertEqual(word.unpack(20060), {"m": 2, "r": 0, "a": 60})
        self.assertEqual([(p.letter, p.allowed()) for p in word.parts],
                         [("m", "01 to 99"), ("r", "00 to 99"),
                          ("a", "one of 80, 60, 55, 30, 29, 00")])

    def test_the_notes_for_a_model_are_read(self):
        notes = load_specs().dialect.notes
        self.assertTrue(notes)
        self.assertTrue(any("no G91" in note for note in notes))

    def test_the_machine_builders_m_codes_are_marked(self):
        dialect = load_specs().dialect
        self.assertIn("M06", dialect.machine_specific)
        self.assertNotIn("M30", dialect.machine_specific)


class BrokenCardTests(unittest.TestCase):
    """Each mistake names the file and the key."""

    def card(self, text, name="G99.toml"):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = os.path.join(folder.name, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(textwrap.dedent(text))
        return path

    def fails(self, text, key, name="G99.toml"):
        with self.assertRaises(SpecError) as caught:
            load_card(self.card(text, name))
        message = str(caught.exception)
        self.assertIn(name, message)
        self.assertIn(key, message)
        return message

    def test_the_good_card_loads(self):
        card = load_card(self.card(GOOD_CARD))
        self.assertEqual(card.code, "G99")
        self.assertFalse(card.blocks[1].words["P"].decimal_point)

    def test_a_misspelt_key_is_not_dropped(self):
        self.fails(GOOD_CARD.replace("decimal_point = false", "decimal_piont = false"),
                   "block[1].words.P.decimal_piont")

    def test_a_number_written_as_text(self):
        message = self.fails(GOOD_CARD.replace("min_exclusive = 0", 'min_exclusive = "0"'),
                             "block[0].words.U.min_exclusive")
        self.assertIn("expected a number", message)

    def test_a_flag_written_as_text(self):
        self.fails(GOOD_CARD.replace("decimal_point = false", 'decimal_point = "no"'),
                   "block[1].words.P.decimal_point")

    def test_a_card_in_the_wrong_file(self):
        self.fails(GOOD_CARD, "code", name="G98.toml")

    def test_a_card_without_a_source(self):
        self.fails(GOOD_CARD.replace('[source]\ntext = "written for a test"\nverified = false\n', ""),
                   "source")

    def test_verified_says_when_and_where(self):
        self.fails(GOOD_CARD.replace("verified = false", "verified = true"), "source.verified_on")

    def test_a_second_block_says_what_marks_it(self):
        self.fails(GOOD_CARD.replace('recognized_by = ["P", "Q"]\n', ""), "block[1].recognized_by")

    def test_g_is_not_a_word_of_a_cycle(self):
        self.fails(GOOD_CARD.replace("[block.words.U]", "[block.words.G]"), "block[0].words.G")

    def test_a_word_without_a_meaning(self):
        self.fails(GOOD_CARD.replace('meaning = "depth"\n', ""), "block[0].words.U.meaning")

    def test_not_toml_at_all(self):
        self.fails(GOOD_CARD.replace("[[block]]", "[[block", 1), "not valid TOML")

    def test_a_dialect_entry_that_is_not_a_code(self):
        path = self.card('name = "x"\nmachine_specific = []\n[source]\ntext = "t"\n'
                         'verified = false\n[g]\nG00 = "Rapid"\nX00 = "no"\n', name="dialect.toml")
        with self.assertRaises(SpecError) as caught:
            load_dialect(path)
        self.assertIn("dialect.toml", str(caught.exception))
        self.assertIn("g.X00", str(caught.exception))

    def test_dialect_notes_that_are_not_a_list_of_text(self):
        path = self.card('name = "x"\nnotes = "one"\n[source]\ntext = "t"\n'
                         'verified = false\n[g]\nG00 = "Rapid"\n', name="dialect.toml")
        with self.assertRaises(SpecError) as caught:
            load_dialect(path)
        self.assertIn("notes: expected a list of text", str(caught.exception))

    PACKED = GOOD_CARD + '''
[block.words.X]
meaning = "packed"
form = "mmaa"
[block.words.X.parts.m]
meaning = "count"
min = 1
max = 99
[block.words.X.parts.a]
meaning = "angle"
values = [60, 0]
'''

    def test_a_packed_word_loads_with_its_parts(self):
        word = load_card(self.card(self.PACKED)).blocks[1].words["X"]
        self.assertEqual([p.letter for p in word.parts], ["m", "a"])
        self.assertEqual(word.unpack(160), {"m": 1, "a": 60})        # X0160
        self.assertIsNone(word.unpack(10160))                        # five digits for four
        self.assertEqual(word.parts[0].out_of_range(0), "01 to 99")
        self.assertIsNone(word.parts[0].out_of_range(99))
        self.assertEqual(word.parts[1].out_of_range(45), "one of 60, 00")
        self.assertIsNone(word.parts[1].out_of_range(0))

    def test_parts_without_a_form(self):
        self.fails(self.PACKED.replace('form = "mmaa"\n', ""), "block[1].words.X.parts")

    def test_a_part_that_is_not_in_the_form(self):
        self.fails(self.PACKED.replace("[block.words.X.parts.a]", "[block.words.X.parts.r]"),
                   "block[1].words.X.parts.r")

    def test_part_values_that_are_not_whole_numbers(self):
        self.fails(self.PACKED.replace("values = [60, 0]", "values = [60.5]"),
                   "block[1].words.X.parts.a.values")

    def test_an_assumption_names_where_the_manual_says_it(self):
        card = load_card(self.card(GOOD_CARD + '''
[[assumption]]
text = "said"
verified = false
manual = "4.2.7"

[[assumption]]
text = "not said"
verified = false
'''))
        self.assertEqual([a.manual for a in card.assumptions], ["4.2.7", ""])

    def test_the_shipped_files_load(self):
        # Through the cache-free path, so a broken shipped file fails here too.
        load_dialect(os.path.join(SPEC_DIR, "dialect.toml"))
        for name in os.listdir(os.path.join(SPEC_DIR, "cycles")):
            load_card(os.path.join(SPEC_DIR, "cycles", name))


if __name__ == "__main__":
    unittest.main()
