import re, json
from typing import List, Dict

class LintEngine:
    # Rule labels for the codes the parser records. The UI shows this string,
    # so it has to read like the hand-written rules above it.
    PARSER_RULES = {
        "ARC_R_TOO_SMALL": "Arc R",
        "ARC_R_ZERO_CHORD": "Arc R",
        "ARC_NO_CENTER": "Arc",
    }

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

    def run_all(self, code:str) -> List[Dict]:
        lines = code.splitlines()
        finds = []
        # 1) Header presence.
        #
        # Whole words on stripped code only. A substring search over the raw
        # first five lines accepted "G18" inside "G180" and inside a comment
        # saying the opposite ("(kein G18 noetig)"), and it missed the header
        # entirely whenever a comment block pushed it past line five.
        header = self._header(lines)
        for token in self.required:
            if not self._has_word(header, token):
                finds.append(self._f(1, "Header", f"{token} expected in header."))
        if self.req_units and not self._has_word(header, self.req_units):
            finds.append(self._f(1, "Units", f"{self.req_units} (mm) expected."))
        if self.req_origin and not self._has_word(header, self.req_origin):
            finds.append(self._f(1, "Origin", f"{self.req_origin} expected."))

        # 2) G50 before G96
        first_g50 = self._first_index(lines, r'\bG50\b')
        first_g96 = self._first_index(lines, r'\bG96\b')
        if first_g96 != -1 and (first_g50 == -1 or first_g50 > first_g96):
            l = first_g96+1 if first_g96!=-1 else 1
            finds.append(self._f(l, "CSS", "G50 must precede first G96."))

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
                    finds.append(self._f(i+1, "M-Invariant",
                                         f"M{m} is commented out (invariant)."))

        # 4) End-of-program retract
        end_idx = max(len(lines)-3, 0)
        end_block = " ".join(lines[end_idx:])
        # G28 is the reference return and retracts both axes by itself; U/W are
        # the incremental words a Fanuc lathe uses for exactly this move, so
        # looking only for X and Z missed the most common correct ending.
        retracted = (re.search(r'\bG28\b', end_block)
                     or (re.search(r'\b[ZW][-+]?\d', end_block)
                         and re.search(r'\b[XU][-+]?\d', end_block)))
        if "M30" in end_block and not retracted:
            finds.append(self._f(len(lines), "Retract", "Before M30: Move Z to safe plane, then retract X."))

        # 5) G7x sanity (rough)
        for i, ln in enumerate(lines):
            if re.search(r'\bG7(0|1|2)\b', ln):
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
                    finds.append(self._f(i+1, "G7x", "Feed F must not be 0 or negative."))
            if re.search(r'\bG76\b', ln):
                if not re.search(r'\b[FRS]\d', ln):
                    finds.append(self._f(i+1, "G76", "Threading: Check F/S/R parameters (heuristic)."))

        # 6) G41/G42 – simple checks (Fanuc TNR)
        comp_active = False
        comp_start_line = None
        current_tool = None
        tool_radius_map = {}
        tool_table_loaded = False
        try:
            # tool_data needs no GUI stack, so this works on a bare
            # interpreter too. The flag still matters for a missing or corrupt
            # tools.json: without it the engine reported every tool's radius as
            # missing rather than admitting it could not read the table.
            from .tool_data import load_tools_json
            j = load_tools_json()
            for it in j.get("tool_table", []):
                try:
                    t = int(it.get("t", 0))
                    r = float(it.get("insert_radius_mm", 0.0) or 0.0)
                    tool_radius_map[t] = r
                except Exception:
                    pass
            tool_table_loaded = True
        except Exception:
            tool_radius_map = {}

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
                    finds.append(self._f(i+1, "G41/G42", "Cancel G40 before tool change."))

            # Compensation on/off
            if re.search(r'\bG0?41\b', code_ln) or re.search(r'\bG0?42\b', code_ln):
                comp_active = True
                comp_start_line = i+1
                # Tool radius exists?
                if current_tool is not None and tool_table_loaded:
                    r = tool_radius_map.get(current_tool, 0.0)
                    if r <= 0.0:
                        finds.append(self._f(i+1, "G41/G42", f"Tool T{current_tool:02d}: insert_radius_mm missing (tools.json)."))
                # Check lead-in for next move (next line with movement)
                for j in range(i+1, min(i+6, len(lines))):
                    nxt = re.sub(r'\(.*?\)', '', lines[j])
                    if re.search(r'\bX[-+]?\d', nxt) or re.search(r'\bZ[-+]?\d', nxt):
                        if re.search(r'\bG0?0\b', nxt):
                            finds.append(self._f(j+1, "G41/G42", "Lead-in must not use G00 — use G01."))
                        if re.search(r'\bG0?[23]\b', nxt):
                            finds.append(self._f(j+1, "G41/G42", "Avoid arcs directly after G41/G42 (use linear lead-in)."))
                        break
            if re.search(r'\bG0?40\b', code_ln):
                comp_active = False
                comp_start_line = None

        # Open compensation at program end
        if comp_active:
            finds.append(self._f(len(lines), "G41/G42", "Set G40 before program end."))

        # 7) Geometry the parser could not make sense of
        finds.extend(self._parser_findings(code))

        # One list, in program order, without exact repeats -- several
        # protected M-codes on one line used to yield the same text twice.
        # Sorting is stable, so findings sharing a line keep the order the
        # rules above produced them in.
        finds = self._dedupe(finds)
        finds.sort(key=lambda f: f["line"])
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
    def _feed_value(line: str):
        """The F word of a line as a number. None if absent or unparsable."""
        m = re.search(r'\bF([-+]?\d*\.?\d*)', line, re.IGNORECASE)
        if not m:
            return None
        try:
            return float(m.group(1))
        except ValueError:      # a bare "F" with no number behind it
            return None

    @staticmethod
    def _dedupe(finds: List[Dict]) -> List[Dict]:
        seen, unique = set(), []
        for f in finds:
            key = (f["line"], f["rule"], f["message"])
            if key not in seen:
                seen.add(key)
                unique.append(f)
        return unique

    def _parser_findings(self, code: str) -> List[Dict]:
        """Warnings the parser records while building the toolpaths.

        Impossible R geometry, arcs without a centre. The parser has always
        known about these; until they were routed here nothing read the list,
        so the operator saw a missing arc and no explanation for it.
        """
        try:
            from .gcode_parser import GCodeParser
            parser = GCodeParser()
            parser.parse(code)
        except Exception:
            # Linting must never fail because the parser choked on something.
            return []

        return [self._f(w["line"],
                        self.PARSER_RULES.get(w["code"], "Geometry"),
                        w["message"])
                for w in parser.warnings]

    def _first_index(self, lines, pattern):
        rx = re.compile(pattern)
        for i, ln in enumerate(lines):
            if rx.search(ln): return i
        return -1

    def _f(self, line:int, rule:str, msg:str):
        return {"line": line, "rule": rule, "message": msg}

