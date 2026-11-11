"""Pieces of the photo viewer: the picture canvas, compare view, video helpers (low light,
markers), photo tools worker, tag box, filmstrip and the person picker."""
import html
import os
import time
import traceback
import numpy as np
import engine
import known
import store
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMenu, QPushButton, QSizePolicy, QSlider, QStyle, QVBoxLayout, QWidget)
from icons import app_icon
from ui_common import (
    log, PHOTO_COL, QVideoFrame, QVideoFrameFormat, round_pixmap, SUGGEST, TATTOO_COL, VIDEO_COL)


def qimage_to_rgb(img):
    """QImage -> RGB numpy array (a copy)."""
    img = img.convertToFormat(QImage.Format_RGB888)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    arr = np.frombuffer(img.constBits(), np.uint8, count=bpl * h).reshape(h, bpl)[:, :w * 3]
    return arr.reshape(h, w, 3).copy()


def rgb_to_qimage(rgb):
    rgb = np.ascontiguousarray(rgb)
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()


def frame_planes(frame):
    """A video frame's own brightness and colour planes (Y, Cb, Cr as uint8 arrays), straight from
    the decoder - every dark level kept, nothing lost converting to a picture first. None when the
    frame isn't in a plain YUV format (or can't be read, e.g. kept on the graphics card)."""
    try:
        f = QVideoFrame(frame)
        fmt = f.pixelFormat()
        F = QVideoFrameFormat.PixelFormat
        if fmt not in (F.Format_YUV420P, F.Format_YV12, F.Format_NV12, F.Format_NV21):
            return None
        if not f.map(QVideoFrame.MapMode.ReadOnly):
            return None
        try:
            w, h = f.width(), f.height()

            def plane(i, rows, cols):
                bpl = f.bytesPerLine(i)
                a = np.frombuffer(f.bits(i), np.uint8, count=bpl * rows).reshape(rows, bpl)
                return a[:, :cols].copy()
            Y = plane(0, h, w)
            if fmt in (F.Format_NV12, F.Format_NV21):
                uv = plane(1, h // 2, (w // 2) * 2).reshape(h // 2, w // 2, 2)
                U, V = (uv[:, :, 0], uv[:, :, 1]) if fmt == F.Format_NV12 else (uv[:, :, 1], uv[:, :, 0])
                U, V = U.copy(), V.copy()
            else:
                U, V = plane(1, h // 2, w // 2), plane(2, h // 2, w // 2)
                if fmt == F.Format_YV12:
                    U, V = V, U
            return Y, U, V
        finally:
            f.unmap()
    except Exception:
        return None


class ComparePane(QWidget):
    """One side of the duplicate comparison: the picture, and what it is."""

    def __init__(self, view):
        super().__init__()
        self.view = view
        self.img = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.pic = QLabel()
        self.pic.setAlignment(Qt.AlignCenter)
        self.pic.setMinimumSize(200, 150)
        self.pic.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.pic.setStyleSheet("background: #111; border-radius: 6px;")
        lay.addWidget(self.pic, 1)
        self.info = QLabel()
        self.info.setTextFormat(Qt.RichText)
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.info)
        row = QHBoxLayout()
        self.keep_b = QPushButton("Keep this one")
        self.keep_b.setToolTip("Keep this copy and mark the other copies in the set to discard")
        self.discard_b = QPushButton("Discard this one")
        row.addWidget(self.keep_b)
        row.addWidget(self.discard_b)
        row.addStretch(1)
        lay.addLayout(row)
        self.pid = None

    def show_photo(self, pid, path, w, h, size, mtime, best, discarded):
        self.pid = pid
        try:
            li = engine.load_image(path, max_side=2400)
            rgb = li.bgr[:, :, ::-1].copy()
            hh, ww = rgb.shape[:2]
            self.img = QImage(rgb.data, ww, hh, 3 * ww, QImage.Format_RGB888).copy()
        except Exception as e:
            self.img = None
            self.pic.setText(f"Couldn't open it:\n{e}")
        tags = []
        if best:
            tags.append("<span style='color:#3cbf78'><b>best copy</b></span>")
        if discarded:
            tags.append("<span style='color:#e05050'><b>to discard</b></span>")
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime)) if mtime else "?"
        self.info.setText(f"<b style='font-size:13pt'>{html.escape(os.path.basename(path))}</b>"
                          + ("  " + " · ".join(tags) if tags else "")
                          + f"<br>{w or '?'} × {h or '?'} px  ·  {(size or 0) / 1e6:.2f} MB  ·  {when}"
                          + f"<br><span style='color:gray'>{html.escape(os.path.dirname(path))}</span>")
        self.discard_b.setText("Restore this one" if discarded else "Discard this one")
        self.fit()

    def fit(self):
        if self.img is None:
            return
        dpr = self.devicePixelRatioF()
        pm = QPixmap.fromImage(self.img).scaled(int(self.pic.width() * dpr), int(self.pic.height() * dpr),
                                                Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pm.setDevicePixelRatio(dpr)
        self.pic.setPixmap(pm)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self.fit)


