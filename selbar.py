"""The selection bar (1.63): what you can do with what's selected, with its key, floating over the
gallery - instead of a row of key hints that's always there."""
from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton

# (label, key, method, the views it's for (None: any with faces))
FACE_ACTIONS = [("Confirm", "C", "act_confirm", ("person", "tattoos", "shoot")),
                ("Not this person", "R", "act_remove", ("person",)),
                ("Not in this group", "R", "act_remove", ("group",)),
                ("Not a face", "N", "act_notface", None),
                ("Ignore", "I", "act_ignore", None)]
PHOTO_ACTIONS = [("Later", "L", "act_sort_later", None),
                 ("Discard", "D", "act_discard", None)]
NO_BAR = {"errors", "smart_home", "same", "ignored", "discarded"}


class SelectionBar(QFrame):
    def __init__(self, win, host):
        super().__init__(host)
        self.win, self.host = win, host
        self.setObjectName("selbar")
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(8, 6, 6, 6)
        self.lay.setSpacing(2)
        self.count = QLabel()
        self.count.setStyleSheet("font-weight: 600; padding: 0 10px 0 4px;")
        self.lay.addWidget(self.count)
        self.buttons = []
        self.hide()
        self._filter = _Follow(self)
        host.installEventFilter(self._filter)

    def _clear(self):
        # (1.63.2) hidden and taken out of the bar where they are - setParent(None) made each old button
        # a window of its own for a moment (they flashed in the corner of the screen)
        for b in self.buttons:
            b.hide()
            self.lay.removeWidget(b)
            b.deleteLater()
        self.buttons = []

    def _add(self, text, key, fn, primary=False):
        b = QToolButton(self)
        b.setText(f"{text}   {key}" if key else text)
        b.setToolTip(f"{text} ({key})" if key else text)
        if primary:
            b.setProperty("primary", "true")
        b.clicked.connect(fn)
        self.lay.addWidget(b)
        b.show()                 # (made while the bar is showing: a new child would otherwise stay hidden)
        self.buttons.append(b)
        return b

    def update_for(self, n_faces, n_photos, kind):
        n = n_faces + n_photos
        if not n or kind in NO_BAR or self.win.center.currentWidget() is not self.win.grid:
            self._sig = None
            self.hide()
            return False
        sig = (n_faces, n_photos, kind, self.host.width())
        if sig == getattr(self, "_sig", None) and self.buttons and self.isVisible():
            self.place()
            return True                  # (nothing changed: the bar stays as it is)
        self._sig = sig
        self._clear()
        what = (f"{n_faces:,} face{'s' if n_faces != 1 else ''}" if n_faces and not n_photos else
                f"{n:,} selected")
        self.count.setText(what)
        if n_faces:
            self._add("Name…", "Enter", self.win.act_name, primary=True)
        else:
            self._add("Tag people…", "Enter", self.win.act_tag, primary=True)
        if n_faces:
            for text, key, meth, kinds in FACE_ACTIONS:
                if kinds is None or kind in kinds:
                    self._add(text, key, getattr(self.win, meth))
        for text, key, meth, _k in PHOTO_ACTIONS:
            self._add(text, key, getattr(self.win, meth))
        more = self._add("More…", "", lambda: None)
        more.clicked.disconnect()
        more.clicked.connect(lambda: self.more(more))
        if self.sizeHint().width() > self.host.width() - 24:       # (narrow: the keys go to the tooltips)
            for b in self.buttons:
                b.setText(b.text().split("   ")[0])
        for b in self.buttons:
            b.setMinimumWidth(b.sizeHint().width())
        # still too wide: the least used go (they're all under More… and on their keys too)
        spare = [b for b in self.buttons[1:-1]]
        self.lay.activate()
        while spare and self.lay.sizeHint().width() > self.host.width() - 24:
            b = spare.pop()
            b.hide()
            self.lay.activate()
        self.adjustSize()
        self.place()
        self.show()
        self.raise_()
        return True

    def more(self, btn):
        g = self.win.grid
        pos = g.viewport().mapFromGlobal(btn.mapToGlobal(QPoint(0, 0)))
        self.win.grid_menu(pos)

    def place(self):
        h = self.host
        self.adjustSize()
        w = self.sizeHint().width()
        self.resize(w, self.sizeHint().height())
        self.move(max(12, (h.width() - w) // 2), h.height() - self.height() - 18)


class _Follow(QObject):
    def __init__(self, bar):
        super().__init__(bar)
        self.bar = bar

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Resize and self.bar.isVisible():
            self.bar.place()
        return False


_unused = (Qt,)
