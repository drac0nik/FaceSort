"""Smart albums (1.48): the window for one smart album's filter, and the suggested smart albums."""
from datetime import datetime

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QCompleter, QDateEdit, QDialog, QDialogButtonBox, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout)

import smart
import store


def _split(text):
    seen, out = set(), []
    for n in text.split(","):
        n = " ".join(n.split())
        if n and n.casefold() not in seen:
            seen.add(n.casefold())
            out.append(n)
    return out


def _completer(edit, words):
    comp = QCompleter(list(words), edit)
    comp.setCaseSensitivity(Qt.CaseInsensitive)
    comp.setFilterMode(Qt.MatchContains)
    edit.setCompleter(comp)
    comp.activated[str].connect(lambda t: edit.setText(
        ", ".join([x.strip() for x in edit.text().split(",")[:-1] if x.strip()] + [t])))
    edit.textEdited.connect(lambda t: comp.setCompletionPrefix(t.split(",")[-1].strip()))


class DateBox(QHBoxLayout):
    """An optional date: a tick box and a date."""

    def __init__(self, text, value):
        super().__init__()
        self.cb = QCheckBox(text)
        self.date = QDateEdit()
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        d = QDate.fromString(value or "", "yyyy-MM-dd")
        self.cb.setChecked(d.isValid())
        self.date.setDate(d if d.isValid() else QDate.currentDate())
        self.date.setEnabled(d.isValid())
        self.cb.toggled.connect(self.date.setEnabled)
        self.addWidget(self.cb)
        self.addWidget(self.date)

    def value(self):
        return self.date.date().toString("yyyy-MM-dd") if self.cb.isChecked() else None


class SmartAlbumDialog(QDialog):
    def __init__(self, parent, conn, name="", f=None, people=(), tags=(), folders=(), count_fn=None):
        """folders: [(id, label)]. count_fn(filter) -> photos it matches (shown as you go)."""
        super().__init__(parent)
        self.conn, self.count_fn = conn, count_fn
        f = dict(f or {})
        self.setWindowTitle("Smart album")
        self.setMinimumWidth(540)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(name)
        self.name.setPlaceholderText("A name (made from the conditions if empty)")
        form.addRow("Name:", self.name)
        lay.addLayout(form)
        lay.addWidget(QLabel("<b>Photos…</b>  (every part you fill in must match; photos in a locked vault never are)"))
        wf = QFormLayout()
        prow = QHBoxLayout()
        self.people = QLineEdit(", ".join(f.get("people_names") or []))
        self.people.setPlaceholderText("with these people - names, separated by commas")
        _completer(self.people, people)
        self.pmode = QComboBox()
        self.pmode.addItem("any of them", "any")
        self.pmode.addItem("all of them", "all")
        self.pmode.setCurrentIndex(1 if f.get("people_mode") == "all" else 0)
        prow.addWidget(self.people)
        prow.addWidget(self.pmode)
        wf.addRow("With:", prow)
        self.sugg = QCheckBox("also count suggestions as sure as the rules' setting (Settings › Rules)")
        self.sugg.setChecked(bool(f.get("suggested")))
        wf.addRow("", self.sugg)
        trow = QHBoxLayout()
        self.tags = QLineEdit(", ".join(f.get("tags") or []))
        self.tags.setPlaceholderText("tagged - tags, separated by commas")
        _completer(self.tags, tags)
        self.tmode = QComboBox()
        self.tmode.addItem("any of them", "any")
        self.tmode.addItem("all of them", "all")
        self.tmode.setCurrentIndex(1 if f.get("tags_mode") == "all" else 0)
        trow.addWidget(self.tags)
        trow.addWidget(self.tmode)
        wf.addRow("Tagged:", trow)
        self.not_tags = QLineEdit(", ".join(f.get("not_tags") or []))
        self.not_tags.setPlaceholderText("but not tagged")
        _completer(self.not_tags, tags)
        wf.addRow("Not tagged:", self.not_tags)
        import content
        from rules_ui import POSES
        self.subject = QComboBox()
        self.subject.addItem("", "")
        for key, (label, _) in content.SUBJECTS.items():
            self.subject.addItem(f"No people: {label}", key)
        self.subject.setCurrentIndex(max(0, self.subject.findData(f.get("subject") or "")))
        wf.addRow("What it is:", self.subject)
        self.pose = QComboBox()
        self.pose.addItems(POSES)
        self.pose.setCurrentText(f.get("pose") or "")
        wf.addRow("Pose:", self.pose)
        drow = QHBoxLayout()
        self.d_from = DateBox("from", f.get("date_from"))
        self.d_to = DateBox("until", f.get("date_to"))
        drow.addLayout(self.d_from)
        drow.addSpacing(12)
        drow.addLayout(self.d_to)
        drow.addStretch()
        wf.addRow("Taken:", drow)
        self.folder = QComboBox()
        self.folder.addItem("", None)
        for fid, label in folders:
            self.folder.addItem(label, fid)
        self.folder.setCurrentIndex(max(0, self.folder.findData(f.get("folder"))))
        wf.addRow("In folder:", self.folder)
        self.faces = QComboBox()
        for d, t in (("", "Any"), ("with", "With faces"), ("without", "Without faces")):
            self.faces.addItem(t, d)
        self.faces.setCurrentIndex(max(0, self.faces.findData(f.get("faces") or "")))
        self.media = QComboBox()
        for d, t in (("", "Photos and videos"), ("photos", "Photos only"), ("videos", "Videos only")):
            self.media.addItem(t, d)
        self.media.setCurrentIndex(max(0, self.media.findData(f.get("media") or "")))
        self.level = QComboBox()
        for d, t in smart.LEVEL_LABELS:
            self.level.addItem(t, d)
        self.level.setCurrentIndex(max(0, self.level.findData(f.get("level") or "")))
        mrow = QHBoxLayout()
        for w in (self.faces, self.media, self.level):
            mrow.addWidget(w)
        mrow.addStretch()
        wf.addRow("Kind:", mrow)
        lay.addLayout(wf)
        self.words = QLabel()
        self.words.setWordWrap(True)
        self.words.setStyleSheet("color: gray;")
        lay.addWidget(self.words)
        for w in (self.people, self.tags, self.not_tags):
            w.editingFinished.connect(self.update_words)
        for w in (self.pmode, self.tmode, self.subject, self.pose, self.folder, self.faces, self.media, self.level):
            w.currentIndexChanged.connect(self.update_words)
        for w in (self.sugg, self.d_from.cb, self.d_to.cb):
            w.toggled.connect(self.update_words)
        for w in (self.d_from.date, self.d_to.date):
            w.dateChanged.connect(self.update_words)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.update_words()

    def value(self, create=True):
        """The filter (people named here that don't exist yet aren't made - they're left out)."""
        names = _split(self.people.text())
        ids, kept = [], []
        for n in names:
            r = self.conn.execute("SELECT id FROM persons WHERE name=? COLLATE NOCASE", (n,)).fetchone()
            if r:
                ids.append(r[0])
                kept.append(n)
        f = {"people": ids, "people_names": kept, "people_mode": self.pmode.currentData() if len(ids) > 1 else None,
             "suggested": self.sugg.isChecked() and bool(ids), "tags": _split(self.tags.text()),
             "tags_mode": self.tmode.currentData() if len(_split(self.tags.text())) > 1 else None,
             "not_tags": _split(self.not_tags.text()), "subject": self.subject.currentData() or None,
             "pose": self.pose.currentText() or None, "date_from": self.d_from.value(), "date_to": self.d_to.value(),
             "folder": self.folder.currentData(), "faces": self.faces.currentData() or None,
             "media": self.media.currentData() or None, "level": self.level.currentData() or None}
        return {k: v for k, v in f.items() if v}

    def missing_people(self):
        return [n for n in _split(self.people.text()) if n.casefold() not in
                {x.casefold() for x in self.value().get("people_names", [])}]

    def update_words(self, *_):
        f = self.value()
        text = smart.describe(f, self.conn)
        if self.count_fn:
            try:
                n = self.count_fn(f)
                text += f"   ·   {n:,} photo{'s' if n != 1 else ''} now"
            except Exception:
                pass
        miss = self.missing_people()
        if miss:
            text += f"\n(nobody called {', '.join(miss)} - left out)"
        self.words.setText(text)

    def chosen_name(self):
        return self.name.text().strip()