class CompareView(QWidget):
    """Two copies of the same picture side by side (V in the viewer): which one to keep."""

    def __init__(self, viewer):
        super().__init__()
        self.viewer = viewer
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        self.title = QLabel()
        self.title.setTextFormat(Qt.RichText)
        head.addWidget(self.title, 1)
        self.next_b = QPushButton("Next copy ▸")
        self.next_b.clicked.connect(self.next_copy)
        self.notdup_b = QPushButton("Not duplicates")
        self.notdup_b.setToolTip("These aren't the same picture: never show them as duplicates again")
        self.close_b = QPushButton("Close compare  (V / Esc)")
        self.close_b.clicked.connect(lambda: viewer.set_compare(False))
        for b in (self.next_b, self.notdup_b, self.close_b):
            head.addWidget(b)
        lay.addLayout(head)
        row = QHBoxLayout()
        self.left, self.right = ComparePane(self), ComparePane(self)
        row.addWidget(self.left, 1)
        row.addWidget(self.right, 1)
        lay.addLayout(row, 1)
        for pane in (self.left, self.right):
            pane.keep_b.clicked.connect(lambda _=False, p=pane: self.keep(p.pid))
            pane.discard_b.clicked.connect(lambda _=False, p=pane: self.discard(p.pid))
        self.notdup_b.clicked.connect(self.not_dup)
        self.cur, self.others, self.k = None, [], 0

    def load(self, pid, others, best):
        self.cur, self.others, self.best = pid, list(others), best
        self.k = min(self.k, max(0, len(self.others) - 1))
        self.refresh()

    def refresh(self):
        c = self.viewer.win.conn
        if not self.others:
            self.viewer.set_compare(False)
            return
        meta = {}
        for pid in (self.cur, self.others[self.k]):
            r = c.execute("SELECT id, path, width, height, size, mtime, discarded FROM photos WHERE id=?", (pid,)).fetchone()
            if r:
                meta[pid] = r
        for pane, pid in ((self.left, self.cur), (self.right, self.others[self.k])):
            r = meta.get(pid)
            if r:
                pane.show_photo(r[0], r[1], r[2], r[3], r[4], r[5], r[0] == self.best, bool(r[6]))
        n = len(self.others) + 1
        self.title.setText(f"<b>Compare</b> &nbsp; copy {self.k + 2} of {n} beside this one" if n > 2
                           else "<b>Compare</b> &nbsp; two copies of the same picture")
        self.next_b.setVisible(len(self.others) > 1)

    def next_copy(self):
        if self.others:
            self.k = (self.k + 1) % len(self.others)
            self.refresh()

    def keep(self, pid):
        self.viewer.win.keep_this_copy(pid)
        self.viewer.toast.setText("Kept that one - the other copies are marked to discard")
        self.viewer.set_compare(False)

    def discard(self, pid):
        c = self.viewer.win.conn
        win = self.viewer.win
        was = bool((c.execute("SELECT discarded FROM photos WHERE id=?", (pid,)).fetchone() or [0])[0])
        ok = win.dup_cache_valid()
        store.set_photos_discarded(c, [pid], not was)
        if not was:
            win.prune_dups([pid], ok)
        win.after_change(regroup=False, defer=True)
        self.viewer.toast.setText("Restored" if was else "Marked to discard (nothing is deleted)")
        self.refresh()

    def not_dup(self):
        other = self.others[self.k]
        store.not_duplicates(self.viewer.win.conn, self.cur, [self.cur, other])
        self.viewer.win.after_change(regroup=False, defer=True)
        self.others.pop(self.k)
        self.k = 0
        self.viewer.toast.setText("Not duplicates - noted")
        self.refresh()


class ToolWorker(QObject):
    """Runs a photo tool (upscale, colourize …) in the background, one at a time."""
    done = Signal(object, object)          # (result, error text)
    step = Signal(float)

    def __init__(self):
        super().__init__()
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.busy = False
        self.cancel = False

    def submit(self, fn):
        self.busy, self.cancel = True, False
        self.pool.submit(self._run, fn)

    def _run(self, fn):
        try:
            res, err = fn(self.step.emit, lambda: self.cancel), None
        except Exception as e:
            log(traceback.format_exc())
            res, err = None, str(e)
        self.busy = False
        self.done.emit(res, err)


class MarkSlider(QSlider):
    """The video position bar, with a start and an end marker: the section between them is
    shaded orange (enhanced copies, best frames and collages can use just that part)."""

    def __init__(self, *a):
        super().__init__(*a)
        self.marks = [None, None]            # seconds; the bar itself is in milliseconds

    def set_marks(self, a, b):
        self.marks = [a, b]
        self.update()

    def _x(self, v):
        from PySide6.QtWidgets import QStyleOptionSlider
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        groove = self.style().subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderGroove, self)
        handle = self.style().subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderHandle, self)
        span = groove.width() - handle.width()
        pos = QStyle.sliderPositionFromValue(self.minimum(), self.maximum(), int(v), max(1, span))
        return groove.x() + handle.width() / 2 + pos, groove

    def paintEvent(self, e):
        super().paintEvent(e)
        a, b = self.marks
        if (a is None and b is None) or self.maximum() <= self.minimum():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        xa, groove = self._x((a or 0) * 1000)
        xb, _ = self._x(b * 1000 if b is not None else self.maximum())
        cy = groove.center().y()
        p.fillRect(QRectF(xa, cy - 3, max(1.0, xb - xa), 6), QColor(245, 140, 30, 150))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(245, 140, 30))
        h = self.height()
        for x, on in ((xa, a is not None), (xb, b is not None)):
            if on:
                p.drawRect(QRectF(x - 1.5, 1, 3, h - 2))
                p.drawPolygon(QPolygonF([QPointF(x - 5, 0), QPointF(x + 5, 0), QPointF(x, 6)]))
        p.end()


