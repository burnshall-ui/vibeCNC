# specs.py — the control's data, read from specs/: which words a cycle takes,
# their units, where a decimal point is forbidden, value ranges, assumptions,
# examples; and the G- and M-codes of the dialect. GUI-free, standard library
# only (tomllib, Python 3.11+), so it loads in the suite with the linter.
#
# The data lives in TOML files; what a cycle *does* -- how G71 becomes passes
# -- stays in the parser. A card that is wrong is reported with its file and
# the key that is wrong, so a typo in a card cannot pass as data.
# See docs/PLAN.md, step B.
import math
import os
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC_DIR = os.path.join(ROOT, "specs", "fanuc_0i_tf")

LETTERS = frozenset("ABCDEFHIJKLMOPQRSTUVWXYZ")    # every address a block can carry but G and N


class SpecError(ValueError):
    """A spec file that cannot be read as one. The message names the file and
    the key."""


@dataclass(frozen=True)
class Source:
    text: str
    verified: bool          # true once checked on the machine
    verified_on: str = ""   # when and on which machine


@dataclass(frozen=True)
class Word:
    letter: str
    meaning: str
    unit: str = ""                          # "mm", "0.001 mm", "block number", "count", ...
    decimal_point: bool = True              # False: the control stops with an alarm on one
    form: str = ""                          # a packed word, e.g. "mmrraa"
    min: Optional[float] = None             # value >= min
    min_exclusive: Optional[float] = None   # value > min_exclusive
    max: Optional[float] = None             # value <= max

    def out_of_range(self, value: float) -> Optional[str]:
        """What the value breaks, in words ("above 0"), or None. Ranges are in
        the word's own unit, as it is written."""
        if self.min_exclusive is not None and not value > self.min_exclusive:
            return f"above {self.min_exclusive:g}"
        if self.min is not None and value < self.min:
            return f"at least {self.min:g}"
        if self.max is not None and value > self.max:
            return f"at most {self.max:g}"
        return None


@dataclass(frozen=True)
class Block:
    words: Dict[str, Word]
    # A block carrying any of these letters is this block. The first block of
    # a card has none: it is what a line is when no other block claims it.
    recognized_by: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Assumption:
    text: str
    verified: bool


@dataclass(frozen=True)
class Example:
    title: str
    code: str


@dataclass(frozen=True)
class CycleCard:
    code: str                       # "G76"
    name: str
    keywords: Tuple[str, ...]
    source: Source
    blocks: Tuple[Block, ...]
    assumptions: Tuple[Assumption, ...]
    examples: Tuple[Example, ...]
    path: str

    def block_of(self, letters) -> Block:
        """Which block of the cycle a line is, from the letters it carries."""
        for block in self.blocks[1:]:
            if any(letter in letters for letter in block.recognized_by):
                return block
        return self.blocks[0]


@dataclass(frozen=True)
class Dialect:
    name: str
    source: Source
    g_codes: Dict[str, str]
    m_codes: Dict[str, str]
    machine_specific: Tuple[str, ...]   # M-codes whose meaning the machine builder decides
    notes: Tuple[str, ...]              # what a model gets wrong about this control, in sentences
    path: str


# ---- reading, with every mistake named -------------------------------------------

class _Reader:
    """Takes values out of one parsed file and says where a wrong one is."""

    def __init__(self, path: str):
        self.path = path

    def fail(self, key: str, problem: str):
        raise SpecError(f"{os.path.relpath(self.path, ROOT)}: {key}: {problem}")

    def table(self, value, key: str) -> dict:
        if not isinstance(value, dict):
            self.fail(key, f"expected a table, got {type(value).__name__}")
        return value

    def only(self, table: dict, key: str, allowed):
        """No keys but these: a misspelt key would otherwise be dropped in silence."""
        for name in table:
            if name not in allowed:
                self.fail(f"{key}.{name}" if key else name,
                          f"unknown key; expected one of {', '.join(sorted(allowed))}")

    def text(self, table: dict, key: str, where: str, required=True, default="") -> str:
        if key not in table:
            if required:
                self.fail(f"{where}.{key}" if where else key, "missing")
            return default
        value = table[key]
        if not isinstance(value, str):
            self.fail(f"{where}.{key}" if where else key,
                      f"expected text, got {type(value).__name__}")
        return value

    def flag(self, table: dict, key: str, where: str, required=True, default=False) -> bool:
        if key not in table:
            if required:
                self.fail(f"{where}.{key}" if where else key, "missing")
            return default
        value = table[key]
        if not isinstance(value, bool):
            self.fail(f"{where}.{key}" if where else key,
                      f"expected true or false, got {value!r}")
        return value

    def number(self, table: dict, key: str, where: str) -> Optional[float]:
        if key not in table:
            return None
        value = table[key]
        # bool is an int in Python; true is not a number here.
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            self.fail(f"{where}.{key}", f"expected a number, got {value!r}")
        return float(value)

    def texts(self, table: dict, key: str, where: str) -> Tuple[str, ...]:
        value = table.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            self.fail(f"{where}.{key}" if where else key, "expected a list of text")
        return tuple(value)

    def tables(self, table: dict, key: str, where: str = "") -> list:
        value = table.get(key, [])
        name = f"{where}.{key}" if where else key
        if not isinstance(value, list):
            self.fail(name, "expected an array of tables, as [[" + key + "]]")
        for index, item in enumerate(value):
            self.table(item, f"{name}[{index}]")
        return value

    def source(self, data: dict) -> Source:
        if "source" not in data:
            self.fail("source", "missing; every spec file says where its content comes from")
        table = self.table(data["source"], "source")
        self.only(table, "source", {"text", "verified", "verified_on"})
        source = Source(self.text(table, "text", "source"),
                        self.flag(table, "verified", "source"),
                        self.text(table, "verified_on", "source", required=False))
        if source.verified and not source.verified_on.strip():
            self.fail("source.verified_on", "a verified file says when and on which machine")
        return source


