"""FaceSort - sort and tag your photos by the people in them. Runs 100% on your PC."""
import os
import sys
import traceback
import cpulimit
cpulimit.APPLIED = cpulimit.apply()    # before numpy/OpenCV load: they read their thread counts once
import engine
import store
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QIntValidator, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListView, QListWidget, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QSlider, QSplitter, QStackedWidget, QStyle, QTabBar, QToolBar, QToolButton,
    QTreeWidget, QVBoxLayout, QWidget, QWidgetAction)
from icons import app_icon
from ui_common import (
    APP_VERSION, DETECTOR_VERSION, ElidedLabel, FOLDER_BAR_MIN, HEAD_COL, LEGEND_GRAY, log, log_path,
    open_file, path_label, show_in_folder, SideTree, std_icon, tool_button)
from gallery import FaceDelegate, FaceModel, GalleryView, HiResLoader, ToolRow
from mw_data import DataMixin
from mw_lists import ListsMixin
from mw_gallery import GalleryMixin
from mw_actions import ActionsMixin
from mw_folders import FoldersMixin
from mw_engine import EngineMixin
from mw_albums import AlbumsMixin
from mw_vaults import VaultsMixin
from mw_settings import SettingsMixin
from mw_jobs import JobsMixin


class MainWindow(DataMixin, ListsMixin, GalleryMixin, ActionsMixin, FoldersMixin,
                 EngineMixin, AlbumsMixin, VaultsMixin, SettingsMixin, JobsMixin, QMainWindow):
    """The main window. Its methods are spread over the mixins (mw_*.py) by topic;
    this file has the window's layout and start-up."""
    def __init__(self):
        super().__init__()
        self.settings = store.load_settings()
        self.conn = store.connect()
        # the window's changes are saved by commit(); anything an action leaves open is saved as
        # soon as the window is idle, so it never holds the write gate (dbgate) for long
        self._save_timer = QTimer(self)
        self._save_timer.timeout.connect(self._save_if_idle)
        self._save_timer.start(250)
        try:
            store.ensure_vault(self.conn)          # there's always a Vault for private photos
            store.apply_vault_hiding(self.conn)    # locked vaults stay hidden, from the very start
        except Exception as e:
            log(f"vaults: {e}")
        self.pix_cache = {}
        self.hi_cache = {}
        self.cell = int(self.settings.get("thumb_size", 120))
        self.viewer_mode = self.settings.get("view_mode") == "viewer"
        self._data_gen = 0
        self._pid_cache = {}
        self.hires = HiResLoader()
        self.worker = None          # scan / tags / export
        self.regroup_worker = None
        self.regroup_pending = False
        self.view = ("unknown", None)
        self.anchor = []            # face ids used to find "the same group" after regrouping

        self.setWindowTitle(f"FaceSort {APP_VERSION}")      # so you can see which version is running
        self.resize(1280, 820)
        icon_path = engine.resource_path("icon.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.regroup_timer = QTimer(self, singleShot=True, interval=2500)   # waits until you pause
        self.regroup_timer.timeout.connect(self.start_regroup)
        self.refresh_timer = QTimer(self, singleShot=True, interval=450)
        self.refresh_timer.timeout.connect(self._deferred_refresh)
        # remembering people (known-people file + reference file) is saved in the background a
        # little while after you stop, not after every confirm
        self.remember_timer = QTimer(self, singleShot=True, interval=15000)
        self.remember_timer.timeout.connect(self.start_remember)
        self.remember_worker = None
        self._last_input = 0.0
        QApplication.instance().installEventFilter(self)     # notices when you're busy clicking / typing
        self.dup_timer = QTimer(self, singleShot=True, interval=1200)     # duplicates: worked out in the background
        self.dup_timer.timeout.connect(self.start_dup_sets)
        self._remember_dirty = False

        self._build_toolbar()
        self._build_body()
        self._build_status()
        self.setAcceptDrops(True)       # drop photos, videos or folders anywhere on the window
        self._scan_after = False
        self.refresh_all(first=True)
        if self.settings.get("detector_version", 1) < DETECTOR_VERSION:
            # face detection improved since these photos were scanned: re-check the ones
            # where nothing was found (names and photo tags are kept)
            self.conn.execute("UPDATE photos SET mtime=NULL WHERE error IS NULL AND nfaces=0")
            self.conn.commit()
            self.settings["detector_version"] = DETECTOR_VERSION
            store.save_settings(self.settings)
        QTimer.singleShot(300, self.startup)

    # ------------------------------------------------------------------ layout
    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        tb.setIconSize(QSize(20, 20))
        self.addToolBar(tb)
        st = self.style()

        def act(text, icon, fn, tip=""):
            a = QAction(std_icon(icon), text, self)
            a.triggered.connect(fn)
            a.setToolTip(tip or text)
            tb.addAction(a)
            return a

        self.a_add = act("Add folder…", QStyle.SP_DirOpenIcon, self.add_folder, "Add a folder of photos")
        self.a_scan = act("Scan", QStyle.SP_BrowserReload, self.toggle_scan, "Look for new or changed photos")
        self.a_scan_videos = act("Scan videos", QStyle.SP_DriveDVDIcon, lambda: self.start_scan(videos_only=True),
                                 "Look for faces in new videos (Settings › Videos: only when I click Scan videos)")
        self.a_scan_videos.setVisible(self.settings.get("video_mode", "after") == "separate")
        lim = QWidget()
        ll = QHBoxLayout(lim)
        ll.setContentsMargins(6, 0, 6, 0)
        ll.setSpacing(4)
        lim.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        lt = QLabel("Import limit:")
        self.limit_edit = QLineEdit(str(int(self.settings.get("import_limit", 0) or 0)))
        self.limit_edit.setValidator(QIntValidator(0, 10_000_000, self))
        self.limit_edit.setFixedWidth(64)
        self.limit_edit.setAlignment(Qt.AlignRight)
        tip = ("How many new files each Scan imports. 0 = no limit.\n"
               "With a limit, FaceSort stops at that many, groups and suggests, and waits - click Scan when "
               "you're ready for the next batch. (It also won't import anything by itself at start-up.)")
        for wdg in (lt, self.limit_edit):
            wdg.setToolTip(tip)
        self.limit_edit.editingFinished.connect(self.save_import_limit)
        ll.addWidget(lt)
        ll.addWidget(self.limit_edit)
        tb.addWidget(lim)
        tb.addSeparator()
        self.a_tags = act("Write tags", QStyle.SP_DialogSaveButton, self.write_tags,
                          "Save people's names into the photo files")
        self.a_export = act("Sort into folders…", QStyle.SP_DirLinkIcon, self.export_folders,
                            "Copy or move photos into one folder per person")
        tb.addSeparator()
        self.a_review = act("Quick review", QStyle.SP_MediaPlay, self.quick_review,
                            "Answer yes/no questions that sort the most faces per keypress")
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.update_btn = QToolButton()                 # shown only when a newer version is out
        self.update_btn.setText("Update available")
        self.update_btn.setIcon(app_icon("export"))
        self.update_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.update_btn.setStyleSheet("QToolButton { color: #22c55e; font-weight: bold; }")
        self.update_btn.clicked.connect(self.show_update)
        self.a_update = tb.addWidget(self.update_btn)
        self.a_update.setVisible(False)
        self.a_details = QAction(app_icon("info"), "Details", self)      # its button sits by the thumbnail slider
        self.a_details.triggered.connect(lambda on: self.show_details(on))
        self.a_details.setToolTip("Show / hide the details panel on the right (size, dimensions, EXIF…)")
        self.a_details.setCheckable(True)
        self.a_details.setChecked(bool(self.settings.get("details_panel", False)))
        act("Settings", QStyle.SP_FileDialogDetailedView, self.open_settings)
        act("Help", QStyle.SP_DialogHelpButton, self.show_help)

    def _build_body(self):
        self.stack = QStackedWidget()
        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(self.stack, 1)
        # progress gets its own full-width strip, so the file name never sits under the bar
        self.prog_strip = QFrame()
        self.prog_strip.setFrameShape(QFrame.StyledPanel)
        # bar centred in the row (Stop beside it), the file being worked on centred underneath
        pv = QVBoxLayout(self.prog_strip)
        pv.setContentsMargins(10, 5, 10, 4)
        pv.setSpacing(2)
        pl = QHBoxLayout()
        self.prog = QProgressBar()
        self.prog.setFixedWidth(420)
        self.prog.setTextVisible(False)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop_worker)
        pl.addStretch(1)
        pl.addSpacing(self.stop_btn.sizeHint().width() + 8)   # balances Stop, so the bar sits in the middle
        pl.addWidget(self.prog)
        pl.addSpacing(8)
        pl.addWidget(self.stop_btn)
        pl.addStretch(1)
        pv.addLayout(pl)
        self.prog_label = ElidedLabel(mode=Qt.ElideMiddle)
        self.prog_label.setMinimumWidth(80)
        self.prog_label.setAlignment(Qt.AlignCenter)
        pv.addWidget(self.prog_label)
        cl.addWidget(self.prog_strip)
        self.setCentralWidget(central)

        # welcome page
        w = QWidget()
        wl = QVBoxLayout(w)
        wl.addStretch()
        t = QLabel("Sort your photos by the people in them")
        f = t.font()
        f.setPointSize(20)
        f.setBold(True)
        t.setFont(f)
        t.setAlignment(Qt.AlignCenter)
        sub = QLabel("Add a folder of photos. FaceSort finds the faces, groups the same person together,\n"
                     "and you give each group a name. Nothing leaves your PC.")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet("color: gray;")
        b = QPushButton("  Add a photo folder…  ")
        b.setMinimumHeight(44)
        bf = b.font()
        bf.setPointSize(12)
        b.setFont(bf)
        b.clicked.connect(self.add_folder)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(b)
        row.addStretch()
        for x in (t, sub):
            wl.addWidget(x)
        wl.addSpacing(18)
        wl.addLayout(row)
        wl.addStretch()
        self.stack.addWidget(w)

        # main page
        split = QSplitter()
        split.setChildrenCollapsible(False)
        side = QWidget()
        self.side_panel = side
        side.setMinimumWidth(240)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(8, 8, 0, 8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a person…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.filter_tree)
        sl.addWidget(self.search)
        # which library folders to look at (narrows the lists and galleries - nothing leaves the library)
        self.scope_btn = QToolButton()
        self.scope_btn.setPopupMode(QToolButton.InstantPopup)
        self.scope_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.scope_btn.setIcon(std_icon(QStyle.SP_DirIcon))
        self.scope_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.scope_btn.setToolTip("Show only photos from some of your library folders - handy in a big library.\n"
                                  "Nothing is removed: the other folders are just hidden from the lists and "
                                  "galleries until you tick them again.")
        self.scope_menu = QMenu(self.scope_btn)
        self.scope_list = QListWidget()
        self.scope_list.setFrameShape(QFrame.NoFrame)
        self.scope_list.itemChanged.connect(self.scope_item_changed)
        wa = QWidgetAction(self.scope_menu)
        wa.setDefaultWidget(self.scope_list)
        self.scope_menu.addAction(wa)
        self.scope_menu.aboutToShow.connect(self.fill_scope_list)
        self.scope_btn.setMenu(self.scope_menu)
        sl.addWidget(self.scope_btn)
        # one tall list, in four tabs: People (folders with the people in them), Folders (just the
        # folders), Albums, and Smart groups (everything still to sort, with its Sort by)
        self._cat_collapsed = set()
        self._view_section = None
        self.tree = SideTree()                  # People
        self.treeF = SideTree()                 # Folders
        self.treeA = SideTree(drag=False)       # Albums
        self.tree2 = SideTree(drag=False)       # Smart groups
        self.trees = (self.tree, self.treeF, self.treeA, self.tree2)
        self._active_tree = self.tree
        for t in self.trees:
            t.dropped.connect(self.tree_drop)
            t.photos_dropped.connect(self.photos_to_album)
            tf = QFont(t.font())
            tf.setWeight(QFont.DemiBold)
            t.setFont(tf)
            t.setHeaderHidden(True)
            t.setIconSize(QSize(36, 36))
            t.setIndentation(10)
            t.setFrameShape(QFrame.NoFrame)
            t.setSelectionMode(QAbstractItemView.ExtendedSelection)   # Ctrl+click / drag to pick several
            t.currentItemChanged.connect(lambda cur, prev, t=t: self.tree_changed(cur, prev, t))
            t.itemSelectionChanged.connect(lambda t=t: self.tree_selection_changed(t))
            # clicking the row that's already picked still takes you back from the viewer
            t.itemPressed.connect(lambda _it, _col: self.leave_viewer_for_list())
            t.setContextMenuPolicy(Qt.CustomContextMenu)
            t.customContextMenuRequested.connect(lambda pos, t=t: self.tree_menu(pos, t))

        def fold_buttons(row, tree):
            for text, tip, fn in (("expand", "Expand everything in this list", tree.expandAll),
                                  ("collapse", "Collapse everything in this list", tree.collapseAll)):
                b = QToolButton()
                b.setIcon(app_icon(text))
                b.setAutoRaise(True)
                b.setToolTip(tip)
                b.clicked.connect(fn)
                row.addWidget(b)

        def page(tree, controls=None):
            w = QWidget()
            v = QVBoxLayout(w)
            v.setContentsMargins(0, 2, 0, 0)
            v.setSpacing(2)
            row = QHBoxLayout()
            row.setSpacing(2)
            for c in (controls or []):
                if isinstance(c, QWidget):
                    row.addWidget(c, 1 if isinstance(c, QComboBox) else 0)
                else:
                    row.addSpacing(c)
            row.addStretch(1)
            fold_buttons(row, tree)
            v.addLayout(row)
            v.addWidget(tree, 1)
            return w
        self.pending_only_cb = QCheckBox("Only to confirm")
        self.pending_only_cb.setToolTip("Show only the people with suggestions waiting for you to confirm")
        self.pending_only_cb.setChecked(bool(self.settings.get("people_pending_only")))
        self.pending_only_cb.toggled.connect(self.set_pending_only)
        self.sort_box = QComboBox()
        self.sort_modes = [("face", "Face"), ("taken", "Date taken"), ("created", "Date created"),
                           ("location", "Original location")]
        for _, label in self.sort_modes:
            self.sort_box.addItem(label)
        self.sort_box.setCurrentIndex(next((i for i, (k, _) in enumerate(self.sort_modes)
                                            if k == self.settings.get("bottom_sort", "face")), 0))
        self.sort_box.setToolTip("How the photos still to sort are grouped in this list")
        self.sort_box.currentIndexChanged.connect(self.set_bottom_sort)
        self.side_tabs = QTabBar()
        self.side_tabs.setExpanding(True)
        self.side_tabs.setDrawBase(False)
        self.side_tabs.setUsesScrollButtons(False)
        self.side_tabs.setElideMode(Qt.ElideNone)
        self.side_tabs.setStyleSheet(
            "QTabBar::tab { padding: 5px 7px; margin-right: 2px; border: 1px solid palette(mid); "
            "border-bottom: none; border-top-left-radius: 6px; border-top-right-radius: 6px; "
            "background: palette(button); }"
            "QTabBar::tab:selected { background: palette(highlight); color: palette(highlighted-text); "
            "font-weight: bold; }"
            "QTabBar::tab:hover:!selected { background: palette(midlight); }")
        for name, tip in (("People", "People, in the folders they're saved in"),
                          ("Folders", "Just the folders (easier to arrange them)"),
                          ("Albums", "Albums - photos in any number of them, files never move"),
                          ("Smart Groups", "Everything still to sort: look-alike faces, unknown faces, "
                                           "tattoo matches, duplicates…")):
            i = self.side_tabs.addTab(name)
            self.side_tabs.setTabToolTip(i, tip)
        self.side_stack = QStackedWidget()
        self.side_stack.addWidget(page(self.tree, [self.pending_only_cb]))
        self.side_stack.addWidget(page(self.treeF))
        self.side_stack.addWidget(page(self.treeA))
        self.side_stack.addWidget(page(self.tree2, [QLabel("Sort by:"), self.sort_box]))
        self.side_tabs.currentChanged.connect(self.side_tab_changed)
        start_tab = int(self.settings.get("side_tab", 0))
        self.side_tabs.setCurrentIndex(max(0, min(3, start_tab)))
        self.side_stack.setCurrentIndex(self.side_tabs.currentIndex())
        sl.addWidget(self.side_tabs)
        sl.addWidget(self.side_stack, 1)
        side.setMinimumWidth(max(240, self.side_tabs.sizeHint().width() + 10))   # all four tabs always fit
        split.addWidget(side)

        main = QWidget()
        main.setMinimumWidth(460)
        ml = QVBoxLayout(main)
        ml.setContentsMargins(12, 10, 10, 6)
        hdr = QHBoxLayout()
        tcol = QVBoxLayout()
        self.title = ElidedLabel()
        tf = self.title.font()
        tf.setPointSize(17)
        tf.setBold(True)
        self.title.setFont(tf)
        self.subtitle = ElidedLabel()
        self.subtitle.setStyleSheet("color: gray;")
        tcol.addWidget(self.title)
        tcol.addWidget(self.subtitle)
        hdr.addLayout(tcol, 1)
        # search by description (a small AI model on this PC - no tags needed)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search by description - “beach at sunset”, “red dress”, “dog”…")
        self.search_box.addAction(app_icon("search"), QLineEdit.LeadingPosition)
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setMinimumWidth(260)
        self.search_box.setMaximumWidth(420)
        self.search_box.setToolTip("Finds photos by what's in them, with no tags needed. A small AI model (CLIP) does "
                                   "this entirely on your PC - it's downloaded once (about 150 MB) the first time.")
        self.search_box.returnPressed.connect(self.describe_search)
        # the Face matching slider (the same one as in Settings), to its left
        mw = QWidget()
        mrow = QHBoxLayout(mw)
        mrow.setContentsMargins(0, 0, 8, 0)
        mrow.setSpacing(4)
        ml_ = QLabel("Face matching")
        ml_.setStyleSheet("color: gray;")
        mrow.addWidget(ml_)
        lo_l = QLabel("Looser")
        lo_l.setStyleSheet("color: gray; font-size: 8pt;")
        mrow.addWidget(lo_l)
        self.match_slider = QSlider(Qt.Horizontal)
        self.match_slider.setFixedWidth(130)
        self.match_slider.setToolTip("How alike two faces must be to be grouped or suggested as the same person.\n"
                                     "Looser: more suggestions, more mistakes · Stricter: fewer, surer ones.\n"
                                     "Suggestions update a moment after you let go (the same setting as in Settings).")
        mrow.addWidget(self.match_slider)
        hi_l = QLabel("Stricter")
        hi_l.setStyleSheet("color: gray; font-size: 8pt;")
        mrow.addWidget(hi_l)
        self.match_timer = QTimer(self)
        self.match_timer.setSingleShot(True)
        self.match_timer.setInterval(700)
        self.match_timer.timeout.connect(self.apply_match_slider)
        self.match_slider.valueChanged.connect(lambda _v: self.match_timer.start())
        self.sync_match_slider()
        hdr.addWidget(mw, 0, Qt.AlignVCenter)
        hdr.addWidget(self.search_box, 0, Qt.AlignVCenter)
        ml.addLayout(hdr)
        self.zoom_row = ToolRow(self.fit_tool_row)
        zoom = QHBoxLayout(self.zoom_row)
        zoom.setContentsMargins(0, 2, 0, 2)
        zoom.setSpacing(6)
        # Sort / Viewer: face thumbnails or whole photos
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        for i, (text, icon, tip) in enumerate((
                ("Sort", "person", "Sort: face thumbnails - name, confirm and sort people"),
                ("Viewer", "image", "Viewer: the whole photo instead of the face (everything still works)"))):
            b = tool_button(text, icon, tip, checkable=True)
            b.setChecked((i == 1) == self.viewer_mode)
            self.mode_group.addButton(b, i)
            zoom.addWidget(b)
        self.mode_group.idClicked.connect(self.set_view_mode)
        sep0 = QFrame()
        sep0.setFrameShape(QFrame.VLine)
        sep0.setFrameShadow(QFrame.Sunken)
        zoom.addWidget(sep0)
        # All media / Pictures / Video: which kind of file the gallery shows
        self.media_group = QButtonGroup(self)
        self.media_group.setExclusive(True)
        cur = self.settings.get("media_filter", "all")
        for i, (key, text, icon, tip) in enumerate((
                ("all", "All media", "filmstrip", "Show photos and videos"),
                ("photos", "Pictures", "image", "Show only photos (no videos)"),
                ("videos", "Video", "video", "Show only videos"))):
            b = tool_button(text, icon, tip, checkable=True)
            b.setChecked(key == cur)
            b.setProperty("media", key)
            self.media_group.addButton(b, i)
            zoom.addWidget(b)
        self.media_group.idClicked.connect(self.set_media_filter)
        self.dupes_btn = tool_button("Duplicates", "dupes", "Show only the duplicates in this gallery, copies side by "
                                     "side (greyed out when there aren't any here)", checkable=True)
        self.dupes_btn.toggled.connect(lambda _on: self.refresh_grid())
        zoom.addWidget(self.dupes_btn)
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setFrameShadow(QFrame.Sunken)
        zoom.addWidget(sep)
        # sort the pictures shown (within each section)
        self.sort_btn = tool_button("Sort", "sort", "How the pictures in each section are ordered")
        self.sort_btn.setPopupMode(QToolButton.InstantPopup)
        sm = QMenu(self.sort_btn)
        self.grid_sorts = [("default", "Best first (default)"), ("downloaded", "Date downloaded (newest first)"),
                           ("taken", "Date taken (newest first)"), ("name_az", "File name (A-Z)"),
                           ("name_za", "File name (Z-A)"), ("size_up", "File size (small to large)"),
                           ("size_down", "File size (large to small)"),
                           ("res_down", "Resolution (largest first)"), ("res_up", "Resolution (smallest first)")]
        for key, label in self.grid_sorts:
            a = sm.addAction(label)
            a.setCheckable(True)
            a.setData(key)
            a.triggered.connect(lambda _=False, key=key: self.set_grid_sort(key))
        self.sort_menu = sm
        self.sort_btn.setMenu(sm)
        self.update_sort_button()
        zoom.addWidget(self.sort_btn)
        # this view's own buttons (Rename, Set folder, Delete person…), in the same style
        self.btn_row = QHBoxLayout()
        self.btn_row.setSpacing(6)
        zoom.addLayout(self.btn_row)
        zoom.addStretch()
        zoom.addStretch()
        small = QLabel("Thumbnail size")
        small.setStyleSheet("color: gray;")
        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setRange(72, 400)
        self.size_slider.setValue(self.cell)
        self.size_slider.setMinimumWidth(80)        # gives way first when the row is full
        self.size_slider.setMaximumWidth(240)
        self.size_slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.size_slider.setToolTip("Make the face thumbnails bigger or smaller (Ctrl + mouse wheel works too)")
        self.size_slider.valueChanged.connect(self.set_cell)
        zoom.addWidget(small)
        zoom.addWidget(self.size_slider)
        self.details_btn = tool_button("Details", "info", self.a_details.toolTip(), checkable=True)
        self.details_btn.setDefaultAction(self.a_details)
        self.details_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        zoom.addWidget(self.details_btn)
        ml.addWidget(self.zoom_row)

        self.model = FaceModel(self)
        self.hires.ready.connect(self.hires_ready)
        self.grid = GalleryView()
        self.grid.setModel(self.model)
        self.grid.setItemDelegate(FaceDelegate(self, self.grid))
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setGridSize(QSize(self.cell, self.cell))
        self.grid.viewport().installEventFilter(self)
        self.grid.setUniformItemSizes(True)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Static)
        self.grid.setLayoutMode(QListView.Batched)
        self.grid.setBatchSize(300)
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.grid.setDragEnabled(True)                    # drag photos onto the folder bar
        self.grid.setDragDropMode(QAbstractItemView.DragOnly)
        self.grid.setFrameShape(QFrame.NoFrame)
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self.grid_menu)
        self.grid.doubleClicked.connect(self.open_viewer)
        self.grid.selectionModel().selectionChanged.connect(self.update_hint)
        self.err_list = QTreeWidget()
        self.err_list.setHeaderLabels(["File", "Problem"])
        self.err_list.setRootIsDecorated(False)
        self.err_list.setUniformRowHeights(True)
        self.err_list.setTextElideMode(Qt.ElideMiddle)
        self.err_list.setFrameShape(QFrame.NoFrame)
        self.err_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        eh = self.err_list.header()
        eh.setStretchLastSection(False)
        eh.setSectionResizeMode(0, QHeaderView.Stretch)
        eh.setSectionResizeMode(1, QHeaderView.Interactive)
        eh.resizeSection(1, 300)
        self.err_list.itemDoubleClicked.connect(lambda it, _c: show_in_folder(it.data(0, Qt.UserRole)))
        self.center = QStackedWidget()
        self.center.addWidget(self.grid)
        self.center.addWidget(self.err_list)
        # folder bar (stacked, left of the gallery): one button per section of the gallery - click
        # to jump there, drop photos on a person's folder to put them in it
        self.folder_bar_area = QScrollArea()
        self.folder_bar_area.setWidgetResizable(True)
        self.folder_bar_area.setFrameShape(QFrame.NoFrame)
        self.folder_bar_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.folder_bar_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.folder_bar_area.setMinimumWidth(FOLDER_BAR_MIN)     # drag the line beside it to make it wider
        fbw = QWidget()
        self.folder_bar = QVBoxLayout(fbw)
        self.folder_bar.setContentsMargins(0, 2, 8, 2)
        self.folder_bar.setSpacing(2)
        self.folder_bar_area.setWidget(fbw)
        self.folder_bar_area.hide()
        self.folder_split = QSplitter(Qt.Horizontal)
        self.folder_split.setChildrenCollapsible(False)
        self.folder_split.setHandleWidth(7)
        self.folder_split.setStyleSheet("QSplitter::handle:horizontal { border-left: 1px solid palette(mid); "
                                        "margin: 0 3px; } QSplitter::handle:horizontal:hover { "
                                        "border-left: 2px solid rgba(59,130,246,0.8); }")
        self.folder_split.addWidget(self.folder_bar_area)
        self.folder_split.addWidget(self.center)
        self.folder_split.setStretchFactor(0, 0)
        self.folder_split.setStretchFactor(1, 1)
        self.folder_split.setSizes([max(FOLDER_BAR_MIN, int(self.settings.get("folder_bar_w", 190))), 1000])
        self.folder_split.splitterMoved.connect(self.folder_bar_resized)
        # the gallery and the Details panel share this row, so the title and buttons above run
        # the full width of the window and Details starts level with the pictures
        self.gal_split = QSplitter(Qt.Horizontal)
        self.gal_split.setChildrenCollapsible(False)
        self.gal_split.addWidget(self.folder_split)
        ml.addWidget(self.gal_split, 1)
        self.grid.verticalScrollBar().valueChanged.connect(lambda _v: self.mark_folder_bar())
        # Recently dropped: the drop's other files, and where they'll go
        self.extra_box = QWidget()
        xb = QVBoxLayout(self.extra_box)
        xb.setContentsMargins(0, 6, 0, 0)
        self.extra_title = QLabel()
        xf = self.extra_title.font()
        xf.setBold(True)
        xf.setPointSizeF(xf.pointSizeF() * 1.15)
        self.extra_title.setFont(xf)
        self.extra_title.setStyleSheet(f"color: {HEAD_COL.name()};")
        xb.addWidget(self.extra_title)
        self.extra_list = QTreeWidget()
        self.extra_list.setHeaderLabels(["File", "Goes into", "Under"])
        self.extra_list.setRootIsDecorated(False)
        self.extra_list.setUniformRowHeights(True)
        self.extra_list.setMaximumHeight(190)
        self.extra_list.setTextElideMode(Qt.ElideMiddle)
        self.extra_list.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.extra_list.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.extra_list.header().setSectionResizeMode(2, QHeaderView.Interactive)
        self.extra_list.header().resizeSection(2, 280)
        self.extra_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.extra_list.itemDoubleClicked.connect(lambda it, _c: open_file(it.data(0, Qt.UserRole)))
        self.extra_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.extra_list.customContextMenuRequested.connect(self.extra_menu)
        xb.addWidget(self.extra_list)
        self.extra_box.hide()
        ml.addWidget(self.extra_box)
        self.path_label = path_label()     # full path of the one selected photo
        self.path_label.hide()
        ml.addWidget(self.path_label)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        ml.addWidget(line)
        self.hint = QLabel()               # shortcut legend: (R)emove Face ... (key letters in blue)
        self.hint.setTextFormat(Qt.RichText)
        self.hint.setWordWrap(True)
        self.hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.hint.setStyleSheet(f"color: {LEGEND_GRAY};")
        ml.addWidget(self.hint)
        # the gallery, or (double-click a photo) the viewer in its place
        self.main_stack = QStackedWidget()
        self.main_stack.addWidget(main)
        self.viewer = None
        self.review = None
        split.addWidget(self.main_stack)
        # details panel on the right (Details button in the toolbar shows / hides it)
        self.details = QWidget()
        self.details.setMinimumWidth(240)
        dl = QVBoxLayout(self.details)
        dl.setContentsMargins(6, 10, 8, 6)
        dt = QLabel("<b>Details</b>")
        dl.addWidget(dt)
        self.details_top = QTreeWidget()
        self.details_meta = QTreeWidget()
        for t in (self.details_top, self.details_meta):
            t.setColumnCount(2)
            t.setHeaderHidden(True)
            t.setRootIsDecorated(False)
            t.setFrameShape(QFrame.NoFrame)
            t.setWordWrap(True)
            t.setTextElideMode(Qt.ElideMiddle)
            t.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
            t.header().setStretchLastSection(True)
            t.setSelectionMode(QAbstractItemView.NoSelection)
            t.setFocusPolicy(Qt.NoFocus)
        dl.addWidget(self.details_top)
        hr = QFrame()
        hr.setFrameShape(QFrame.HLine)
        hr.setFrameShadow(QFrame.Sunken)
        dl.addWidget(hr)
        dl.addWidget(QLabel("<b>EXIF / metadata</b>"))
        dl.addWidget(self.details_meta, 1)
        dl.setContentsMargins(6, 0, 0, 0)
        self.gal_split.addWidget(self.details)
        self.gal_split.setStretchFactor(0, 1)
        self.gal_split.setStretchFactor(1, 0)
        self.gal_split.setSizes([900, 300])
        self.main_split = split
        split.setStretchFactor(1, 1)
        split.setSizes([290, 1290])
        self.details.setVisible(bool(self.settings.get("details_panel", False)))
        self.details_timer = QTimer(self, singleShot=True, interval=120)
        self.details_timer.timeout.connect(self.update_details)
        self.stack.addWidget(split)

        self._main_shortcuts = []
        # Letter keys work anywhere in the window (also right after clicking a person or group on
        # the left); the search box still gets normal typing. Enter stays with the face grid.
        for keys, fn, ctx in ((("Return", "Enter"), self.act_name, Qt.WidgetShortcut),
                              (("C",), self.act_confirm, Qt.WindowShortcut),
                              (("R",), self.act_remove, Qt.WindowShortcut),
                              (("D",), self.act_discard, Qt.WindowShortcut),
                              (("I",), self.act_ignore, Qt.WindowShortcut),
                              (("T",), self.act_tag_person, Qt.WindowShortcut),
                              (("N",), self.act_notface, Qt.WindowShortcut),
                              (("A",), self.act_category, Qt.WindowShortcut),
                              (("L",), self.act_sort_later, Qt.WindowShortcut),
                              (("[",), lambda: self.rotate_selected(False), Qt.WindowShortcut),
                              (("]",), lambda: self.rotate_selected(True), Qt.WindowShortcut)):
            for k in keys:
                sc = QShortcut(QKeySequence(k), self.grid if ctx == Qt.WidgetShortcut else self)
                sc.setContext(ctx)
                sc.activated.connect(fn)
                self._main_shortcuts.append(sc)
        sc = QShortcut(QKeySequence("F11"), self)
        sc.activated.connect(self.toggle_fullscreen)
        sc = QShortcut(QKeySequence("Ctrl+Shift+N"), self)       # new folder, as in Windows Explorer
        sc.setContext(Qt.WindowShortcut)
        sc.activated.connect(self.new_folder_here)

    def _build_status(self):
        sb = self.statusBar()
        self.stats_label = QLabel()
        sb.addWidget(self.stats_label)
        self.engine_label = QLabel()
        self.engine_label.setStyleSheet("color: gray;")
        sb.addPermanentWidget(self.engine_label)
        self.set_busy(False)


