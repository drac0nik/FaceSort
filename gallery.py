"""The gallery: its model, view, folder bar buttons, thumbnail painting and hi-res loading."""
import hashlib
import os
import engine
import store
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import (
    QAbstractListModel, QMimeData, QModelIndex, QObject, QPoint, QRectF, QSize, Qt, QTimer, Signal)
from PySide6.QtGui import QColor, QDrag, QFont, QFontMetrics, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QListView, QProxyStyle, QPushButton, QStyle, QStyledItemDelegate, QWidget)
from icons import app_icon
from ui_common import (
    AI_COL, FOLDER_MIME, GRID_MIME, HEAD_COL, PHOTO_COL, ROLE_AI, ROLE_HEADER, ROLE_ID, ROLE_LABEL, ROLE_RES,
    ROLE_SUGGESTED, ROLE_VIDEO, SUGGEST, VIDEO_COL, GIF_COL)


class FaceModel(QAbstractListModel):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.rows = []  # (face_id, path, person_name, confirmed)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, idx, role=Qt.DisplayRole):
        if not idx.isValid():
            return None
        fid, path, name, confirmed = self.rows[idx.row()]
        if fid == 0:                       # a section heading (e.g. a sub-category or person)
            if role in (Qt.DisplayRole, ROLE_HEADER):
                return path
            return None
        if role == Qt.DecorationRole:
            return self.win.face_pixmap(fid, path)
        if role == ROLE_ID:
            return fid
        if role == ROLE_VIDEO:
            return engine.is_video(path)
        if role == ROLE_AI:
            return self.win.photo_info(path)[3] or 0
        if role == ROLE_RES:
            info = self.win.photo_info(path)
            w, h = info[4], info[5]
            return f"{min(w, h)}p" if w and h else None
        shoot = getattr(self.win, "_shoot", None)
        if shoot and self.win.view[0] == "shoot" and fid in shoot:
            if role == ROLE_LABEL:
                return " / ".join(f"{v[3]}?" for v in shoot[fid])
            if role == Qt.ToolTipRole:
                why = "\n".join(f"Probably {v[3]}: {v[1]}" for v in shoot[fid])
                return (f"{os.path.basename(path)}\n{why}\n"
                        "C = yes · R = not them\nDouble-click to open the photo")
        if role == ROLE_SUGGESTED:
            return bool(name) and not confirmed
        if role == ROLE_LABEL:
            if fid < 0 and not name and os.path.splitext(path)[1].lower() not in engine.IMAGE_EXTS \
                    and not engine.is_video(path):
                return os.path.basename(path)          # an other file: its name under the icon
            return name if fid < 0 else None
        if role == Qt.ToolTipRole:
            tip = os.path.basename(path) + ("   (video)" if engine.is_video(path) else "")
            copies = self.win.copies_of(fid) if hasattr(self.win, "copies_of") else []
            if copies:
                tip += (f"\nAlso saved as {len(copies)} exact cop{'y' if len(copies) == 1 else 'ies'} (shown once here):\n"
                        + "\n".join(copies[:6]) + ("\n…" if len(copies) > 6 else ""))
            if fid < 0:
                return tip + (f"\nTagged: {name}" if name else "\nNo one tagged yet - press Enter to tag") \
                    + "\nDouble-click to open the photo"
            if name and not confirmed:
                tip += f"\nSuggested: {name}\nC = yes, it's them · R = not them"
            elif name:
                tip += f"\n{name}"
            return tip + "\nDouble-click to open the photo"
        return None

    def flags(self, idx):
        if idx.isValid() and self.rows[idx.row()][0] == 0:
            return Qt.ItemIsEnabled                 # headings can't be selected
        return super().flags(idx) | Qt.ItemIsDragEnabled

    def mimeTypes(self):
        return [GRID_MIME]

    def mimeData(self, indexes):
        md = QMimeData()
        md.setData(GRID_MIME, b",".join(str(self.rows[i.row()][0]).encode() for i in indexes if i.isValid()))
        return md

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def remove_ids(self, ids):
        """Take these tiles out without reloading the whole gallery (quick, keeps your place)."""
        want = set(ids)
        for i in range(len(self.rows) - 1, -1, -1):
            if self.rows[i][0] in want:
                self.beginRemoveRows(QModelIndex(), i, i)
                del self.rows[i]
                self.endRemoveRows()

    def refresh_face(self, fid):
        """A sharper picture is ready: fid is a face id, or -photo id for a whole photo
        (in Viewer mode faces show their whole photo, so those rows update too)."""
        for i, r in enumerate(self.rows):
            if r[0] == fid or (fid < 0 and r[0] > 0 and self.win.viewer_mode
                                and self.win.photo_id_of(r[1]) == -fid):
                ix = self.index(i)
                self.dataChanged.emit(ix, ix, [Qt.DecorationRole])


