"""Confirm the sure ones (1.59): a person's suggestions confirmed down to a match-strength line, with
the weakest of them - the ones just above the line - shown to check by eye first."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QSlider, QVBoxLayout)

STRIP = 40               # how many of the weakest above the line are shown
DEFAULT_LINE = 80        # % - suggestions this sure are confirmed unless you move the line


def suggestions(conn, person_id):
    """[(face id, strength 50-100)] of a person's suggestions, strongest first."""
    return [(fid, float(s or 50)) for fid, s in conn.execute(
        """SELECT id, sugg_conf FROM faces WHERE person_id=? AND confirmed=0 AND ignored=0
           ORDER BY COALESCE(sugg_conf, 50) DESC, id""", (person_id,))]


class SureDialog(QDialog):
    def __init__(self, win, person_id, name, items):
        super().__init__(win)
        self.win, self.items = win, items
        self.setWindowTitle(f"Confirm the sure ones - {name}")
        self.resize(820, 560)
        lay = QVBoxLayout(self)
        head = QLabel(f"<b>{len(items):,} suggestion{'s' if len(items) != 1 else ''} for {name}.</b> Everything at or "
                      "above the line is confirmed; the rest stay suggestions for you to look at. The ones "
                      "nearest the line are shown below - untick any that aren't them.")
        head.setWordWrap(True)
        lay.addWidget(head)
        row = QHBoxLayout()
        row.addWidget(QLabel("Match strength:"))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(50, 100)
        low = int(min(s for _f, s in items)) if items else 50
        self.slider.setValue(max(low, DEFAULT_LINE) if any(s >= DEFAULT_LINE for _f, s in items) else low)
        self.slider.setTickPosition(QSlider.TicksBelow)
        self.slider.setTickInterval(5)
        row.addWidget(self.slider, 1)
        self.val = QLabel()
        self.val.setMinimumWidth(60)
        row.addWidget(self.val)
        lay.addLayout(row)
        self.count = QLabel()
        self.count.setStyleSheet("font-weight: 600;")
        lay.addWidget(self.count)
        self.strip = QListWidget()
        self.strip.setViewMode(QListWidget.IconMode)
        self.strip.setIconSize(QSize(96, 96))
        self.strip.setResizeMode(QListWidget.Adjust)
        self.strip.setMovement(QListWidget.Static)
        self.strip.setSpacing(6)
        lay.addWidget(self.strip, 1)
        self.unticked = set()
        self.strip.itemChanged.connect(self._ticked)
        bb = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.go = bb.addButton("Confirm", QDialogButtonBox.AcceptRole)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.slider.valueChanged.connect(self.update_view)
        self.update_view()

    def above(self):
        line = self.slider.value()
        return [f for f, s in self.items if s >= line]

    def chosen(self):
        return [f for f in self.above() if f not in self.unticked]

    def _ticked(self, it):
        fid = it.data(Qt.UserRole)
        if it.checkState() == Qt.Checked:
            self.unticked.discard(fid)
        else:
            self.unticked.add(fid)
        self._count()

    def _count(self):
        n, total = len(self.chosen()), len(self.items)
        self.count.setText(f"{n:,} of {total:,} will be confirmed · {total - n:,} stay suggestions")
        self.go.setText(f"Confirm {n:,}")
        self.go.setEnabled(bool(n))

    def update_view(self):
        line = self.slider.value()
        self.val.setText(f"{line}%")
        near = [(f, s) for f, s in self.items if s >= line][-STRIP:]       # the weakest above the line
        self.strip.blockSignals(True)
        self.strip.clear()
        for fid, s in reversed(near):
            it = QListWidgetItem(f"{s:.0f}%")
            pm = self._face(fid)
            if pm is not None:
                it.setIcon(QIcon(pm))
            it.setData(Qt.UserRole, fid)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked if fid in self.unticked else Qt.Checked)
            it.setToolTip(f"Match strength {s:.0f}% - untick if it isn't them")
            self.strip.addItem(it)
        self.strip.blockSignals(False)
        self._count()

    def _face(self, fid):
        try:
            pm = self.win.face_pixmap(fid, want_px=96, face_only=True)
            if pm is not None and not pm.isNull():
                return pm
        except Exception:
            pass
        row = self.win.conn.execute("SELECT thumb FROM faces WHERE id=?", (fid,)).fetchone()
        if row and row[0]:
            pm = QPixmap()
            pm.loadFromData(row[0], "JPG")
            return pm
        return None
