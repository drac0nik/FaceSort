"""Unassociated files as a list (1.64), like Windows' Details view: File name, Type, Location,
Description, Size, Date modified. Columns are resizable and sortable (click a heading), and FaceSort
remembers them. Location is shortened from the left, since the end of a path says the most.

Web shortcuts and saved web pages show the address they point to; double-clicking one opens that
address in your default web browser. Other files open as before (programs, scripts and the like are
only ever shown in their folder).

The list is a different view of the same gallery: picking rows here picks them in the gallery too,
so every key, the selection bar, the right-click menu and dragging onto a folder work unchanged."""
import os
import threading
import time

from PySide6.QtCore import QItemSelection, QItemSelectionModel, QObject, QPoint, QSortFilterProxyModel, Qt, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QDrag, QKeySequence, QShortcut, QStandardItem, \
    QStandardItemModel
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLineEdit, QMenu, \
    QStyledItemDelegate, QTreeView, QVBoxLayout, QWidget

import describe

COLS = ["File name", "Type", "Location", "Description", "Size", "Date modified"]
C_NAME, C_EXT, C_LOC, C_DESC, C_SIZE, C_DATE = range(6)
KEY = Qt.UserRole + 1          # the gallery's id for the row (-photo id)
SORT = Qt.UserRole + 2         # what a column sorts by
PATH = Qt.UserRole + 3
URL = Qt.UserRole + 4
GROUP = Qt.UserRole + 5        # the type group (links, documents…)
SAME = Qt.UserRole + 6         # how many other files point to the same address
ORDER = Qt.UserRole + 7        # a group heading's place
AMBER = "#F2B544"


def nice_size(n):
    if n is None:
        return ""
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,} {unit}" if unit == "bytes" else f"{n:,.1f} {unit}".replace(".0 ", " ")
        n /= 1024
    return ""


class LeftElide(QStyledItemDelegate):
    """The Location column: …\\the\\end\\of\\the\\path."""
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.textElideMode = Qt.ElideLeft


class Filter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.words = []
        self.group = "all"
        self.setRecursiveFilteringEnabled(True)
        self.setSortRole(SORT)

    def set(self, text, group):
        self.words = text.casefold().split()
        self.group = group
        self.invalidateRowsFilter()

    def filterAcceptsRow(self, row, parent):
        m = self.sourceModel()
        first = m.index(row, 0, parent)
        if first.data(ORDER) is not None:          # a group heading: shown when something under it is
            return False
        if self.group == "same":
            if not first.data(SAME):
                return False
        elif self.group != "all" and first.data(GROUP) != self.group:
            return False
        if self.words:
            hay = " ".join(str(m.index(row, c, parent).data() or "") for c in (C_NAME, C_EXT, C_LOC, C_DESC))
            hay = (hay + " " + str(first.data(PATH) or "")).casefold()
            return all(w in hay for w in self.words)
        return True

    def lessThan(self, a, b):
        oa, ob = a.data(ORDER), b.data(ORDER)
        if oa is not None and ob is not None:      # group headings keep their order
            return oa < ob if self.sortOrder() == Qt.AscendingOrder else oa > ob
        va, vb = a.data(SORT), b.data(SORT)
        if va is None or vb is None:
            return (va is None) and (vb is not None)
        if isinstance(va, str) and isinstance(vb, str):
            return va.casefold() < vb.casefold()
        try:
            return va < vb
        except TypeError:
            return str(va) < str(vb)


class List(QTreeView):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self.setDragEnabled(True)
        self.setDragDropMode(QTreeView.DragOnly)

    def startDrag(self, actions):
        """Rows dragged onto a folder (or album): the same as dragging the gallery's tiles."""
        win = self.page.win
        idxs = win.grid.selectionModel().selectedIndexes()
        if not idxs:
            return
        md = win.model.mimeData(idxs)
        if md is None:
            return
        drag = QDrag(self)
        drag.setMimeData(md)
        drag.setPixmap(win.grid.drag_badge(len(idxs)))
        drag.setHotSpot(QPoint(-14, -14))
        drag.exec(actions or Qt.CopyAction)


class _Signals(QObject):
    described = Signal(list)       # [(path, mtime, size, desc, url, title)]


