"""Quick review: a strip of faces to confirm or reject quickly."""
import review
import store
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCompleter, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget)
from icons import app_icon
from ui_common import round_pixmap, SUGGEST


class _Click(QLabel):
    def __init__(self, cb, tip=""):
        super().__init__()
        self._cb = cb
        self.setCursor(Qt.PointingHandCursor)
        if tip:
            self.setToolTip(tip)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._cb()
        super().mousePressEvent(e)


class FaceStrip(QWidget):
    """A few faces of one side of a question, plus a caption. (1.59.1) Click a small face to see it big;
    the big picture is the face close up or the whole photo (F, or click it) with the face outlined."""

    def __init__(self, win, on_toggle=None):
        super().__init__()
        self.win = win
        self.unit, self.main_i, self.full = None, 0, False
        self.on_toggle = on_toggle
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
        self.big = _Click(lambda: self.on_toggle and self.on_toggle(),
                          "Click (or F) to switch between the face close up and the whole photo")
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
        if unit is not self.unit:
            self.main_i = 0
        self.unit = unit
        self.main_i = min(self.main_i, max(0, len(unit.show[:7]) - 1))
        if unit.kind == "person":
            self.title.setText(unit.name)
            self.sub.setText(f"{unit.size:,} confirmed face{'s' if unit.size != 1 else ''}")
        elif unit.kind == "group":
            self.title.setText("Unnamed group")
            self.sub.setText(f"{unit.size:,} faces")
        else:
            self.title.setText("Single face")
            self.sub.setText("not grouped with anyone yet")
        main = unit.show[self.main_i]
        row = self.win.conn.execute("SELECT ph.path, f.x, f.y, f.w, f.h FROM faces f JOIN photos ph "
                                    "ON ph.id=f.photo_id WHERE f.id=?", (main,)).fetchone()
        dpr = self.devicePixelRatioF()
        rp = None
        if self.full and row:
            from viewer_parts import framed_pixmap
            bw = max(200, self.big.width() - 10)
            bh = max(200, self.big.height() - 10)
            rp = framed_pixmap(row[0], row[1:5], int(bw * dpr), int(bh * dpr), side=1400)
            if rp.isNull():
                rp = None
        if rp is None:
            # a sharp crop from the photo at this size (the small saved thumbnail until it's loaded)
            pm = self.win.face_pixmap(main, row[0] if row else None, want_px=big * dpr, face_only=True)
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
        shown = unit.show[:7]
        if len(shown) > 1:
            for i, fid in enumerate(shown):
                lb = _Click(lambda i=i: self.pick(i), "Show this one big")
                lb.setPixmap(round_pixmap(self.win.face_pixmap(fid, face_only=True), small, radius=10))
                lb.setStyleSheet("border: 3px solid #2d8cd1; border-radius: 12px;" if i == self.main_i
                                 else "border: 3px solid transparent; border-radius: 12px;")
                self.row.addWidget(lb)
        self.row.addStretch()

    def pick(self, i):
        self.main_i = i
        self.show_unit(self.unit)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.unit is not None:
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
        self.full_b = QPushButton("Whole photo   (F)")
        self.full_b.setCheckable(True)
        self.full_b.setFocusPolicy(Qt.NoFocus)
        self.full_b.setToolTip("Show the whole photos (the face outlined) instead of the faces close up")
        self.full_b.toggled.connect(self.set_full)
        top.addWidget(self.full_b)
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
        self.left, self.right = FaceStrip(win, self.toggle_full), FaceStrip(win, self.toggle_full)
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
                        ("Space", self.skip), ("Z", self.undo), ("F", self.toggle_full), ("Backspace", self.undo)):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)       # only while Quick review is open
            sc.activated.connect(lambda fn=fn: None if self.name.hasFocus() else fn())
        self.win.hires.ready.connect(self.hires_ready)
        self.next_question()

    def toggle_full(self):
        self.full_b.toggle()

    def set_full(self, on):
        """Whole photos or faces close up, both sides together (1.59.1)."""
        self.full_b.setText("Faces close up   (F)" if on else "Whole photo   (F)")
        for s in (self.left, self.right):
            s.full = on
            if s.unit is not None and self.q is not None:
                s.show_unit(s.unit)

    def restart(self):
        """Opened again: a fresh session (it's kept, just hidden, in between)."""
        self.sess = review.Session(self.win.conn)
        self._reset_queue()
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
        if self.q and fid in (self.q.a.show[self.left.main_i], self.q.b.show[self.right.main_i]):
            self.left.show_unit(self.q.a)
            self.right.show_unit(self.q.b)

    # ---- (1.61) the questions are worked out a batch at a time - the next one is instant; a fresh batch is
    # read in the background while you answer (it used to work out the whole library again for every
    # question: seconds per keypress on a big library)
    QUEUE_LOW = 25          # this few left: the next batch is read in the background
    QUEUE_STALE = 30        # answers since the batch was read: read a fresh one

    def _reset_queue(self):
        self._queue, self._total, self._touched = [], 0, set()
        self._since_build, self._shown = 0, []
        self._opening = True
        self._bg = None

    def _build_now(self):
        queue, total = review.build_queue(self.win.conn, store.threshold(self.win.settings),
                                          self.sess.exclude(), limit=200)
        self._queue, self._total, self._touched, self._since_build = list(queue), total, set(), 0
        self._more = total > len(queue)

    def _build_later(self):
        """A fresh batch on a background thread (its own connection), swapped in when it's ready."""
        import threading
        if getattr(self, "_bg", None) is not None:
            return
        thr, excl = store.threshold(self.win.settings), self.sess.exclude()
        box, started = {}, len(self.sess.history)

        def work():
            conn = None
            try:
                conn = store.connect()
                box["got"] = review.build_queue(conn, thr, excl, limit=200)
            except Exception as e:
                box["error"] = e
            finally:
                if conn is not None:
                    conn.close()
        th = threading.Thread(target=work, daemon=True, name="quick review questions")
        self._bg = (th, box, started, set())
        th.start()
        QTimer.singleShot(100, self._bg_check)

    def _bg_check(self):
        bg = getattr(self, "_bg", None)
        if bg is None:
            return
        th, box, started, touched = bg
        if th.is_alive():
            QTimer.singleShot(100, self._bg_check)
            return
        self._bg = None
        self._opening = False
        if getattr(self.win, "_closing", False) or self.win._gone():
            return
        if "got" not in box:                       # (couldn't read them there: here, then)
            from ui_common import log
            log(f"quick review: {box.get('error')}")
            box["got"] = review.build_queue(self.win.conn, store.threshold(self.win.settings),
                                            self.sess.exclude(), limit=200)
        queue, total = box["got"]
        excl = self.sess.exclude()
        # answers given while it was being read: those units have changed - leave their questions out
        self._queue = [q for q in queue if q.key not in excl and q.a.key not in touched and q.b.key not in touched]
        self._total, self._touched, self._since_build = total, set(touched), len(touched)
        self._more = total > len(queue)
        if self.q is None:
            self.next_question()

    def _touch(self, *keys):
        self._touched.update(keys)
        if getattr(self, "_bg", None) is not None:
            self._bg[3].update(keys)

    def _pop(self):
        excl = self.sess.exclude()
        while self._queue:
            q = self._queue.pop(0)
            if q.key in excl or q.a.key in self._touched or q.b.key in self._touched:
                continue
            return q
        return None

    def next_question(self):
        if not hasattr(self, "_queue"):
            self._reset_queue()
        q = None if self._opening else self._pop()
        if q is None and (self._opening or (self._since_build and getattr(self, "_bg", None) is None)):
            # opening, or this batch used up after answers: the next batch is read in the background - never
            # while you wait on a keypress
            self._build_later()
        if q is None and getattr(self, "_bg", None) is not None:
            self.q = None
            self.question.setText("Finding the questions that sort the most…" if self._opening else
                                  "Finding more questions…")
            self.why.setText("")
            for w in (self.left, self.right, self.name, self.name_label, self.yes_b, self.no_b, self.skip_b,
                      self.full_b):
                w.setVisible(False)
            self.update_progress(0)
            return
        # more questions than one batch held, and it's running low - or answers have changed a lot since
        if (len(self._queue) < self.QUEUE_LOW and getattr(self, "_more", False)) or \
                self._since_build >= self.QUEUE_STALE:
            self._build_later()
        self.q = q
        total = max(self._total - self._since_build, len(self._queue) + (1 if q else 0))
        if q is not None:
            self._shown.append(q)
        if self.q is None:
            self.question.setText("All done - nothing left to ask right now")
            self.why.setText("Name any remaining groups in the main window. New questions appear as you name "
                             "people and add photos.")
            for w in (self.left, self.right, self.name, self.name_label, self.yes_b, self.no_b, self.skip_b,
                      self.full_b):
                w.setVisible(False)
            self.update_progress(0)
            return
        for w in (self.left, self.right, self.name, self.name_label, self.yes_b, self.no_b, self.skip_b,
                  self.full_b):
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
                              + "      Y yes · N no · Space skip · Z undo · F whole photo · click a small face to see it big"
                              + " · type a name to also name them")
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
        q = self.q
        self._since_build += 1
        self._touch(q.a.key, *((q.b.key,) if yes and q.b.kind != "person" else ()))
        if self.sess.answered % self.REGROUP_EVERY == 0:
            # new names -> new suggestions: (1.61) in the background, not under your keypresses (it used
            # to freeze the window for several seconds every fifth answer)
            self.win.schedule_regroup(now=True)
        self.next_question()

    def skip(self):
        if self.q:
            self.sess.skip(self.q)
            self.next_question()

    def undo(self):
        if self.sess.undo():
            # the question undone comes back first (its units are as they were again)
            if self._shown:
                if self.q is not None:
                    self._queue.insert(0, self.q)
                    self._shown.pop()
                if self._shown:
                    back = self._shown.pop()
                    self._touched.discard(back.a.key)
                    self._touched.discard(back.b.key)
                    self._queue.insert(0, back)
            self.next_question()
