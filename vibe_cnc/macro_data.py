# macro_data.py — the macro library, free of GUI dependencies.
#
# tools/macros.json is the single source. It replaces tools/macros.db, which was
# a checked-in SQLite file the app wrote on every run: each start produced a diff
# nobody could read, and the seed list existed twice, once in the schema helper
# and once again as a fallback.
#
# The file stays under version control on purpose. A macro library is curated
# content, like the tool table — you want to see what changed and commit it
# deliberately, which is exactly what a binary database made impossible.
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MACROS_JSON = os.path.join(HERE, "tools", "macros.json")

CALL_TYPES = ("M98", "G65")


def load_macros_json() -> dict:
    """The macro library as stored. An unreadable file yields an empty table."""
    try:
        with open(MACROS_JSON, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {"macro_table": []}


def save_macros_json(payload: dict) -> None:
    """Writes the macro library back. The only place that writes it."""
    os.makedirs(os.path.dirname(MACROS_JSON), exist_ok=True)
    with open(MACROS_JSON, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def parse_macro_number(value):
    """Macro numbers arrive as int or str depending on who wrote the file."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def macro_rows(payload: dict) -> list:
    """(NR, name, category) rows for the macro table, in macro-number order."""
    rows = []
    for item in payload.get("macro_table", []):
        number = parse_macro_number(item.get("nr"))
        if number is None:
            continue
        rows.append((number, item.get("name", ""), item.get("category", "") or ""))
    rows.sort(key=lambda row: row[0])
    return rows


def macros_by_number(payload: dict) -> dict:
    """Full macro records keyed by number. Unparsable numbers are dropped."""
    macros = {}
    for item in payload.get("macro_table", []):
        number = parse_macro_number(item.get("nr"))
        if number is None:
            continue
        macros[number] = {
            "nr": number,
            "name": item.get("name", ""),
            "category": item.get("category", "") or "",
            "call_type": item.get("call_type") or "M98",
            "description": item.get("description", "") or "",
        }
    return macros
