"""Add photos (1.46): the one window for everything that brings photos in - dropping files or
folders on FaceSort, and "Add folder…".

- Import once (the default for a drop): what's there now is added and the folder is forgotten
  (never looked at again for new photos, and it doesn't clutter your library folders). Tick
  "Keep watching" to make it a library folder instead (new photos in it come in on every scan).
- People and a folder for everything you're adding, as before.
- The folders table: every sub-folder with how many photos it has and what to use its name for -
  a person, a folder, the folder of whoever gets tagged, an album - or leave it out. FaceSort
  guesses from the names (someone you know -> person; "Beach 2024" -> folder; "New folder",
  "DCIM" -> nothing), and remembers your choices for next time.
- One "Add" button; "Add without tagging" is a link, so it can't throw away what you typed.
"""
import html
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QCompleter, QDialog, QDialogButtonBox, QFormLayout,
                               QHeaderView, QLabel, QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout)

import engine
import store
from ui_common import ElidedLabel

# what a sub-folder's name is used for
NOTHING, PERSON, FOLDER, THEIR_FOLDER, ALBUM, LEAVE_OUT = "", "person", "folder", "their", "album", "skip"
USES = [(NOTHING, "—"), (PERSON, "Person"), (FOLDER, "Folder"), (THEIR_FOLDER, "Folder of whoever's tagged"),
        (ALBUM, "Album"), (LEAVE_OUT, "Leave out")]
MAX_ROWS = 300
MAX_DEPTH = 4


def media_count(d):
    """Photos and videos directly in a folder."""
    n = 0
    try:
        for e in os.scandir(d):
            if e.is_file():
                ext = os.path.splitext(e.name)[1].lower()
                if ext in engine.IMAGE_EXTS or ext in engine.VIDEO_EXTS:
                    n += 1
    except OSError:
        pass
    return n


def folder_rows(dirs, max_rows=MAX_ROWS, max_depth=MAX_DEPTH):
    """[(path, depth, name, photos directly in it, photos in it and below)] for the folders being added."""
    rows, more = [], False

    def walk(d, depth):
        nonlocal more
        if len(rows) >= max_rows:
            more = True
            return 0
        here = media_count(d)
        idx = len(rows)
        rows.append([d, depth, os.path.basename(d.rstrip("\\/")) or d, here, here])
        total = here
        if depth < max_depth:
            try:
                subs = sorted((e.path for e in os.scandir(d) if e.is_dir() and not e.name.startswith((".", "$"))
                               and e.name.lower() not in ("~links", "~archives", "~files")), key=str.casefold)
            except OSError:
                subs = []
            for s in subs:
                total += walk(s, depth + 1)
        rows[idx][4] = total
        return total
    for d in dirs:
        walk(d, 0)
    return [tuple(r) for r in rows], more


def guess_use(name, depth, known_people):
    """What FaceSort guesses a folder name is."""
    if any(n.casefold() == name.casefold() for n in known_people):
        return PERSON
    if store.is_generic_folder(name):
        return NOTHING
    return THEIR_FOLDER if depth == 0 else FOLDER


