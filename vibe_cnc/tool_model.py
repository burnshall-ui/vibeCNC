# tool_model.py — Qt table model over the tool library.
#
# The data itself lives in tool_data, which needs no GUI stack. This module only
# adapts it to QAbstractTableModel.
from PyQt6.QtCore import QAbstractTableModel, Qt, QVariant

from .tool_data import (  # noqa: F401 - load_tools_json is re-exported on purpose
    load_tools_json,
    parse_tool_number,
    save_tools_json,
    tool_rows,
    tools_by_number,
)


class ToolModel(QAbstractTableModel):
    HEADERS = ["T", "D", "KOMMENTAR"]

    def __init__(self):
        super().__init__()
        self.reload()

    def reload(self):
        """Re-reads tools.json and tells attached views to start over.

        Rows and details are built from the same payload. They used to come from
        two different files — the rows from tools.db, the details from
        tools.json — and only the JSON was ever written, so an edit showed up in
        the detail pane and nowhere else.

        beginResetModel rather than layoutChanged: adding or deleting a tool
        changes the row count, which layoutChanged does not cover.
        """
        self.beginResetModel()
        payload = load_tools_json()
        self.rows = tool_rows(payload)
        self.tool_data = tools_by_number(payload)
        self.endResetModel()

    def get_tool_info(self, tool_num: int) -> dict:
        """Get detailed information about a specific tool"""
        return self.tool_data.get(tool_num, {})

    def get_tool_code(self, tool_num: int) -> str:
        """Generate G-code for tool (e.g. T0101)"""
        return f"T{tool_num:02d}01"

    def get_tool_speed_feed(self, tool_num: int) -> tuple:
        """Get recommended speed and feed for tool (S, F)"""
        tool = self.tool_data.get(tool_num, {})
        limits = tool.get("limits", {})

        # Default values if limits not set
        vc_max = limits.get("vc_max", 150)
        f_max = limits.get("f_max", 0.25)

        # Calculate S (RPM) from vc_max (assuming material-dependent, using middle value)
        # For demonstration: S = vc_max * 318 / d (dummy formula)
        S = int(vc_max * 2)  # Simplified conversion

        # F is direct
        F = f_max

        return (S, F)

    def rowCount(self, parent=None): return len(self.rows)
    def columnCount(self, parent=None): return 3

    def headerData(self, sec, orient, role):
        if role == Qt.ItemDataRole.DisplayRole and orient == Qt.Orientation.Horizontal:
            return self.HEADERS[sec]

    def data(self, index, role):
        if not index.isValid(): return QVariant()
        r, c = index.row(), index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return str(self.rows[r][c])
        return QVariant()
