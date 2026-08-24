# macro_model.py — Qt table model over the macro library.
#
# The data itself lives in macro_data, which needs no GUI stack.
from typing import Optional

from PyQt6.QtCore import QAbstractTableModel, Qt, QVariant

from .macro_data import (  # noqa: F401 - re-exported for existing callers
    CALL_TYPES,
    load_macros_json,
    macro_rows,
    macros_by_number,
    parse_macro_number,
    save_macros_json,
)


class MacroModel(QAbstractTableModel):
    HEADERS = ["NR", "NAME", "CATEGORY"]

    def __init__(self):
        super().__init__()
        self.reload()

    def reload(self):
        """Re-reads macros.json and tells attached views to start over.

        beginResetModel rather than layoutChanged: adding or deleting a macro
        changes the row count, which layoutChanged does not cover — views keep
        their old count and read past the end of the list.
        """
        self.beginResetModel()
        payload = load_macros_json()
        self.rows = macro_rows(payload)
        self.macro_data = macros_by_number(payload)
        self.endResetModel()

    def get_macro(self, nr: int) -> Optional[dict]:
        return self.macro_data.get(nr)

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