class UnassocView(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setObjectName("unassoc")
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 6, 8, 4)
        v.setSpacing(6)
        top = QHBoxLayout()
        self.find = QLineEdit()
        self.find.setPlaceholderText("Filter by name, folder, address or description…")
        self.find.setClearButtonEnabled(True)
        top.addWidget(self.find, 1)
        self.kind = QComboBox()
        self.kind.addItem("All types", "all")
        for k, label in describe.GROUPS:
            self.kind.addItem(label, k)
        self.kind.addItem("Same address as another file", "same")
        top.addWidget(self.kind)
        self.group_box = QCheckBox("Group by type")
        top.addWidget(self.group_box)
        v.addLayout(top)

        self.model = QStandardItemModel(0, len(COLS), self)
        self.model.setHorizontalHeaderLabels(COLS)
        self.proxy = Filter(self)
        self.proxy.setSourceModel(self.model)
        self.view = List(self)
        self.view.setModel(self.proxy)
        self.view.setRootIsDecorated(False)
        self.view.setUniformRowHeights(True)
        self.view.setAlternatingRowColors(False)
        self.view.setSelectionMode(QTreeView.ExtendedSelection)
        self.view.setSelectionBehavior(QTreeView.SelectRows)
        self.view.setEditTriggers(QTreeView.NoEditTriggers)
        self.view.setSortingEnabled(True)
        self.view.setItemDelegateForColumn(C_LOC, LeftElide(self.view))
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self.menu)
        self.view.doubleClicked.connect(self.open_row)
        h = self.view.header()
        h.setSectionsMovable(True)
        h.setStretchLastSection(False)
        h.setSectionResizeMode(QHeaderView.Interactive)
        h.setContextMenuPolicy(Qt.CustomContextMenu)
        h.customContextMenuRequested.connect(self.header_menu)
        for c, w in ((C_NAME, 220), (C_EXT, 60), (C_LOC, 260), (C_DESC, 320), (C_SIZE, 80), (C_DATE, 130)):
            h.resizeSection(c, w)
        h.setSortIndicator(C_NAME, Qt.AscendingOrder)
        self.restore_layout()
        h.sectionResized.connect(lambda *_: self.save_later())
        h.sectionMoved.connect(lambda *_: self.save_later())
        h.sortIndicatorChanged.connect(lambda *_: self.save_later())
        v.addWidget(self.view, 1)

        s = win.settings
        self.group_box.setChecked(bool(s.get("unassoc_group", False)))
        i = self.kind.findData(s.get("unassoc_type", "all"))
        self.kind.setCurrentIndex(max(0, i))
        self.find.textChanged.connect(self.apply_filter)
        self.kind.currentIndexChanged.connect(self.apply_filter)
        self.group_box.toggled.connect(self.regroup)
        self.view.selectionModel().selectionChanged.connect(self.mirror_selection)
        sc = QShortcut(QKeySequence("Return"), self.view)
        sc.setContext(Qt.WidgetShortcut)
        sc.activated.connect(win.act_name)
        sc = QShortcut(QKeySequence("Enter"), self.view)
        sc.setContext(Qt.WidgetShortcut)
        sc.activated.connect(win.act_name)
        self.sig = _Signals()
        self.sig.described.connect(self.got)
        self._gen = 0
        self._rows = []            # (key, path)
        self._info = {}            # path -> (mtime, size, desc, url, title)
        self._items = {}           # path -> [QStandardItem per column]
        self._syncing = False
        from PySide6.QtCore import QTimer
        self._save_timer = QTimer(self, singleShot=True, interval=600)
        self._save_timer.timeout.connect(self.save_layout)

    # ── layout kept between sessions ──
    def restore_layout(self):
        st = self.win.settings.get("unassoc_header")
        h = self.view.header()
        if st:
            from PySide6.QtCore import QByteArray
            try:
                h.restoreState(QByteArray.fromHex(st.encode()))
            except Exception:
                pass
        else:
            h.setSectionHidden(C_SIZE, False)
            h.setSectionHidden(C_DATE, False)

    def save_later(self):
        self._save_timer.start()

    def save_layout(self):
        s = self.win.settings
        s["unassoc_header"] = bytes(self.view.header().saveState().toHex()).decode()
        s["unassoc_group"] = self.group_box.isChecked()
        s["unassoc_type"] = self.kind.currentData()
        try:
            import store
            store.save_settings(s)
        except Exception:
            pass

    def header_menu(self, pos):
        """Right-click a heading: which columns show (File name always does)."""
        h = self.view.header()
        m = QMenu(self)
        for c in range(1, len(COLS)):
            a = m.addAction(COLS[c])
            a.setCheckable(True)
            a.setChecked(not h.isSectionHidden(c))
            a.toggled.connect(lambda on, c=c: (h.setSectionHidden(c, not on), self.save_later()))
        m.addSeparator()
        m.addAction("Size columns to fit", lambda: [self.view.resizeColumnToContents(c) for c in range(len(COLS))])
        m.exec(h.mapToGlobal(pos))

    # ── filling ──
    def refresh(self):
        """The gallery's rows changed: the list shows the same files."""
        rows = [(r[0], r[1]) for r in self.win.model.rows if r[0] and r[1]]
        self._rows = rows
        self._gen += 1
        paths = [p for _k, p in rows]
        have = describe.cached(self.win.conn, paths)
        self._info = {p: have[p] for p in paths if p in have}
        self.build()
        todo = [p for p in paths if p not in have]
        stale = [p for p in paths if p in have]
        if todo or stale:
            threading.Thread(target=self._describe, args=(self._gen, todo, stale, dict(have)), daemon=True).start()

    def _describe(self, gen, todo, stale, have):
        """In the background: describe files never seen (and any changed since), a batch at a time."""
        batch, last = [], time.time()
        for p in todo + stale:
            if gen != self._gen:
                return
            try:
                st = os.stat(p)
            except OSError:
                continue
            if p in have and abs((have[p][0] or 0) - st.st_mtime) < 1 and have[p][1] == st.st_size:
                continue
            d = describe.describe(p)
            batch.append((p, st.st_mtime, st.st_size, d["desc"], d["url"], d["title"]))
            if len(batch) >= 200 or time.time() - last > 0.5:
                self.sig.described.emit(batch)
                batch, last = [], time.time()
        if batch and gen == self._gen:
            self.sig.described.emit(batch)

    def got(self, batch):
        describe.store(self.win.conn, batch)
        for p, mt, sz, d, u, t in batch:
            self._info[p] = (mt, sz, d, u, t)
            its = self._items.get(p)
            if its:
                self.fill_info(its, p)
        self.mark_same()

    def fill_info(self, its, path):
        mt, sz, d, u, t = self._info.get(path, (None, None, "", "", ""))
        its[C_DESC].setText(d or "")
        its[C_DESC].setData((d or "").casefold(), SORT)
        tip = u or d or ""
        if t and t != d:
            tip += f"\n{t}"
        its[C_DESC].setToolTip(tip)
        its[0].setData(u or "", URL)
        its[C_SIZE].setText(nice_size(sz))
        its[C_SIZE].setData(sz if sz is not None else -1, SORT)
        its[C_SIZE].setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        its[C_DATE].setText(time.strftime("%Y-%m-%d %H:%M", time.localtime(mt)) if mt else "")
        its[C_DATE].setData(mt or 0.0, SORT)

    def row_items(self, key, path):
        base = os.path.basename(path)
        stem, ext = os.path.splitext(base)
        folder = os.path.dirname(path)
        its = [QStandardItem() for _ in COLS]
        its[C_NAME].setText(stem or base)
        its[C_NAME].setData((stem or base).casefold(), SORT)
        its[C_NAME].setToolTip(base)
        its[C_EXT].setText(ext[1:].lower() if ext else "")
        its[C_EXT].setData(ext.lower(), SORT)
        its[C_LOC].setText(folder)
        its[C_LOC].setData(folder.casefold(), SORT)
        its[C_LOC].setToolTip(folder)
        its[0].setData(key, KEY)
        its[0].setData(path, PATH)
        its[0].setData(describe.group_of(path), GROUP)
        its[0].setIcon(self.icon_for(ext.lower()))
        self._items[path] = its
        self.fill_info(its, path)
        return its

    def icon_for(self, ext):
        """The system's icon for the file type (one look-up per type)."""
        cache = self.__dict__.setdefault("_icons", {})
        if ext not in cache:
            from PySide6.QtCore import QFileInfo
            from PySide6.QtWidgets import QFileIconProvider
            prov = self.__dict__.setdefault("_prov", QFileIconProvider())
            cache[ext] = prov.icon(QFileInfo("x" + ext)) if ext else prov.icon(QFileIconProvider.File)
        return cache[ext]

    def build(self):
        self._syncing = True
        h = self.view.header()
        col, order = h.sortIndicatorSection(), h.sortIndicatorOrder()
        self.view.setSortingEnabled(False)
        self.model.removeRows(0, self.model.rowCount())
        self._items = {}
        grouped = self.group_box.isChecked()
        self.view.setRootIsDecorated(grouped)
        if grouped:
            by = {}
            for key, path in self._rows:
                by.setdefault(describe.group_of(path), []).append((key, path))
            for n, (g, label) in enumerate(describe.GROUPS):
                if g not in by:
                    continue
                head = [QStandardItem() for _ in COLS]
                head[0].setText(f"{label}   {len(by[g]):,}")
                head[0].setData(n, ORDER)
                for it in head:
                    it.setFlags(Qt.ItemIsEnabled)
                    it.setData(n, ORDER)
                f = head[0].font()
                f.setBold(True)
                head[0].setFont(f)
                for key, path in by[g]:
                    head[0].appendRow(self.row_items(key, path))
                self.model.appendRow(head)
        else:
            for key, path in self._rows:
                self.model.appendRow(self.row_items(key, path))
        self.mark_same()
        self.view.setSortingEnabled(True)
        self.view.sortByColumn(col, order)
        if grouped:
            for r in range(self.proxy.rowCount()):
                self.view.setFirstColumnSpanned(r, self.view.rootIndex(), True)
            self.view.expandAll()
        self._syncing = False
        self.select_from_gallery()

    def mark_same(self):
        """Files that point to the same address: amber, with how many others there are."""
        by = {}
        for p, info in self._info.items():
            u = info[3]
            if u:
                by.setdefault(describe.norm_url(u), []).append(p)
        same = {p: len(ps) - 1 for ps in by.values() if len(ps) > 1 for p in ps}
        for p, its in self._items.items():
            n = same.get(p, 0)
            if (its[0].data(SAME) or 0) == n:
                continue
            its[0].setData(n, SAME)
            if n:
                its[C_DESC].setForeground(QBrush(QColor(AMBER)))
                u = self._info[p][3]
                its[C_DESC].setToolTip(f"{u}\nSame address as {n} other file{'s' if n != 1 else ''} - "
                                       "pick “Same address as another file” above to see them together.")
            else:
                its[C_DESC].setData(None, Qt.ForegroundRole)

    def regroup(self):
        self.build()
        self.save_later()

    def apply_filter(self):
        self.proxy.set(self.find.text(), self.kind.currentData())
        if self.group_box.isChecked():
            self.view.expandAll()
            for r in range(self.proxy.rowCount()):
                self.view.setFirstColumnSpanned(r, self.view.rootIndex(), True)
        self.mirror_selection()          # (rows filtered out are no longer picked in the gallery either)
        self.save_later()

    # ── the selection is the gallery's selection ──
    def mirror_selection(self, *_):
        if self._syncing:
            return
        keys = set()
        for i in self.view.selectionModel().selectedRows(0):
            k = i.data(KEY)
            if k:
                keys.add(k)
        g = self.win.grid
        sel = QItemSelection()
        for row, r in enumerate(self.win.model.rows):
            if r[0] in keys:
                ix = self.win.model.index(row)
                sel.select(ix, ix)
        self._syncing = True
        try:
            g.selectionModel().select(sel, QItemSelectionModel.ClearAndSelect)
        finally:
            self._syncing = False
        self.win.update_hint()

    def select_from_gallery(self):
        """After the list is rebuilt: the rows picked in the gallery are picked here too."""
        keys = {self.win.model.rows[i.row()][0] for i in self.win.grid.selectionModel().selectedIndexes()}
        if not keys:
            return
        sel = QItemSelection()

        def walk(parent):
            for r in range(self.proxy.rowCount(parent)):
                ix = self.proxy.index(r, 0, parent)
                if ix.data(KEY) in keys:
                    sel.select(ix, self.proxy.index(r, len(COLS) - 1, parent))
                walk(ix)
        walk(self.view.rootIndex())
        self._syncing = True
        try:
            self.view.selectionModel().select(sel, QItemSelectionModel.ClearAndSelect)
        finally:
            self._syncing = False

    def selected(self):
        """[(path, url, title)] of the picked rows."""
        out = []
        for i in self.view.selectionModel().selectedRows(0):
            p = i.data(PATH)
            if p:
                info = self._info.get(p, (None, None, "", "", ""))
                out.append((p, info[3], info[4]))
        return out

    # ── opening ──
    def open_row(self, index):
        ix = index.siblingAtColumn(0)
        path, url = ix.data(PATH), ix.data(URL)
        if not path:
            return
        if url and url.lower().startswith(("http://", "https://")):
            QDesktopServices.openUrl(QUrl(url))          # (your default web browser)
            self.win.statusBar().showMessage(f"Opened in your web browser: {url}", 6000)
            return
        self.win.open_other(path)

    # ── right-click: the gallery's menu, with what links can do ──
    def menu(self, pos):
        if not self.view.selectionModel().selectedRows(0):
            return
        self.win.grid_menu(QPoint(-1, -1), self.view.viewport().mapToGlobal(pos), extra=self.menu_extra)

    def menu_extra(self, m):
        picked = self.selected()
        links = [(p, u, t) for p, u, t in picked if u.lower().startswith(("http://", "https://"))]
        if links:
            n = len(links)
            m.addAction("Open in browser" + (f" ({n})" if n > 1 else ""), lambda: self.open_links(links))
            m.addAction("Copy address" + ("es" if n > 1 else ""),
                        lambda: QApplication.clipboard().setText("\n".join(u for _p, u, _t in links)))
            if n == 1:
                p, u, t = links[0]
                m.addAction("Watch this gallery…", lambda: self.watch(u, t))
            for cid, name in self.folder_suggestions(links)[:3]:
                m.addAction(f"Put in folder “{name}” (named in the address)",
                            lambda cid=cid, name=name: self.put_in(cid, name))
        m.addAction("Copy path" + ("s" if len(picked) > 1 else ""),
                    lambda: QApplication.clipboard().setText("\n".join(p for p, _u, _t in picked)))

    def open_links(self, links):
        if len(links) > 10:
            from PySide6.QtWidgets import QMessageBox
            if QMessageBox.question(self, "Open in browser", f"Open {len(links):,} addresses in your web browser?") \
                    != QMessageBox.Yes:
                return
        for _p, u, _t in links:
            QDesktopServices.openUrl(QUrl(u))

    def watch(self, url, title):
        import watcher_ui
        watcher_ui.edit_watch(self.win, self.win, url=url, name=title or "")

    def folder_suggestions(self, links):
        """Folders whose name appears in the addresses: [(folder id, name)], most matches first."""
        import store
        words = {}
        for _p, u, _t in links:
            for w in describe.folder_tokens(u):
                words[w] = words.get(w, 0) + 1
        if not words:
            return []
        paths = store.category_paths(self.win.conn)
        hits = []
        for cid, chain in paths.items():
            if not chain:
                continue
            name = chain[-1]
            key = " ".join(name.replace("_", " ").replace("-", " ").split()).casefold()
            if len(key) >= 3 and key in words:
                hits.append((-words[key], "/".join(chain), cid, name))
        hits.sort()
        return [(cid, path) for _n, path, cid, _name in hits]

    def put_in(self, cid, name):
        import store
        photos = self.win.sel_photos()
        if not photos:
            return
        store.set_photo_folder(self.win.conn, photos, cid)
        self.win.conn.commit()
        self.win.after_change(regroup=False)
        self.win.statusBar().showMessage(f"{len(photos):,} file{'s' if len(photos) != 1 else ''} put in “{name}”", 6000)
