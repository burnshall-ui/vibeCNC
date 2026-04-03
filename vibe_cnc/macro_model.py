import os, sqlite3
from typing import Optional
from PyQt6.QtCore import QAbstractTableModel, Qt, QVariant

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DB_PATH = os.path.join(HERE, "tools", "macros.db")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS macros (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  nr INTEGER NOT NULL UNIQUE,
  name TEXT NOT NULL,
  category TEXT DEFAULT '',
  call_type TEXT DEFAULT 'M98', -- 'M98' (Unterprogramm) oder 'G65' (Makro)
  description TEXT DEFAULT ''
);
"""

def ensure_macro_db() -> None:
    os.makedirs(os.path.join(HERE, "tools"), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        # Prüfe vorhandene Tabelle/Schema
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='macros';")
        exists = cur.fetchone() is not None
        if exists:
            cur.execute("PRAGMA table_info(macros);")
            cols = [r[1] for r in cur.fetchall()]  # r[1] = name
            required = {"id","nr","name","category","call_type","description"}
            if not required.issubset(set(cols)):
                # inkompatibles Schema → neu anlegen
                cur.execute("DROP TABLE IF EXISTS macros;")
                conn.commit()
        # Erstelle Zieltabelle (falls nicht vorhanden oder gedroppt)
        cur.execute(SCHEMA_SQL)
        # Seed examples (idempotent via OR IGNORE)
        seed = [
            (9001, 'Bohrzyklus Peck', 'Bohren', 'G65', 'Makro: G65 P9001 (Peck Drilling)'),
            (9002, 'Ansenken 90°', 'Bohren', 'G65', 'Makro: G65 P9002 (Countersink)'),
            (9010, 'Antasten Z', 'Antasten', 'G65', 'Makro: G65 P9010 (Probe Z)'),
            (1000, 'Nutprogramm', 'Drehen', 'M98', 'Unterprogramm: M98 P1000'),
            (1100, 'Gewindeprogramm', 'Drehen', 'M98', 'Unterprogramm: M98 P1100'),
        ]
        cur.executemany(
            "INSERT OR IGNORE INTO macros(nr,name,category,call_type,description) VALUES (?,?,?,?,?);",
            seed,
        )
        conn.commit()
    finally:
        conn.close()

class MacroModel(QAbstractTableModel):
    HEADERS = ["NR", "NAME", "CATEGORY"]

    def __init__(self):
        super().__init__()
        ensure_macro_db()
        # zusätzliche Absicherung: Seed, falls DB bereits existiert, aber leer ist
        try:
            self._seed_examples_if_empty()
        except Exception:
            pass
        self.rows = self._load_rows()

    def _load_rows(self):
        try:
            conn = sqlite3.connect(DB_PATH)
            try:
                cur = conn.cursor()
                cur.execute("SELECT nr, name, COALESCE(category,'') FROM macros ORDER BY nr ASC;")
                rows = cur.fetchall()
                return rows
            finally:
                conn.close()
        except Exception:
            return []

    # Public helpers
    def get_macro(self, nr: int) -> Optional[dict]:
        try:
            conn = sqlite3.connect(DB_PATH)
            try:
                cur = conn.cursor()
                cur.execute("SELECT nr, name, category, call_type, description FROM macros WHERE nr=?;", (nr,))
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "nr": row[0],
                    "name": row[1],
                    "category": row[2] or '',
                    "call_type": row[3] or 'M98',
                    "description": row[4] or '',
                }
            finally:
                conn.close()
        except Exception:
            return None

    # Qt model impl
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

    # --- internal helpers ---
    def _seed_examples_if_empty(self):
        conn = sqlite3.connect(DB_PATH)
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(1) FROM macros;")
            row = cur.fetchone()
            count = (row[0] if row else 0) or 0
            if count == 0:
                seed = [
                    (9001, 'Bohrzyklus Peck', 'Bohren', 'G65', 'Makro: G65 P9001 (Peck Drilling)'),
                    (9002, 'Ansenken 90°', 'Bohren', 'G65', 'Makro: G65 P9002 (Countersink)'),
                    (9010, 'Antasten Z', 'Antasten', 'G65', 'Makro: G65 P9010 (Probe Z)'),
                    (1000, 'Nutprogramm', 'Drehen', 'M98', 'Unterprogramm: M98 P1000'),
                    (1100, 'Gewindeprogramm', 'Drehen', 'M98', 'Unterprogramm: M98 P1100'),
                ]
                cur.executemany(
                    "INSERT OR IGNORE INTO macros(nr,name,category,call_type,description) VALUES (?,?,?,?,?);",
                    seed,
                )
                conn.commit()
        finally:
            conn.close()

