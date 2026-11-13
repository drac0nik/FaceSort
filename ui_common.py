"""Shared helpers for the FaceSort window: logging, opening files and folders, buttons and icons,
list keys, the background Worker and its jobs, SQL snippets and item roles."""
import html
import os
import re
import subprocess
import sys
import time
import traceback
import bodies
import clip_search
import engine
import jobs
import known
import store
from PySide6.QtCore import QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QLabel, QMenu, QSizePolicy, QStyle, QToolButton, QTreeWidget)
from icons import app_icon


try:                                     # video playback (Qt's own player, FFmpeg inside: FLV, WMV, AVI, MPEG...)
    from PySide6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer, QVideoFrame, QVideoFrameFormat, QVideoSink
    from PySide6.QtMultimediaWidgets import QVideoWidget
    MEDIA_CLASSES = (QAudioOutput, QMediaMetaData, QMediaPlayer, QVideoFrame, QVideoFrameFormat, QVideoSink,
                     QVideoWidget)            # (imported here for the viewer modules)
    MEDIA_OK = True
except Exception:
    MEDIA_OK = False
SPEEDS = (0.1, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 4.0)
APP_VERSION = "1.41.2"
IDLE_REDRAW = 1.2       # seconds without a key / click before the sidebar and lists are fully redrawn
DETECTOR_VERSION = 3   # bump when detection improves, so photos with no faces get re-checked
SUGGEST = QColor(232, 160, 32)


def log_path():
    return os.path.join(store.data_dir(), "facesort.log")


def log(msg):
    try:
        with open(log_path(), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass


def open_file(path):
    if sys.platform == "win32":
        os.startfile(path)  # noqa
    else:
        subprocess.Popen(["xdg-open", path])


def _win_select(folder, names):
    """Windows: open (or bring up) an Explorer window on folder with these files selected -
    the shell's own call, which works when the window is already open and with any characters
    in the names. -> True if it worked."""
    try:
        import ctypes
        from ctypes import wintypes
        shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32
        ole32.CoInitialize(None)
        shell32.ILCreateFromPathW.restype = ctypes.c_void_p
        shell32.ILCreateFromPathW.argtypes = [wintypes.LPCWSTR]
        shell32.ILFree.argtypes = [ctypes.c_void_p]
        shell32.SHOpenFolderAndSelectItems.argtypes = [ctypes.c_void_p, wintypes.UINT,
                                                       ctypes.POINTER(ctypes.c_void_p), wintypes.DWORD]
        parent = shell32.ILCreateFromPathW(folder)
        if not parent:
            return False
        kids = [shell32.ILCreateFromPathW(os.path.join(folder, n)) for n in names]
        kids = [k for k in kids if k]
        try:
            arr = (ctypes.c_void_p * max(1, len(kids)))(*kids)
            # (the children are full paths: the shell accepts those and matches them in the folder)
            hr = shell32.SHOpenFolderAndSelectItems(parent, len(kids), arr if kids else None, 0)
            return hr == 0
        finally:
            for k in kids:
                shell32.ILFree(k)
            shell32.ILFree(parent)
    except Exception:
        return False


def show_in_folder(path, also=()):
    """Open the file's folder with the file selected (and any others in also from the same folder)."""
    path = os.path.normpath(path)
    if sys.platform == "win32":
        folder = os.path.dirname(path)
        names = [os.path.basename(path)] + [os.path.basename(os.path.normpath(a)) for a in also
                                            if os.path.dirname(os.path.normpath(a)) == folder]
        if not _win_select(folder, names):
            subprocess.Popen(f'explorer /select,"{path}"')
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path)])


def open_folder_window(path):
    path = os.path.normpath(path)
    if sys.platform == "win32":
        subprocess.Popen(f'explorer "{path}"')
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def tool_button(text, icon, tip="", checkable=False):
    """The gallery's row of buttons: icon + text, 25% bigger than standard."""
    b = QToolButton()
    b.setText(text)
    b.setIcon(app_icon(icon))
    b.setIconSize(QSize(25, 25))
    b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    b.setMinimumHeight(40)
    b.setCheckable(checkable)
    b.setFocusPolicy(Qt.NoFocus)
    f = b.font()
    f.setPointSizeF(f.pointSizeF() * 1.12)
    b.setFont(f)
    if tip:
        b.setToolTip(tip)
    b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)      # never squeezed down to just the icon
    b.setStyleSheet("QToolButton { padding: 3px 9px; border: 1px solid rgba(127,127,127,0.35); border-radius: 7px; }"
                    "QToolButton:hover { background: rgba(59,130,246,0.15); }"
                    "QToolButton:checked { background: rgba(59,130,246,0.30); border-color: rgba(59,130,246,0.9); }"
                    "QToolButton::menu-indicator { subcontrol-position: right center; right: 4px; }")
    return b


