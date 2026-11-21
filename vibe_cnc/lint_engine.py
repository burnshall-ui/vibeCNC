import re
from typing import List, Optional

from .diagnostics import ERROR, INFO, WARNING, Diagnostic, Severity
from .gcode_parser import PROGRAM_END
from .specs import load_specs


def word(text: str):
    """A code word as the control reads it: G96 also as G096, and run
    together with its neighbours as in N10G96S180M03; not G960, and not the
    96 of X96. The word boundaries these rules used before missed the
    run-together form: a digit and a letter are both word characters, so
    there is no boundary between them."""
    match = re.fullmatch(r'([A-Z])0*(\d+)', text.strip(), re.IGNORECASE)
    if match is None:
        return re.compile(rf'(?<![A-Z]){re.escape(text.strip())}(?!\d)', re.IGNORECASE)
    letter, number = match.groups()
    return re.compile(rf'(?<![A-Z]){letter}0*{int(number)}(?!\d)', re.IGNORECASE)


def code_only(line: str) -> str:
    """One line without its comments: parentheses, and everything after ';'."""
    return re.sub(r';.*', '', re.sub(r'\(.*?\)', '', line))


def is_snippet(code: str) -> bool:
    """Code that never ends the program is a piece of one, not a program.

    A generated "G71 for T1" has no header and no M30, and is not wrong for
    it: the program it goes into has them. Only the code words count; a
    comment that says M30 ends nothing.
    """
    # PROGRAM_END is the parser's, so the two cannot disagree on where a
    # program ends: M30, M02, and M99 for a subprogram.
    return not any(PROGRAM_END.search(code_only(ln)) for ln in code.splitlines())


