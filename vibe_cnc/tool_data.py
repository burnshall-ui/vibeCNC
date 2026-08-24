# tool_data.py — the tool library, free of GUI dependencies.
#
# tools/tools.json is the single source for tool data. These functions used to
# live in tool_model, which imports PyQt6 at module level, so every GUI-free
# caller — the parser and the lint engine — silently fell back to an empty tool
# table whenever Qt was absent. The parser then compensated with a nose radius
# of zero and the lint engine reported every tool's radius as missing.
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_JSON = os.path.join(HERE, "tools", "tools.json")


def load_tools_json() -> dict:
    """The tool library as stored. An unreadable file yields an empty table."""
    try:
        with open(TOOLS_JSON, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {"tool_table": []}


def save_tools_json(payload: dict) -> None:
    """Writes the tool library back. The only place that writes it."""
    os.makedirs(os.path.dirname(TOOLS_JSON), exist_ok=True)
    with open(TOOLS_JSON, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


def parse_tool_number(value):
    """Tool numbers arrive as int or str depending on who wrote the file."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def tool_rows(payload: dict) -> list:
    """(T, D, comment) rows for the tool table, in tool-number order.

    Sorted here because the table used to come from SQLite with an ORDER BY
    while the details came from the JSON in file order — the two drifted apart
    and showed different tools under the same row.
    """
    rows = []
    for item in payload.get("tool_table", []):
        number = parse_tool_number(item.get("t"))
        if number is None:
            continue
        diameter = item.get("d_mm")
        rows.append((number, "-" if diameter is None else diameter, item.get("name")))
    rows.sort(key=lambda row: row[0])
    return rows


def tools_by_number(payload: dict) -> dict:
    """Full tool records keyed by tool number. Unparsable numbers are dropped."""
    tools = {}
    for item in payload.get("tool_table", []):
        number = parse_tool_number(item.get("t"))
        if number is not None:
            tools[number] = item
    return tools