def button_icon(label):
    """An icon in the app's style for a gallery button, from its wording."""
    t = label.casefold()
    for words, icon in ((("rename",), "tag"), (("confirm", "done checking", "yes"), "check"),
                        (("delete",), "trash"), (("new subfolder", "create folder"), "folder_add"),
                        (("folder",), "folder"), (("name this", "name "), "person"), (("ignore",), "cancel"),
                        (("restore",), "back"), (("unlock",), "unlock"), (("lock",), "lock"), (("keep the best",), "dupes"), (("tattoo",), "sparkle"),
                        (("look again", "try again", "rescan"), "scan"), (("not ai",), "ai"),
                        (("not faces", "not a face"), "cancel"), (("take all out", "take out"), "clock")):
        if any(w in t for w in words):
            return icon
    return "info"


STD_ICONS = {getattr(QStyle, k): v for k, v in {'SP_DirOpenIcon': 'folder_open', 'SP_DirIcon': 'folder', 'SP_BrowserReload': 'scan', 'SP_DriveDVDIcon': 'video', 'SP_DialogSaveButton': 'save', 'SP_DirLinkIcon': 'export', 'SP_MediaPlay': 'play', 'SP_FileDialogInfoView': 'info', 'SP_FileDialogDetailedView': 'settings', 'SP_DialogHelpButton': 'help', 'SP_ArrowDown': 'sort', 'SP_MessageBoxQuestion': 'question', 'SP_FileDialogListView': 'clock', 'SP_FileIcon': 'nofaces', 'SP_TrashIcon': 'trash', 'SP_FileDialogContentsView': 'sparkle', 'SP_DialogCancelButton': 'cancel', 'SP_MessageBoxWarning': 'warning', 'SP_DialogDiscardButton': 'cancel'}.items()}


def std_icon(sp):
    """The app's own icon for a spot that used a standard Qt icon."""
    return app_icon(STD_ICONS.get(sp, "info"))


class ElidedLabel(QLabel):
    """A one-line label that shortens long text with "…" (full text in the tooltip)
    instead of forcing its window to grow wider."""

    def __init__(self, text="", mode=Qt.ElideRight):
        super().__init__()
        self._full, self._mode = "", mode
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text):
        self._full = text or ""
        self._refit()

    def text(self):
        return self._full

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._refit()

    def _refit(self):
        one_line = " ".join(self._full.split())
        shown = self.fontMetrics().elidedText(one_line, self._mode, max(0, self.width() - 2))
        QLabel.setText(self, shown)
        self.setToolTip(self._full if shown != one_line else "")

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

    def sizeHint(self):
        h = super().sizeHint()
        return QSize(min(h.width(), 240), h.height())


# Generic names: right-click a face > Tag new “Model 01”; each goes in the folder “Unknown Model” etc.
# These are the starting list; the user's own list is settings["generic_names"] (Settings > Tagging).
PLACEHOLDER_NAMES = ("Amateur", "Celebrity", "Model", "Pornstar", "Streamer")


def generic_names(settings):
    """[{"name": "Model", "menu": True}, ...] - the user's generic names in their order; "menu" =
    shown straight in the right-click menu (the rest are under “More generic names”)."""
    raw = (settings or {}).get("generic_names")
    if not isinstance(raw, list):
        return [{"name": n, "menu": True} for n in PLACEHOLDER_NAMES]
    out, seen = [], set()
    for it in raw:
        if isinstance(it, str):
            it = {"name": it, "menu": True}
        if not isinstance(it, dict):
            continue
        name = clean_generic(it.get("name"))
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            out.append({"name": name, "menu": bool(it.get("menu", True))})
    return out