class LintEngine:
    # The parser's own codes are the rule ids. Their severity: what cannot be
    # cut as written is an ERROR -- the control alarms, or the cycle does
    # nothing that was meant. Too many passes is drawn short, not wrong.
    PARSER_SEVERITY = {
        "ARC_R_TOO_SMALL": ERROR,
        "ARC_R_ZERO_CHORD": ERROR,
        "ARC_NO_CENTER": ERROR,
        "ARC_COMP_TOO_TIGHT": ERROR,
        "CYCLE_NO_PASS": ERROR,
        "CYCLE_NO_DEPTH": ERROR,
        "CYCLE_NO_CONTOUR": ERROR,
        "CYCLE_BLOCKS_MISSING": ERROR,
        "CYCLE_CONTOUR_TURNS_BACK": ERROR,
        "CYCLE_TOO_MANY_PASSES": WARNING,
        "CODE_AFTER_END": WARNING,
    }

    # Header codes whose absence can crash a machine rather than just break a
    # shop rule: compensation or inch input left over from the previous
    # program. G21 is checked as the units rule, an ERROR of its own.
    HEADER_ERRORS = frozenset({"G40"})

    # How many code-bearing lines count as "the header". Comments and the
    # tape-start '%' do not count: a program that explains itself in a comment
    # block up top still has a header, it just starts further down.
    HEADER_LINES = 5

    def __init__(self, cfg):
        p = cfg.data.get("policies", {})
        self.required = p.get("require_header_codes", ["G18","G40","G80","G97"])
        self.req_units = p.get("require_units", "G21")
        self.req_origin = p.get("require_origin", "G54")
        self.protected_m = set(p.get("protected_m_codes", []))
        # Where the chuck is, as the plot has it. Without a configured face
        # there are no collision findings: a guessed chuck would report moves
        # that are fine, and the app's own config always names one.
        machine = cfg.data.get("machine") or {}
        self.chuck_z = machine.get("chuck_z_limit")
        self.chuck_diameter = machine.get("chuck_diameter")
        # What the cycles take, from specs/. A broken card raises here and
        # names its file and key: linting with half the rules would look clean.
        self.cards = load_specs().cycles

    def run_all(self, code: str, snippet: bool = False) -> List[Diagnostic]:
        """Every finding, in program order.

        snippet: the code is a piece of a program (see is_snippet), so the
        rules about a whole program -- the header, G50 before the first G96,
        the retract before M30, compensation left on at the end -- do not
        apply. The caller decides; an analysis of the editor is always of a
        program, so one that merely lost its M30 still has its header checked.
        """
        lines = code.splitlines()
        finds = []
        # 1) Header presence.
        #
        # Whole words on stripped code only. A substring search over the raw
        # first five lines accepted "G18" inside "G180" and inside a comment
        # saying the opposite ("(kein G18 noetig)"), and it missed the header
        # entirely whenever a comment block pushed it past line five.
        header = "" if snippet else self._header(lines)
        for token in ([] if snippet else self.required):
            if not self._has_word(header, token):
                severity = ERROR if token.upper() in self.HEADER_ERRORS else WARNING
                finds.append(self._f(1, severity, "HEADER_CODE", f"{token} expected in header.",
                                     token=token,
                                     hint=f"Add {token} to the safety block at the start."))
        if not snippet and self.req_units and not self._has_word(header, self.req_units):
            finds.append(self._f(1, ERROR, "UNITS", f"{self.req_units} (mm) expected.",
                                 token=self.req_units,
                                 hint=f"Add {self.req_units} to the header, so no inch mode "
                                      "is left over from another program."))
        if not snippet and self.req_origin and not self._has_word(header, self.req_origin):
            finds.append(self._f(1, WARNING, "ORIGIN", f"{self.req_origin} expected.",
                                 token=self.req_origin,
                                 hint=f"Add {self.req_origin} to the header, or say in a "
                                      "comment which work offset the program uses."))

        # 2) G50 before G96
        #
        # Code words only: a comment saying "(G50)" limits nothing. And only a
        # G50 with an S is the limit -- G50 X Z sets the coordinate system.
        codes = [self._strip_comments(ln) for ln in lines]
        first_g96 = next((i for i, c in enumerate(codes) if self._G96.search(c)), -1)
        first_g50 = next((i for i, c in enumerate(codes)
                          if self._G50.search(c) and self._S_WORD.search(c)), -1)
        if not snippet and first_g96 != -1 and (first_g50 == -1 or first_g50 > first_g96):
            finds.append(self._f(first_g96 + 1, ERROR, "G50_BEFORE_G96",
                                 "G50 S... must precede first G96.", token="G96",
                                 hint="Limit the spindle speed with G50 S... before the first "
                                      "G96, or constant surface speed can overspeed near "
                                      "the centre.",
                                 related_code="G96"))

        # 3) Protected M-codes must not be commented out.
        #
        # Whether one was *removed* cannot be decided from a single line; that
        # needs a before/after comparison. What can be decided is that one is
        # still in the file but commented out, which is the form removing it
        # usually takes. The old "override" rule fired on any line carrying more
        # than one M-code, so a perfectly normal "M62 M08" was reported.
        protected = [(m, word(f"M{m}")) for m in sorted(self.protected_m)]
        for i, ln in enumerate(lines):
            code_ln = self._strip_comments(ln)
            for m, pat in protected:
                if pat.search(ln) and not pat.search(code_ln):
                    finds.append(self._f(i+1, ERROR, "PROTECTED_M_CODE",
                                         f"M{m} is commented out (invariant).", token=f"M{m}",
                                         hint=f"Leave M{m} in the code as it was; it is "
                                              "protected and must not be removed."))

        # 4) The program has an end. A whole program without M30 (or M02) is
        #    not finished, and the rule about the end -- the retract -- has
        #    nothing to look at, so a program that merely lost its M30 came
        #    through clean.
        if not snippet and is_snippet(code):
            finds.append(self._f(len(lines), ERROR, "PROGRAM_END",
                                 "No program end: M30 (or M02) is missing.",
                                 hint="End the program with M30, after the retract."))

        # 5) Cycle words, as the cycle cards in specs/ give them: which words
        #    take no decimal point, and the ranges of their values. Only the
        #    words a block carries are judged: a G71 is two blocks, and F is
        #    modal, so "no F here" says nothing.
        for i, ln in enumerate(lines):
            finds.extend(self._card_findings(i + 1, ln))
            code_ln = self._strip_comments(ln)
            if self._G76.search(code_ln):
                if not re.search(r'(?<![A-Z])[FRS][\d.]', code_ln, re.IGNORECASE):
                    finds.append(self._f(i+1, INFO, "G76_CHECK",
                                         "Threading: Check F/S/R parameters (heuristic).",
                                         related_code="G76"))

        # 5b) The spindle runs before the tool cuts.
        if not snippet:
            finds.extend(self._spindle_findings(lines))

        # 6) G41/G42 – simple checks (Fanuc TNR)
        comp_active = False
        current_tool = None
        tool_map = {}
        nose_numbers = set()
        tool_table_loaded = False
        try:
            # tool_data needs no GUI stack, so this works on a bare
            # interpreter too. The flag still matters for a missing or corrupt
            # tools.json: without it the engine reported every tool's radius as
            # missing rather than admitting it could not read the table.
            from .tool_data import NOSE_OFFSETS, load_tools_json
            nose_numbers = set(NOSE_OFFSETS)
            j = load_tools_json()
            for it in j.get("tool_table", []):
                try:
                    tool_map[int(it.get("t", 0))] = it
                except Exception:
                    pass
            tool_table_loaded = True
        except Exception:
            tool_map = {}

        for i, ln in enumerate(lines):
            code_ln = self._strip_comments(ln)
            # Tool change
            m_t = self._T_WORD.search(code_ln)
            if m_t:
                try:
                    current_tool = int(m_t.group(1)) // 100
                except Exception:
                    current_tool = None
                if comp_active:
                    finds.append(self._f(i+1, WARNING, "TNR_TOOL_CHANGE",
                                         "Cancel G40 before tool change.",
                                         hint="Put G40 on a move before the tool change."))

            # Compensation on/off
            if self._G41.search(code_ln) or self._G42.search(code_ln):
                comp_active = True
                # Tool radius exists?
                if current_tool is not None and tool_table_loaded:
                    tool = tool_map.get(current_tool, {})
                    r = self._insert_radius(tool)
                    if r <= 0.0:
                        finds.append(self._f(i+1, WARNING, "TNR_NO_RADIUS",
                                             f"Tool T{current_tool:02d}: insert_radius_mm missing "
                                             "(tools.json).", token=f"T{current_tool:02d}"))
                    elif tool.get("nose_direction") not in nose_numbers:
                        # Only worth saying once there is a radius to
                        # compensate with -- without one nothing is offset and
                        # the tip number makes no difference either way.
                        finds.append(self._f(i+1, WARNING, "TNR_NO_NOSE_DIRECTION",
                                             f"Tool T{current_tool:02d}: nose_direction missing (tools.json) "
                                             "— compensation assumes 0 (nose point = centre).",
                                             token=f"T{current_tool:02d}"))
                # Check lead-in for next move (next line with movement)
                for j in range(i+1, min(i+6, len(lines))):
                    nxt = self._strip_comments(lines[j])
                    if self._XZ_WORD.search(nxt):
                        if self._G00.search(nxt):
                            finds.append(self._f(j+1, WARNING, "TNR_LEADIN_RAPID",
                                                 "Lead-in must not use G00 — use G01.",
                                                 hint="Make the first move after G41/G42 a G01."))
                        if self._G02.search(nxt) or self._G03.search(nxt):
                            finds.append(self._f(j+1, WARNING, "TNR_LEADIN_ARC",
                                                 "Avoid arcs directly after G41/G42 (use linear lead-in).",
                                                 hint="Put a straight G01 move between G41/G42 "
                                                      "and the first arc."))
                        break
            if self._G40.search(code_ln):
                comp_active = False

        # Open compensation at program end. A snippet may leave it on: the
        # program it goes into cancels it.
        if comp_active and not snippet:
            finds.append(self._f(len(lines), WARNING, "TNR_OPEN_AT_END",
                                 "Set G40 before program end.",
                                 hint="Cancel compensation with G40 before the program ends."))

        # 7) Geometry the parser could not make sense of, moves into the
        #    chuck, and where the tool stands when the program ends
        parser = self._parse(code)
        if parser is not None:
            finds.extend(self._parser_findings(parser))
            if not snippet:
                finds.extend(self._retract_findings(parser, lines))

        # One list, in program order, without exact repeats -- several
        # protected M-codes on one line used to yield the same text twice.
        # Sorting is stable, so findings sharing a line keep the order the
        # rules above produced them in.
        finds = self._dedupe(finds)
        # A zero depth on the block that carries it is one fault, and the card
        # names the word; the parser's "nothing to cut" on the same line goes.
        named = {f.line for f in finds if f.rule == "VALUE_RANGE"}
        finds = [f for f in finds if not (f.rule == "CYCLE_NO_DEPTH" and f.line in named)]
        finds.sort(key=lambda f: f.line)
        return finds

    def _header(self, lines) -> str:
        """The first HEADER_LINES lines that carry code, comments removed."""
        code_lines = []
        for ln in lines:
            stripped = self._strip_comments(ln).lstrip('%').strip()
            if not stripped:
                continue
            code_lines.append(stripped)
            if len(code_lines) >= self.HEADER_LINES:
                break
        return " ".join(code_lines)

    @staticmethod
    def _strip_comments(line: str) -> str:
        return re.sub(r';.*', '', re.sub(r'\(.*?\)', '', line)).strip()

    @staticmethod
    def _has_word(haystack: str, token: str) -> bool:
        return word(token).search(haystack) is not None

    @staticmethod
    def _insert_radius(tool: dict) -> float:
        """The nose radius of one tool record, 0.0 when unusable."""
        try:
            return float(tool.get("insert_radius_mm", 0.0) or 0.0)
        except (AttributeError, TypeError, ValueError):
            return 0.0

    _G00, _G02, _G03 = word("G00"), word("G02"), word("G03")
    _G40, _G41, _G42 = word("G40"), word("G41"), word("G42")
    _G50, _G76, _G96 = word("G50"), word("G76"), word("G96")
    _G28_G30 = re.compile(r'(?<![A-Z])G0*(?:28|30)(?!\d)', re.IGNORECASE)
    _S_WORD = re.compile(r'(?<![A-Z])S\d', re.IGNORECASE)
    _T_WORD = re.compile(r'(?<![A-Z])T(\d+)', re.IGNORECASE)
    _XZ_WORD = re.compile(r'(?<![A-Z])[XZ][-+]?[\d.]', re.IGNORECASE)

    # Spindle on (M03, M04) and off (M05); not M30, M40 or M50.
    _SPINDLE = re.compile(r'(?<![A-Z])M0*([345])(?!\d)', re.IGNORECASE)
    # The motion group: G00 rapid, G01-G03 cutting. G0 and G1 are the same.
    _MOTION = re.compile(r'(?<![A-Z])G0*([0-3])(?!\d)', re.IGNORECASE)
    # A cycle cuts whatever motion was active: G70-G76, and G90, G92, G94 on
    # a block with a target (without one they are another G-code system's
    # words, which G-code system A does not have).
    _CUTTING_CYCLE = re.compile(r'(?<![A-Z])G(?:7[0-6]|9[024])(?!\d)', re.IGNORECASE)
    _AXIS = re.compile(r'(?<![A-Z])[XZUW][-+]?[\d.]', re.IGNORECASE)
    # Blocks whose X/Z/U/W are no cut, whatever motion is active: the dwell
    # (G04 X1. is a second), offset setting, the reference returns -- M05 then
    # G28 U0 W0 is the usual end of a program -- and G50 X Z.
    _NOT_A_MOVE = re.compile(r'(?<![A-Z])G(?:0*4|10|2[789]|30|50)(?!\d)', re.IGNORECASE)

    def _spindle_findings(self, lines) -> List[Diagnostic]:
        """A cutting move or a cycle while the spindle is not running: no M03
        or M04 before it, or an M05 since.

        With feed per revolution (G99) the axes then wait for a spindle that
        never turns; with feed per minute (G98) the tool is fed into a
        standing part. Both start a program that cannot be right. One finding
        per stretch without the spindle, at its first cut: the moves after it
        are the same fault. A whole program only -- a snippet goes into a
        program that starts the spindle, as is_snippet explains.
        """
        found = []
        running, reported, motion = False, False, None
        for i, ln in enumerate(lines):
            code = self._strip_comments(ln)
            # An M03 on the block of the move counts: the control turns the
            # spindle on before it moves.
            for m in self._SPINDLE.findall(code):
                running = m in ("3", "4")
                if not running:
                    reported = False
            for g in self._MOTION.findall(code):
                motion = int(g)
            cycle = self._CUTTING_CYCLE.search(code)
            axis = None if self._NOT_A_MOVE.search(code) else self._AXIS.search(code)
            cuts = bool(cycle and (cycle.group(0)[1] == "7" or axis)) or (motion in (1, 2, 3)
                                                                          and axis is not None)
            if cuts and not running and not reported:
                found.append(self._f(i + 1, ERROR, "SPINDLE_NOT_STARTED",
                                     "The tool cuts with the spindle not running: no M03 or M04 "
                                     "before this move, or an M05 since.",
                                     hint="Start the spindle (G97 S... M03, or G50 S... and G96 "
                                          "S... M03) before the first cutting move."))
                reported = True
        return found

    # A G70-G76 block, the cycle number captured; not G700. A digit may come
    # before the G -- N10G75X26. is a legal block without spaces.
    _CYCLE_BLOCK = re.compile(r'(?<![A-Z])G(7[0-6])(?!\d)', re.IGNORECASE)
    # Every address word but G and N, the number as written: P1.5, Q3., Q.4.
    _CYCLE_WORD = re.compile(r'(?<![A-Z])([A-FH-MO-Z])([-+]?(?:\d+\.?\d*|\.\d+))',
                             re.IGNORECASE)

    def _card_findings(self, line_no: int, line: str) -> List[Diagnostic]:
        """The words of one G70-G76 block, against the card of its cycle.

        A word the card marks decimal_point = false and that has one is an
        ERROR: the control stops with an alarm, while the parser draws P1.5
        as the 1.5 mm that was meant, so the plot looks fine. A value outside
        the card's range is an ERROR too -- a depth, peck or feed of zero or
        below, which policies.md forbids. A pointed value is not also judged
        for its range: the point is the mistake.
        """
        code = self._strip_comments(line)
        cycle = self._CYCLE_BLOCK.search(code)
        card = self.cards.get(f"G{cycle.group(1)}") if cycle else None
        if card is None:
            return []
        words = [(m.group(1).upper(), m.group(2)) for m in self._CYCLE_WORD.finditer(code)]
        block = card.block_of({letter for letter, _text in words})
        found = []
        for letter, text in words:
            word = block.words.get(letter)
            if word is None:
                continue
            token = f"{letter}{text}"
            if not word.decimal_point and "." in text:
                meant = self._without_point(text, word.unit)
                why = {"block number": f"block numbers take no point (N100 is {letter}100)",
                       "0.001 mm": f"it is written in 0.001 mm ({letter}1500 = 1.5 mm)"}.get(
                    word.unit, f"it is packed digits ({word.form})" if word.form else "it takes none")
                found.append(self._f(
                    line_no, ERROR, "DECIMAL_POINT",
                    f"{card.code}: {token} has a decimal point. {letter} is the {word.meaning}; "
                    f"{why}. The control stops with an alarm.",
                    token=token, hint=f"Write {letter}{meant}." if meant is not None else None,
                    related_code=card.code))
                continue
            wanted = word.out_of_range(float(text))
            if wanted:
                found.append(self._f(
                    line_no, ERROR, "VALUE_RANGE",
                    f"{card.code}: {token} — {letter} is the {word.meaning}, and must be {wanted}.",
                    token=token, hint=f"Give {letter} a value {wanted}.", related_code=card.code))
        return found

    @staticmethod
    def _without_point(text: str, unit: str) -> Optional[str]:
        """What a pointed word most likely meant: the block number, or the
        amount in 0.001 mm. None when the unit says neither, or the number
        does not read as one."""
        number = float(text)
        if unit == "block number":
            return str(int(number)) if number == int(number) else None
        if unit == "0.001 mm":
            return str(int(round(number * 1000)))
        return None

    @staticmethod
    def _dedupe(finds: List[Diagnostic]) -> List[Diagnostic]:
        seen, unique = set(), []
        for f in finds:
            key = (f.line, f.rule, f.message)
            if key not in seen:
                seen.add(key)
                unique.append(f)
        return unique

    def _parse(self, code: str):
        """The parser, run over the code; None when it choked."""
        try:
            from .gcode_parser import GCodeParser
            if self.chuck_z is not None:
                parser = GCodeParser(chuck_z=float(self.chuck_z), chuck_diameter=self.chuck_diameter)
            else:
                parser = GCodeParser()
            parser.parse(code)
        except Exception:
            # Linting must never fail because the parser choked on something.
            return None
        return parser

    # What removes material, as the parser files it.
    _CUTTING_KINDS = ("cut", "finish", "thread")

    def _retract_findings(self, parser, lines) -> List[Diagnostic]:
        """Before M30 (or M02) the tool stands clear of the part.

        Clear means in front of it: Z beyond the highest Z the program cut
        at, or beyond the face of the STOCK comment if that is further out --
        or a G28/G30 reference return after the last move. Whether Z went
        before X is not judged, only where the tool ends.

        It read the text before: an X and a Z word in the last three lines
        were taken as a retract, so a G01 into the part right before M30
        passed. M99 ends a subprogram, which hands the tool back to the
        program that called it, so it is not judged.
        """
        end = parser.program_end
        if end is None or end["code"] == "M99":
            return []
        paths = parser.paths
        cut_z = [z for kind in self._CUTTING_KINDS for segment in paths[kind]
                 for _x, z in segment[:2]]
        cut_z += [z for arc in paths["arc"] for _x, z in parser._sample_arc(arc)]
        if not cut_z:
            return []                   # nothing cut, nothing to stand clear of
        front = max(cut_z)
        if paths.get("stock"):
            front = max(front, paths["stock"]["z_front"])
        if end["z"] > front + 1e-9:
            return []
        # The reference return moves the tool off to the machine's home; the
        # parser does not follow it there, so it is read from the code: a
        # G28 or G30 after the last move drawn.
        last_move = max((line for _kind, _index, line in paths["order"]), default=0)
        if any(self._G28_G30.search(self._strip_comments(lines[i]))
               for i in range(last_move, end["line"])):
            return []
        code = end["code"]
        return [self._f(end["line"], ERROR, "RETRACT_BEFORE_M30",
                        f"Before {code} the tool still stands at the part: it ends at "
                        f"Z{end['z']:g}, and the program cut up to Z{front:g}.",
                        hint=f"Before {code}, move Z to a safe plane in front of the part, "
                             "then retract X (or G28 U0 W0).")]

    def _parser_findings(self, parser) -> List[Diagnostic]:
        """Warnings the parser records while building the toolpaths, and the
        moves it found running into the chuck.

        Impossible R geometry, arcs without a centre. The parser has always
        known about these; until they were routed here nothing read the list,
        so the operator saw a missing arc and no explanation for it. The
        collisions were drawn in the plot and nowhere else, so a program that
        ran into the chuck came through the linter without a word.
        """
        finds = [self._f(w["line"], self.PARSER_SEVERITY.get(w["code"], WARNING),
                         w["code"], w["message"])
                 for w in parser.warnings]
        if self.chuck_z is not None:
            finds.extend(self._collisions(parser))
        return finds

    def _collisions(self, parser) -> List[Diagnostic]:
        """One finding per line that moves into the chuck zone."""
        face = parser.chuck_z
        diameter = parser.chuck_diameter
        where = (f"behind the chuck face at Z{face:g} and inside Ø{diameter:g}" if diameter
                 else f"behind the chuck face at Z{face:g} (no chuck diameter is set, so "
                      "every diameter there counts)")
        lines = sorted({segment[2] for segment in parser.paths.get('collisions', [])})
        return [self._f(line, ERROR, "CHUCK_COLLISION", f"The move runs into the chuck: {where}.",
                        hint="Keep the tool in front of the chuck face or outside the jaws; "
                             "if the chuck values are wrong, correct them in Settings → Machine.")
                for line in lines]

    @staticmethod
    def _f(line: int, severity: Severity, rule: str, msg: str, token: Optional[str] = None,
           hint: Optional[str] = None, related_code: Optional[str] = None) -> Diagnostic:
        return Diagnostic(line=line, severity=severity, rule=rule, message=msg, token=token,
                          hint=hint, related_code=related_code)

