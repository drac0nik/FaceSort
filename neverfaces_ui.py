"""Settings > Never track list… (1.51): the permanent list of things that are never faces."""
import time

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog, QLabel, QListWidget,
                               QListWidgetItem, QPushButton, QVBoxLayout)

import neverfaces


class NeverTrackDialog(QDialog):
    def __init__(self, parent, conn):
        super().__init__(parent)
        self.conn = conn
        self.changed = False
        self.setWindowTitle("Never track")
        self.setMinimumSize(460, 420)
        lay = QVBoxLayout(self)
        note = QLabel("Things that look like a face but never are - a logo, a watermark, a drawing. Every face that "
                      "matches one closely, now and in photos scanned later, is set aside as not a face by itself "
                      "(faces you've named are never touched). Add to this list by right-clicking a face > "
                      "Never track this….")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.list = QListWidget()
        self.list.setIconSize(QSize(56, 56))
        lay.addWidget(self.list, 1)
        self.cb_restore = QCheckBox("When removing: bring back the faces it set aside")
        self.cb_restore.setChecked(True)
        lay.addWidget(self.cb_restore)
        row = QHBoxLayout()
        for text, fn in (("Rename…", self.do_rename), ("Remove", self.do_remove)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.fill()

    def fill(self):
        self.list.clear()
        counts = {}
        try:
            counts = dict(self.conn.execute("SELECT entry_id, COUNT(*) FROM never_hits GROUP BY entry_id"))
        except Exception:
            pass
        entries = neverfaces.load()
        for e in entries:
            n = counts.get(e["id"], 0)
            it = QListWidgetItem(f"{e['label']}   ·   {n:,} face{'s' if n != 1 else ''} set aside   ·   added "
                                 f"{time.strftime('%d %b %Y', time.localtime(e['added']))}")
            if e.get("thumb"):
                pm = QPixmap()
                if pm.loadFromData(e["thumb"]):
                    it.setIcon(QIcon(pm))
            it.setData(Qt.UserRole, e["id"])
            self.list.addItem(it)
        if not entries:
            self.list.addItem("Nothing yet - right-click a face > Never track this….")

    def current(self):
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it is not None else None

    def do_rename(self):
        eid = self.current()
        if eid is None:
            return
        old = next((e["label"] for e in neverfaces.load() if e["id"] == eid), "")
        name, ok = QInputDialog.getText(self, "Rename", "Name:", text=old)
        if ok and name.strip():
            neverfaces.rename(eid, name)
            self.fill()

    def do_remove(self):
        eid = self.current()
        if eid is None:
            return
        neverfaces.remove(self.conn, eid, restore=self.cb_restore.isChecked())
        self.changed = True
        self.fill()