# =========================================================================== startup
def excepthook(etype, value, tb):
    text = "".join(traceback.format_exception(etype, value, tb))
    log(text)
    try:
        QMessageBox.critical(None, "FaceSort error", f"{value}\n\nDetails were saved to:\n{log_path()}")
    except Exception:
        pass


def apply_look(app):
    """Easier-to-read text (a slightly larger, heavier font) and the app icon's blue for
    selections and highlights."""
    from PySide6.QtGui import QFontDatabase, QPalette
    fams = set(QFontDatabase.families())
    f = QFont(app.font())
    for fam in ("Segoe UI Variable Text", "Segoe UI", "Inter", "Noto Sans", "Cantarell", "DejaVu Sans"):
        if fam in fams:
            f.setFamily(fam)
            break
    f.setPointSizeF(max(10.0, f.pointSizeF() + 0.5))
    f.setWeight(QFont.Medium)
    app.setFont(f)
    pal = app.palette()
    blue = QColor(47, 116, 230)
    for group in (QPalette.Active, QPalette.Inactive):
        pal.setColor(group, QPalette.Highlight, blue)
        pal.setColor(group, QPalette.HighlightedText, QColor(255, 255, 255))
    pal.setColor(QPalette.Link, QColor(59, 130, 246))
    app.setPalette(pal)
    app.setStyleSheet("QTreeView::item { padding: 2px 0; } QToolTip { padding: 4px; }")