class ToolRow(QWidget):
    """The row of gallery buttons: when the window is too narrow for every label, the
    buttons drop their text (icons stay, the name shows when you point at one)."""

    def __init__(self, fit):
        super().__init__()
        self._fit = fit

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self._fit)


class _IndentStyle(QProxyStyle):
    """Tells the gallery's layout the scroll bar is wider than it is, so each line of
    thumbnails leaves room for the indent (the scroll bar itself keeps its real width)."""

    def __init__(self, view):
        super().__init__()
        self.view = view

    def pixelMetric(self, metric, option=None, widget=None):
        v = super().pixelMetric(metric, option, widget)
        if metric == QStyle.PM_ScrollBarExtent and option is None and widget is not None \
                and widget is self.view.verticalScrollBar():
            return v + self.view.indent
        return v


class GalleryView(QListView):
    """The gallery. Dragging pictures shows a small see-through badge (picture icon + how many)
    beside the cursor instead of big faded thumbnails, so you can see the folder you're over.
    In a list with section headings, the thumbnails sit a little to the right of their heading
    (indent) so the headings stand out."""
    indent = 0

    def set_indent(self, px):
        px = max(0, int(px))
        if px == self.indent:
            return False
        self.indent = px
        if getattr(self, "_istyle", None) is None:
            self._istyle = _IndentStyle(self)
            self.setStyle(self._istyle)
        return True

    def _is_head(self, index):
        return bool(index.isValid() and index.data(ROLE_HEADER))

    def visualRect(self, index):
        r = super().visualRect(index)
        if self.indent and index.isValid():
            if self._is_head(index):
                r.setWidth(r.width() + self.indent)
            else:
                r.translate(self.indent, 0)
        return r

    def indexAt(self, pos):
        if not self.indent:
            return super().indexAt(pos)
        i = super().indexAt(pos - QPoint(self.indent, 0))
        if i.isValid() and not self._is_head(i):
            return i
        i = super().indexAt(pos)
        return i if self._is_head(i) else QModelIndex()

    def setSelection(self, rect, flags):
        if self.indent:
            rect = rect.translated(-self.indent, 0)
        super().setSelection(rect, flags)

    def startDrag(self, actions):
        idxs = [i for i in self.selectedIndexes() if i.isValid() and i.data(ROLE_ID)]
        if not idxs:
            return
        md = self.model().mimeData(idxs)
        if md is None:
            return
        drag = QDrag(self)
        drag.setMimeData(md)
        drag.setPixmap(self.drag_badge(len(idxs)))
        drag.setHotSpot(QPoint(-14, -14))      # badge sits below-right of the cursor, never under it
        drag.exec(actions or Qt.CopyAction)

    def drag_badge(self, n):
        dpr = self.devicePixelRatioF()
        text = f"{n:,}"
        f = QFont(self.font())
        f.setBold(True)
        fm = QFontMetrics(f)
        w = int(30 + 8 + fm.horizontalAdvance(text) + 10)
        h = 30
        pm = QPixmap(int(w * dpr), int(h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(0.85)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(20, 24, 32, 200))
        p.drawRoundedRect(QRectF(0, 0, w, h), 8, 8)
        p.drawPixmap(QRectF(3, 3, 24, 24).toRect(), app_icon("image").pixmap(int(24 * dpr), int(24 * dpr)))
        p.setOpacity(1.0)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRectF(32, 0, w - 36, h), Qt.AlignVCenter | Qt.AlignLeft, text)
        p.end()
        return pm


