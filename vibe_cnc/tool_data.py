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


# Imaginary tool nose direction (Fanuc "tool nose radius compensation" tip
# number, 0-9). The programmed point is the imaginary tool nose -- the corner
# where the two tangents of the insert meet -- while compensation is computed
# around the centre of the nose radius. The tip number says where the one sits
# relative to the other.
#
# Values below are the vector from the imaginary nose to the radius centre, in
# units of the nose radius and in (X, Z) machine directions: X away from the
# spindle centre line (radius, not diameter), Z away from the chuck. On a 90
# degree corner both components are a full radius, not r/sqrt(2) -- the nose
# point is the intersection of the tangents, not a point on the circle.
#
# 3 is the ordinary right-hand OD turning tool (centre sits outboard of the
# turned surface and behind the face), 2 the ordinary boring bar. 0 and 9 mean
# the nose point *is* the centre, which is what an unset field falls back to.
NOSE_OFFSETS = {
    0: (0.0, 0.0),
    1: (-1.0, -1.0),   # nose towards +X/+Z
    2: (-1.0, 1.0),    # nose towards +X/-Z  -- boring
    3: (1.0, 1.0),     # nose towards -X/-Z  -- OD turning
    4: (1.0, -1.0),    # nose towards -X/+Z
    5: (-1.0, 0.0),    # nose towards +X
    6: (0.0, 1.0),     # nose towards -Z
    7: (1.0, 0.0),     # nose towards -X
    8: (0.0, -1.0),    # nose towards +Z
    9: (0.0, 0.0),
}

# What the tool editor offers, in the order it offers it.
NOSE_DIRECTION_LABELS = {
    0: "0 - nose point = centre",
    1: "1 - nose towards +X/+Z",
    2: "2 - nose towards +X/-Z (boring)",
    3: "3 - nose towards -X/-Z (OD turning)",
    4: "4 - nose towards -X/+Z",
    5: "5 - nose towards +X",
    6: "6 - nose towards -Z",
    7: "7 - nose towards -X",
    8: "8 - nose towards +Z",
    9: "9 - nose point = centre",
}


def nose_direction_of(tool: dict) -> int:
    """The tip number of one tool record. Anything unusable reads as 0.

    Falling back to 0 keeps the geometry defined; the lint engine is the place
    that tells the operator the field is missing, see rule G41/G42.
    """
    try:
        direction = int(tool.get("nose_direction"))
    except (AttributeError, TypeError, ValueError):
        return 0
    return direction if direction in NOSE_OFFSETS else 0


def nose_offset(direction: int, radius: float) -> tuple:
    """Vector from the imaginary tool nose to the radius centre, in mm.

    Returned as (X, Z) in radius space -- the parser converts to diameter
    where it needs to.
    """
    ox, oz = NOSE_OFFSETS.get(direction, (0.0, 0.0))
    return ox * radius, oz * radius
