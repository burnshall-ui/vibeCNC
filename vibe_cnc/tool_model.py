import os, sqlite3, json
from PyQt6.QtCore import QAbstractTableModel, Qt, QVariant

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _parse_tool_number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

class ToolModel(QAbstractTableModel):
    HEADERS = ["T", "D", "KOMMENTAR"]
    def __init__(self):
        super().__init__()
        self.rows = self._load_tools()
        self.tool_data = self._load_tool_data()  # Full tool data for details

    def _load_tools(self):
        """Load table rows"""
        try:
            db = os.path.join(HERE, "tools", "tools.db")
            conn = sqlite3.connect(db)
            try:
                cur = conn.cursor()
                cur.execute("SELECT t, COALESCE(d_mm,'-') AS d, name FROM tools ORDER BY t ASC;")
                rows = cur.fetchall()
                return rows
            finally:
                conn.close()
        except Exception as e:
            # Fallback: JSON
            j = load_tools_json()
            rows = []
            for item in j.get("tool_table", []):
                d = item.get("d_mm", "-")
                rows.append((item.get("t"), d, item.get("name")))
            return rows

    def _load_tool_data(self):
        """Load full tool data from JSON for details"""
        j = load_tools_json()
        tools = {}
        for item in j.get("tool_table", []):
            tool_num = _parse_tool_number(item.get("t"))
            if tool_num is None:
                continue
            tools[tool_num] = item
        return tools

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

def load_tools_json():
    try:
        with open(os.path.join(HERE, "tools", "tools.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"tool_table": []}

