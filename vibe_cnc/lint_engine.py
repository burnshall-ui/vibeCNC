import re, json
from typing import List, Dict

class LintEngine:
    def __init__(self, cfg):
        p = cfg.data.get("policies", {})
        self.required = p.get("require_header_codes", ["G18","G40","G80","G97"])
        self.req_units = p.get("require_units", "G21")
        self.req_origin = p.get("require_origin", "G54")
        self.protected_m = set(p.get("protected_m_codes", []))

    def run_all(self, code:str) -> List[Dict]:
        lines = code.splitlines()
        finds = []
        # 1) Header presence
        header = " ".join(lines[:5])
        for token in self.required:
            if token not in header:
                finds.append(self._f(1, "Header", f"{token} im Header erwartet."))
        if self.req_units and self.req_units not in header:
            finds.append(self._f(1, "Einheiten", f"{self.req_units} (mm) erwartet."))
        if self.req_origin and self.req_origin not in header:
            finds.append(self._f(1, "Nullpunkt", f"{self.req_origin} erwartet."))

        # 2) G50 before G96
        first_g50 = self._first_index(lines, r'\bG50\b')
        first_g96 = self._first_index(lines, r'\bG96\b')
        if first_g96 != -1 and (first_g50 == -1 or first_g50 > first_g96):
            l = first_g96+1 if first_g96!=-1 else 1
            finds.append(self._f(l, "CSS", "G50 muss vor erstem G96 stehen."))

        # 3) Protected M-codes unchanged
        for i, ln in enumerate(lines):
            for m in self.protected_m:
                pat = rf'\bM{m}\b'
                if re.search(pat, ln):
                    if ln.strip().startswith('(') or ln.strip().startswith(';'):
                        finds.append(self._f(i+1, "M-Invarianz", f"M{m} nicht auskommentieren (invariant)."))
                    if "M" in ln and re.search(r'\bM0?\b', ln):
                        finds.append(self._f(i+1, "M-Invarianz", f"M{m} nicht überschreiben."))

        # 4) End-of-program retract
        end_idx = max(len(lines)-3, 0)
        end_block = " ".join(lines[end_idx:])
        if ("M30" in end_block) and not (re.search(r'\bZ\d', end_block) and re.search(r'\bX\d', end_block)):
            finds.append(self._f(len(lines), "Rückzug", "Vor M30: Z in sichere Ebene, dann X rausfahren."))

        # 5) G7x sanity (rough)
        for i, ln in enumerate(lines):
            if re.search(r'\bG7(0|1|2)\b', ln):
                if re.search(r'\bF0(\.0+)?\b', ln) or re.search(r'\bF-?\b', ln):
                    finds.append(self._f(i+1, "G7x", "Vorschub F darf nicht 0/negativ sein."))
            if re.search(r'\bG76\b', ln):
                if not re.search(r'\b[FRS]\d', ln):
                    finds.append(self._f(i+1, "G76", "Gewinde: F/S/R Parameter prüfen (Heuristik)."))
        return finds

    def _first_index(self, lines, pattern):
        rx = re.compile(pattern)
        for i, ln in enumerate(lines):
            if rx.search(ln): return i
        return -1

    def _f(self, line:int, rule:str, msg:str):
        return {"line": line, "rule": rule, "message": msg}