def _load(path: str) -> Tuple[_Reader, dict]:
    reader = _Reader(path)
    try:
        with open(path, "rb") as handle:
            return reader, tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:
        raise SpecError(f"{os.path.relpath(path, ROOT)}: not valid TOML: {error}") from None


_CARD_KEYS = {"code", "name", "keywords", "source", "block", "assumption", "example"}
_WORD_KEYS = {"meaning", "unit", "decimal_point", "form", "min", "min_exclusive", "max"}


def load_card(path: str) -> CycleCard:
    reader, data = _load(path)
    reader.only(data, "", _CARD_KEYS)
    code = reader.text(data, "code", "")
    expected = os.path.splitext(os.path.basename(path))[0]
    if code != expected:
        reader.fail("code", f"is {code!r}, but the file is named for {expected!r}")

    blocks = []
    for index, table in enumerate(reader.tables(data, "block")):
        where = f"block[{index}]"
        reader.only(table, where, {"words", "recognized_by"})
        recognized = reader.texts(table, "recognized_by", where)
        if index and not recognized:
            reader.fail(f"{where}.recognized_by",
                        "missing; every block after the first says which letters mark it")
        if index == 0 and recognized:
            reader.fail(f"{where}.recognized_by",
                        "the first block is what a line is when no other block claims it")
        words = {}
        for letter, spec in reader.table(table.get("words", {}), f"{where}.words").items():
            key = f"{where}.words.{letter}"
            if letter not in LETTERS:
                reader.fail(key, "not an address letter (G and N are not words of a cycle)")
            reader.table(spec, key)
            reader.only(spec, key, _WORD_KEYS)
            words[letter] = Word(letter, reader.text(spec, "meaning", key),
                                 reader.text(spec, "unit", key, required=False),
                                 reader.flag(spec, "decimal_point", key, required=False, default=True),
                                 reader.text(spec, "form", key, required=False),
                                 reader.number(spec, "min", key),
                                 reader.number(spec, "min_exclusive", key),
                                 reader.number(spec, "max", key))
        blocks.append(Block(words, recognized))
    if not blocks:
        reader.fail("block", "missing; a card has at least one [[block]]")

    assumptions = []
    for index, table in enumerate(reader.tables(data, "assumption")):
        where = f"assumption[{index}]"
        reader.only(table, where, {"text", "verified"})
        assumptions.append(Assumption(reader.text(table, "text", where),
                                      reader.flag(table, "verified", where)))
    examples = []
    for index, table in enumerate(reader.tables(data, "example")):
        where = f"example[{index}]"
        reader.only(table, where, {"title", "code"})
        examples.append(Example(reader.text(table, "title", where),
                                reader.text(table, "code", where).strip("\n")))

    return CycleCard(code, reader.text(data, "name", ""), reader.texts(data, "keywords", ""),
                     reader.source(data), tuple(blocks), tuple(assumptions), tuple(examples), path)


def load_dialect(path: str) -> Dialect:
    reader, data = _load(path)
    reader.only(data, "", {"name", "source", "notes", "g", "m", "machine_specific"})
    codes = {}
    for group in ("g", "m"):
        table = reader.table(data.get(group, {}), group)
        for code, text in table.items():
            if not isinstance(text, str):
                reader.fail(f"{group}.{code}", f"expected text, got {type(text).__name__}")
            if not (code[:1] == group.upper() and code[1:].isdigit()):
                reader.fail(f"{group}.{code}", f"not a {group.upper()}-code such as {group.upper()}00")
        codes[group] = dict(table)
    machine = reader.texts(data, "machine_specific", "")
    for code in machine:
        if code not in codes["m"]:
            reader.fail("machine_specific", f"{code} is not in [m]")
    return Dialect(reader.text(data, "name", ""), reader.source(data), codes["g"], codes["m"],
                   machine, reader.texts(data, "notes", ""), path)


@dataclass(frozen=True)
class Specs:
    dialect: Dialect
    cycles: Dict[str, CycleCard]    # by code, "G70" to "G76"


@lru_cache(maxsize=None)
def load_specs(folder: str = SPEC_DIR) -> Specs:
    """Every spec file of one control, read once."""
    dialect = load_dialect(os.path.join(folder, "dialect.toml"))
    cycles = {}
    cycle_dir = os.path.join(folder, "cycles")
    for name in sorted(os.listdir(cycle_dir)):
        if name.endswith(".toml"):
            card = load_card(os.path.join(cycle_dir, name))
            cycles[card.code] = card
    return Specs(dialect, cycles)