class FolderButton(QPushButton):
    """A folder bar button: click to jump to the section; drop photos on it to file them there;
    drag it onto another folder (here or in the left panel) to move it; right-click for more."""

    def __init__(self, text, on_drop=None, folder=None, on_folder_drop=None, on_menu=None):
        super().__init__(text)
        self.on_drop = on_drop
        self.folder = folder                    # its folder id (can be dragged), or None
        self.on_folder_drop = on_folder_drop    # fn(dragged folder id) - a folder dropped on this one
        self.on_menu = on_menu
        self.setAcceptDrops(on_drop is not None or on_folder_drop is not None)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCheckable(True)
        self.setToolTip(text)
        self.setStyleSheet("QPushButton { text-align: left; padding: 5px 8px; border: none; border-radius: 6px; }"
                           "QPushButton:hover { background: rgba(59,130,246,0.18); }"
                           "QPushButton:checked { background: rgba(59,130,246,0.32); font-weight: bold; }")
        self.full = text
        self._press = None
        self.setFixedHeight(round(super().sizeHint().height() * 1.2))      # a bit taller: easier to hit

    def contextMenuEvent(self, e):
        if self.on_menu:
            self.on_menu(e.globalPos())

    def mousePressEvent(self, e):
        self._press = e.position().toPoint() if e.button() == Qt.LeftButton else None
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.folder is not None and self._press is not None and \
                (e.position().toPoint() - self._press).manhattanLength() >= QApplication.startDragDistance():
            self._press = None
            self.setDown(False)
            md = QMimeData()
            md.setData(FOLDER_MIME, str(self.folder).encode())
            drag = QDrag(self)
            drag.setMimeData(md)
            pm = QPixmap(self.size() * self.devicePixelRatioF())
            pm.setDevicePixelRatio(self.devicePixelRatioF())
            pm.fill(Qt.transparent)
            self.render(pm)
            drag.setPixmap(pm)
            drag.setHotSpot(QPoint(12, self.height() // 2))
            drag.exec(Qt.MoveAction)
            return
        super().mouseMoveEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        fm = self.fontMetrics()
        room = self.width() - 22 - (self.iconSize().width() + 6 if not self.icon().isNull() else 0)
        self.setText(fm.elidedText(self.full, Qt.ElideMiddle, max(20, room)))

    def dragEnterEvent(self, e):
        md = e.mimeData()
        ok = (self.on_drop and md.hasFormat(GRID_MIME)) or (
            self.on_folder_drop and md.hasFormat(FOLDER_MIME) and int(bytes(md.data(FOLDER_MIME)).decode()) != self.folder)
        if ok:
            e.acceptProposedAction()
            self.setProperty("dropTarget", True)        # light up the folder you're over
            self.setStyleSheet(self.styleSheet() + "QPushButton { background: rgba(59,130,246,0.55); color: white; }")
        else:
            e.ignore()

    def _unlight(self):
        if self.property("dropTarget"):
            self.setProperty("dropTarget", False)
            self.setStyleSheet(self.styleSheet().replace(
                "QPushButton { background: rgba(59,130,246,0.55); color: white; }", ""))

    def dragLeaveEvent(self, e):
        self._unlight()
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        self._unlight()
        md = e.mimeData()
        e.acceptProposedAction()
        if md.hasFormat(FOLDER_MIME):
            src = int(bytes(md.data(FOLDER_MIME)).decode())
            QTimer.singleShot(0, lambda: self.on_folder_drop(src))
            return
        ids = [int(x) for x in bytes(md.data(GRID_MIME)).decode().split(",") if x.strip()]
        QTimer.singleShot(0, lambda: self.on_drop(ids))


class FaceDelegate(QStyledItemDelegate):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win

    def sizeHint(self, option, index):
        if index.data(ROLE_HEADER):
            # a full-width line, so the next thumbnails start on a new row
            return QSize(max(self.win.cell, self.parent().viewport().width() - 8 - self.parent().indent), 46)
        return QSize(self.win.cell, self.win.cell)

    def paint(self, p, option, index):
        title = index.data(ROLE_HEADER)
        if title:
            p.save()
            r = option.rect.adjusted(6, 8, -6, -4)
            f = QFont(option.font)
            f.setBold(True)
            f.setPointSizeF(f.pointSizeF() * 1.4)
            p.setFont(f)
            p.setPen(HEAD_COL)                     # section titles stand out in the app's blue
            fm = p.fontMetrics()
            text = fm.elidedText(title, Qt.ElideMiddle, r.width() - 20)
            p.drawText(r, Qt.AlignLeft | Qt.AlignVCenter, text)
            line_y = r.center().y()
            x0 = r.left() + fm.horizontalAdvance(text) + 12
            c = QColor(HEAD_COL)
            c.setAlpha(110)
            p.setPen(QPen(c, 1.5))
            if x0 < r.right():
                p.drawLine(x0, line_y, r.right(), line_y)
            p.restore()
            return
        pad = max(4, self.win.cell // 24)
        r = QRectF(option.rect.adjusted(pad, pad, -pad, -pad))
        radius = max(8, self.win.cell / 12)
        pm = index.data(Qt.DecorationRole)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(r, radius, radius)
        p.setClipPath(path)
        if pm:
            p.drawPixmap(r.toRect(), pm)
        selected = option.state & QStyle.State_Selected
        if selected:
            c = QColor(option.palette.highlight().color())
            c.setAlpha(70)
            p.fillRect(r, c)
        p.setClipping(False)
        video = index.data(ROLE_VIDEO)
        try:
            gif = str(index.model().rows[index.row()][1] or "").lower().endswith(".gif")
        except (AttributeError, IndexError):
            gif = False
        p.setBrush(Qt.NoBrush)
        # thin: orange = video, purple = GIF, blue = photo
        p.setPen(QPen(VIDEO_COL if video else GIF_COL if gif else PHOTO_COL, 2))
        p.drawRoundedRect(r.adjusted(1, 1, -1, -1), radius - 1, radius - 1)
        if video:
            badge = QRectF(r.left() + 5, r.top() + 5, 20, 16)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 150))
            p.drawRoundedRect(badge, 4, 4)
            p.setBrush(VIDEO_COL)
            tri = QPainterPath()
            tri.moveTo(badge.left() + 7.5, badge.top() + 3.5)
            tri.lineTo(badge.left() + 7.5, badge.bottom() - 3.5)
            tri.lineTo(badge.right() - 5.5, badge.center().y())
            tri.closeSubpath()
            p.drawPath(tri)
            p.setBrush(Qt.NoBrush)
        ai = index.data(ROLE_AI)
        if ai:
            # "AI" badge in the bottom-left corner (dashed when only the detector thinks so)
            f = QFont(option.font)
            f.setBold(True)
            f.setPointSizeF(max(7.0, f.pointSizeF() * 0.85))
            p.setFont(f)
            fm = p.fontMetrics()
            text = "AI" if ai == 1 else "AI?"
            lab = index.data(ROLE_LABEL)
            lift = (fm.height() + 8) if lab else 0
            bw, bh = fm.horizontalAdvance(text) + 10, fm.height() + 4
            badge = QRectF(r.left() + 5, r.bottom() - bh - 5 - lift, bw, bh)
            p.setPen(QPen(QColor(255, 255, 255), 1, Qt.SolidLine if ai == 1 else Qt.DashLine))
            p.setBrush(AI_COL if ai == 1 else QColor(0, 0, 0, 170))
            p.drawRoundedRect(badge, 4, 4)
            p.setPen(Qt.white)
            p.drawText(badge, Qt.AlignCenter, text)
            p.setBrush(Qt.NoBrush)
        res = index.data(ROLE_RES)
        if res and r.width() >= 64:
            # resolution badge in the bottom-right corner: the smaller side ("1080p")
            f = QFont(option.font)
            f.setBold(True)
            f.setPointSizeF(max(7.0, f.pointSizeF() * 0.8))
            p.setFont(f)
            fm = p.fontMetrics()
            lab = index.data(ROLE_LABEL)
            lift = (fm.height() + 8) if lab else 0
            bw, bh = fm.horizontalAdvance(res) + 10, fm.height() + 4
            badge = QRectF(r.right() - bw - 5, r.bottom() - bh - 5 - lift, bw, bh)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 165))
            p.drawRoundedRect(badge, 4, 4)
            p.setPen(QColor(255, 255, 255))
            p.drawText(badge, Qt.AlignCenter, res)
            p.setBrush(Qt.NoBrush)
        if index.data(ROLE_SUGGESTED):
            p.setPen(QPen(SUGGEST, 3, Qt.DashLine))
            p.drawRoundedRect(r.adjusted(1.5, 1.5, -1.5, -1.5), radius - 1, radius - 1)
            badge = QRectF(r.right() - 24, r.top() + 4, 20, 20)
            p.setPen(Qt.NoPen)
            p.setBrush(SUGGEST)
            p.drawEllipse(badge)
            p.setPen(Qt.white)
            f = QFont(option.font)
            f.setBold(True)
            p.setFont(f)
            p.drawText(badge, Qt.AlignCenter, "?")
        label = index.data(ROLE_LABEL)
        if label:
            fm = p.fontMetrics()
            bar = QRectF(r.left(), r.bottom() - fm.height() - 8, r.width(), fm.height() + 8)
            clip = QPainterPath()
            clip.addRoundedRect(r, radius, radius)
            p.setClipPath(clip)
            p.fillRect(bar, QColor(0, 0, 0, 170))
            p.setClipping(False)
            p.setPen(Qt.white)
            p.drawText(bar.adjusted(6, 0, -6, 0), Qt.AlignVCenter | Qt.AlignLeft,
                       fm.elidedText(label, Qt.ElideRight, int(bar.width() - 12)))
        if selected:
            p.setPen(QPen(option.palette.highlight().color(), 4))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(2, 2, -2, -2), radius - 1, radius - 1)
        p.restore()


