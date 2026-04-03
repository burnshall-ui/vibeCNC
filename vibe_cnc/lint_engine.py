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
            # Kommentare ignorieren für M-Code Prüfung
            code_ln = re.sub(r'\(.*?\)', '', ln)
            code_ln = re.sub(r';.*', '', code_ln)
            for m in self.protected_m:
                pat = rf'\bM0?{m}\b'
                if re.search(pat, code_ln):
                    if ln.strip().startswith('(') or ln.strip().startswith(';'):
                        finds.append(self._f(i+1, "M-Invarianz", f"M{m} nicht auskommentieren (invariant)."))
                
                # Prüfe ob ein anderer M-Code den geschützten überschreibt
                m_codes = re.findall(r'\bM\d+\b', code_ln)
                if len(m_codes) > 1 and any(re.match(pat, mc) for mc in m_codes):
                    finds.append(self._f(i+1, "M-Invarianz", f"M{m} nicht mit anderem M-Code überschreiben."))

        # 4) End-of-program retract
        end_idx = max(len(lines)-3, 0)
        end_block = " ".join(lines[end_idx:])
        if ("M30" in end_block) and not (re.search(r'\bZ[-+]?\d', end_block) and re.search(r'\bX[-+]?\d', end_block)):
            finds.append(self._f(len(lines), "Rückzug", "Vor M30: Z in sichere Ebene, dann X rausfahren."))

        # 5) G7x sanity (rough)
        for i, ln in enumerate(lines):
            if re.search(r'\bG7(0|1|2)\b', ln):
                if re.search(r'\bF0(\.0+)?\b', ln) or re.search(r'\bF-?\b', ln):
                    finds.append(self._f(i+1, "G7x", "Vorschub F darf nicht 0/negativ sein."))
            if re.search(r'\bG76\b', ln):
                if not re.search(r'\b[FRS]\d', ln):
                    finds.append(self._f(i+1, "G76", "Gewinde: F/S/R Parameter prüfen (Heuristik)."))

        # 6) G41/G42 – einfache Prüfungen (Fanuc TNR)
        comp_active = False
        comp_start_line = None
        current_tool = None
        tool_radius_map = {}
        try:
            from .tool_model import load_tools_json
            j = load_tools_json()
            for it in j.get("tool_table", []):
                try:
                    t = int(it.get("t", 0))
                    r = float(it.get("insert_radius_mm", 0.0) or 0.0)
                    tool_radius_map[t] = r
                except Exception:
                    pass
        except Exception:
            tool_radius_map = {}

        for i, ln in enumerate(lines):
            # Kommentare auslassen für einfache Suche
            code_ln = re.sub(r'\(.*?\)', '', ln)
            # Werkzeugwechsel
            m_t = re.search(r'\bT(\d+)\b', code_ln)
            if m_t:
                try:
                    current_tool = int(m_t.group(1)) // 100
                except Exception:
                    current_tool = None
                if comp_active:
                    finds.append(self._f(i+1, "G41/G42", "Vor Werkzeugwechsel G40 aufheben."))

            # Kompensation on/off
            if re.search(r'\bG0?41\b', code_ln) or re.search(r'\bG0?42\b', code_ln):
                comp_active = True
                comp_start_line = i+1
                # Werkzeugradius vorhanden?
                if current_tool is not None:
                    r = tool_radius_map.get(current_tool, 0.0)
                    if r <= 0.0:
                        finds.append(self._f(i+1, "G41/G42", f"Tool T{current_tool:02d}: insert_radius_mm fehlt (tools.json)."))
                # Lead-in nächste Bewegung prüfen (nächste Zeile mit Bewegung)
                for j in range(i+1, min(i+6, len(lines))):
                    nxt = re.sub(r'\(.*?\)', '', lines[j])
                    if re.search(r'\bX[-+]?\d', nxt) or re.search(r'\bZ[-+]?\d', nxt):
                        if re.search(r'\bG0?0\b', nxt):
                            finds.append(self._f(j+1, "G41/G42", "Lead-in nicht mit G00 – G01 verwenden."))
                        if re.search(r'\bG0?[23]\b', nxt):
                            finds.append(self._f(j+1, "G41/G42", "Bögen direkt nach G41/G42 vermeiden (Lead-in linear)."))
                        break
            if re.search(r'\bG0?40\b', code_ln):
                comp_active = False
                comp_start_line = None

        # Offene Kompensation am Programmende
        if comp_active:
            finds.append(self._f(len(lines), "G41/G42", "Vor Programmende G40 setzen."))
        return finds

    def _first_index(self, lines, pattern):
        rx = re.compile(pattern)
        for i, ln in enumerate(lines):
            if rx.search(ln): return i
        return -1

    def _f(self, line:int, rule:str, msg:str):
        return {"line": line, "rule": rule, "message": msg}