def clean_generic(name):
    """A usable generic name, or "" (blank, or ending in a number, which would get mixed up with
    the number added to it)."""
    name = " ".join(str(name or "").split())
    name = re.sub(r"\s*\d+$", "", name).strip()
    return name[:40]


def key_of(item):
    d = item.data(0, Qt.UserRole) if item is not None else None
    return tuple(d) if d else None


ROLE_DROP = Qt.UserRole + 3     # what a sidebar row means as a drop target


def drop_key(item, src=None):
    if item is None:
        return None
    d = item.data(0, ROLE_DROP)
    if d:
        return tuple(d)
    k = key_of(item)
    if k and k[0] == "category":
        return k
    if k and k[0] == "person":
        if src and src[0] == "category":
            return ("owner", k[1])        # a folder dropped on a person: becomes one of their folders
        if item.parent() is not None and item.parent().data(0, ROLE_DROP) is None:
            # a person dropped on a person inside a folder: means that folder
            return drop_key(item.parent(), src)
    return None


class SideTree(QTreeWidget):
    """The left panel. Drag a person onto a category to place them there, a category onto
    another to nest it, or onto the Categories / People headings to move it back out.
    Photos dragged from the gallery can be dropped on an album (or the Albums heading)."""
    dropped = Signal(object, object)    # (dragged key, target key)
    photos_dropped = Signal(object, object)   # (gallery ids, album key)

    def __init__(self, drag=True):
        super().__init__()
        self.can_drag = drag
        self.setDragEnabled(drag)
        self.setAcceptDrops(drag)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDrop if drag else QAbstractItemView.NoDragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self._drag = None

    def mousePressEvent(self, e):
        # pressing on a row that isn't selected yet and dragging = drag a box over several rows;
        # pressing on a selected person / folder and dragging = move it (onto a folder / person)
        it = self.itemAt(e.position().toPoint())
        if self.can_drag:
            self.setDragEnabled(bool(it is not None and it.isSelected() and e.button() == Qt.LeftButton))
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        if self.can_drag:
            self.setDragEnabled(True)

    def selected_keys(self):
        return [k for k in (key_of(i) for i in self.selectedItems()) if k]

    def startDrag(self, actions):
        keys = [k for k in self.selected_keys() if k[0] in ("person", "category")] or \
            [k for k in [key_of(self.currentItem())] if k and k[0] in ("person", "category")]
        if not keys:
            return
        self._drag = keys[0]
        self._drag_all = keys
        super().startDrag(actions)
        self._drag = None

    def target_for(self, src, item):
        t = drop_key(item, src)
        if not src or not t:
            return None
        if t == ("people_root",) and src[0] != "person":
            return None
        if t[0] == "owner" and src[0] != "category":
            return None
        if t == src:
            return None
        return t

    def _album_at(self, pos):
        it = self.itemAt(pos)
        k = key_of(it) if it is not None else None
        if k and k[0] == "album":
            return k
        d = it.data(0, ROLE_DROP) if it is not None else None
        return tuple(d) if d and tuple(d) == ("album_root",) else None

    def dragEnterEvent(self, e):
        if self.can_drag and not self._drag and e.mimeData().hasFormat(GRID_MIME):
            self._grid = True                       # photos from the gallery: albums take them
            e.acceptProposedAction()
            return
        if not self._drag and self.can_drag and e.mimeData().hasFormat(FOLDER_MIME):
            # a folder dragged from the folder bar beside the gallery
            self._drag = ("category", int(bytes(e.mimeData().data(FOLDER_MIME)).decode()))
            self._drag_all = [self._drag]
            self._ext = True
        if self._drag:              # only our own rows (set while a drag from this panel runs)
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        if getattr(self, "_ext", False):
            self._drag, self._ext = None, False
        self._grid = False
        super().dragLeaveEvent(e)

    def dragMoveEvent(self, e):
        if getattr(self, "_grid", False):
            if self._album_at(e.position().toPoint()):
                e.acceptProposedAction()
            else:
                e.ignore()
            return
        if self.target_for(self._drag, self.itemAt(e.position().toPoint())):
            e.setDropAction(Qt.MoveAction)
            e.accept()
        else:
            e.ignore()

    def dropEvent(self, e):
        if getattr(self, "_grid", False):
            self._grid = False
            k = self._album_at(e.position().toPoint())
            ids = [int(x) for x in bytes(e.mimeData().data(GRID_MIME)).decode().split(",") if x.strip()]
            e.setDropAction(Qt.CopyAction)
            e.accept()
            if k and ids:
                QTimer.singleShot(0, lambda: self.photos_dropped.emit(ids, k))
            return
        # never let Qt move the rows itself: the sidebar is rebuilt from the library
        src = self._drag
        t = self.target_for(src, self.itemAt(e.position().toPoint()))
        e.setDropAction(Qt.IgnoreAction)
        e.accept()
        if getattr(self, "_ext", False):
            self._drag, self._ext = None, False
        if t:
            many = [k for k in getattr(self, "_drag_all", [src]) if k[0] == src[0]] or [src]
            QTimer.singleShot(0, lambda: [self.dropped.emit(k, t) for k in many])