class LowLightWorker(QObject):
    """Works on video frames in the background (one at a time, always the newest), so the
    window stays smooth while a dark video plays brightened."""
    done = Signal(object)

    def __init__(self):
        super().__init__()
        self.pool = ThreadPoolExecutor(max_workers=1)

    def submit(self, data, strength, level, dpr):
        self.pool.submit(self._run, data, strength, level, dpr)

    def _run(self, data, strength, level, dpr):
        try:
            if isinstance(data, tuple):           # the decoder's own planes
                out = engine.low_light_yuv(data[0], data[1], data[2], strength, level, fast=True)
            else:
                out = engine.low_light(data, strength, level, fast=True)
            img = rgb_to_qimage(out)
            img.setDevicePixelRatio(dpr)
        except Exception as e:
            log(f"low light: {e}")
            img = None
        self.done.emit(img)


class EnhancedView(QWidget):
    """The video while low-light recovery is on: each frame, brightened, drawn to fit."""

    def __init__(self):
        super().__init__()
        self.img = None
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_OpaquePaintEvent)

    def set_image(self, img):
        self.img = img
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0))
        if self.img is not None and not self.img.isNull():
            dpr = self.devicePixelRatioF()
            iw, ih = self.img.width() / dpr, self.img.height() / dpr
            k = min(self.width() / max(1, iw), self.height() / max(1, ih))
            w, h = iw * k, ih * k
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawImage(QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h), self.img)
        p.end()