def thumb_cache_dir():
    return os.path.join(store.data_dir(), "thumbs")


def thumb_cache_size(folder=None):
    """(pictures, bytes) in the thumbnail cache. os.scandir gives each file's size with the
    folder listing on Windows (no separate look-up per file, which took seconds on big caches)."""
    total = n = 0
    try:
        entries = list(os.scandir(folder or thumb_cache_dir()))
    except OSError:
        return 0, 0
    for e in entries:
        try:
            if e.is_dir(follow_symlinks=False):
                a, b = thumb_cache_size(e.path)
                n, total = n + a, total + b
            else:
                total += e.stat(follow_symlinks=False).st_size
                n += 1
        except OSError:
            pass
    return n, total


class HiResLoader(QObject):
    """Loads sharp face crops from the original photos when thumbnails are shown large."""
    ready = Signal(int)

    def __init__(self):
        super().__init__()
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.cache = OrderedDict()
        self.pending = set()
        self.generation = 0

    def get(self, fid, path, box, size):
        key = (fid, size)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        if key not in self.pending:
            self.pending.add(key)
            gen = self.generation
            self.pool.submit(self._load, key, path, box, size, gen)
        return None

    def _load(self, key, path, box, size, gen):
        data = None
        cache = None
        try:
            st = os.stat(path)
            sig = f"{path}|{st.st_mtime:.3f}|{st.st_size}|{box}|{size}|{key[0] < 0}"
            h = hashlib.sha1(sig.encode("utf-8", "replace")).hexdigest()
            cache = os.path.join(thumb_cache_dir(), h[:2], h + ".jpg")
            if os.path.exists(cache):
                with open(cache, "rb") as f:
                    data = f.read()
        except Exception:
            cache = None
        if not data:
            try:
                data = engine.photo_thumb(path, size, box) if key[0] < 0 else engine.face_crop(path, box, size)
            except Exception:
                data = None
            if data and cache:     # kept on disk: next time the library opens, no photo is decoded
                try:
                    os.makedirs(os.path.dirname(cache), exist_ok=True)
                    with open(cache + ".tmp", "wb") as f:
                        f.write(data)
                    os.replace(cache + ".tmp", cache)
                except OSError:
                    pass
        self.pending.discard(key)
        if gen != self.generation or not data:
            return
        img = QImage.fromData(data, "JPG")  # QImage is safe to make off the main thread
        self.cache[key] = img
        while len(self.cache) > 600:
            self.cache.popitem(last=False)
        self.ready.emit(key[0])

    def clear(self):
        self.generation += 1
        self.cache.clear()
        self.pending.clear()