LEGEND_GRAY = "#9a9a9a"


def key_color():
    """Shortcut letters: white (on a light window, where white can't be read: black)."""
    app = QApplication.instance()
    dark = app is None or app.palette().window().color().lightness() < 128
    return "#ffffff" if dark else "#111111"


def key_html(item):
    """'[R]emove Face' -> '(R)emove Face': the key letter white and bold, the rest gray."""
    out = html.escape(item)
    col = key_color()
    out = re.sub(r"\[([^\]]+)\]", lambda m: f"(<span style='color:{col}; font-weight:700'>{m.group(1)}</span>)", out)
    return f"<span style='color:{LEGEND_GRAY}'>{out}</span>"


def legend_html(items):
    return "&nbsp;&nbsp;&nbsp;".join(key_html(i) for i in items if i)


def path_label():
    """A file path line: shortened in the middle so the file name stays visible, selectable
    with the mouse to copy, and double-click shows the file in its folder."""
    lb = ElidedLabel(mode=Qt.ElideMiddle)
    lb.setStyleSheet("color: gray;")
    lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
    lb.setFocusPolicy(Qt.NoFocus)
    lb.setCursor(Qt.IBeamCursor)
    lb.mouseDoubleClickEvent = lambda e: lb.text() and show_in_folder(lb.text())

    def menu(e):
        if not lb.text():
            return
        m = QMenu(lb)
        m.addAction("Copy full path", lambda: QApplication.clipboard().setText(lb.text()))
        m.addAction("Show in folder", lambda: show_in_folder(lb.text()))
        m.exec(e.globalPos())
    lb.contextMenuEvent = menu
    return lb


class PixCache(dict):
    """The window's picture cache. gen goes up each time it's emptied (after anything that may have
    changed pictures), so caches kept elsewhere (the side lists' face icons) know to re-check theirs."""
    gen = 0

    def clear(self):
        self.gen += 1
        super().clear()


def round_pixmap(pm, size, radius=None):
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    if radius is None:
        path.addEllipse(0, 0, size, size)
    else:
        path.addRoundedRect(0, 0, size, size, radius, radius)
    p.setClipPath(path)
    p.drawPixmap(0, 0, size, size, pm)
    p.end()
    return out


# =========================================================================== background work
class Worker(QThread):
    """Runs a job on a background thread. Results are read only after the thread has
    completely finished (QThread.finished), so the thread object is never destroyed
    while it is still running - doing that makes Qt abort the whole program."""
    progress = Signal(int, int, str)
    ok = Signal(object)
    failed = Signal(str)

    _alive = set()  # keeps every worker referenced until its thread has really ended

    def __init__(self, fn, *args, **kw):
        super().__init__()
        self.fn, self.args, self.kw = fn, args, kw
        self._stop = False
        self._last = 0.0
        self.result, self.error = None, None
        Worker._alive.add(self)
        self.finished.connect(self._deliver)

    def _deliver(self):
        # runs on the main thread, after run() has returned and the thread has stopped
        try:
            if self.error is not None:
                self.failed.emit(self.error)
            else:
                self.ok.emit(self.result)
        finally:
            QTimer.singleShot(0, lambda: Worker._alive.discard(self))

    def stop(self):
        self._stop = True

    def start_background(self, settings):
        """Start as background housekeeping: below normal priority unless the user asked for full speed,
        so whatever else you're doing on the PC (and the window itself) comes first."""
        gentle = (settings or {}).get("background_cpu", "gentle") != "full"
        self.start(QThread.LowPriority if gentle else QThread.InheritPriority)

    def _progress(self, done, total, msg=""):
        now = time.monotonic()
        if now - self._last > 0.06 or done == total:
            self._last = now
            self.progress.emit(int(done), int(total), str(msg))

    def run(self):
        conn = store.connect()
        try:
            self.result = self.fn(conn, *self.args, progress=self._progress, cancelled=lambda: self._stop, **self.kw)
        except Exception:
            self.error = traceback.format_exc()
            log(self.error)
        finally:
            try:
                conn.close()
            except Exception:
                pass


