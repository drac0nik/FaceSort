"""History (1.47): the list of what you've done, with an Undo for each entry."""
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QVBoxLayout)

import history


def when(t):
    d = time.time() - t
    if d < 60:
        return "just now"
    if d < 3600:
        return f"{int(d // 60)} min ago"
    if d < 86400:
        return time.strftime("%H:%M", time.localtime(t))
    return time.strftime("%b %d %H:%M", time.localtime(t))


class HistoryDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("History")
        self.resize(640, 520)
        lay = QVBoxLayout(self)
        note = QLabel("What you've done in the last 30 days (and at least the last 5,000 changes). Undo any of them - "
                      "anything changed again since stays as it is now. Ctrl+Z undoes the latest.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        lay.addWidget(note)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _it: self.undo())
        lay.addWidget(self.list)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        self.undo_btn = QPushButton("Undo")
        self.undo_btn.clicked.connect(self.undo)
        bb.addButton(self.undo_btn, QDialogButtonBox.ActionRole)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.list.currentItemChanged.connect(self.update_button)
        self.fill()

    def fill(self):
        self.list.clear()
        for e in history.entries(self.win.conn):
            it = QListWidgetItem(f"{when(e['at'])}   ·   {e['text']}" + ("   (undone)" if e["undone"] else ""))
            it.setData(Qt.UserRole, e)
            if e["undone"]:
                it.setForeground(Qt.gray)
            self.list.addItem(it)
        self.update_button()

    def update_button(self, *_):
        it = self.list.currentItem()
        e = it.data(Qt.UserRole) if it else None
        self.undo_btn.setEnabled(bool(e) and not e["undone"])

    def undo(self):
        it = self.list.currentItem()
        e = it.data(Qt.UserRole) if it else None
        if not e or e["undone"]:
            return
        done, skipped = history.undo(self.win.conn, e["id"])
        self.win._pid_cache = {}
        self.win.after_change()
        if skipped:
            QMessageBox.information(self, "Undone", f"Undone: {e['text']}\n\n{skipped:,} item"
                                    f"{'s were' if skipped != 1 else ' was'} changed since (or moved), so "
                                    f"{'they were' if skipped != 1 else 'it was'} left as {'they are' if skipped != 1 else 'it is'} now.")
        self.fill()