def main():
    global _crash_file
    sys.excepthook = excepthook
    try:
        import faulthandler
        _crash_file = open(log_path(), "a", encoding="utf-8")
        faulthandler.enable(_crash_file)
    except Exception:
        pass
    log(f"FaceSort {APP_VERSION} starting")
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("FaceSort.App")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("FaceSort")
    app.setStyle("Fusion")
    apply_look(app)
    icon_path = engine.resource_path("icon.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    try:
        engine.FaceEngine()  # fail early and clearly if the models are missing
    except Exception as e:
        QMessageBox.critical(None, "FaceSort", f"Couldn't load the face recognition models:\n{e}")
        return 1
    win = MainWindow()
    win.show()
    return app.exec()



# (scripts and tests written before the split reach everything through facesort)
from ui_common import *      # noqa: F401,F403
from gallery import *        # noqa: F401,F403
from viewer_parts import *   # noqa: F401,F403
from dialogs import *        # noqa: F401,F403
from viewer import *         # noqa: F401,F403
from quick_review import *   # noqa: F401,F403

if __name__ == "__main__":
    code = main()
    # Everything is saved by now (the window closed the library). Ending here skips Python
    # and Qt tearing each other down in a random order, which can crash on the way out
    # ("Fatal Python error: Aborted" in the log after a normal close).
    for f in (sys.stdout, sys.stderr, globals().get("_crash_file")):
        try:
            f.flush()
        except Exception:
            pass
    os._exit(code or 0)
