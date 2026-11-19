"""The Rename… window (1.53): pattern, numbering, order, and a preview of every new name."""
import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

import renamer

PREVIEW_MAX = 300
TOKENS = [("{person}", "Person"), ("{people}", "Everyone"), ("{date}", "Date"), ("{time}", "Time"),
          ("{folder}", "Folder"), ("{n}", "Number"), ("{name}", "Old name")]


class PatternBox(QWidget):
    """Pattern field + buttons that insert the parts (shared with Sort into folders)."""

    def __init__(self, text, on_change=None, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.box = QComboBox()
        self.box.setEditable(True)
        self.box.addItems(renamer.EXAMPLES)
        self.box.setEditText(text)
        lay.addWidget(self.box)
        row = QHBoxLayout()
        row.setSpacing(4)
        for tok, label in TOKENS:
            b = QPushButton(label)
            b.setToolTip(f"Adds {tok}")
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _=False, t=tok: self.insert(t))
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        if on_change:
            self.box.editTextChanged.connect(lambda _t: on_change())

    def insert(self, tok):
        le = self.box.lineEdit()
        txt = le.text()
        pos = le.cursorPosition()
        sep = " " if pos and txt[pos - 1:pos] not in (" ", "-", "_") else ""
        le.setText(txt[:pos] + sep + tok + txt[pos:])
        le.setCursorPosition(pos + len(sep) + len(tok))
        le.setFocus()

    def text(self):
        return self.box.currentText().strip()


class RenameDialog(QDialog):
    def __init__(self, win, pids, person=None, title=None, gallery_order=None):
        super().__init__(win)
        self.win, self.pids, self.person, self.order_ids = win, list(pids), person, gallery_order
        st = win.settings
        n = len(self.pids)
        self.setWindowTitle(title or f"Rename {n:,} file{'s' if n != 1 else ''}")
        self.resize(820, 560)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.pattern = PatternBox(st.get("rename_pattern") or renamer.EXAMPLES[0], self.schedule)
        form.addRow("New name:", self.pattern)
        row = QHBoxLayout()
        self.start = QSpinBox()
        self.start.setRange(0, 999999)
        self.start.setValue(int(st.get("rename_start", 1)))
        self.digits = QSpinBox()
        self.digits.setRange(0, 8)
        self.digits.setSpecialValueText("auto")
        self.digits.setValue(int(st.get("rename_digits", 0)))
        self.order = QComboBox()
        for key, label in renamer.ORDERS:
            if key == "gallery" and not gallery_order:
                continue
            self.order.addItem(label, key)
        self.order.setCurrentIndex(max(0, self.order.findData(st.get("rename_order", "date"))))
        for lbl, w in (("Numbers start at", self.start), ("digits", self.digits), ("in order of", self.order)):
            row.addWidget(QLabel(lbl))
            row.addWidget(w)
        row.addStretch()
        form.addRow("", row)
        lay.addLayout(form)
        for w in (self.start, self.digits):
            w.valueChanged.connect(self.schedule)
        self.order.currentIndexChanged.connect(self.schedule)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Now", "After"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        lay.addWidget(self.table, 1)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: gray;")
        lay.addWidget(self.note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = bb.button(QDialogButtonBox.Ok)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.rows = []
        self.timer = QTimer(self, singleShot=True, interval=250)
        self.timer.timeout.connect(self.refresh)
        self.refresh()

    def schedule(self, *_):
        self.timer.start()

    def options(self):
        return dict(pattern=self.pattern.text() or "{name}", start=self.start.value(), digits=self.digits.value(),
                    order=self.order.currentData())

    def refresh(self):
        o = self.options()
        try:
            self.rows = renamer.plan(self.win.conn, self.pids, o["pattern"], o["start"], o["digits"], o["order"],
                                     self.person, self.order_ids)
        except Exception as e:
            self.rows = []
            self.note.setText(f"That pattern doesn't work: {e}")
            self.ok.setEnabled(False)
            return
        shown = self.rows[:PREVIEW_MAX]
        self.table.setRowCount(len(shown))
        for i, (_pid, old, new) in enumerate(shown):
            a = QTableWidgetItem(os.path.basename(old))
            a.setToolTip(old)
            self.table.setItem(i, 0, a)
            self.table.setItem(i, 1, QTableWidgetItem(os.path.basename(new)))
        n = len(self.rows)
        same = len(self.pids) - n
        msg = f"{n:,} file{'s' if n != 1 else ''} get a new name"
        if same > 0:
            msg += f" ({same:,} already have it)"
        if n > PREVIEW_MAX:
            msg += f" - the first {PREVIEW_MAX} are shown"
        msg += (". Files stay in their folders; their .xmp / .json / .aae files are renamed with them. "
                "A name that's taken gets (2), (3)… Undo from History if you change your mind.")
        self.note.setText(msg)
        self.ok.setText(f"Rename {n:,}" if n else "Rename")
        self.ok.setEnabled(n > 0)

    def accept(self):
        o = self.options()
        st = self.win.settings
        st["rename_pattern"], st["rename_start"], st["rename_digits"], st["rename_order"] = (
            o["pattern"], o["start"], o["digits"], o["order"])
        super().accept()


def run_rename(win, pids, person=None, title=None, gallery_order=None):
    """Show the window; rename in the background; refresh."""
    from PySide6.QtWidgets import QMessageBox
    import renamer as r
    if not pids:
        return
    dlg = RenameDialog(win, pids, person, title, gallery_order)
    if dlg.exec() != QDialog.Accepted or not dlg.rows:
        return
    rows = list(dlg.rows)

    def job(conn, progress=None, cancelled=None):
        res = r.apply(conn, rows, progress=progress, cancelled=cancelled)
        res["history_label"] = f"Renamed {res['renamed']:,} file{'s' if res['renamed'] != 1 else ''}"
        return res

    def done(res):
        if not res:
            return
        win.pix_cache.clear() if hasattr(win, "pix_cache") else None
        msg = f"Renamed {res['renamed']:,} file{'s' if res['renamed'] != 1 else ''}."
        if res["errors"]:
            msg += f"\n\n{len(res['errors'])} couldn't be renamed:\n" + "\n".join(
                f"• {os.path.basename(p)}: {e}" for p, e in res["errors"][:8])
            QMessageBox.warning(win, "Rename", msg)
        else:
            win.statusBar().showMessage("✓ " + msg + " (History can undo it)", 10000)

    if not win.run_job("rename", job, on_done=done):
        QMessageBox.information(win, "Rename", "Wait for the current job to finish first.")
