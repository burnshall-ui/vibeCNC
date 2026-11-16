# fonts.py — the monospace faces the interface asks for.
#
# Consolas is the right face on the Windows machines this runs on, and exists
# nowhere else. Asking Qt for a family the machine does not have costs a few
# hundred milliseconds at every start while it hunts for a substitute (it logs
# 'Replace uses of missing font family "Consolas"'). So only the families that
# are actually installed are asked for, in the order of preference below.
#
# Needs a QGuiApplication: QFontDatabase cannot be read before one exists.
from functools import lru_cache

from PyQt6.QtGui import QFontDatabase

# Most preferred first.
CANDIDATES = ("Consolas", "Menlo", "SF Mono", "DejaVu Sans Mono", "Fira Code")


@lru_cache(maxsize=1)
def mono_families() -> tuple:
    """The candidates installed on this machine, most preferred first.

    Empty when none is -- callers then fall back on the generic monospace hint.
    """
    installed = set(QFontDatabase.families())
    return tuple(name for name in CANDIDATES if name in installed)


def mono_css() -> str:
    """The same list as a CSS font-family value, ending in the generic family."""
    names = [f"'{name}'" if " " in name else name for name in mono_families()]
    return ", ".join(names + ["monospace"])
