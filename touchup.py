"""Touch-up brush: paint over a spot, scratch, date stamp or stray object and it's filled in from
what's around it (photo_tools.heal). One photo at a time, from the viewer's Tools menu; the
result goes back to the viewer as a preview, and is only ever saved as a copy next to the original.
"""
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QPainterPath, QPen, QShortcut
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout,
                               QWidget)

import cv2

import photo_tools
from viewer_parts import ToolWorker

UNDO_STEPS = 30


def to_qimage(rgb):
    rgb = np.ascontiguousarray(rgb)
    return QImage(rgb.data, rgb.shape[1], rgb.shape[0], 3 * rgb.shape[1], QImage.Format_RGB888).copy()


class BrushCanvas(QWidget):
    """The photo, zoomable (wheel) and movable (right- or middle-drag, or Space + drag), with the
    brush strokes drawn over it in red."""

    def __init__(self, dlg):
        super().__init__()
        self.dlg = dlg
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(400, 300)
        self.zoom = None                     # None = fit the window
        self.center = None                   # image point at the middle of the view
        self.strokes = []                    # [[(x, y) image px, ...], ...] with the radius of each
        self.radii = []
        self.cur = None
        self.pan_from = None
        self.mouse = None
        self.space = False
        self.show_before = False

    # -- geometry
    def fit_scale(self):
        h, w = self.dlg.rgb.shape[:2]
        return min(self.width() / w, self.height() / h)

    def scale(self):
        return self.zoom or self.fit_scale()

    def origin(self):
        h, w = self.dlg.rgb.shape[:2]
        s = self.scale()
        cx, cy = self.center if self.center and self.zoom else (w / 2, h / 2)
        return self.width() / 2 - cx * s, self.height() / 2 - cy * s

    def to_image(self, p):
        ox, oy = self.origin()
        s = self.scale()
        return (p.x() - ox) / s, (p.y() - oy) / s

    def radius_px(self):
        """Brush radius in image pixels (the slider is in screen pixels, so it feels the same at any zoom)."""
        return max(1.0, self.dlg.size.value() / 2 / self.scale())

    # -- drawing
    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(30, 30, 30))
        ox, oy = self.origin()
        s = self.scale()
        img = self.dlg.before_q if self.show_before else self.dlg.qimg
        h, w = self.dlg.rgb.shape[:2]
        p.setRenderHint(QPainter.SmoothPixmapTransform, s < 2)
        p.drawImage(QRectF(ox, oy, w * s, h * s), img)
        if not self.show_before:
            p.setRenderHint(QPainter.Antialiasing)
            for pts, r in list(zip(self.strokes, self.radii)) + ([(self.cur, self.cur_r)] if self.cur else []):
                pen = QPen(QColor(255, 40, 40, 130), max(1.0, 2 * r * s), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
                p.setPen(pen)
                path = QPainterPath(QPointF(ox + pts[0][0] * s, oy + pts[0][1] * s))
                for x, y in pts[1:] or pts[:1]:
                    path.lineTo(ox + x * s + (0.01 if len(pts) == 1 else 0), oy + y * s)
                p.drawPath(path)
        if self.mouse is not None and not self.pan_from and not self.space:
            r = self.dlg.size.value() / 2
            p.setPen(QPen(QColor(255, 255, 255, 220), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(self.mouse, r, r)
            p.setPen(QPen(QColor(0, 0, 0, 160), 1))
            p.drawEllipse(self.mouse, r + 1.5, r + 1.5)

    # -- mouse
    def mousePressEvent(self, e):
        if self.dlg.busy():
            return
        if e.button() in (Qt.RightButton, Qt.MiddleButton) or (e.button() == Qt.LeftButton and self.space):
            self.pan_from = (e.position(), self.center or self._mid())
            self.setCursor(Qt.ClosedHandCursor)
            return
        if e.button() == Qt.LeftButton:
            self.cur = [self.to_image(e.position())]
            self.cur_r = self.radius_px()
            self.update()

    def _mid(self):
        h, w = self.dlg.rgb.shape[:2]
        return (w / 2, h / 2)

    def mouseMoveEvent(self, e):
        self.mouse = e.position()
        if self.pan_from:
            if self.zoom is None:
                self.zoom = self.fit_scale()
            (p0, c0) = self.pan_from
            s = self.scale()
            self.center = (c0[0] - (e.position().x() - p0.x()) / s, c0[1] - (e.position().y() - p0.y()) / s)
        elif self.cur is not None:
            pt = self.to_image(e.position())
            last = self.cur[-1]
            if abs(pt[0] - last[0]) + abs(pt[1] - last[1]) >= 0.5:
                self.cur.append(pt)
        self.update()

    def mouseReleaseEvent(self, e):
        if self.pan_from:
            self.pan_from = None
            self.setCursor(Qt.OpenHandCursor if self.space else Qt.BlankCursor)
            return
        if self.cur is not None and e.button() == Qt.LeftButton:
            self.strokes.append(self.cur)
            self.radii.append(self.cur_r)
            self.cur = None
            self.update()
            if self.dlg.auto.isChecked():
                self.dlg.fill()

    def leaveEvent(self, e):
        self.mouse = None
        self.update()

    def enterEvent(self, e):
        self.setCursor(Qt.BlankCursor)

    def wheelEvent(self, e):
        steps = e.angleDelta().y() / 120
        if e.modifiers() & Qt.ShiftModifier:            # Shift + wheel: brush size
            self.dlg.size.setValue(int(self.dlg.size.value() * (1.15 ** steps)) + (1 if steps > 0 else -1))
            self.update()
            return
        before = self.to_image(e.position())
        s = self.scale() * (1.25 ** steps)
        s = max(self.fit_scale() * 0.5, min(16.0, s))
        self.zoom = s
        # keep the point under the mouse where it is
        self.center = (before[0] - (e.position().x() - self.width() / 2) / s,
                       before[1] - (e.position().y() - self.height() / 2) / s)
        self.update()

    def mask(self):
        h, w = self.dlg.rgb.shape[:2]
        m = np.zeros((h, w), np.uint8)
        for pts, r in zip(self.strokes, self.radii):
            a = np.round(np.array(pts)).astype(np.int32)
            t = max(1, int(round(2 * r)))
            if len(a) == 1:
                cv2.circle(m, tuple(int(v) for v in a[0]), max(1, int(round(r))), 255, -1, cv2.LINE_AA)
            else:
                cv2.polylines(m, [a.reshape(-1, 1, 2)], False, 255, t, cv2.LINE_AA)
        return m


class TouchUpDialog(QDialog):
    def __init__(self, parent, rgb, title=""):
        super().__init__(parent)
        self.setWindowTitle("Touch-up brush" + (f" - {title}" if title else ""))
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self.rgb = rgb
        self.first = rgb
        self.qimg = to_qimage(rgb)
        self.before_q = self.qimg
        self.undo, self.redo = [], []
        self.fills = 0
        self.worker = ToolWorker()
        self.worker.done.connect(self._filled)

        lay = QVBoxLayout(self)
        tip = QLabel("Paint over what should go - a spot, a scratch, a date stamp, a stray hair - and let go: it's "
                     "filled in from what's around it. Paint a little past its edges. Wheel = zoom, right-drag = move "
                     "around, Shift + wheel or [ ] = brush size, hold \\ to see the photo before.")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: gray;")
        lay.addWidget(tip)
        self.canvas = BrushCanvas(self)
        lay.addWidget(self.canvas, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel("Brush"))
        self.size = QSlider(Qt.Horizontal)
        self.size.setRange(4, 300)
        self.size.setValue(30)
        self.size.setFixedWidth(180)
        self.size.valueChanged.connect(lambda v: self.canvas.update())
        row.addWidget(self.size)
        self.auto = QCheckBox("Fill when I let go")
        self.auto.setChecked(True)
        self.auto.setToolTip("Off: paint several strokes, then press Fill (Enter)")
        row.addWidget(self.auto)
        self.fill_b = QPushButton("Fill")
        self.fill_b.setToolTip("Fill what's painted (Enter)")
        self.fill_b.clicked.connect(self.fill)
        row.addWidget(self.fill_b)
        self.clear_b = QPushButton("Clear painting")
        self.clear_b.clicked.connect(self.clear_strokes)
        row.addWidget(self.clear_b)
        self.undo_b = QPushButton("Undo")
        self.undo_b.setToolTip("Ctrl+Z")
        self.undo_b.clicked.connect(self.do_undo)
        row.addWidget(self.undo_b)
        self.redo_b = QPushButton("Redo")
        self.redo_b.setToolTip("Ctrl+Y")
        self.redo_b.clicked.connect(self.do_redo)
        row.addWidget(self.redo_b)
        self.before_b = QPushButton("Hold: before")
        self.before_b.setToolTip("Hold to see the photo as it was (or hold \\)")
        self.before_b.pressed.connect(lambda: self._before(True))
        self.before_b.released.connect(lambda: self._before(False))
        row.addWidget(self.before_b)
        self.fit_b = QPushButton("Fit")
        self.fit_b.clicked.connect(self.fit)
        row.addWidget(self.fit_b)
        row.addStretch()
        self.status = QLabel("")
        self.status.setStyleSheet("color: gray;")
        row.addWidget(self.status)
        self.done_b = QPushButton("Done")
        self.done_b.setDefault(False)
        self.done_b.setAutoDefault(False)
        self.done_b.setToolTip("Back to the photo, with the touch-ups as a preview (save it as a copy there)")
        self.done_b.clicked.connect(self.accept)
        row.addWidget(self.done_b)
        cancel = QPushButton("Cancel")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        for b in (self.fill_b, self.clear_b, self.undo_b, self.redo_b, self.before_b, self.fit_b):
            b.setAutoDefault(False)
        lay.addLayout(row)
        QShortcut(QKeySequence.Undo, self, self.do_undo)
        QShortcut(QKeySequence("Ctrl+Y"), self, self.do_redo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, self.do_redo)
        QShortcut(QKeySequence("["), self, lambda: self.size.setValue(int(self.size.value() / 1.2)))
        QShortcut(QKeySequence("]"), self, lambda: self.size.setValue(int(self.size.value() * 1.2) + 1))
        QShortcut(QKeySequence("Return"), self, self.fill)
        QShortcut(QKeySequence("Enter"), self, self.fill)
        QShortcut(QKeySequence("F"), self, self.fit)
        scr = parent.screen().availableGeometry() if parent and parent.screen() else None
        if scr:
            self.resize(int(scr.width() * 0.85), int(scr.height() * 0.85))
        else:
            self.resize(1200, 850)
        self._buttons()

    # -- state
    def busy(self):
        return self.worker.busy

    def _buttons(self):
        busy = self.busy()
        self.undo_b.setEnabled(bool(self.undo) and not busy)
        self.redo_b.setEnabled(bool(self.redo) and not busy)
        self.fill_b.setEnabled(bool(self.canvas.strokes) and not busy)
        self.clear_b.setEnabled(bool(self.canvas.strokes) and not busy)
        self.done_b.setEnabled(not busy)

    def _before(self, on):
        self.canvas.show_before = on
        self.canvas.update()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Backslash and not e.isAutoRepeat():
            self._before(True)
        elif e.key() == Qt.Key_Space and not e.isAutoRepeat():
            self.canvas.space = True
            self.canvas.setCursor(Qt.OpenHandCursor)
        elif e.key() == Qt.Key_Escape and self.busy():
            self.worker.cancel = True
        else:
            super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key_Backslash and not e.isAutoRepeat():
            self._before(False)
        elif e.key() == Qt.Key_Space and not e.isAutoRepeat():
            self.canvas.space = False
            self.canvas.setCursor(Qt.BlankCursor)
        else:
            super().keyReleaseEvent(e)

    def fit(self):
        self.canvas.zoom = None
        self.canvas.center = None
        self.canvas.update()

    def clear_strokes(self):
        self.canvas.strokes, self.canvas.radii = [], []
        self.canvas.update()
        self._buttons()

    # -- filling
    def fill(self):
        if self.busy() or not self.canvas.strokes:
            return
        mask = self.canvas.mask()
        src = self.rgb
        self.status.setText("Filling…  (Esc to stop)")
        self._buttons()
        self.fill_b.setEnabled(False)
        self.worker.submit(lambda step, cancelled: (photo_tools.heal(src, mask, cancelled), src))

    def _filled(self, res, err):
        self.clear_strokes()
        if res is None:
            self.status.setText("Stopped" if err and "cancel" in err else f"Couldn't fill that: {err}")
            self._buttons()
            return
        out, src = res
        if src is not self.rgb:
            return
        self._push(out)
        self.fills += 1
        self.status.setText(f"{self.fills} fill{'s' if self.fills != 1 else ''}")
        self._buttons()

    def _push(self, out):
        # undo keeps only the part that changed
        diff = np.any(out != self.rgb, axis=2)
        if diff.any():
            ys, xs = np.nonzero(diff)
            y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
            self.undo.append((y0, y1, x0, x1, self.rgb[y0:y1, x0:x1].copy(), out[y0:y1, x0:x1].copy()))
            del self.undo[:-UNDO_STEPS]
            self.redo.clear()
        self.rgb = out
        self.qimg = to_qimage(out)
        self.canvas.update()

    def _apply(self, rec, after):
        y0, y1, x0, x1, before, aft = rec
        out = self.rgb.copy()
        out[y0:y1, x0:x1] = aft if after else before
        self.rgb = out
        self.qimg = to_qimage(out)
        self.canvas.update()

    def do_undo(self):
        if self.undo and not self.busy():
            rec = self.undo.pop()
            self._apply(rec, False)
            self.redo.append(rec)
            self.fills = max(0, self.fills - 1)
            self.status.setText("Undone")
            self._buttons()

    def do_redo(self):
        if self.redo and not self.busy():
            rec = self.redo.pop()
            self._apply(rec, True)
            self.undo.append(rec)
            self.fills += 1
            self.status.setText("Redone")
            self._buttons()

    def changed(self):
        return self.rgb is not self.first and bool(np.any(self.rgb != self.first))

    def reject(self):
        if self.busy():
            self.worker.cancel = True
        super().reject()
