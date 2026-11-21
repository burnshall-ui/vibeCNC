import re
from typing import List, Optional

from .diagnostics import ERROR, INFO, WARNING, Diagnostic, Severity

# Code words that end a program: M30, M02, and M99 for a subprogram.
_PROGRAM_END = re.compile(r'(?<![A-Z])M0*(?:30|2|99)(?!\d)', re.IGNORECASE)


def code_only(line: str) -> str:
    """One line without its comments: parentheses, and everything after ';'."""
    return re.sub(r';.*', '', re.sub(r'\(.*?\)', '', line))


def is_snippet(code: str) -> bool:
    """Code that never ends the program is a piece of one, not a program.

    A generated "G71 for T1" has no header and no M30, and is not wrong for
    it: the program it goes into has them. Only the code words count; a
    comment that says M30 ends nothing.
    """
    return not any(_PROGRAM_END.search(code_only(ln)) for ln in code.splitlines())


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
        "CYCLE_TOO_MANY_PASSES": WARNING,
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
        first_g50 = self._first_index(lines, r'\bG50\b')
        first_g96 = self._first_index(lines, r'\bG96\b')
        if not snippet and first_g96 != -1 and (first_g50 == -1 or first_g50 > first_g96):
            finds.append(self._f(first_g96 + 1, ERROR, "G50_BEFORE_G96",
                                 "G50 must precede first G96.", token="G96",
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
        for i, ln in enumerate(lines):
            code_ln = self._strip_comments(ln)
            for m in sorted(self.protected_m):
                pat = rf'\bM0?{m}\b'
                if re.search(pat, ln, re.IGNORECASE) and not re.search(pat, code_ln, re.IGNORECASE):
                    finds.append(self._f(i+1, ERROR, "PROTECTED_M_CODE",
                                         f"M{m} is commented out (invariant).", token=f"M{m}",
                                         hint=f"Leave M{m} in the code as it was; it is "
                                              "protected and must not be removed."))

        # 4) End-of-program retract
        #
        # The last three lines that carry code. Three raw lines were read
        # before, so the tape-end '%' after M30 -- or a blank line, or a closing
        # comment -- took the place of the Z retract, and a program ending
        # "G00 Z50. / X200. / M30 / %" was reported for a retract it has.
        tail = [(i, code) for i, code in
                ((i, self._strip_comments(ln).lstrip('%').strip()) for i, ln in enumerate(lines))
                if code][-3:]
        end_block = " ".join(code for _i, code in tail)
        # G28 is the reference return and retracts both axes by itself; U/W are
        # the incremental words a Fanuc lathe uses for exactly this move, so
        # looking only for X and Z missed the most common correct ending.
        retracted = (re.search(r'\bG28\b', end_block)
                     or (re.search(r'\b[ZW][-+]?\d', end_block)
                         and re.search(r'\b[XU][-+]?\d', end_block)))
        m30_line = next((i + 1 for i, code in reversed(tail) if self._M30.search(code)), None)
        if not snippet and m30_line is not None and not retracted:
            finds.append(self._f(m30_line, ERROR, "RETRACT_BEFORE_M30",
                                 "Before M30: Move Z to safe plane, then retract X.",
                                 hint="Before M30, move Z to a safe plane, then retract X "
                                      "(or G28 U0 W0)."))

        # 5) G7x sanity (rough)
        for i, ln in enumerate(lines):
            cycle_code = re.search(r'\bG7(0|1|2)\b', ln)
            if cycle_code:
                # Read the number rather than pattern-match it. `\bF0(\.0+)?\b`
                # matched the "F0" inside "F0.25", because the '.' that follows
                # satisfies the word boundary -- so every ordinary turning feed
                # (0.1 to 0.3 mm/rev) was reported as zero.
                # Only judge a feed that is actually on this block. A G71 is
                # two blocks -- depth and retract on the first, P/Q/U/W/F on
                # the second -- and F is modal besides, so "no F here" says
                # nothing. Catching a genuinely missing feed needs the modal
                # state the parser tracks, not a per-line regex.
                feed = self._feed_value(ln)
                if feed is not None and feed <= 0.0:
                    finds.append(self._f(i+1, ERROR, "CYCLE_FEED_ZERO",
                                         "Feed F must not be 0 or negative.", token=f"F{feed:g}",
                                         hint="Give the cycle a feed above zero, e.g. F0.25.",
                                         related_code=f"G7{cycle_code.group(1)}"))
            if re.search(r'\bG76\b', ln):
                if not re.search(r'\b[FRS]\d', ln):
                    finds.append(self._f(i+1, INFO, "G76_CHECK",
                                         "Threading: Check F/S/R parameters (heuristic).",
                                         related_code="G76"))
            finds.extend(self._cycle_decimal_points(i+1, ln))

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
            # Skip comments for simple search
            code_ln = re.sub(r'\(.*?\)', '', ln)
            # Tool change
            m_t = re.search(r'\bT(\d+)\b', code_ln)
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
            if re.search(r'\bG0?41\b', code_ln) or re.search(r'\bG0?42\b', code_ln):
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
                    nxt = re.sub(r'\(.*?\)', '', lines[j])
                    if re.search(r'\bX[-+]?\d', nxt) or re.search(r'\bZ[-+]?\d', nxt):
                        if re.search(r'\bG0?0\b', nxt):
                            finds.append(self._f(j+1, WARNING, "TNR_LEADIN_RAPID",
                                                 "Lead-in must not use G00 — use G01.",
                                                 hint="Make the first move after G41/G42 a G01."))
                        if re.search(r'\bG0?[23]\b', nxt):
                            finds.append(self._f(j+1, WARNING, "TNR_LEADIN_ARC",
                                                 "Avoid arcs directly after G41/G42 (use linear lead-in).",
                                                 hint="Put a straight G01 move between G41/G42 "
                                                      "and the first arc."))
                        break
            if re.search(r'\bG0?40\b', code_ln):
                comp_active = False

        # Open compensation at program end. A snippet may leave it on: the
        # program it goes into cancels it.
        if comp_active and not snippet:
            finds.append(self._f(len(lines), WARNING, "TNR_OPEN_AT_END",
                                 "Set G40 before program end.",
                                 hint="Cancel compensation with G40 before the program ends."))

        # 7) Geometry the parser could not make sense of, and moves into the chuck
        finds.extend(self._parser_findings(code))

        # One list, in program order, without exact repeats -- several
        # protected M-codes on one line used to yield the same text twice.
        # Sorting is stable, so findings sharing a line keep the order the
        # rules above produced them in.
        finds = self._dedupe(finds)
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
        return re.search(rf'\b{re.escape(token)}\b', haystack, re.IGNORECASE) is not None

    @staticmethod
    def _insert_radius(tool: dict) -> float:
        """The nose radius of one tool record, 0.0 when unusable."""
        try:
            return float(tool.get("insert_radius_mm", 0.0) or 0.0)
        except (AttributeError, TypeError, ValueError):
            return 0.0

    @staticmethod
    def _feed_value(line: str):
        """The F word of a line as a number. None if absent or unparsable."""
        m = re.search(r'\bF([-+]?\d*\.?\d*)', line, re.IGNORECASE)
        if not m:
            return None
        try:
            return float(m.group(1))
        except ValueError:      # a bare "F" with no number behind it
            return None

    # M30, also written M030 or run together as N100M30; not M300.
    _M30 = re.compile(r'(?<![A-Z])M0*30(?!\d)', re.IGNORECASE)

    # A G70-G76 block, the cycle number captured; not G700. A digit may come
    # before the G -- N10G75X26. is a legal block without spaces.
    _CYCLE_BLOCK = re.compile(r'(?<![A-Z])G7([0-6])(?!\d)', re.IGNORECASE)
    # A P or Q word written with a decimal point: P1.5, Q3., Q.4.
    _POINTED_PQ = re.compile(r'(?<![A-Z])([PQ])([-+]?(?:\d+\.\d*|\.\d+))', re.IGNORECASE)

    def _cycle_decimal_points(self, line_no: int, line: str) -> List[Diagnostic]:
        """P and Q of the cycles G70-G76 take no decimal point.

        In G70-G73 they are block numbers. In G74-G76 they are amounts in the
        least input increment, 0.001 mm: P1500 is 1.5 mm. A Fanuc control
        rejects a point there with an alarm; the parser draws P1.5 as the
        1.5 mm that was meant, so without this the plot looked fine for a
        program the machine will not run. Taken from training material and
        forum posts that describe the Fanuc format, not from the manual.
        """
        code = self._strip_comments(line)
        cycle = self._CYCLE_BLOCK.search(code)
        if not cycle:
            return []
        name = f"G7{cycle.group(1)}"
        blocks = int(cycle.group(1)) <= 3
        if blocks:
            why = "they name block numbers (N100 → P100)"
        else:
            why = "they are written in 0.001 mm (P1500 = 1.5 mm)"
        found = []
        for m in self._POINTED_PQ.finditer(code):
            word, value = m.group(1).upper(), m.group(2)
            meant = self._pq_without_point(value, blocks)
            found.append(self._f(
                line_no, ERROR, "PQ_DECIMAL_POINT",
                f"{name}: {word}{value} has a decimal point. "
                f"P and Q of this cycle take none — {why}; "
                "the control stops with an alarm.",
                token=f"{word}{value}",
                hint=f"Write {word}{meant}." if meant is not None else None,
                related_code=name))
        return found

    @staticmethod
    def _pq_without_point(value: str, blocks: bool) -> Optional[str]:
        """What P/Q with a point most likely meant: the block number, or the
        amount in 0.001 mm. None when the number does not read as either."""
        try:
            number = float(value)
        except ValueError:
            return None
        if blocks:
            return str(int(number)) if number == int(number) else None
        return str(int(round(number * 1000)))

    @staticmethod
    def _dedupe(finds: List[Diagnostic]) -> List[Diagnostic]:
        seen, unique = set(), []
        for f in finds:
            key = (f.line, f.rule, f.message)
            if key not in seen:
                seen.add(key)
                unique.append(f)
        return unique

    def _parser_findings(self, code: str) -> List[Diagnostic]:
        """Warnings the parser records while building the toolpaths, and the
        moves it found running into the chuck.

        Impossible R geometry, arcs without a centre. The parser has always
        known about these; until they were routed here nothing read the list,
        so the operator saw a missing arc and no explanation for it. The
        collisions were drawn in the plot and nowhere else, so a program that
        ran into the chuck came through the linter without a word.
        """
        try:
            from .gcode_parser import GCodeParser
            if self.chuck_z is not None:
                parser = GCodeParser(chuck_z=float(self.chuck_z), chuck_diameter=self.chuck_diameter)
            else:
                parser = GCodeParser()
            parser.parse(code)
        except Exception:
            # Linting must never fail because the parser choked on something.
            return []

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

    def _first_index(self, lines, pattern):
        rx = re.compile(pattern)
        for i, ln in enumerate(lines):
            if rx.search(ln): return i
        return -1

    @staticmethod
    def _f(line: int, severity: Severity, rule: str, msg: str, token: Optional[str] = None,
           hint: Optional[str] = None, related_code: Optional[str] = None) -> Diagnostic:
        return Diagnostic(line=line, severity=severity, rule=rule, message=msg, token=token,
                          hint=hint, related_code=related_code)