def dup_sets_job(conn, progress, cancelled):
    sets = store.duplicate_sets(conn, check_new=False)
    return sets, store.DUP_UNCHECKED[0]


def misplaced_job(conn, dest, inc, ai, progress=None, cancelled=None):
    return jobs.misplaced(conn, dest, inc, ai)


def clip_index_job(conn, progress=lambda d, t, m: None, cancelled=lambda: False):
    return clip_search.index_photos(conn, progress, cancelled)


def clip_download_job(conn, progress=lambda d, t, m: None, cancelled=lambda: False):
    return clip_search.download(progress, cancelled)


def copies_job(conn, progress=None, cancelled=None):
    return store.names_from_copies(conn)


def bodies_job(conn, progress=lambda d, t, m: None, cancelled=lambda: False):
    """Find the people in photos not looked at yet, then work out the same-shoot suggestions."""
    n = bodies.analyze_photos(conn, progress, cancelled)
    if not cancelled():
        progress(0, 0, "Working out same-shoot suggestions…")
        bodies.suggest(conn, cancelled=cancelled)
    return n


def bodies_download_job(conn, progress=lambda d, t, m: None, cancelled=lambda: False):
    return bodies.download(progress, cancelled)


def find_dark_job(conn, progress=lambda d, t, m: None, cancelled=lambda: False):
    return jobs.find_dark_photos(conn, progress, cancelled)


def clarity_job(conn, progress=lambda d, t, m: None, cancelled=lambda: False):
    return store.read_missing_clarity(conn, lambda d, t: progress(d, t, f"Reading face clarity… {d:,} / {t:,}"),
                                      cancelled)


def stats_job(conn, progress=None, cancelled=lambda: False):
    return store.refresh_stats(conn)


def collage_job(conn, path, strength=0, t0=None, t1=None, progress=None, cancelled=lambda: False):
    return engine.video_collage(path, cancelled=cancelled, strength=strength, t0=t0, t1=t1)


def dup_check_job(conn, progress, cancelled):
    progress(0, 0, "Checking possible duplicates closely…")
    try:
        store.duplicate_sets(conn, progress=lambda d, t: progress(d, t, "Checking possible duplicates closely…"),
                             cancelled=cancelled)
    except Exception:
        log(traceback.format_exc())


def scan_and_group(conn, settings, progress, cancelled, videos_only=False, check_only=False, only=None, opts=None):
    o = opts or {}
    if o.get("mode", "both") != "new" and only is None and not check_only:
        # "look again" choices of the Scan window: those photos count as changed for this scan
        if o.get("nofaces_again"):
            conn.execute("UPDATE photos SET mtime=NULL WHERE error IS NULL AND nfaces=0 AND COALESCE(is_other, 0)=0")
            conn.commit()
        if o.get("dark_again"):
            jobs.find_dark_photos(conn, progress, cancelled)
    stats = jobs.scan_library(conn, settings, progress, cancelled, videos_only=videos_only, check_only=check_only,
                              only=only, opts=opts)
    if not cancelled() and (stats.get("scanned") or stats.get("removed") or not check_only):
        progress(0, 0, "Grouping faces…")
        store.regroup(conn, store.threshold(settings), progress=lambda d, t: progress(d, t, "Grouping faces…"),
                      cancelled=cancelled)
    stats["cancelled"] = cancelled()
    return stats


SUGGESTION_SIG = """SELECT COUNT(*), TOTAL(person_id), TOTAL(cluster), TOTAL(later_cluster)
                    FROM faces WHERE ignored IN (0, 5) AND confirmed=0"""


