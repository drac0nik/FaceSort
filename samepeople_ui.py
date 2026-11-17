"""Possibly the same person (1.49): the window for merging two people."""
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QRadioButton, QVBoxLayout)

import store


class MergeDialog(QDialog):
    def __init__(self, parent, conn, a, b, why=""):
        super().__init__(parent)
        self.conn, self.a, self.b = conn, a, b
        info = {}
        for pid in (a, b):
            name, cat = conn.execute("SELECT name, category_id FROM persons WHERE id=?", (pid,)).fetchone()
            n = conn.execute("SELECT COUNT(*) FROM faces WHERE person_id=? AND confirmed=1", (pid,)).fetchone()[0]
            info[pid] = (name, cat, n)
        self.info = info
        self.setWindowTitle("Merge into one person")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        na, nb = info[a][0], info[b][0]
        head = QLabel(f"<b>{na}</b> ({info[a][2]:,} faces) and <b>{nb}</b> ({info[b][2]:,} faces) become one person."
                      + (f"<br><span style='color:gray'>Suggested because: {why}</span>" if why else ""))
        head.setWordWrap(True)
        lay.addWidget(head)
        form = QFormLayout()
        nrow = QVBoxLayout()
        self.names = QButtonGroup(self)
        self.rb_a, self.rb_b, self.rb_other = QRadioButton(na), QRadioButton(nb), QRadioButton("Another name:")
        big = a if info[a][2] >= info[b][2] else b
        (self.rb_a if big == a else self.rb_b).setChecked(True)
        self.other = QLineEdit()
        self.other.setEnabled(False)
        self.rb_other.toggled.connect(self.other.setEnabled)
        for i, rb in enumerate((self.rb_a, self.rb_b)):
            self.names.addButton(rb, i)
            nrow.addWidget(rb)
        orow = QHBoxLayout()
        orow.addWidget(self.rb_other)
        orow.addWidget(self.other)
        self.names.addButton(self.rb_other, 2)
        nrow.addLayout(orow)
        form.addRow("Name:", nrow)
        self.folder = QComboBox()
        label = lambda cat: store.folder_label(conn, cat) if cat else "no folder"
        self.folder.addItem(f"{na}'s: {label(info[a][1])}", "a")
        if info[b][1] != info[a][1]:
            self.folder.addItem(f"{nb}'s: {label(info[b][1])}", "b")
        self.folder.setCurrentIndex(0 if info[a][1] or not info[b][1] else 1)
        form.addRow("Folder:", self.folder)
        lay.addLayout(form)
        note = QLabel("Their faces, tags, tattoos and own folders all go to the one person. You can undo this in History.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        lay.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Merge")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def choice(self):
        """(keep id, other id, name, folder) for samepeople.merge."""
        if self.rb_b.isChecked():
            keep, other = self.b, self.a
        else:
            keep, other = self.a, self.b
        name = " ".join(self.other.text().split()) if self.rb_other.isChecked() else self.info[keep][0]
        side = self.folder.currentData()
        cat = self.info[self.a if side == "a" else self.b][1]
        return keep, other, name or self.info[keep][0], cat
