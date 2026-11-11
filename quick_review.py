"""Quick review: a strip of faces to confirm or reject quickly."""
import review
import store
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCompleter, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget)
from icons import app_icon
from ui_common import round_pixmap, SUGGEST


class FaceStrip(QWidget):
    """A few faces of one side of a question, plus a caption."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel()
        f = self.title.font()
        f.setPointSize(f.pointSize() + 3)
        f.setBold(True)
        self.title.setFont(f)
        self.title.setAlignment(Qt.AlignCenter)
        self.sub = QLabel()
        self.sub.setAlignment(Qt.AlignCenter)
        self.sub.setStyleSheet("color: gray;")
        self.big = QLabel()
        self.big.setAlignment(Qt.AlignCenter)
        self.big.setMinimumSize(200, 200)
        self.big.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.row = QHBoxLayout()
        self.row.setSpacing(6)
        lay.addWidget(self.title)
        lay.addWidget(self.sub)
        lay.addWidget(self.big, 1)
        lay.addLayout(self.row)

    def show_unit(self, unit, big=None, small=84):
        if big is None:          # as big as there's room for
            big = max(200, min(720, self.big.width() - 10, self.big.height() - 10))
        self.unit = unit
        if unit.kind == "person":
            self.title.setText(unit.name)
            self.sub.setText(f"{unit.size:,} confirmed face{'s' if unit.size != 1 else ''}")
        elif unit.kind == "group":
            self.title.setText("Unnamed group")
            self.sub.setText(f"{unit.size:,} faces")
        else:
            self.title.setText("Single face")
            self.sub.setText("not grouped with anyone yet")
        path = self.win.conn.execute("SELECT ph.path FROM faces f JOIN photos ph ON ph.id=f.photo_id WHERE f.id=?",
                                     (unit.show[0],)).fetchone()
        dpr = self.devicePixelRatioF()
        # a sharp crop from the photo at this size (the small saved thumbnail until it's loaded)
        pm = self.win.face_pixmap(unit.show[0], path[0] if path else None, want_px=big * dpr, face_only=True)
        rp = round_pixmap(pm, int(big * dpr), radius=int(16 * dpr))
        rp.setDevicePixelRatio(dpr)
        self.big.setPixmap(rp)
        while self.row.count():
            w = self.row.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self.row.addStretch()
        for fid in unit.show[1:7]:
            lb = QLabel()
            lb.setPixmap(round_pixmap(self.win.face_pixmap(fid, face_only=True), small, radius=10))
            self.row.addWidget(lb)
        self.row.addStretch()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if getattr(self, "unit", None) is not None:
            QTimer.singleShot(0, lambda: self.show_unit(self.unit))


class QuickReview(QWidget):
    """One yes/no question at a time, picked to sort the most faces per keypress.

    Y / Enter  yes, same person      N  no      Space  skip      Z or Backspace  undo
    Type a name first and press Enter to also name them (for two unnamed sides)."""

    REGROUP_EVERY = 5

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.sess = review.Session(win.conn)
        self.q = None
        self.pending_hires = []
        self.setFocusPolicy(Qt.StrongFocus)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        top = QHBoxLayout()
        back = QToolButton()
        back.setIcon(app_icon("back"))
        back.setIconSize(QSize(26, 26))
        back.setAutoRaise(True)
        back.setToolTip("Back to the gallery   (Esc)")
        back.clicked.connect(self.close_review)
        top.addWidget(back)
        tl = QLabel("Quick review")
        tf = tl.font()
        tf.setBold(True)
        tl.setFont(tf)
        top.addWidget(tl)
        top.addStretch(1)
        lay.addLayout(top)
        self.question = QLabel()
        f = self.question.font()
        f.setPointSize(f.pointSize() + 7)
        f.setBold(True)
        self.question.setFont(f)
        self.question.setAlignment(Qt.AlignCenter)
        self.why = QLabel()
        self.why.setAlignment(Qt.AlignCenter)
        self.why.setStyleSheet("color: gray;")
        lay.addWidget(self.question)
        lay.addWidget(self.why)
        sides = QHBoxLayout()
        self.left, self.right = FaceStrip(win), FaceStrip(win)
        sides.addWidget(self.left, 1)
        vs = QLabel("?")
        vf = vs.font()
        vf.setPointSize(vf.pointSize() + 18)
        vf.setBold(True)
        vs.setFont(vf)
        vs.setStyleSheet(f"color: {SUGGEST.name()};")
        sides.addWidget(vs)
        sides.addWidget(self.right, 1)
        lay.addLayout(sides, 1)
        nrow = QHBoxLayout()
        self.name_label = QLabel("Who is it? (optional)")
        self.name = QLineEdit()
        self.name.setPlaceholderText("type a name, then Enter = yes and name them")
        self.name.setClearButtonEnabled(True)
        comp = QCompleter(win.people_by_recent(), self.name)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setFilterMode(Qt.MatchContains)
        self.name.setCompleter(comp)
        self.name.returnPressed.connect(lambda: self.answer(True))
        nrow.addWidget(self.name_label)
        nrow.addWidget(self.name, 1)
        lay.addLayout(nrow)
        brow = QHBoxLayout()
        self.yes_b = QPushButton("Yes, same person   (Y)")
        self.no_b = QPushButton("No   (N)")
        self.skip_b = QPushButton("Skip   (Space)")
        self.undo_b = QPushButton("Undo   (Z)")
        done_b = QPushButton("Done")
        for b in (self.yes_b, self.no_b):
            b.setMinimumHeight(44)
            bf = b.font()
            bf.setPointSize(bf.pointSize() + 2)
            bf.setBold(True)
            b.setFont(bf)
        self.yes_b.setStyleSheet("QPushButton { color: #1f9d55; }")
        self.no_b.setStyleSheet("QPushButton { color: #c0392b; }")
        self.yes_b.clicked.connect(lambda: self.answer(True))
        self.no_b.clicked.connect(lambda: self.answer(False))
        self.skip_b.clicked.connect(self.skip)
        self.undo_b.clicked.connect(self.undo)
        done_b.clicked.connect(self.close_review)
        for b in (self.yes_b, self.no_b, self.skip_b, self.undo_b, done_b):
            b.setFocusPolicy(Qt.NoFocus)
            b.setAutoDefault(False)
        brow.addWidget(self.yes_b, 2)
        brow.addWidget(self.no_b, 2)
        brow.addWidget(self.skip_b, 1)
        brow.addWidget(self.undo_b, 1)
        brow.addWidget(done_b, 1)
        lay.addLayout(brow)
        self.progress = QLabel()
        self.progress.setStyleSheet("color: gray;")
        lay.addWidget(self.progress)
        for key, fn in (("Y", lambda: self.answer(True)), ("N", lambda: self.answer(False)),
                        ("Space", self.skip), ("Z", self.undo), ("Backspace", self.undo)):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)       # only while Quick review is open
            sc.activated.connect(lambda fn=fn: None if self.name.hasFocus() else fn())
        self.win.hires.ready.connect(self.hires_ready)
        self.next_question()

    def restart(self):
        """Opened again: a fresh session (it's kept, just hidden, in between)."""
        self.sess = review.Session(self.win.conn)
        self.name.setCompleter(None)
        comp = QCompleter(self.win.people_by_recent(), self.name)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setFilterMode(Qt.MatchContains)
        self.name.setCompleter(comp)
        self.next_question()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close_review()
            return
        super().keyPressEvent(e)

    def close_review(self):
        self.win.close_review()

    def hires_ready(self, fid):
        if self.q and fid in (self.q.a.show[0], self.q.b.show[0]):
            self.left.show_unit(self.q.a)
            self.right.show_unit(self.q.b)

    def next_question(self):
        queue, total = review.build_queue(self.win.conn, store.threshold(self.win.settings),
                                          self.sess.exclude(), limit=1)
        self.q = queue[0] if queue else None
        if self.q is None:
            self.question.setText("All done - nothing left to ask right now")
            self.why.setText("Name any remaining groups in the main window. New questions appear as you name "
                             "people and add photos.")
            for w in (self.left, self.right, self.name, self.name_label, self.yes_b, self.no_b, self.skip_b):
                w.setVisible(False)
            self.update_progress(0)
            return
        for w in (self.left, self.right, self.name, self.name_label, self.yes_b, self.no_b, self.skip_b):
            w.setVisible(True)
        q = self.q
        if q.b.kind == "person":
            self.question.setText(f"Is this {q.b.name}?" if q.a.kind == "single" else f"Is this group {q.b.name}?")
            self.yes_b.setText(f"Yes, it's {q.b.name}   (Y)")
            self.name.setVisible(False)
            self.name_label.setVisible(False)
        else:
            self.question.setText("Same person?")
            self.yes_b.setText("Yes, same person   (Y)")
        bits = [f"{max(0, min(99, round((q.sim + 0.15) * 100 / 0.85)))}% alike"]
        if q.event_gap is not None and q.event_gap <= review.EVENT_WINDOW:
            mins = int(q.event_gap // 60)
            bits.append("photos taken " + ("within a minute" if mins < 1 else f"{mins} min apart") + " (same event?)")
        n = q.a.size + (q.b.size if q.b.kind != "person" else 0)
        bits.append(f"yes sorts {q.a.size:,} face{'s' if q.a.size != 1 else ''}" if q.b.kind == "person"
                    else f"yes puts {n:,} faces together")
        self.why.setText("   ·   ".join(bits))
        self.left.show_unit(q.a)
        self.right.show_unit(q.b)
        self.name.clear()
        self.update_progress(total)
        self.setFocus()

    def update_progress(self, total):
        s = self.sess
        self.progress.setText(f"Answered {s.answered}  ·  {s.sorted_faces:,} faces sorted"
                              + (f"  ·  about {total:,} question{'s' if total != 1 else ''} left" if total else "")
                              + "      Y yes · N no · Space skip · Z undo · type a name to also name them")
        self.undo_b.setEnabled(bool(s.history))

    def answer(self, yes):
        if not self.q:
            return
        if yes:
            name = " ".join(self.name.text().split()) if self.name.isVisible() else ""
            if name:
                match = next((n for n in self.win.people_by_recent() if n.casefold() == name.casefold()), name)
                self.sess.yes(self.q, match)
            else:
                self.sess.yes(self.q)
        else:
            self.sess.no(self.q)
        if self.sess.answered % self.REGROUP_EVERY == 0:
            store.regroup(self.win.conn, store.threshold(self.win.settings))   # new names -> new suggestions
        self.next_question()

    def skip(self):
        if self.q:
            self.sess.skip(self.q)
            self.next_question()

    def undo(self):
        if self.sess.undo():
            self.next_question()