class SmartSuggestDialog(QDialog):
    """Suggested smart albums: tick the ones to make; No thanks = never suggested again."""

    def __init__(self, parent, conn, suggestions, preview=None):
        super().__init__(parent)
        self.conn = conn
        self.made, self.edit = [], None
        self.preview = preview
        self.setWindowTitle("Suggested smart albums")
        self.setMinimumSize(520, 420)
        lay = QVBoxLayout(self)
        note = QLabel("From your library: people often photographed together, bursts of photos over a few days, and "
                      "people often in one scene. A smart album keeps itself up to date.")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.list = QListWidget()
        for s in suggestions:
            it = QListWidgetItem(f"{s['name']}   ·   {s['why']}")
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked)
            it.setData(Qt.UserRole, s)
            self.list.addItem(it)
        if not suggestions:
            self.list.addItem("Nothing to suggest right now - name more people and tag more photos.")
        self.list.itemDoubleClicked.connect(self.show_one)
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        for text, fn in (("Make ticked", self.make), ("Change first…", self.change), ("Show photos", self.show_one),
                         ("No thanks", self.no)):
            b = QPushButton(text)
            b.setAutoDefault(False)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _items(self, ticked=True):
        out = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.UserRole) and (it.checkState() == Qt.Checked if ticked else it.isSelected()):
                out.append(it)
        if not out and ticked:
            cur = self.list.currentItem()
            if cur is not None and cur.data(Qt.UserRole):
                out = [cur]
        return out

    def make(self):
        for it in self._items():
            s = it.data(Qt.UserRole)
            self.made.append(smart.save(self.conn, s["name"], s["filter"]))
            self.list.takeItem(self.list.row(it))

    def change(self):
        it = self.list.currentItem()
        if it is not None and it.data(Qt.UserRole):
            self.edit = it.data(Qt.UserRole)
            self.accept()

    def show_one(self, *_):
        it = self.list.currentItem()
        if it is not None and it.data(Qt.UserRole) and self.preview:
            self.preview(it.data(Qt.UserRole))

    def no(self):
        for it in self._items() or self._items(ticked=False):
            smart.dismiss(self.conn, it.data(Qt.UserRole)["key"])
            self.list.takeItem(self.list.row(it))


def folder_choices(conn):
    """[(folder id, "a › b")] for the In folder list."""
    paths = store.category_paths(conn)
    return sorted(((fid, " › ".join(p)) for fid, p in paths.items() if p), key=lambda r: r[1].casefold())


def year_bounds(conn):
    r = conn.execute("SELECT MIN(taken), MAX(taken) FROM photos WHERE taken IS NOT NULL").fetchone()
    return tuple(datetime.fromtimestamp(t).year if t else None for t in (r or (None, None)))