class ImportDialog(QDialog):
    def __init__(self, win, dirs, files, already, watch=False, defaults=None, source=""):
        """defaults: people / category / later filled in to start with (a watched gallery's settings);
        source: where they came from, shown under the title."""
        super().__init__(win)
        self.win = win
        self.dirs, self.files = dirs, files
        self.setWindowTitle("Add photos")
        self.setMinimumWidth(640)
        lay = QVBoxLayout(self)
        nvid = sum(1 for f in files if engine.is_video(f))
        parts = []
        if dirs:
            parts.append(f"{len(dirs)} folder{'s' if len(dirs) != 1 else ''}")
        if len(files) - nvid:
            parts.append(f"{len(files) - nvid:,} photo{'s' if len(files) - nvid != 1 else ''}")
        if nvid:
            parts.append(f"{nvid:,} video{'s' if nvid != 1 else ''}")
        lay.addWidget(QLabel(f"<b>Adding {' + '.join(parts)}</b>"))
        if source:
            src = QLabel(html.escape(source, quote=False))
            src.setStyleSheet("color: #b07a12; font-weight: 600;")
            lay.addWidget(src)
        names = [os.path.basename(p.rstrip("\\/")) or p for p in dirs + files]
        sub = ElidedLabel(", ".join(names[:12]) + ("…" if len(names) > 12 else ""))
        sub.setStyleSheet("color: gray;")
        lay.addWidget(sub)
        if already:
            al = QLabel(f"{already:,} of these {'is' if already == 1 else 'are'} already in your library - "
                        "they get the tags right away.")
            al.setStyleSheet("color: gray;")
            lay.addWidget(al)
        self.watch = QCheckBox("Keep watching " + ("this folder" if len(dirs) == 1 else "these folders")
                               + " (new photos in it are added on every scan)")
        self.watch.setChecked(bool(watch))
        self.watch.setToolTip("Off: just what's there now is added, and the folder is forgotten - it isn't "
                              "looked at again and doesn't stay in your library folders.\nOn: it becomes one "
                              "of your library folders.")
        if dirs:
            lay.addWidget(self.watch)
        lay.addSpacing(4)
        # ---- everything being added: people and a folder
        form = QFormLayout()
        self.known = win.people_by_recent()
        self.people = QLineEdit()
        self.people.setPlaceholderText("Who's in all of them? Separate names with commas - leave empty if mixed")
        comp = QCompleter(self.known, self.people)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setFilterMode(Qt.MatchContains)
        self.people.setCompleter(comp)
        comp.activated[str].connect(lambda t: self.people.setText(
            ", ".join([x.strip() for x in self.people.text().split(",")[:-1] if x.strip()] + [t])))
        self.people.textEdited.connect(lambda t: comp.setCompletionPrefix(t.split(",")[-1].strip()))
        form.addRow("People:", self.people)
        self.category = QComboBox()
        self.category.setEditable(True)
        self.category.addItems([""] + list(win.categories_by_use()))
        self.category.lineEdit().setPlaceholderText("Folder for all of them (an event or group) - optional")
        form.addRow("Folder:", self.category)
        lay.addLayout(form)
        # ---- the folders table
        self.table = None
        self.rows, more = folder_rows(dirs) if dirs else ([], False)
        if self.rows:
            lay.addSpacing(6)
            head = QLabel("<b>The folders</b> - what to use each name for")
            lay.addWidget(head)
            self.table = QTreeWidget()
            self.table.setColumnCount(3)
            self.table.setHeaderLabels(["Folder", "Photos", "Use the name as"])
            self.table.setRootIsDecorated(True)
            self.table.setUniformRowHeights(True)
            self.table.header().setSectionResizeMode(0, QHeaderView.Stretch)
            self.table.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
            self.table.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
            remembered = store.import_choices(win.conn, [r[0] for r in self.rows]) if hasattr(win, "conn") else {}
            self.combos = {}
            parents = {}
            for path, depth, name, here, total in self.rows:
                it = QTreeWidgetItem([name, f"{total:,}" + (f" ({here:,} here)" if here != total and here else "")])
                it.setToolTip(0, path)
                par = parents.get(os.path.dirname(path.rstrip("\\/")))
                (par.addChild(it) if par is not None else self.table.addTopLevelItem(it))
                parents[path.rstrip("\\/")] = it
                cb = QComboBox()
                for key, label in USES:
                    cb.addItem(label, key)
                got = remembered.get(store.norm_path(path))
                use = got[0] if got and got[1] == name else guess_use(name, depth, self.known)
                cb.setCurrentIndex(max(0, cb.findData(use)))
                cb.currentIndexChanged.connect(self.update_preview)
                self.table.setItemWidget(it, 2, cb)
                self.combos[path] = cb
            self.table.expandToDepth(1)
            self.table.setMinimumHeight(min(320, 26 * (len(self.rows) + 1) + 8))
            lay.addWidget(self.table)
            if more:
                m = QLabel(f"(the first {MAX_ROWS} folders are listed - folders further down follow the ones above them)")
                m.setStyleSheet("color: gray;")
                lay.addWidget(m)
            self.preview = QLabel()
            self.preview.setWordWrap(True)
            self.preview.setTextFormat(Qt.RichText)
            self.preview.setStyleSheet("color: gray;")
            lay.addWidget(self.preview)
            self.people.textChanged.connect(self.update_preview)
            self.category.currentTextChanged.connect(self.update_preview)
            self.update_preview()
        self.later = QCheckBox("Put them in Sort Later (out of the way until you're ready)")
        lay.addWidget(self.later)
        d = defaults or {}
        if d.get("people"):
            self.people.setText(", ".join(d["people"]))
        if d.get("category"):
            self.category.setCurrentText(d["category"])
        self.later.setChecked(bool(d.get("later")))
        bb = QDialogButtonBox()
        self.ok = bb.addButton("Add", QDialogButtonBox.AcceptRole)
        bb.addButton(QDialogButtonBox.Cancel)
        self.ok.setDefault(True)
        self.choice = None
        self.ok.clicked.connect(lambda: self._done("tag"))
        bb.rejected.connect(self.reject)
        plain = QPushButton("Add without tagging")
        plain.setFlat(True)
        plain.setCursor(Qt.PointingHandCursor)
        plain.setStyleSheet("text-align: left; color: palette(link);")
        plain.setToolTip("Just add and scan them - nothing you typed or picked above is used")
        plain.clicked.connect(lambda: self._done("plain"))
        bb.addButton(plain, QDialogButtonBox.ResetRole)
        lay.addWidget(bb)

    def use_of(self, path):
        cb = self.combos.get(path) if self.table else None
        return cb.currentData() if cb is not None else NOTHING

    def update_preview(self, *_):
        """An example: where one photo will end up."""
        if not self.table:
            return
        best = max(self.rows, key=lambda r: (r[1], r[3] > 0)) if self.rows else None
        if not best:
            self.preview.setText("")
            return
        path = best[0]
        chain = []
        p = path.rstrip("\\/")
        while True:
            chain.append(p)
            if p in (d.rstrip("\\/") for d in self.dirs):
                break
            q = os.path.dirname(p)
            if q == p:
                break
            p = q
        chain.reverse()
        people = [n for n in (" ".join(x.split()) for x in self.people.text().split(",")) if n]
        who, folders, album, skipped = list(people), [], None, False
        cat = " ".join(self.category.currentText().split())
        if cat:
            folders.append(cat)
        for c in chain:
            use, name = self.use_of(c), os.path.basename(c)
            if use == PERSON:
                who.append(name)
            elif use in (FOLDER, THEIR_FOLDER):
                folders.append(name)
            elif use == ALBUM:
                album = name
            elif use == LEAVE_OUT:
                skipped = True
        rel = os.path.relpath(path, os.path.dirname(self.dirs[0].rstrip("\\/"))) if self.dirs else path
        if skipped:
            self.preview.setText(f"e.g. photos in {html.escape(rel)} → left out")
            return
        dest = "\\".join(([f"<b>{html.escape(who[0])}</b>"] if who else ["<i>whoever is tagged</i>"
                                                                       if folders else "~Uncategorized"])
                         + [html.escape(f) for f in folders])
        self.preview.setText(f"e.g. photos in {html.escape(rel)} → saved under {dest}"
                             + (f" · album “{html.escape(album)}”" if album else "")
                             + (f" · tagged {html.escape(', '.join(who))}" if who else ""))

    def _done(self, how):
        self.choice = how
        self.accept()

    def choices(self):
        """{"how", "people", "category", "later", "watch", "folders": [(path, use, name, folder chain)]}"""
        out = {"how": self.choice, "watch": self.watch.isChecked() if self.dirs else False,
               "people": [], "category": None, "later": False, "folders": []}
        if self.choice != "tag":
            return out
        seen = set()
        for n in self.people.text().split(","):
            n = " ".join(n.split())
            if n and n.casefold() not in seen:
                seen.add(n.casefold())
                out["people"].append(n)
        out["category"] = " ".join(self.category.currentText().split()) or None
        out["later"] = self.later.isChecked()
        if self.table:
            for path, depth, name, here, total in self.rows:
                use = self.use_of(path)
                if use != NOTHING:
                    out["folders"].append((path, use, name))
        return out