class PhotoCanvas(QWidget):
    def __init__(self, viewer):
        super().__init__()
        self.viewer = viewer
        self.setMinimumSize(400, 300)
        self.setMouseTracking(True)
        self.hover = None
        self.drag_from = None   # drawing a tattoo box
        self.drag_to = None
        self.zoom = 1.0         # 1 = the whole photo fits; more = zoomed in
        self.pan = QPointF(0, 0)
        self.pan_from = None
        self.band_from = self.band_to = None    # dragging a box round several faces

    def image_rect(self):
        img = self.viewer.image
        if img is None or img.isNull():
            return QRectF()
        s = min(self.width() / img.width(), self.height() / img.height()) * self.zoom
        w, h = img.width() * s, img.height() * s
        # keep the photo on screen: no panning past its edges
        mx, my = max(0.0, (w - self.width()) / 2), max(0.0, (h - self.height()) / 2)
        self.pan = QPointF(min(mx, max(-mx, self.pan.x())), min(my, max(-my, self.pan.y())))
        return QRectF((self.width() - w) / 2 + self.pan.x(), (self.height() - h) / 2 + self.pan.y(), w, h)

    def reset_zoom(self):
        self.zoom, self.pan = 1.0, QPointF(0, 0)
        self.update()

    def zoom_by(self, factor, at=None):
        """Zoom in / out, keeping the point under the mouse (or the middle) where it is."""
        if self.viewer.image is None:
            return
        at = at if at is not None else QPointF(self.width() / 2, self.height() / 2)
        old = self.image_rect()
        if old.isEmpty():
            return
        rel = QPointF((at.x() - old.x()) / old.width(), (at.y() - old.y()) / old.height())
        self.zoom = min(16.0, max(1.0, self.zoom * factor))
        new = self.image_rect()
        self.pan += QPointF(at.x() - (new.x() + rel.x() * new.width()), at.y() - (new.y() + rel.y() * new.height()))
        self.viewer.zoom_changed()
        self.update()

    def actual_size(self):
        """1:1 pixels (or as close as the loaded picture allows)."""
        img = self.viewer.image
        if img is None:
            return
        fit = min(self.width() / img.width(), self.height() / img.height())
        real = getattr(self.viewer, "_full_w", 0) or img.width()
        target = max(1.0, real / (fit * img.width()))
        self.zoom_by(target / self.zoom)

    def wheelEvent(self, e):
        self.zoom_by(1.2 ** (e.angleDelta().y() / 120), e.position())     # mouse wheel zooms
        e.accept()

    def mouseDoubleClickEvent(self, e):
        if self.face_at(e.position()) is None:
            if self.zoom > 1.01:
                self.reset_zoom()
                self.viewer.zoom_changed()
            else:
                self.zoom_by(2.5, e.position())

    def box_rect(self, x, y, w, h, ir):
        return QRectF(ir.x() + x * ir.width(), ir.y() + y * ir.height(), w * ir.width(), h * ir.height())

    def face_rect(self, f, ir):
        return self.box_rect(f[1], f[2], f[3], f[4], ir)

    def face_at(self, pos, any_state=False):
        ir = self.image_rect()
        if self.viewer.boxes_hidden():
            return None                    # boxes switched off: clicks and right-clicks are just on the photo
        hits = [f for f in self.viewer.faces if (any_state or f[7] in (0, 1, 3, 4)) and self.face_rect(f, ir).contains(pos)]
        # boxes inside boxes: the smallest one is the one you mean
        return min(hits, key=lambda f: f[3] * f[4]) if hits else None

    def tattoo_at(self, pos):
        ir = self.image_rect()
        for t in self.viewer.tattoos:
            if self.box_rect(*t[1:5], ir).contains(pos):
                return t
        return None

    def drawing(self, e=None):
        # T / M buttons, or any time: Ctrl + drag = a face, Shift + drag = a tattoo
        return self.viewer.marking or (e is not None and e.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier))

    def mouseMoveEvent(self, e):
        if self.pan_from is not None:
            self.pan += e.position() - self.pan_from
            self.pan_from = e.position()
            self.update()
            return
        if self.drag_from is not None:
            self.drag_to = e.position()
            self.update()
            return
        if getattr(self, "band_from", None) is not None:
            self.band_to = e.position()
            self.update()
            return
        if self.drawing(e):
            self.setCursor(Qt.CrossCursor)
            return
        f = self.face_at(e.position())
        self.hover = f[0] if f else None
        self.setCursor(Qt.PointingHandCursor if f else Qt.ArrowCursor)
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            t = self.tattoo_at(e.position())
            if t:
                m = QMenu(self)
                m.addAction(f"Remove {t[5]}'s tattoo mark", lambda: self.viewer.remove_tattoo(t[0]))
                m.exec(self.mapToGlobal(e.position().toPoint()))
                return
            f = self.face_at(e.position(), any_state=True)
            if f:
                self.viewer.face_menu(f, self.mapToGlobal(e.position().toPoint()))
            return
        if self.drawing(e) and self.image_rect().contains(e.position()):
            self.drag_from = self.drag_to = e.position()
            return
        f = self.face_at(e.position())
        v = self.viewer
        if f and e.button() == Qt.LeftButton and e.modifiers() & Qt.AltModifier:
            v.toggle_selected(f)                    # Alt + click adds / takes away one face
            return
        if f:
            v.clear_selection(quiet=True)
            v.focus_face(f)
        else:
            self.setFocus()
            if self.zoom > 1.01 and not e.modifiers() & Qt.AltModifier or e.button() == Qt.MiddleButton:
                self.pan_from = e.position()        # zoomed in: drag to look around
                self.setCursor(Qt.ClosedHandCursor)
            elif e.button() == Qt.LeftButton and not v.boxes_hidden() and v.faces:
                self.band_from = self.band_to = e.position()     # drag a box round several faces

    def mouseReleaseEvent(self, e):
        if self.pan_from is not None:
            self.pan_from = None
            self.setCursor(Qt.ArrowCursor)
            return
        if getattr(self, "band_from", None) is not None:
            band = QRectF(self.band_from, e.position()).normalized()
            self.band_from = self.band_to = None
            self.update()
            if band.width() < 6 and band.height() < 6:
                self.viewer.clear_selection(quiet=True)       # just a click on the photo
                return
            ir = self.image_rect()
            # a face counts when most of its box is inside what you dragged
            hit = []
            for f in self.viewer.faces:
                if f[7] not in (0, 4):
                    continue
                rr = self.face_rect(f, ir)
                inter = rr.intersected(band)
                if rr.width() * rr.height() > 0 and inter.width() * inter.height() >= 0.5 * rr.width() * rr.height():
                    hit.append(f)
            self.viewer.select_faces(hit, add=bool(e.modifiers() & Qt.AltModifier))
            return
        if self.drag_from is None:
            return
        ir = self.image_rect()
        a, b = self.drag_from, e.position()
        self.drag_from = self.drag_to = None
        x0, x1 = sorted((a.x(), b.x()))
        y0, y1 = sorted((a.y(), b.y()))
        x0, y0 = max(x0, ir.left()), max(y0, ir.top())
        x1, y1 = min(x1, ir.right()), min(y1, ir.bottom())
        self.update()
        if x1 - x0 < 12 or y1 - y0 < 12:
            return
        self.viewer.box_drawn(((x0 - ir.x()) / ir.width(), (y0 - ir.y()) / ir.height(),
                               (x1 - x0) / ir.width(), (y1 - y0) / ir.height()),
                              shift=bool(e.modifiers() & Qt.ShiftModifier),
                              ctrl=bool(e.modifiers() & Qt.ControlModifier))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.viewer.align_legend()

    def draw_label(self, p, rr, label, col, below=True):
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(label) + 12
        y = rr.bottom() + 4 if below else rr.top() - fm.height() - 10
        lr = QRectF(rr.center().x() - tw / 2, y, tw, fm.height() + 6)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 170))
        p.drawRoundedRect(lr, 5, 5)
        p.setPen(col)
        p.drawText(lr, Qt.AlignCenter, label)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        ir = self.image_rect()
        if self.viewer.image is None:
            p.drawText(self.rect(), Qt.AlignCenter, self.viewer.error or "Loading…")
            return
        p.drawImage(ir, self.viewer.image)
        # tattoos: your marks (solid purple) and matches to check (dashed orange)
        for tid, x, y, w, h, name, src in ([] if self.viewer.hide_boxes else self.viewer.tattoos):
            rr = self.box_rect(x, y, w, h, ir)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(0, 0, 0, 140), 4))
            p.drawRoundedRect(rr, 4, 4)
            p.setPen(QPen(TATTOO_COL, 2))
            p.drawRoundedRect(rr, 4, 4)
            self.draw_label(p, rr, f"{name} · tattoo", TATTOO_COL, below=False)
        for person_id, name, x, y, w, h, inl in ([] if self.viewer.hide_boxes else self.viewer.tsugg):
            rr = self.box_rect(x, y, w, h, ir)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(0, 0, 0, 140), 5))
            p.drawRoundedRect(rr, 4, 4)
            p.setPen(QPen(SUGGEST, 3, Qt.DashLine))
            p.drawRoundedRect(rr, 4, 4)
            self.draw_label(p, rr, f"{name}'s tattoo?", SUGGEST, below=False)
        if self.viewer.pending_tattoo:
            rr = self.box_rect(*self.viewer.pending_tattoo, ir)
            p.setBrush(QColor(170, 110, 240, 40))
            p.setPen(QPen(TATTOO_COL, 2, Qt.DashLine))
            p.drawRoundedRect(rr, 4, 4)
            self.draw_label(p, rr, "Whose tattoo?", TATTOO_COL, below=False)
        if self.viewer.pending_face:
            rr = self.box_rect(*self.viewer.pending_face, ir)
            p.setBrush(QColor(255, 255, 255, 40))
            p.setPen(QPen(QColor(255, 255, 255), 2, Qt.DashLine))
            p.drawRoundedRect(rr, 6, 6)
            self.draw_label(p, rr, "Who is this?", QColor(255, 255, 255))
        if self.drag_from is not None and self.drag_to is not None:
            mods = QApplication.keyboardModifiers()
            face = (self.viewer.marking and self.viewer.mark_kind == "face" and not mods & Qt.ShiftModifier) \
                or bool(mods & Qt.ControlModifier)
            p.setBrush(QColor(255, 255, 255, 40) if face else QColor(170, 110, 240, 40))
            p.setPen(QPen(QColor(255, 255, 255) if face else TATTOO_COL, 2, Qt.DashLine))
            p.drawRect(QRectF(self.drag_from, self.drag_to).normalized())
        if getattr(self, "band_from", None) is not None and self.band_to is not None:
            p.setBrush(QColor(56, 189, 248, 45))
            p.setPen(QPen(QColor(56, 189, 248), 1.5, Qt.DashLine))
            p.drawRect(QRectF(self.band_from, self.band_to).normalized())
        sel = self.viewer.sel_faces
        for f in ([] if self.viewer.boxes_hidden() else self.viewer.faces):
            fid, x, y, w, h, name, confirmed, ignored = f[:8]
            if fid in sel:                          # picked with a drag: bright blue, shaded
                rr = self.face_rect(f, ir)
                p.setBrush(QColor(56, 189, 248, 60))
                p.setPen(QPen(QColor(0, 0, 0, 150), 6))
                p.drawRoundedRect(rr, 6, 6)
                p.setPen(QPen(QColor(56, 189, 248), 4))
                p.drawRoundedRect(rr, 6, 6)
                self.draw_label(p, rr, (name + " · selected") if name else "Selected", QColor(56, 189, 248))
                continue
            focus = fid == self.viewer.focus_id
            if ignored in (1, 3, 4) and not focus:
                rr = self.face_rect(f, ir)
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(QColor(160, 160, 160, 170), 1.5, Qt.DotLine))
                p.drawRoundedRect(rr, 6, 6)
                continue
            rr = self.face_rect(f, ir)
            if ignored == 2:
                col, label = QColor(220, 70, 70), "Discard"
            elif ignored in (1, 3, 4):
                col, label = QColor(150, 150, 150), {1: "Ignored", 3: "Not a face", 4: "Probably not a face"}[ignored]
            elif name:
                col = QColor(60, 200, 120) if confirmed else SUGGEST
                label = name + ("" if confirmed else "?")
            else:
                col, label = QColor(255, 255, 255), "Who is this?"
            width = 5 if focus else (4 if fid == self.hover else 2)
            p.setBrush(Qt.NoBrush)
            if focus:  # glow around the face you're working on
                glow = QColor(col)
                glow.setAlpha(90)
                p.setPen(QPen(glow, 14))
                p.drawRoundedRect(rr, 8, 8)
            p.setPen(QPen(QColor(0, 0, 0, 140), width + 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rr, 6, 6)
            p.setPen(QPen(col, width, Qt.SolidLine if (confirmed or not name) else Qt.DashLine))
            p.drawRoundedRect(rr, 6, 6)
            self.draw_label(p, rr, label, col)


class TagEdit(QLineEdit):
    """A text bar that predicts names as you type.

    Tab        complete the first (or highlighted) suggestion
    ↑ / ↓      pick another suggestion
    Enter      apply what's typed
    Ctrl+Enter apply and go to the next photo
    Esc        close the suggestions, or leave the bar
    PgUp/PgDn  previous / next photo without leaving the bar"""

    def __init__(self, viewer, kind, placeholder):
        super().__init__()
        self.viewer, self.kind = viewer, kind
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self.setMinimumWidth(220)
        self.sugg = []          # [(name, note)]
        self.row = 0
        self.popup = QListWidget(viewer)   # an overlay inside the window (never steals focus)
        self.popup.setFocusPolicy(Qt.NoFocus)
        self.popup.setUniformItemSizes(True)
        self.popup.setStyleSheet("QListWidget { border: 1px solid palette(mid); }")
        self.popup.hide()
        self.popup.itemClicked.connect(lambda it: self.accept_suggestion(self.popup.row(it), apply=True))
        self.new_label = QLabel()
        self.new_label.setStyleSheet(f"color: {SUGGEST.name()}; font-weight: bold;")
        self.new_label.setFixedWidth(118)
        self.textEdited.connect(self.update_suggestions)

    # ---- suggestions
    def candidates(self):
        return self.viewer.candidates(self.kind)

    @staticmethod
    def rank(text, cands):
        t = text.casefold().strip()
        if not t:
            return list(cands)
        starts, words, contains = [], [], []
        for name, note in cands:
            n = name.casefold()
            if n.startswith(t):
                starts.append((name, note))
            elif any(w.startswith(t) for w in n.replace("-", " ").split()):
                words.append((name, note))
            elif t in n:
                contains.append((name, note))
        return starts + words + contains

    def update_suggestions(self, *_):
        text = self.text()
        self.sugg = self.rank(text, self.candidates())[:8] if text.strip() else []
        self.row = 0
        exact = any(n.casefold() == text.strip().casefold() for n, _ in self.candidates())
        self.new_label.setText(("new person" if self.kind == "people" else "new folder")
                               if text.strip() and not exact else "")
        self.show_popup()

    def show_popup(self):
        self.popup.clear()
        if not self.sugg or not self.hasFocus():
            self.popup.hide()
            return
        for i, (name, note) in enumerate(self.sugg):
            it = QListWidgetItem(name + (f"     ({note})" if note else ""))
            if i == 0:
                it.setText(it.text() + "     Tab ↹")
            self.popup.addItem(it)
        self.popup.setCurrentRow(self.row)
        rh = self.popup.sizeHintForRow(0) or 22
        h = rh * len(self.sugg) + 6
        top_left = self.mapTo(self.viewer, QPoint(0, 0))
        self.popup.setGeometry(top_left.x(), top_left.y() - h - 2, max(self.width(), 260), h)
        self.popup.show()
        self.popup.raise_()

    def accept_suggestion(self, row=None, apply=False):
        if not self.sugg:
            return False
        row = self.row if row is None else row
        self.setText(self.sugg[row][0])
        self.sugg = []
        self.popup.hide()
        self.new_label.setText("")
        if apply:
            self.apply()
        return True

    def apply(self, advance=False):
        text = " ".join(self.text().split())
        self.popup.hide()
        if text:
            # use the existing spelling if it matches someone/something already known
            match = next((n for n, _ in self.candidates() if n.casefold() == text.casefold()), None)
            self.viewer.apply_bar(self.kind, match or text, advance)
        self.clear()
        self.new_label.setText("")

    # ---- keys
    def event(self, e):
        if e.type() == QEvent.KeyPress and e.key() in (Qt.Key_Tab, Qt.Key_Backtab):
            if e.key() == Qt.Key_Tab and self.sugg:
                self.accept_suggestion()
            return True          # Tab completes; never moves focus out of the bar
        return super().event(e)

    def keyPressEvent(self, e):
        k, mods = e.key(), e.modifiers()
        if k in (Qt.Key_Return, Qt.Key_Enter):
            self.apply(advance=bool(mods & Qt.ControlModifier))
            if not (mods & Qt.ControlModifier):
                self.viewer.canvas.setFocus()
            return
        if k == Qt.Key_Escape:
            if self.popup.isVisible():
                self.popup.hide()
            elif self.kind == "people" and (self.viewer.pending_tattoo or self.viewer.pending_face):
                self.clear()
                self.viewer.cancel_tattoo()
            else:
                self.clear()
                self.new_label.setText("")
                self.viewer.canvas.setFocus()
            return
        if k in (Qt.Key_Down, Qt.Key_Up) and self.sugg:
            self.row = (self.row + (1 if k == Qt.Key_Down else -1)) % len(self.sugg)
            self.popup.setCurrentRow(self.row)
            return
        if k in (Qt.Key_PageDown, Qt.Key_PageUp):
            self.viewer.go(1 if k == Qt.Key_PageDown else -1)
            return
        super().keyPressEvent(e)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.update_suggestions()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.popup.hide()


def chip(text, tip, on_click):
    b = QPushButton(f"{text}  ✕")
    b.setToolTip(tip)
    b.setFocusPolicy(Qt.NoFocus)
    b.setStyleSheet("QPushButton { border-radius: 10px; padding: 2px 10px; }")
    b.clicked.connect(on_click)
    return b


class Filmstrip(QWidget):
    """Along the bottom of the viewer: the photo you're on in the middle, the ones before it to
    the left and after it to the right. Click one to jump to it; the mouse wheel browses."""

    def __init__(self, viewer):
        super().__init__()
        self.viewer = viewer
        self.setFixedHeight(104)
        self.setMouseTracking(True)
        self.hits = []
        viewer.win.hires.ready.connect(self.thumb_ready)

    def thumb_ready(self, _fid):
        # a bound method (not a lambda), so Qt drops the connection when the strip is deleted
        try:
            self.update()
        except RuntimeError:          # already gone
            self.detach()

    def detach(self):
        try:
            self.viewer.win.hires.ready.disconnect(self.thumb_ready)
        except (RuntimeError, TypeError):
            pass

    def thumb(self, i):
        fid, path = self.viewer.items[i]
        info = self.viewer.win.photo_info(path)
        pid = info[0]
        if not pid:
            return None
        img = self.viewer.win.hires.get(-pid, path, info[2] if info[1] else None, 128)
        return img

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(0, 0, 0, 40))
        items, pos = self.viewer.items, self.viewer.pos
        if not items:
            return
        big, small, gap = 88, 66, 8
        cx = self.width() / 2
        self.hits = []
        slots = [(pos, cx - big / 2, big)]
        x = cx - big / 2 - gap
        i = pos - 1
        while i >= 0 and x > -small:
            slots.append((i, x - small, small))
            x -= small + gap
            i -= 1
        x = cx + big / 2 + gap
        i = pos + 1
        while i < len(items) and x < self.width() + small:
            slots.append((i, x, small))
            x += small + gap
            i += 1
        for i, x, size in slots:
            y = (self.height() - size) / 2
            r = QRectF(x, y, size, size)
            img = self.thumb(i)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(60, 60, 64))
            p.drawRoundedRect(r, 6, 6)
            if img is not None and not img.isNull():
                s_ = min(size / img.width(), size / img.height())
                w, h = img.width() * s_, img.height() * s_
                path = QPainterPath()
                path.addRoundedRect(r, 6, 6)
                p.save()
                p.setClipPath(path)
                p.drawImage(QRectF(x + (size - w) / 2, y + (size - h) / 2, w, h), img)
                p.restore()
            if i != pos:
                p.setBrush(QColor(0, 0, 0, 70))
                p.drawRoundedRect(r, 6, 6)
            video = engine.is_video(items[i][1])
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(VIDEO_COL if video else PHOTO_COL, 3 if i == pos else 1.5))
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 6, 6)
            self.hits.append((r, i))

    def mousePressEvent(self, e):
        for r, i in self.hits:
            if r.contains(e.position()):
                self.viewer.go(i - self.viewer.pos)
                return

    def wheelEvent(self, e):
        self.viewer.go(-1 if e.angleDelta().y() > 0 else 1)
        e.accept()


