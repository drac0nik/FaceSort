"""Gallery watcher window, the gallery / site settings windows and saved logins (1.56)."""
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

import watcher


def _ago(t):
    if not t:
        return "never"
    s = time.time() - t
    if s < 90:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)} min ago"
    if s < 86400 * 2:
        return f"{int(s // 3600)} h ago"
    return time.strftime("%d %b %Y", time.localtime(t))


def _names(text):
    return [n.strip() for n in (text or "").split(",") if n.strip()]


class RuleFields(QWidget):
    """How to read and how to tag - for one gallery (with "as the site says") or for a site."""

    def __init__(self, win, values, inherit=False, parent=None, host=""):
        super().__init__(parent)
        self.inherit = inherit
        f = QFormLayout(self)
        f.setContentsMargins(0, 0, 0, 0)
        self.mode = QComboBox()
        self._host = None
        self.set_host(host, values.get("mode"))
        f.addRow("Read:", self.mode)
        note = QLabel("Signed-in pages, Flickr, SuicideGirls, Patreon, ModelMayhem, YouPic… are read the way Max Image "
                      "Saver reads them (whole albums, full size, date taken).")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        f.addRow("", note)
        self.own = QCheckBox("Its own tagging (not the site's)") if inherit else None
        if self.own is not None:
            self.own.setChecked(any(values.get(k) is not None for k in ("people", "category", "later", "ask")))
            f.addRow("", self.own)
        self.people = QLineEdit(", ".join(values.get("people") or []))
        self.people.setPlaceholderText("Names, separated by commas (e.g. the model's name)")
        try:
            from PySide6.QtWidgets import QCompleter
            names = [r[0] for r in win.conn.execute("SELECT name FROM persons ORDER BY name COLLATE NOCASE")]
            if names:
                self.people.setCompleter(QCompleter(names, self))
        except Exception:
            pass
        f.addRow("Tag them as:", self.people)
        self.category = QLineEdit(values.get("category") or "")
        self.category.setPlaceholderText("A FaceSort folder (optional) - e.g. \"Jane Doe › Shoots\"")
        f.addRow("Folder:", self.category)
        self.later = QCheckBox("Put them in Sort Later")
        self.later.setChecked(bool(values.get("later")))
        f.addRow("", self.later)
        self.ask = QCheckBox("")                   # (1.57.6: the Add photos window always comes up)
        self.ask.setChecked(bool(values.get("ask")))
        self.ask.hide()
        hint = QLabel("The Add photos window comes up with these filled in, so you can check them first.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        f.addRow("", hint)
        if self.own is not None:
            self.own.toggled.connect(self._own)
            self._own(self.own.isChecked())

    def set_host(self, host, mode=Ellipsis):
        """(1.57.1) The Read: list for this site - its own Max Image Saver way of reading first."""
        host = (host or "").lower()
        if host == self._host:
            return
        self._host = host
        keep = self.mode.currentData() if mode is Ellipsis else mode
        self.mode.clear()
        if self.inherit:
            self.mode.addItem("As set for the site", None)
        for key, label in watcher.modes_for(host):
            self.mode.addItem(label, key)
        i = self.mode.findData(keep)
        if i < 0 and not self.inherit:
            i = self.mode.findData(watcher.site_default(host))
        self.mode.setCurrentIndex(max(0, i))

    def _own(self, on):
        for w in (self.people, self.category, self.later, self.ask):
            w.setEnabled(on)

    def values(self):
        own = self.own is None or self.own.isChecked()
        return {"mode": self.mode.currentData(),
                "people": _names(self.people.text()) if own else None,
                "category": (self.category.text().strip() or None) if own else None,
                "later": int(self.later.isChecked()) if own else None,
                "ask": int(self.ask.isChecked()) if own else None}


class WatchDialog(QDialog):
    def __init__(self, win, w=None, parent=None, url="", name=""):
        super().__init__(parent or win)
        self.win = win
        self.w = dict(w or {"url": url, "name": name, "enabled": 1})
        self.setWindowTitle("Watch a gallery" if not w else "Gallery")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        f = QFormLayout()
        self.url = QLineEdit(self.w.get("url") or "")
        self.url.setPlaceholderText("https://… (a gallery, album, profile or model page)")
        f.addRow("Address:", self.url)
        self.name = QLineEdit(self.w.get("name") or "")
        self.name.setPlaceholderText("e.g. the model's name")
        f.addRow("Name:", self.name)
        self.enabled = QCheckBox("Included when galleries are checked")
        self.enabled.setChecked(bool(self.w.get("enabled", 1)))
        f.addRow("", self.enabled)
        lay.addLayout(f)
        self.fields = RuleFields(win, self.w, inherit=True, host=watcher.host_of(self.w.get("url") or ""))
        lay.addWidget(self.fields)
        self.url.textChanged.connect(lambda u: self.fields.set_host(watcher.host_of(u.strip())))
        # (1.56.1) the name is who they're tagged as, unless you type other names under "Tag them as"
        self._synced = not self.fields.people.text().strip() or \
            self.fields.people.text().strip() == (self.w.get("name") or "").strip()
        if not self.fields.people.text().strip() and self.name.text().strip():
            self._name_changed(self.name.text())
        self.name.textEdited.connect(self._name_changed)
        self.fields.people.textEdited.connect(lambda _t: setattr(self, "_synced", False))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _name_changed(self, text):
        if not self._synced:
            return
        self.fields.people.setText(text.strip())
        if self.fields.own is not None and text.strip():
            self.fields.own.setChecked(True)

    def ok(self):
        u = self.url.text().strip()
        if not u.lower().startswith(("http://", "https://")):
            QMessageBox.warning(self, "Gallery", "Type the gallery's web address (starting with https://).")
            return
        self.w.update(self.fields.values())
        self.w.update(url=u, name=self.name.text().strip() or watcher.host_of(u), enabled=int(self.enabled.isChecked()))
        self.w["id"] = watcher.save_watch(self.win.conn, self.w)
        self.accept()


def edit_watch(win, parent=None, w=None, url="", name=""):
    d = WatchDialog(win, w, parent, url, name)
    if d.exec() == QDialog.Accepted:
        ww = getattr(win, "web_window", None)
        if ww is not None:
            ww.panel.fill()
        return d.w["id"]
    return None


class SiteRulesDialog(QDialog):
    """How each site is read and its pictures tagged (galleries can have their own)."""

    def __init__(self, win, parent=None):
        super().__init__(parent or win)
        self.win = win
        self.setWindowTitle("Sites")
        self.resize(760, 480)
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentTextChanged.connect(self.show_rule)
        left.addWidget(self.list, 1)
        row = QHBoxLayout()
        add = QPushButton("Add site…")
        add.clicked.connect(self.add)
        rm = QPushButton("Remove")
        rm.clicked.connect(self.remove)
        row.addWidget(add)
        row.addWidget(rm)
        left.addLayout(row)
        lay.addLayout(left, 1)
        right = QVBoxLayout()
        self.title = QLabel()
        right.addWidget(self.title)
        self.box = QVBoxLayout()
        right.addLayout(self.box)
        right.addStretch()
        save = QPushButton("Save for this site")
        save.clicked.connect(self.save)
        right.addWidget(save)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        right.addWidget(bb)
        lay.addLayout(right, 2)
        self.fields = None
        hosts = sorted({r["host"] for r in watcher.site_rules(win.conn)} |
                       {watcher.host_of(w["url"]) for w in watcher.watches(win.conn)})
        self.list.addItems(hosts)
        if hosts:
            self.list.setCurrentRow(0)

    def show_rule(self, host):
        if self.fields is not None:
            self.fields.setParent(None)
            self.fields = None
        if not host:
            return
        self.title.setText(f"<b>{host}</b> - for every gallery there that doesn't have its own settings")
        self.fields = RuleFields(self.win, watcher.site_rule(self.win.conn, host), host=host)
        self.box.addWidget(self.fields)

    def save(self):
        it = self.list.currentItem()
        if it is None or self.fields is None:
            return
        rule = dict(self.fields.values(), host=it.text())
        watcher.save_site_rule(self.win.conn, rule)
        self.title.setText(f"<b>{it.text()}</b> - saved")

    def add(self):
        from PySide6.QtWidgets import QInputDialog
        t, ok = QInputDialog.getText(self, "Add site", "Site (e.g. flickr.com) or any address on it:")
        if ok and t.strip():
            h = watcher.host_of(t if "://" in t else "https://" + t.strip())
            if h and not self.list.findItems(h, Qt.MatchExactly):
                self.list.addItem(h)
            items = self.list.findItems(h, Qt.MatchExactly)
            if items:
                self.list.setCurrentItem(items[0])

    def remove(self):
        it = self.list.currentItem()
        if it is not None:
            watcher.remove_site_rule(self.win.conn, it.text())
            self.show_rule(it.text())


class LoginsDialog(QDialog):
    def __init__(self, win, parent=None):
        super().__init__(parent or win)
        import logins
        self.win = win
        self.setWindowTitle("Saved logins")
        self.resize(560, 360)
        lay = QVBoxLayout(self)
        note = QLabel("Logins are kept encrypted on this PC and filled in only on their own site - in FaceSort's "
                      "browser, and when the gallery watcher checks a gallery there. Sign in once in the browser "
                      "and FaceSort offers to save it.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        lay.addWidget(note)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Site", "User", "Saved"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        add = QPushButton("Add…")
        add.clicked.connect(self.add)
        rm = QPushButton("Forget")
        rm.clicked.connect(self.forget)
        row.addWidget(add)
        row.addWidget(rm)
        row.addStretch()
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.logins = logins
        self.fill()

    def fill(self):
        rows = self.logins.listing(self.win.conn)
        self.table.setRowCount(len(rows))
        for i, (h, u, t) in enumerate(rows):
            for j, v in enumerate((h, u, _ago(t))):
                self.table.setItem(i, j, QTableWidgetItem(v))

    def add(self):
        d = QDialog(self)
        d.setWindowTitle("Add a login")
        f = QFormLayout(d)
        host, user, pw = QLineEdit(), QLineEdit(), QLineEdit()
        pw.setEchoMode(QLineEdit.Password)
        host.setPlaceholderText("e.g. flickr.com")
        f.addRow("Site:", host)
        f.addRow("User / email:", user)
        f.addRow("Password:", pw)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        f.addRow(bb)
        if d.exec() == QDialog.Accepted and host.text().strip() and pw.text():
            h = watcher.host_of(host.text() if "://" in host.text() else "https://" + host.text().strip())
            self.logins.save(self.win.conn, h, user.text().strip(), pw.text())
            self.fill()

    def forget(self):
        r = self.table.currentRow()
        if r >= 0:
            self.logins.forget(self.win.conn, self.table.item(r, 0).text())
            self.fill()


class WatcherPanel(QWidget):
    """(1.57.1) The watched galleries, checking them, and their settings - the right side of the
    Gallery Watcher window (the browser is on its left; the page being checked shows in its own tab)."""

    def __init__(self, win, view, host):
        super().__init__(host)
        self.win, self.view, self.host = win, view, host
        self.checker = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 0, 0, 0)
        head = QLabel("<b>Watched galleries</b>")
        lay.addWidget(head)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Gallery", "Site", "Last checked", "Last time"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.doubleClicked.connect(lambda _i: self.open_in_browser())   # (double-click: open it on the left)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        for text, fn in (("Add…", self.add), ("Edit…", self.edit), ("Remove", self.remove),
                         ("Open", self.open_in_browser)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        row = QHBoxLayout()
        self.check_b = QPushButton("Check all now")
        self.check_b.setStyleSheet("QPushButton { font-weight: bold; }")
        self.check_b.clicked.connect(lambda: self.check(None))
        self.check_sel_b = QPushButton("Check selected")
        self.check_sel_b.clicked.connect(self.check_selected)
        self.stop_b = QPushButton("Stop")
        self.stop_b.setEnabled(False)
        self.stop_b.clicked.connect(self.stop)
        row.addWidget(self.check_b)
        row.addWidget(self.check_sel_b)
        row.addWidget(self.stop_b)
        row.addStretch()
        lay.addLayout(row)
        f = QFormLayout()
        self.hours = QDoubleSpinBox()
        self.hours.setRange(0, 168)
        self.hours.setDecimals(0)
        self.hours.setSuffix(" hours (while FaceSort is open)")
        self.hours.setSpecialValueText("only when I click Check")
        self.hours.setValue(float(win.settings.get("gallery_check_hours", 0) or 0))
        self.hours.valueChanged.connect(lambda v: win.settings.__setitem__("gallery_check_hours", float(v)))
        f.addRow("Check every:", self.hours)
        frow = QHBoxLayout()
        self.folder = QLineEdit(watcher.base_folder(win.settings))
        self.folder.editingFinished.connect(lambda: win.settings.__setitem__("gallery_folder", self.folder.text().strip()))
        fb = QPushButton("…")
        fb.setFixedWidth(30)
        fb.clicked.connect(self.pick_folder)
        frow.addWidget(self.folder)
        frow.addWidget(fb)
        f.addRow("Download into:", frow)
        lay.addLayout(f)
        row = QHBoxLayout()
        for text, fn in (("Site settings…", lambda: SiteRulesDialog(win, self).exec()),):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.setPlaceholderText("What each check found shows here.")
        lay.addWidget(self.log, 1)
        self.fill()

    def statusBar(self):
        return self.host.statusBar()

    def fill(self):
        ws = watcher.watches(self.win.conn)
        self.table.setRowCount(len(ws))
        for i, w in enumerate(ws):
            vals = (w["name"] + ("" if w["enabled"] else "  (off)"), watcher.host_of(w["url"]), _ago(w["last_check"]),
                    w["last_msg"] or "")
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.UserRole, w["id"])
                it.setToolTip(w["url"])
                self.table.setItem(i, j, it)

    def _sel(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.table.item(r, 0).data(Qt.UserRole) for r in rows]

    def add(self):
        edit_watch(self.win, self)
        self.fill()

    def edit(self):
        ids = self._sel()
        if ids:
            edit_watch(self.win, self, watcher.get_watch(self.win.conn, ids[0]))
            self.fill()

    def remove(self):
        ids = self._sel()
        if ids and QMessageBox.question(self, "Gallery watcher", f"Stop watching {len(ids)} gallery(s)? "
                                        "(Pictures already downloaded stay.)") == QMessageBox.Yes:
            for i in ids:
                watcher.remove_watch(self.win.conn, i)
            self.fill()

    def open_in_browser(self):
        ids = self._sel()
        if ids:
            self.host.browse(watcher.get_watch(self.win.conn, ids[0])["url"])

    def pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Download galleries into…", self.folder.text())
        if d:
            import os
            self.folder.setText(os.path.normpath(d))
            self.win.settings["gallery_folder"] = self.folder.text()

    def check_selected(self):
        ids = self._sel()
        if ids:
            self.check(ids)

    def check(self, ids=None):
        if self.checker is not None:
            return
        if not watcher.watches(self.win.conn):
            QMessageBox.information(self, "Gallery watcher", "Add a gallery first - Add…, or Watch this gallery in "
                                                             "FaceSort's browser.")
            return
        self.checker = watcher.Checker(self.win, self.view, ids)
        self.host.checking(True)
        self.checker.status.connect(lambda s: self.statusBar().showMessage(s))
        self.checker.gallery_done.connect(self._gallery_done)
        self.checker.finished.connect(self._finished)
        self.check_b.setEnabled(False)
        self.check_sel_b.setEnabled(False)
        self.stop_b.setEnabled(True)
        self.log.appendPlainText(time.strftime("%H:%M") + " - checking…")
        self.checker.start()

    def stop(self):
        if self.checker is not None:
            self.checker.stop()

    def _gallery_done(self, wid, n, msg):
        w = watcher.get_watch(self.win.conn, wid)
        self.log.appendPlainText(f"   {w['name'] if w else wid}: {msg}")
        self.fill()

    def _finished(self, total):
        self.checker = None
        self.host.checking(False)
        self.check_b.setEnabled(True)
        self.check_sel_b.setEnabled(True)
        self.stop_b.setEnabled(False)
        msg = f"Done - {total:,} new file{'s' if total != 1 else ''}" + (" (in Recently dropped)" if total else "")
        self.log.appendPlainText(time.strftime("%H:%M") + " - " + msg)
        self.statusBar().showMessage(msg, 15000)
        self.win.statusBar().showMessage("Gallery watcher: " + msg, 15000)
        self.fill()

    def shutdown(self):
        if self.checker is not None:
            self.checker.stop()
        self.view.setPage(None)
