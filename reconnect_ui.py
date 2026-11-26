"""The Reconnect to galleries window (1.60): where each file in ~Unsorted probably belongs, and why."""
import os

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

ICON = 56


class ReconnectDialog(QDialog):
    def __init__(self, win, found, gals, lib):
        super().__init__(win)
        self.win, self.found, self.gals, self.lib = win, found, gals, lib
        self.setWindowTitle("Reconnect files to their galleries")
        self.resize(1340, 740)
        lay = QVBoxLayout(self)
        n = sum(len(v) for v in found.values())
        sure = sum(1 for v in found.values() for m in v if m["sure"])
        head = QLabel(f"<b>{n:,} file{'s' if n != 1 else ''} in ~Unsorted probably belong in {len(found):,} "
                      f"galler{'ies' if len(found) != 1 else 'y'}.</b> {sure:,} {'is' if sure == 1 else 'are'} sure and ticked; "
                      "tick any others "
                      "you agree with. Matching is by shoot evidence only - where a file came from, its name and "
                      "number, when it was taken, the scene, the outfit at that shoot. Files with no shoot evidence "
                      "stay in ~Unsorted.")
        head.setWordWrap(True)
        lay.addWidget(head)
        body = QHBoxLayout()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["File", "How sure", "Why"])
        self.tree.setIconSize(QSize(ICON, ICON))
        self.tree.header().setSectionResizeMode(0, QHeaderView.Interactive)
        self.tree.header().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tree.setColumnWidth(0, 330)
        self.tree.setColumnWidth(1, 80)
        self.tree.setUniformRowHeights(False)
        body.addWidget(self.tree, 1)
        # preview: the file, and photos from the gallery beside it
        from viewer_parts import PicPanel
        side = QVBoxLayout()
        self.file_panel = PicPanel(win, "This file")
        self.gal_panel = PicPanel(win, "The gallery")
        pw = QWidget()
        pl = QVBoxLayout(pw)
        pl.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(self.file_panel)
        row.addWidget(self.gal_panel)
        pl.addLayout(row)
        side.addWidget(pw)
        body.addLayout(side)
        self.file_panel.face.hide()
        self.gal_panel.face.hide()
        lay.addLayout(body, 1)
        self.tag = QCheckBox("Also tag the gallery's person on the files moved (when nobody's named on them yet)")
        self.tag.setChecked(True)
        lay.addWidget(self.tag)
        bb = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.go = bb.addButton("Move", QDialogButtonBox.AcceptRole)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._icons = []
        self.fill()
        self.tree.itemChanged.connect(self._changed)
        self.tree.currentItemChanged.connect(self._show)
        self._count()
        QTimer.singleShot(0, self._load_icons)

    def fill(self):
        self.tree.blockSignals(True)
        order = sorted(self.found.items(), key=lambda kv: (-sum(m["sure"] for m in kv[1]), -len(kv[1]), kv[0]))
        for gd, matches in order:
            g = self.gals[gd]
            rel = os.path.relpath(gd, self.lib) if self.lib else gd
            top = QTreeWidgetItem([f"{rel}  ·  {len(matches):,} file{'s' if len(matches) != 1 else ''}"
                                   + (f"  ·  {g.person_name}" if g.person_name else ""), "", f"{len(g.ids):,} photos already"])
            top.setFlags(top.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            top.setData(0, Qt.UserRole, ("gallery", gd))
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            for m in matches:
                txt = "; ".join(m["reasons"])
                if m["also"]:
                    txt += "  ·  also fits " + ", ".join(os.path.basename(a) for a in m["also"])
                it = QTreeWidgetItem([os.path.basename(m["path"]), "sure" if m["sure"] else
                                      ("likely" if not m["also"] else "close call"), txt])
                it.setToolTip(2, txt)
                it.setToolTip(0, m["path"])
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(0, Qt.Checked if m["sure"] else Qt.Unchecked)
                it.setData(0, Qt.UserRole, ("file", gd, m["id"], m["path"]))
                top.addChild(it)
                self._icons.append(it)
            self.tree.addTopLevelItem(top)
            top.setExpanded(len(self.found) <= 12)
        self.tree.blockSignals(False)

    def _load_icons(self, start=0):
        from viewer_parts import framed_pixmap
        end = min(len(self._icons), start + 12)
        for it in self._icons[start:end]:
            try:
                pm = framed_pixmap(it.data(0, Qt.UserRole)[3], None, ICON, ICON, crop=True, side=240)
                if not pm.isNull():
                    it.setIcon(0, QIcon(pm))
            except RuntimeError:          # (the window closed)
                return
        if end < len(self._icons) and self.isVisible() or end < len(self._icons) and start == 0:
            QTimer.singleShot(0, lambda: self._load_icons(end))

    def _changed(self, *_a):
        self._count()

    def picks(self):
        out = []
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            gd = top.data(0, Qt.UserRole)[1]
            person = self.gals[gd].person
            for k in range(top.childCount()):
                it = top.child(k)
                if it.checkState(0) == Qt.Checked:
                    out.append((it.data(0, Qt.UserRole)[2], gd, person))
        return out

    def _count(self):
        n = len(self.picks())
        self.go.setText(f"Move {n:,} into their galleries")
        self.go.setEnabled(bool(n))

    def _show(self, it, _prev=None):
        if it is None:
            return
        d = it.data(0, Qt.UserRole)
        gd = d[1]
        if d[0] == "file":
            self.file_panel.head.setText(f"<b>{os.path.basename(d[3])}</b>")
            self.file_panel.show_items([(None, d[3], None)], "<span style='color:gray'>"
                                       + "<br>".join(it.text(2).split("; ")) + "</span>")
        g = self.gals[gd]
        rows = self.win.conn.execute(f"SELECT path FROM photos WHERE id IN ({','.join('?' * min(6, len(g.ids)))})",
                                     g.ids[:: max(1, len(g.ids) // 6)][:6]).fetchall()
        self.gal_panel.head.setText(f"<b>{os.path.basename(gd)}</b>")
        self.gal_panel.show_items([(None, r[0], None) for r in rows],
                                  f"{len(g.ids):,} photos" + (f" · {g.person_name}" if g.person_name else "")
                                  + ("<br><span style='color:gray'>Click a small picture to see it big</span>"
                                     if len(rows) > 1 else ""))