class PersonPicker(QDialog):
    """Who is this? Type a name - the list below shows everyone you have, with their face (and
    where they're saved), narrowing down as you type, so "Ashley 1" and "Ashley 2" are easy to
    tell apart. Enter takes the highlighted person, or makes a new one from what you typed."""

    def __init__(self, win, title, current=""):
        super().__init__(win)
        self.win = win
        self.setWindowTitle(title)
        self.resize(640, 560)
        lay = QVBoxLayout(self)
        head = QLabel(f"<b>{html.escape(title)}</b>")
        f = head.font()
        f.setPointSizeF(f.pointSizeF() * 1.2)
        head.setFont(f)
        lay.addWidget(head)
        self.edit = QLineEdit(current)
        self.edit.setPlaceholderText("Type a name - ↑ ↓ pick from the list · Enter takes the highlighted one")
        self.edit.setClearButtonEnabled(True)
        ef = self.edit.font()
        ef.setPointSizeF(ef.pointSizeF() * 1.2)
        self.edit.setFont(ef)
        self.edit.setMinimumHeight(34)
        lay.addWidget(self.edit)
        body = QHBoxLayout()
        self.list = QListWidget()
        self.list.setIconSize(QSize(56, 56))
        self.list.setUniformItemSizes(True)
        self.list.setSpacing(1)
        body.addWidget(self.list, 1)
        side = QVBoxLayout()
        self.big = QLabel()
        self.big.setFixedSize(180, 180)
        self.big.setAlignment(Qt.AlignCenter)
        self.big.setStyleSheet("border-radius: 10px; background: rgba(127,127,127,0.15);")
        side.addWidget(self.big)
        self.more = QHBoxLayout()
        self.more.setSpacing(4)
        side.addLayout(self.more)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setFixedWidth(180)
        side.addWidget(self.info)
        side.addStretch(1)
        body.addLayout(side)
        lay.addLayout(body, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.take)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.result_name = None
        # everyone: library people (with their best face, photo count and folder) + remembered names
        c = win.conn
        paths = store.category_paths(c)
        self.people = []
        rep = {pid: fid for pid, fid, _ in c.execute(        # their best confirmed face (else a suggested one)
            """SELECT person_id, id, MAX(confirmed * 1000000 + COALESCE(px * score, 0)) FROM faces WHERE ignored=0
               AND person_id IS NOT NULL GROUP BY person_id""")}
        counts = dict(c.execute("""SELECT person_id, COUNT(DISTINCT photo_id) FROM (
                                       SELECT person_id, photo_id FROM faces WHERE ignored=0 AND person_id IS NOT NULL
                                       UNION SELECT person_id, photo_id FROM photo_people) GROUP BY person_id"""))
        seen = set()
        hidden = win.hidden_names()
        for pid, name, cat, used in c.execute("SELECT id, name, category_id, last_used FROM persons"):
            if name.casefold() in hidden:
                seen.add(name.casefold())
                continue
            where = " › ".join(paths.get(cat, [])) if cat else ""
            self.people.append((name, pid, rep.get(pid), counts.get(pid, 0), where, used or 0))
            seen.add(name.casefold())
        try:
            for n in known.names():
                if n.casefold() not in seen:
                    self.people.append((n, None, None, 0, "remembered - no photos here now", -1))
        except Exception:
            pass
        self.people.sort(key=lambda r: (-r[5], r[0].casefold()))
        self.edit.textChanged.connect(self.fill)
        self.list.currentRowChanged.connect(self.show_person)
        self.list.itemDoubleClicked.connect(lambda _it: self.take())
        self.edit.installEventFilter(self)
        self.list.installEventFilter(self)
        self.fill()
        self.edit.selectAll()
        self.edit.setFocus(Qt.OtherFocusReason)

    def showEvent(self, e):
        super().showEvent(e)
        # opened from a right-click menu, Windows may not hand this window the keyboard: take it,
        # and put the cursor in the name box, so typing straight away works
        def grab():
            self.raise_()
            self.activateWindow()
            self.edit.setFocus(Qt.OtherFocusReason)
        grab()
        QTimer.singleShot(0, grab)
        QTimer.singleShot(150, grab)
        self.edit.setFocus()

    def eventFilter(self, obj, ev):
        # ↑ ↓ move through the list while you keep typing
        if obj is self.edit and ev.type() == QEvent.KeyPress and ev.key() in (Qt.Key_Down, Qt.Key_Up,
                                                                                Qt.Key_PageDown, Qt.Key_PageUp):
            step = {Qt.Key_Down: 1, Qt.Key_Up: -1, Qt.Key_PageDown: 6, Qt.Key_PageUp: -6}[ev.key()]
            n = self.list.count()
            if n:
                self.list.setCurrentRow(max(0, min(n - 1, self.list.currentRow() + step)))
            return True
        # typing while the list has the keyboard goes into the name box
        if obj is self.list and ev.type() == QEvent.KeyPress and ev.text() and ev.text().isprintable() \
                and not ev.modifiers() & (Qt.ControlModifier | Qt.AltModifier):
            self.edit.setFocus(Qt.OtherFocusReason)
            self.edit.insert(ev.text())
            return True
        return super().eventFilter(obj, ev)

    def fill(self, *_):
        text = " ".join(self.edit.text().split())
        ranked = TagEdit.rank(text, [(r[0], r) for r in self.people])
        self.list.clear()
        exact = any(n.casefold() == text.casefold() for n, _ in ranked)
        if text and not exact:
            it = QListWidgetItem(app_icon("person", "orange"), f"New person:  {text}" +
                                 ("      (↑ to pick this)" if ranked else ""))
            it.setData(Qt.UserRole, ("new", text))
            f = it.font()
            f.setBold(True)
            it.setFont(f)
            self.list.addItem(it)
        for name, r in ranked[:300]:
            _, pid, fid, n, where, _u = r
            icon = QIcon(round_pixmap(self.win.face_pixmap(fid), 56, 10)) if fid else app_icon("person", "grey")
            extra = "  ·  ".join(x for x in (f"{n:,} photo{'s' if n != 1 else ''}" if pid else "", where) if x)
            it = QListWidgetItem(icon, f"{name}\n{extra}" if extra else name)
            it.setData(Qt.UserRole, ("person", r))
            self.list.addItem(it)
        if self.list.count():
            # the typed name itself if it's someone, else the first match (or "New person")
            people_rows = [i for i in range(self.list.count()) if self.list.item(i).data(Qt.UserRole)[0] == "person"]
            row = next((i for i in people_rows
                        if self.list.item(i).data(Qt.UserRole)[1][0].casefold() == text.casefold()),
                       people_rows[0] if people_rows and text else 0)
            self.list.setCurrentRow(row)
        else:
            self.show_person(-1)

    def show_person(self, row):
        while self.more.count():
            w = self.more.takeAt(0).widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        it = self.list.item(row) if row >= 0 else None
        data = it.data(Qt.UserRole) if it else None
        if not data or data[0] == "new":
            self.big.setPixmap(app_icon("person", "orange" if data else "grey").pixmap(120, 120))
            self.info.setText(f"<b>{html.escape(data[1])}</b><br>a new person" if data else "")
            return
        name, pid, fid, n, where, _u = data[1]
        if fid:
            self.big.setPixmap(round_pixmap(self.win.face_pixmap(fid), 180, 14))
            for (other,) in self.win.conn.execute(
                    """SELECT id FROM faces WHERE person_id=? AND ignored=0 AND id<>?
                       ORDER BY confirmed DESC, px*score DESC LIMIT 4""", (pid, fid)):
                lb = QLabel()
                lb.setPixmap(round_pixmap(self.win.face_pixmap(other), 42, 6))
                self.more.addWidget(lb)
            self.more.addStretch(1)
        else:
            self.big.setPixmap(app_icon("person", "grey").pixmap(120, 120))
        self.info.setText(f"<b>{html.escape(name)}</b><br>" + (f"{n:,} photo{'s' if n != 1 else ''}"
                          if pid else "") + (f"<br>Saved in: {html.escape(where)}" if where else ""))

    def take(self):
        it = self.list.currentItem()
        text = " ".join(self.edit.text().split())
        data = it.data(Qt.UserRole) if it else None
        if data and data[0] == "person":
            self.result_name = data[1][0]
        elif text:
            self.result_name = text
        if self.result_name:
            self.accept()
