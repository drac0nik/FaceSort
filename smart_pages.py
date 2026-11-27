"""Smart groups pages (1.63): the overview (every smart group at a glance - what needs you first) and
Possibly the same person as pair cards (both people's clearest faces side by side, why, Merge / Not the
same right there)."""
import html
import re

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from ui_common import round_pixmap

ROLE_USER = 0x0100
DESCRIBE = {
    "same": "Pairs of people who may be one - by faces, tattoos or names. Merge them or keep them apart.",
    "shoot": "Faces turned away, matched by their clothes to someone you confirmed at the same shoot.",
    "tattoos": "Photos where a tattoo or piece of jewelry you marked turned up again.",
    "maybe_notfaces": "Look like things you marked “not a face” - patterns, prints, posters.",
    "unknown": "Faces not like anyone, on photos that aren't sorted yet.",
    "partial": "Group photos where someone is tagged and other faces aren't named yet.",
    "nofaces": "No face found, nobody tagged, not sorted.",
    "nopeople": "Nobody in them - food, landscapes, screenshots, animals…",
    "sortlater": "Put aside for later.",
    "ai": "Made or edited with AI (or the detector thinks so).",
}
ACTION = {"same": "Review pairs", "shoot": "Check them", "tattoos": "Check matches", "maybe_notfaces": "Look through"}


def _count(text):
    m = re.search(r"([\d,]+)\D*$", text)
    return m.group(1) if m else ""


def _name(text):
    return re.sub(r"\s+[\d,]+(\s+sets?)?\s*$", "", text.split("   ")[0]).strip()


def _clear(lay):
    while lay.count():
        it = lay.takeAt(0)
        w = it.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
        elif it.layout() is not None:
            _clear(it.layout())


def _label(text, obj=None, wrap=False, style=None):
    lb = QLabel(text)
    if obj:
        lb.setObjectName(obj)
    lb.setWordWrap(wrap)
    if style:
        lb.setStyleSheet(style)
    return lb


