# fold_panel.py — the tabbed panel under the simulation, which folds away.
#
# The program and the simulation each want height: the program to show many
# blocks at once, the half section to show the part at a useful size. So they
# stand side by side, and the tool library, the macros and the assist share
# one strip below the simulation. A click on a tab opens it; a click on the
# open tab folds the panel down to its row of tabs, and the simulation gets
# the height. The window decides what folding means for its splitter -- this
# says when it happens.
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QPushButton, QStackedWidget, QVBoxLayout, QWidget

# Qt's QWIDGETSIZE_MAX, which PyQt6 does not export: "no maximum".
NO_MAXIMUM = 16777215

FOLD = "▾"
UNFOLD = "▴"


class FoldPanel(QWidget):
    """Tabs over a stack of pages, folding down to the tabs alone."""

    folded_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tabs = []
        self._folded = False

        self.bar = QWidget()
        self._bar_layout = QHBoxLayout(self.bar)
        self._bar_layout.setContentsMargins(0, 0, 0, 0)
        self._bar_layout.setSpacing(1)
        self.toggle = QPushButton(FOLD)
        self.toggle.setObjectName("PanelTab")
        self.toggle.clicked.connect(lambda: self.set_folded(not self._folded))
        self._bar_layout.addWidget(self.toggle)

        self.stack = QStackedWidget()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.bar)
        layout.addWidget(self.stack)
        self._sync()

    def add_page(self, title: str, widget: QWidget) -> int:
        """Adds a tab and its page; returns the page's index."""
        index = len(self.tabs)
        button = QPushButton(title)
        button.setObjectName("PanelTab")
        button.setCheckable(True)
        button.clicked.connect(lambda _checked, i=index: self.tab_clicked(i))
        self._bar_layout.insertWidget(index, button, 1)
        self.tabs.append(button)
        self.stack.addWidget(widget)
        self._sync()
        return index

    def tab_clicked(self, index: int):
        """The open tab folds the panel; any other tab opens its page."""
        if index == self.stack.currentIndex() and not self._folded:
            self.set_folded(True)
        else:
            self.show_page(index)

    def show_page(self, index: int):
        """Opens the panel on one page, whatever state it was in."""
        self.stack.setCurrentIndex(index)
        self.set_folded(False)
        self._sync()

    def set_folded(self, folded: bool):
        folded = bool(folded)
        changed = folded != self._folded
        self._folded = folded
        self.stack.setVisible(not folded)
        # A splitter will not make a widget taller than its maximum, so the
        # simulation above takes up what the pages gave back.
        self.setMaximumHeight(self.bar.sizeHint().height() if folded else NO_MAXIMUM)
        self._sync()
        if changed:
            self.folded_changed.emit(folded)

    def is_folded(self) -> bool:
        return self._folded

    def current_index(self) -> int:
        return self.stack.currentIndex()

    def toggle_folded(self):
        self.set_folded(not self._folded)

    def _sync(self):
        """Tabs and the fold button say what is open."""
        current = self.stack.currentIndex()
        for index, button in enumerate(self.tabs):
            button.setChecked(not self._folded and index == current)
        self.toggle.setText(UNFOLD if self._folded else FOLD)
        self.toggle.setToolTip("Open the panel (Ctrl+J)" if self._folded
                               else "Fold the panel away; the simulation gets the room (Ctrl+J)")