SHOOT_SIG = "SELECT COUNT(*), TOTAL(body_id), TOTAL(person_id) FROM body_suggestions"


def regroup_job(conn, threshold, progress, cancelled, same_shoot=True):
    """Returns True when the suggestions / groups came out different."""
    before = conn.execute(SUGGESTION_SIG).fetchone()
    conn.commit()
    store.regroup(conn, threshold, progress=lambda d, t: progress(d, t, "Updating suggestions…"), cancelled=cancelled,
                  sync=False)
    shoot_before = conn.execute(SHOOT_SIG).fetchone()
    if same_shoot and not cancelled() and conn.execute("SELECT 1 FROM bodies LIMIT 1").fetchone():
        bodies.suggest(conn, cancelled=cancelled)          # who's confirmed decides the same-shoot suggestions
    return (conn.execute(SUGGESTION_SIG).fetchone() != before or
            conn.execute(SHOOT_SIG).fetchone() != shoot_before)


def remember_job(conn, progress, cancelled):
    """Save who's who to the known-people file (and the Photos library's reference file)."""
    known.sync_from_library(conn)


# =========================================================================== face grid
# SQL: the photo (alias ph / column given) has at least one person on it
def HAS_PERSON(photo_col):
    return (f"(EXISTS (SELECT 1 FROM photo_people x WHERE x.photo_id={photo_col}) OR "
            f"EXISTS (SELECT 1 FROM faces y WHERE y.photo_id={photo_col} AND y.person_id IS NOT NULL "
            f"AND y.confirmed=1 AND y.ignored=0))")


# SQL: names of the people on photo ph (named faces + people tagged on the photo), for captions
PEOPLE_LABEL = """(SELECT GROUP_CONCAT(n, ', ') FROM (
    SELECT DISTINCT p.name AS n FROM faces f JOIN persons p ON p.id=f.person_id
    WHERE f.photo_id=ph.id AND f.confirmed=1 AND f.ignored=0
    UNION SELECT p.name FROM photo_people pp JOIN persons p ON p.id=pp.person_id
    WHERE pp.photo_id=ph.id))"""


# the same, counting faces hidden by Sort Later (for the Sort Later list itself)
PEOPLE_LABEL_ALL = PEOPLE_LABEL.replace("f.ignored=0", "(f.ignored=0 OR (f.ignored=5 AND COALESCE(f.later_prev, 0)=0))")


# SQL: photos for "No faces found" (not discarded, not in Sort Later, nobody tagged)
NOFACES_WHERE = """ph.error IS NULL AND ph.nfaces=0 AND ph.discarded=0 AND ph.sort_later=0 AND COALESCE(ph.is_other, 0)=0
    AND NOT EXISTS (SELECT 1 FROM photo_people x WHERE x.photo_id=ph.id)"""


# other files nobody has tagged or put in a folder yet (Smart Groups > Unassociated files)
UNASSOC_WHERE = """ph.is_other=1 AND ph.error IS NULL AND ph.discarded=0 AND ph.sort_later=0
    AND ph.category_id IS NULL AND NOT EXISTS (SELECT 1 FROM photo_people x WHERE x.photo_id=ph.id)"""

GRID_MIME = "application/x-facesort-items"


ROLE_HEADER = Qt.UserRole + 7


ROLE_VIDEO = Qt.UserRole + 8


ROLE_AI = Qt.UserRole + 9


ROLE_RES = Qt.UserRole + 10          # "1080p": the picture's smaller side, in pixels


AI_COL = QColor(168, 85, 247)        # the "AI" badge


ROLE_ID = Qt.UserRole


ROLE_SUGGESTED = Qt.UserRole + 1


ROLE_LABEL = Qt.UserRole + 2   # caption drawn on whole-photo items (who's tagged)


FOLDER_MIME = "application/x-facesort-folder"


FOLDER_BAR_MIN = 150      # the folder bar can be made wider, never narrower than this     # a folder dragged from the folder bar


# =========================================================================== photo viewer
TATTOO_COL = QColor(170, 110, 240)


VIDEO_COL = QColor(255, 140, 26)     # thin border around video thumbnails


