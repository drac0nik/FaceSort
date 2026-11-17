"""Settings > Rules (1.46): the list of rules for incoming photos and the window for one rule."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QCompleter, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                               QSpinBox, QVBoxLayout, QWidget)

import rules
import store

POSES = ["", "Standing", "Sitting", "Lying down", "Kneeling", "Arms raised", "Facing away", "Full body", "Close-up"]


class RuleDialog(QDialog):
    def __init__(self, parent, conn, rule=None, people=(), folders=(), tags=()):
        super().__init__(parent)
        self.conn = conn
        self.rule = dict(rule or {})
        self.setWindowTitle("Rule" if rule else "New rule")
        self.setMinimumWidth(520)
        c, a = self.rule.get("cond") or {}, self.rule.get("action") or {}
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(self.rule.get("name", ""))
        self.name.setPlaceholderText("A name (optional)")
        form.addRow("Name:", self.name)
        lay.addLayout(form)
        lay.addWidget(QLabel("<b>When a new photo…</b>  (every part you fill in must match)"))
        wf = QFormLayout()
        row = QHBoxLayout()
        self.folder = QLineEdit(c.get("folder") or "")
        self.folder.setPlaceholderText("comes from inside this folder (any folder if empty)")
        pick = QPushButton("…")
        pick.setFixedWidth(30)
        pick.clicked.connect(self.pick_folder)
        row.addWidget(self.folder)
        row.addWidget(pick)
        wf.addRow("From:", row)
        self.people = QLineEdit(", ".join(c.get("people_names") or []))
        self.people.setPlaceholderText("has these people on it - names, separated by commas")
        comp = QCompleter(list(people), self.people)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setFilterMode(Qt.MatchContains)
        self.people.setCompleter(comp)
        comp.activated[str].connect(lambda t: self.people.setText(
            ", ".join([x.strip() for x in self.people.text().split(",")[:-1] if x.strip()] + [t])))
        self.people.textEdited.connect(lambda t: comp.setCompletionPrefix(t.split(",")[-1].strip()))
        prow = QHBoxLayout()
        prow.addWidget(self.people)
        self.mode = QComboBox()
        self.mode.addItem("any of them", "any")
        self.mode.addItem("all of them", "all")
        self.mode.setCurrentIndex(1 if c.get("people_mode") == "all" else 0)
        prow.addWidget(self.mode)
        wf.addRow("With:", prow)
        import content
        self.subject = QComboBox()
        self.subject.addItem("", "")
        for key, (label, _) in content.SUBJECTS.items():
            self.subject.addItem(f"No people: {label}", key)
        self.subject.setCurrentIndex(max(0, self.subject.findData(c.get("subject") or "")))
        wf.addRow("What it is:", self.subject)
        self.tag = QComboBox()
        self.tag.setEditable(True)
        self.tag.addItems([""] + list(tags))
        self.tag.setCurrentText(c.get("tag") or "")
        wf.addRow("Tagged:", self.tag)
        self.pose = QComboBox()
        self.pose.addItems(POSES)
        self.pose.setCurrentText(c.get("pose") or "")
        wf.addRow("Pose:", self.pose)
        lay.addLayout(wf)
        lay.addWidget(QLabel("<b>…then</b>"))
        tf = QFormLayout()
        self.to_folder = QComboBox()
        self.to_folder.setEditable(True)
        self.to_folder.addItems([""] + list(folders))
        self.to_folder.setCurrentText(" › ".join(a.get("folder") or []))
        self.to_folder.lineEdit().setPlaceholderText("put it in this folder (a › b for a folder inside another)")
        tf.addRow("Folder:", self.to_folder)
        self.album = QLineEdit(a.get("album") or "")
        self.album.setPlaceholderText("add it to this album")
        tf.addRow("Album:", self.album)
        self.to_tag = QComboBox()
        self.to_tag.setEditable(True)
        self.to_tag.addItems([""] + list(tags))
        self.to_tag.setCurrentText(a.get("tag") or "")
        tf.addRow("Tag:", self.to_tag)
        self.later = QCheckBox("Put it in Sort Later")
        self.later.setChecked(bool(a.get("later")))
        tf.addRow("", self.later)
        lay.addLayout(tf)
        self.existing = QCheckBox("Also do this to matching photos already in the library")
        if not self.rule.get("id"):
            lay.addWidget(self.existing)
        self.words = QLabel()
        self.words.setStyleSheet("color: gray;")
        self.words.setWordWrap(True)
        lay.addWidget(self.words)
        for w in (self.folder, self.people, self.album, self.name):
            w.textChanged.connect(self.update_words)
        for w in (self.subject, self.tag, self.pose, self.to_folder, self.to_tag, self.mode):
            w.currentTextChanged.connect(self.update_words)
        self.later.toggled.connect(self.update_words)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        self.ok = bb.button(QDialogButtonBox.Ok)
        lay.addWidget(bb)
        self.update_words()

    def pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Photos that come from inside…")
        if d:
            self.folder.setText(store.norm_path(d))

    def names(self):
        seen, out = set(), []
        for n in self.people.text().split(","):
            n = " ".join(n.split())
            if n and n.casefold() not in seen:
                seen.add(n.casefold())
                out.append(n)
        return out

    def value(self):
        names = self.names()
        ids = [store.get_or_create_person(self.conn, n) for n in names] if names else []
        chain = [x.strip() for x in self.to_folder.currentText().replace(">", "›").split("›") if x.strip()]
        cond = {k: v for k, v in {
            "folder": self.folder.text().strip() or None, "people": ids, "people_names": names,
            "people_mode": self.mode.currentData(), "subject": self.subject.currentData() or None,
            "tag": self.tag.currentText().strip() or None, "pose": self.pose.currentText() or None}.items() if v}
        action = {k: v for k, v in {"folder": chain, "album": self.album.text().strip() or None,
                                    "tag": self.to_tag.currentText().strip() or None,
                                    "later": self.later.isChecked()}.items() if v}
        out = dict(self.rule)
        out.update(name=self.name.text().strip(), cond=cond, action=action, enabled=self.rule.get("enabled", True),
                   existing=self.existing.isChecked())
        return out

    def update_words(self, *_):
        names = self.names()
        r = {"cond": {"folder": self.folder.text().strip(), "people_names": names, "people": names,
                      "people_mode": self.mode.currentData(), "subject": self.subject.currentData(),
                      "tag": self.tag.currentText().strip(), "pose": self.pose.currentText()},
             "action": {"folder": [x.strip() for x in self.to_folder.currentText().replace(">", "›").split("›")
                                   if x.strip()],
                        "album": self.album.text().strip(), "tag": self.to_tag.currentText().strip(),
                        "later": self.later.isChecked()}}
        has_then = any(r["action"].values())
        self.ok.setEnabled(has_then)
        self.words.setText(rules.describe(r) + ("" if has_then else "   (choose what to do with them)"))


class RulesEditor(QWidget):
    """The list in Settings: add, change, switch off, reorder, delete."""

    def __init__(self, conn, settings, people=(), folders=(), tags=(), parent=None):
        super().__init__(parent)
        self.conn, self.settings = conn, settings
        self.people, self.folders, self.tags = people, folders, tags
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        note = QLabel("Rules act on new photos after a scan (and when suggestions change): each photo once, so "
                      "anything you change by hand afterwards stays as you left it. Everything a rule does is in "
                      "the history.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        lay.addWidget(note)
        srow = QHBoxLayout()
        self.cb_sugg = QCheckBox("Rules may count a suggested person when the match is at least")
        self.cb_sugg.setChecked(bool(settings.get("rules_suggestions", True)))
        self.pct = QSpinBox()
        self.pct.setRange(50, 100)
        self.pct.setSuffix(" %")
        self.pct.setValue(int(settings.get("rules_suggestion_pct", 95)))
        self.cb_sugg.toggled.connect(self.pct.setEnabled)
        self.pct.setEnabled(self.cb_sugg.isChecked())
        srow.addWidget(self.cb_sugg)
        srow.addWidget(self.pct)
        srow.addStretch()
        lay.addLayout(srow)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _it: self.edit())
        self.list.itemChanged.connect(self.toggled)
        lay.addWidget(self.list)
        brow = QHBoxLayout()
        for text, fn in (("Add rule…", self.add), ("Change…", self.edit), ("Up", lambda: self.move(-1)),
                         ("Down", lambda: self.move(1)), ("Delete", self.delete)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            brow.addWidget(b)
        brow.addStretch()
        lay.addLayout(brow)
        self.fill()

    def fill(self):
        self.list.blockSignals(True)
        self.list.clear()
        for r in rules.all_rules(self.conn):
            n = self.conn.execute("SELECT COUNT(*) FROM rule_applied WHERE rule_id=?", (r["id"],)).fetchone()[0]
            it = QListWidgetItem((r["name"] + ": " if r["name"] else "") + rules.describe(r)
                                 + (f"   ·   {n:,} photo{'s' if n != 1 else ''} so far" if n else ""))
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if r["enabled"] else Qt.Unchecked)
            it.setData(Qt.UserRole, r)
            self.list.addItem(it)
        self.list.blockSignals(False)

    def current(self):
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else None

    def dialog(self, rule=None):
        return RuleDialog(self, self.conn, rule, self.people, self.folders, self.tags)

    def add(self):
        d = self.dialog()
        if d.exec() == QDialog.Accepted:
            rules.save_rule(self.conn, d.value())
            self.fill()

    def edit(self):
        r = self.current()
        if not r:
            return
        d = self.dialog(r)
        if d.exec() == QDialog.Accepted:
            rules.save_rule(self.conn, d.value())
            self.fill()

    def toggled(self, it):
        r = dict(it.data(Qt.UserRole))
        r["enabled"] = it.checkState() == Qt.Checked
        rules.save_rule(self.conn, r)
        it.setData(Qt.UserRole, r)

    def move(self, step):
        r = self.current()
        if r:
            row = self.list.currentRow()
            rules.move_rule(self.conn, r["id"], step)
            self.fill()
            self.list.setCurrentRow(max(0, min(self.list.count() - 1, row + step)))

    def delete(self):
        r = self.current()
        if r:
            rules.delete_rule(self.conn, r["id"])
            self.fill()

    def save_to(self, s):
        s["rules_suggestions"] = self.cb_sugg.isChecked()
        s["rules_suggestion_pct"] = int(self.pct.value())