class SmartHome(QScrollArea):
    """Smart groups > Overview."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        self.lay = QVBoxLayout(body)
        self.lay.setContentsMargins(4, 6, 14, 14)
        self.lay.setSpacing(12)
        self.setWidget(body)

    def sections(self):
        """[(section title, [(name, count, key, tip)])] from the Smart groups list as it is now."""
        spec = getattr(self.win, "_last_spec", None)
        out = []
        tops = spec.get(3, []) if spec else []
        for top in tops:
            k = top.dat.get(ROLE_USER)
            if k is not None:
                continue                       # (the Overview row itself)
            title = top.txt.split(" (")[0]
            items = []
            for ch in top.kids:
                key = ch.dat.get(ROLE_USER)
                if key is None:
                    continue
                items.append((_name(ch.txt), _count(ch.txt), tuple(key), ch.tip))
            out.append((title, top.txt, items))
        return out

    def refresh(self):
        _clear(self.lay)
        secs = self.sections()
        if not secs:
            self.lay.addWidget(_label("The smart groups appear here once the lists are ready.", style="color: gray;"))
            self.lay.addStretch(1)
            return
        for title, full, items in secs:
            if title == "Unnamed groups":
                n = _count(full.replace(")", ""))
                items = [("Unnamed groups", n, None, "Look-alike faces nobody is named for - biggest first: naming "
                                                     "them sorts the most")]
            if not items:
                continue
            head = _label(full.upper() if title == "Needs you" else title.upper(), "sectionlabel")
            if title == "Needs you":
                head.setStyleSheet("color: #F2B544;")
            self.lay.addWidget(head)
            if title == "Needs you":
                grid = QGridLayout()
                grid.setSpacing(12)
                for i, it in enumerate(items):
                    grid.addWidget(self.big_card(*it), 0, i)
                for i in range(len(items), 4):
                    grid.setColumnStretch(i, 1)
                self.lay.addLayout(grid)
            elif title == "Library health":
                row = QHBoxLayout()
                row.setSpacing(8)
                for name, n, key, tip in items:
                    b = QPushButton(f"{name}   {n}")
                    b.setToolTip(tip or name)
                    b.clicked.connect(lambda _c=False, k=key: self.open(k))
                    row.addWidget(b)
                row.addStretch(1)
                self.lay.addLayout(row)
            else:
                grid = QGridLayout()
                grid.setSpacing(12)
                for i, it in enumerate(items):
                    grid.addWidget(self.small_card(*it), i // 3, i % 3)
                for i in range(3):
                    grid.setColumnStretch(i, 1)
                self.lay.addLayout(grid)
        self.lay.addStretch(1)

    def open(self, key):
        if key is None:                              # (Unnamed groups: the first group)
            sec = next((t for t in (getattr(self.win, "_last_spec", None) or {}).get(3, [])
                        if t.txt.startswith("Unnamed groups")), None)
            first = next((k.dat.get(ROLE_USER) for k in (sec.kids if sec else []) if k.dat.get(ROLE_USER)), None)
            if first is None:
                return
            key = tuple(first)
        self.win.show_view(key)

    def big_card(self, name, n, key, tip):
        card = QFrame()
        card.setObjectName("card")
        card.setMinimumWidth(200)
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(8)
        top = QHBoxLayout()
        top.addWidget(_label(f"<b>{html.escape(name)}</b>"), 1)
        top.addWidget(_label(n, style="font-size: 15pt; font-weight: 600;"))
        v.addLayout(top)
        strip = QHBoxLayout()
        strip.setSpacing(6)
        for pm in self.samples(key)[:4]:
            lb = QLabel()
            lb.setPixmap(round_pixmap(pm, 52, 8))
            strip.addWidget(lb)
        strip.addStretch(1)
        v.addLayout(strip)
        v.addWidget(_label(DESCRIBE.get(key[0], tip or ""), wrap=True, style="color: #A9ABB1;"))
        b = QPushButton(ACTION.get(key[0], "Open"))
        b.clicked.connect(lambda: self.open(key))
        v.addWidget(b)
        return card

    def small_card(self, name, n, key, tip):
        card = QFrame()
        card.setObjectName("card")
        card.setMinimumWidth(240)
        h = QHBoxLayout(card)
        h.setContentsMargins(14, 12, 14, 12)
        v = QVBoxLayout()
        top = QHBoxLayout()
        top.addWidget(_label(f"<b>{html.escape(name)}</b>"), 1)
        top.addWidget(_label(n, style="color: #C9CACF;"))
        v.addLayout(top)
        v.addWidget(_label(DESCRIBE.get(key[0] if key else "", "") or (tip or "").split("\n")[0], wrap=True,
                           style="color: #8D8F96; font-size: 9pt;"))
        h.addLayout(v, 1)
        b = QToolButton()
        b.setText("Open")
        b.clicked.connect(lambda: self.open(key))
        h.addWidget(b)
        card.mousePressEvent = lambda e, k=key: self.open(k)
        return card

    def samples(self, key):
        """A few pictures for a card."""
        c, w = self.win.conn, self.win
        out = []
        try:
            if key[0] == "same":
                for a, b in c.execute("SELECT a, b FROM same_people ORDER BY score DESC LIMIT 2"):
                    for pid in (a, b):
                        r = c.execute("SELECT id FROM faces WHERE person_id=? AND confirmed=1 AND ignored=0 "
                                      "ORDER BY COALESCE(clarity, 0) DESC LIMIT 1", (pid,)).fetchone()
                        if r:
                            out.append(w.face_pixmap(r[0], want_px=104, face_only=True))
            elif key[0] == "maybe_notfaces":
                out = [w.face_pixmap(f, want_px=104, face_only=True)
                       for (f,) in c.execute("SELECT id FROM faces WHERE ignored=4 LIMIT 4")]
            elif key[0] in ("tattoos", "shoot"):
                sql = ("SELECT DISTINCT photo_id FROM tattoo_suggestions LIMIT 4" if key[0] == "tattoos" else
                       "SELECT DISTINCT b.photo_id FROM body_suggestions s JOIN bodies b ON b.id=s.body_id LIMIT 4")
                from viewer_parts import framed_pixmap
                for (pid,) in c.execute(sql):
                    p = c.execute("SELECT path FROM photos WHERE id=?", (pid,)).fetchone()
                    if p:
                        pm = framed_pixmap(p[0], None, 104, 104, crop=True, side=240)
                        if not pm.isNull():
                            out.append(pm)
        except Exception:
            pass
        return [p for p in out if p is not None and not p.isNull()]


class PairsPage(QScrollArea):
    """Possibly the same person, as pair cards."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        outer = QVBoxLayout(body)
        outer.setContentsMargins(4, 4, 14, 14)
        outer.setSpacing(10)
        bar = QHBoxLayout()
        self.filters = QButtonGroup(self)
        self.filters.setExclusive(True)
        for i, (text, tip) in enumerate((("All", "Every pair"), ("Faces", "Pairs whose faces match"),
                                         ("Tattoos", "A tattoo or jewelry of one on the other's photos"),
                                         ("Names", "Names almost the same"))):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setCheckable(True)
            b.setChecked(i == 0)
            self.filters.addButton(b, i)
            bar.addWidget(b)
        self.filters.idClicked.connect(lambda _i: self.refresh())
        bar.addStretch(1)
        outer.addLayout(bar)
        self.lay = QVBoxLayout()
        self.lay.setSpacing(10)
        outer.addLayout(self.lay)
        outer.addStretch(1)
        self.setWidget(body)
        self.limit = 40

    def refresh(self):
        import samepeople
        _clear(self.lay)
        c = self.win.conn
        names = dict(c.execute("SELECT id, name FROM persons"))
        mode = self.filters.checkedId()
        want = {1: ("face",), 2: ("tattoo",), 3: ("name",)}.get(mode)
        pairs = [p for p in samepeople.pairs(c)
                 if not want or any(w in p[3] for w in want)][:self.limit]
        if not pairs:
            self.lay.addWidget(_label("No pairs here." if want else "Nobody looks like they're two people right now.",
                                      style="color: gray;"))
            return
        for a, b, score, why in pairs:
            self.lay.addWidget(self.card(a, b, why, names))

    def strength(self, why):
        if "faces look alike" in why or "looks like them" in why:
            return "Strong", "#57C27A"
        if "match closely" in why or "tattoo" in why:
            return "Likely", "#F2B544"
        return "Possible", "#A9ABB1"

    def person(self, pid, names, right=False):
        c, w = self.win.conn, self.win
        box = QVBoxLayout()
        box.setSpacing(8)
        n = c.execute("""SELECT COUNT(DISTINCT photo_id) FROM (SELECT photo_id FROM faces WHERE person_id=? AND
                         confirmed=1 AND ignored=0 UNION SELECT photo_id FROM photo_people WHERE person_id=?)""",
                      (pid, pid)).fetchone()[0]
        head = _label(f"<b>{html.escape(names.get(pid, '?'))}</b><br><span style='color:#8D8F96'>{n:,} photo"
                      f"{'s' if n != 1 else ''}</span>")
        head.setAlignment(Qt.AlignRight if right else Qt.AlignLeft)
        box.addWidget(head)
        strip = QHBoxLayout()
        strip.setSpacing(6)
        if right:
            strip.addStretch(1)
        for (fid,) in c.execute("SELECT id FROM faces WHERE person_id=? AND confirmed=1 AND ignored=0 "
                                "ORDER BY COALESCE(clarity, 0) DESC LIMIT 5", (pid,)):
            lb = QLabel()
            lb.setPixmap(round_pixmap(w.face_pixmap(fid, want_px=128, face_only=True), 64, 8))
            strip.addWidget(lb)
        if not right:
            strip.addStretch(1)
        box.addLayout(strip)
        return box

    def card(self, a, b, why, names):
        card = QFrame()
        card.setObjectName("card")
        h = QHBoxLayout(card)
        h.setContentsMargins(16, 14, 16, 14)
        h.setSpacing(16)
        h.addLayout(self.person(a, names), 1)
        mid = QVBoxLayout()
        mid.setSpacing(6)
        word, col = self.strength(why)
        s = _label(word, style=f"color: {col}; font-size: 14pt; font-weight: 600;")
        s.setAlignment(Qt.AlignCenter)
        mid.addWidget(s)
        for part in why.split("; ")[:4]:
            tag = _label(part, style="background: #2A2C32; color: #C9CACF; border-radius: 9px; padding: 2px 8px; "
                                     "font-size: 8.5pt;")
            tag.setAlignment(Qt.AlignCenter)
            mid.addWidget(tag, 0, Qt.AlignHCenter)
        btns = QHBoxLayout()
        m = QPushButton("Merge…")
        m.setProperty("primary", "true")
        m.clicked.connect(lambda: self.win.merge_selected_pair((a, b, why)))
        ns = QPushButton("Not the same")
        ns.clicked.connect(lambda: self.win.not_same_selected_pair((a, b, why)))
        btns.addWidget(m)
        btns.addWidget(ns)
        mid.addLayout(btns)
        wrap = QWidget()
        wrap.setLayout(mid)
        wrap.setFixedWidth(250)
        wrap.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        h.addWidget(wrap)
        h.addLayout(self.person(b, names, right=True), 1)
        return card


_unused = (QSize,)