PHOTO_COL = QColor(59, 130, 246)     # thin border around photo thumbnails


GIF_COL = QColor(147, 51, 234)       # thin border around GIF thumbnails


HEAD_COL = QColor(59, 130, 246)      # gallery section titles


HELP = r"""
<h3>How FaceSort works</h3>
<ol>
<li><b>Add your photo folders</b> and FaceSort scans them for faces. Everything stays on this PC.</li>
<li>Look-alike faces are put into <b>Unnamed groups</b>. Open a group, remove any faces that don't belong
    (select them and press <b>R</b>), then click <b>Name this group…</b>.</li>
<li>Once someone has a name, FaceSort <b>suggests</b> them on other faces (orange dashed border, <b>?</b> badge).
    Open the person and press <b>Confirm all suggestions</b>, or check them one by one.</li>
<li>Click <b>Write tags</b> to save names into the photos, so Windows Explorer (search
    <code>tags:Name</code>), Lightroom, digiKam, Google Photos uploads and others can find them.</li>
<li>Optional: <b>Sort into folders</b> copies or moves photos into one folder per person.</li>
</ol>
<h3>Folders: your save layout</h3>
<p>Folders on the left decide where <b>Sort into folders</b> puts photos:
<code>Folder\Sub-folder\Person\Person's folder\photo</code>. Drag a <b>person</b> onto a folder and all their
photos are saved there. Drag a folder onto another to nest it, or onto a <b>person</b> to make it one of their
own folders (<code>Jane Doe\Beach 2023</code>) - right-click a person for <i>New folder for…</i>. Names only
have to differ from the folders right next to them, so Jane and Bob can both have a "Beach 2023". A photo in
Jane's "Beach 2023" that Bob is also on goes into Bob's "Beach 2023" too. <b>A</b> (C<b>a</b>tegory) gives
photos a folder. Folders are only used for saving - they aren't written into the photo files.</p>
<p>Formats: WebP / HEIC / AVIF / BMP / TIFF photos are saved as <b>.jpg</b> (quality 95), see-through pictures and
screenshots as <b>.png</b> (lossless), also when a file has the wrong name; JPEG, PNG, RAW and animated GIFs stay as
they are (when moving, the originals go to <i>~To Be Discarded</i>). Right-click RAW photos › <b>Make a JPEG copy</b>.
And when moving,
afterwards empty folders left behind are moved to <i>~To Be Discarded</i> - folders with any other files stay.</p>
<p><b>Videos</b> are scanned too: faces are looked for at 7 moments spread through each video and each
person is kept once. Thumbnails have a thin <span style="color:#ff8c1a">orange</span> border for videos and
<span style="color:#3b82f6">blue</span> for photos. In the preview, buttons along the top switch between the
moments with faces. Settings › Videos: scan them after the photos, together with them, or only when you click
<b>Scan videos</b>. Video tags are saved in an .xmp file next to the video.</p>
<p><b>Drag and drop</b>: drop photos, videos or folders from Explorer anywhere on the window. FaceSort asks
right away who's in them and which folder (both optional, or Sort Later) - and for an organized folder tree,
which folder level is the person, so the tree is kept. Dropped items are imported in full and first (the import
limit doesn't apply to them). A photo with one face gets that face named.</p>
<p><b>Scan</b> (toolbar) opens the Scan window: import new files, check the ones already in the library
(changed, moved, deleted) or both; which folders; how many and in what order; videos; other files
(documents, music, archives - only recorded, never opened; they show in Smart Groups › Unassociated files
until you tag people on them or set a folder, then they move with Sort into folders like photos);
small pictures to skip; folders to leave out; and what happens after. Tick Remember to keep the choices.</p>
<p><b>Ctrl+Shift+N</b> (as in Windows Explorer): a new folder where you are - inside the folder you're
looking at, in a person's folder from their gallery, a new album among the albums, otherwise a new folder at the
top of the Folders tab.</p>
<p><b>Import limit</b> (toolbar): how many new files each Scan imports (0 = all). FaceSort stops there,
groups and suggests, and waits for you to click Scan again.</p>
<p><b>Sort / Viewer</b> (tabs above the title): Sort shows face thumbnails; Viewer shows the whole photo
instead. Everything else works the same.</p>
<p>If your photos are already in folders like <i>Aunt May</i>, FaceSort uses them as hints: faces from the
same folder group together more easily, and the folder name is offered when you name the group.</p>
<h3>Left panel, Photos library</h3>
<p>The top list holds your people and folders; the bottom list everything still to sort - grouped by face,
date taken, date created or original location (“To sort, by”). Drag the bar between them to resize.
Ctrl+click, or press on an unselected row and drag, to pick several and see them together.</p>
<p>Settings › <b>Photos library</b>: the main folder FaceSort sorts into. A reference file inside it remembers every
confirmation, tag and folder, so choosing it again later (or on another PC) brings everything back. It can be
renamed from Settings. In a person's gallery, right-click › <b>Create Folder</b>; the folder bar beside the gallery
jumps between sections and takes dropped photos.</p>
<p><b>Recently dropped</b> (top of the left list): what you drag in lands here first, laid out the way it'll be saved.
Check it, then right-click › Done checking - until then Sort into folders leaves it alone.</p>
<p><b>Viewer</b>: double-click a photo - it opens in this panel. Esc, Backspace or the back arrow goes back. The strip
along the bottom browses; mouse wheel / + − zoom, 0 fits, 1 = actual size, Space = slideshow, F11 = full screen.</p>
<h3>AI images, duplicates, details</h3>
<p><b>AI images</b> (Other): photos whose file says they were made or edited with AI (reliable), and - only with the
optional detector in Settings - photos it guesses are AI (often wrong). They get an <b>AI</b> badge.
<b>Duplicates</b> (Other): the same picture more than once; the largest copy is marked Keep, D discards a copy.
<b>Details</b> (toolbar): a panel on the right with the picture's size, dimensions, dates and its EXIF data.</p>
<h3>Keyboard</h3>
<p>The key for each action is the blue letter in brackets in the list at the bottom of the window (and down
the left side of the preview).</p>
<table cellpadding=3>
<tr><td><b>Enter</b></td><td>Name the selected faces (whole photos: tag who's in them)</td></tr>
<tr><td><b>C</b></td><td>(C)onfirm a suggestion ("yes, that's them")</td></tr>
<tr><td><b>R</b></td><td>(R)emove Face: not them / doesn't belong in this group</td></tr>
<tr><td><b>T</b></td><td>(T)ag Person: draw a box around a face the scan missed, then type who it is
(Ctrl + drag works any time). Right-click any face box to unassign the name or delete a drawn box</td></tr>
<tr><td><b>D</b></td><td>(D)iscard: the photo goes to <i>~To Be Discarded</i> and the face is never matched again</td></tr>
<tr><td><b>L</b></td><td>Sort (L)ater: the photo leaves every other list until you take it out (L again in
<i>Sort Later</i>). Open up <i>Sort Later</i> to sort its photos by face on their own - they stay there until
you take them out. Sort into folders puts these in <i>~Sort Later</i></td></tr>
<tr><td><b>A</b></td><td>C(a)tegory: set the photo's folder (in the preview: the Category bar)</td></tr>
<tr><td><b>M</b></td><td>(M)ark Tattoo (preview): drag a box around a tattoo and type whose it is (Shift + drag too)</td></tr>
<tr><td><b>N</b></td><td>(N)ot a Face (knee, elbow, pattern…). FaceSort learns from these</td></tr>
<tr><td><b>I</b></td><td>(I)gnore: a stranger you don't care about</td></tr>
<tr><td><b>Quick review</b></td><td>Toolbar button: yes/no questions, most useful first. Y yes · N no · Space skip · Z undo</td></tr>
<tr><td><b>Ctrl+A</b></td><td>Select all</td></tr>
<tr><td><b>Double-click</b></td><td>Preview the photo. ← → step through the list</td></tr>
<tr><td><b>Ctrl + wheel</b></td><td>Bigger / smaller thumbnails (or use the slider)</td></tr>
</table>
<p>FaceSort remembers everyone you name in a separate <i>known people</i> file, so they're recognized
again even after clearing the library. Back it up from Settings › Export people.</p>
<p>Tags are written losslessly into JPEG and PNG files. HEIC, TIFF and other formats get an
<code>.xmp</code> sidecar file next to the photo.</p>
"""


_crash_file = None
