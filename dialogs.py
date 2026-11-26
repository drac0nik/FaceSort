"""Dialogs: Settings, Export, Save an enhanced copy, and the drop dialog."""
import html
import os
import subprocess
import threading
import sys
import aidetect
import engine
import engine_hq
import jobs
import known
import store
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QCompleter, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QRadioButton, QScrollArea,
    QSlider, QSpinBox, QTabWidget, QVBoxLayout, QWidget)
from icons import app_icon
from ui_common import APP_VERSION, clean_generic, ElidedLabel, generic_names, HEAD_COL, PLACEHOLDER_NAMES, show_in_folder
from gallery import thumb_cache_dir, thumb_cache_size


# =========================================================================== settings
class SettingsDialog(QDialog):
    def __init__(self, win, settings):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Settings")
        self.setMinimumWidth(600)
        self.s = dict(settings)
        self.clear_requested = False
        self.imported = False
        self.rescan_request = None   # "nofaces" or "all"
        outer = QVBoxLayout(self)
        # three tabs: General / Library · Scanning / CPU / GPU · Tagging
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)
        pages = {}
        for key, title, icon in (("general", "General / Library", "folder"),
                                 ("scan", "Scanning / CPU / GPU", "scan"),
                                 ("tags", "Tagging", "tag")):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            body = QWidget()
            pl = QVBoxLayout(body)
            pl.setContentsMargins(10, 10, 14, 10)
            pl.setSpacing(6)
            scroll.setWidget(body)
            self.tabs.addTab(scroll, app_icon(icon), title)
            pages[key] = (pl, body)
        import privacy_ui
        self.privacy_page = privacy_ui.PrivacyPage(win)
        a_scroll = QScrollArea()
        a_scroll.setWidgetResizable(True)
        a_scroll.setWidget(self.privacy_page)
        self.tabs.addTab(a_scroll, app_icon("lock"), "Privacy")
        import viewer_ui
        self.viewer_page = viewer_ui.ViewerSettingsPage(self.s)
        self.tabs.addTab(self.viewer_page, app_icon("search"), "Viewer")
        import backup_ui
        self.backups_page = backup_ui.BackupsPage(win, self.s)
        self.tabs.addTab(self.backups_page, app_icon("folder"), "Backups")
        lay = pages["general"][0]

        def heading(text, layout):
            lb = QLabel(text)
            f = lb.font()
            f.setBold(True)
            f.setPointSizeF(f.pointSizeF() * 1.1)
            lb.setFont(f)
            lb.setStyleSheet(f"color: {HEAD_COL.name()};")
            if layout.count():
                layout.addSpacing(10)
            layout.addWidget(lb)

        heading("Photos library", lay)
        rl = QLabel("The main folder FaceSort sorts your photos into and looks after (Sort into folders uses "
                    "it). A reference file inside it remembers every confirmation, tag and folder, so choosing "
                    "it again later - even after using another folder, or on another PC - brings it all back.")
        rl.setWordWrap(True)
        rl.setStyleSheet("color: gray;")
        lay.addWidget(rl)
        rr = QHBoxLayout()
        self.root_label = ElidedLabel(self.s.get("library_root") or "Not chosen yet", mode=Qt.ElideMiddle)
        choose_root, self.rename_root_b = QPushButton("Choose…"), QPushButton("Rename…")
        choose_root.clicked.connect(self.choose_root)
        self.rename_root_b.clicked.connect(self.rename_root)
        self.rename_root_b.setEnabled(bool(self.s.get("library_root")))
        rr.addWidget(self.root_label, 1)
        rr.addWidget(choose_root)
        rr.addWidget(self.rename_root_b)
        lay.addLayout(rr)
        # (1.57.3) imports go straight into the library; tagged photos move on to their person's folder
        self.cb_import_lib = QCheckBox("Move what I import into the library, under “~Unsorted\\<where it was>” "
                                       "(drops, Import once, Web drops, the browser, watched galleries)")
        self.cb_import_lib.setChecked(self.s.get("import_into_library", True))
        self.cb_import_lib.setToolTip("C:\\Downloads\\a.jpg becomes <library>\\~Unsorted\\C\\Downloads\\a.jpg. "
                                      "Folders you keep watching aren't moved.")
        lay.addWidget(self.cb_import_lib)
        self.cb_auto_move = QCheckBox("Once someone's confirmed on a photo in ~Unsorted, move it to their folder "
                                      "(after a short pause; group photos: biggest face, copies for the others)")
        self.cb_auto_move.setChecked(self.s.get("auto_move", True))
        lay.addWidget(self.cb_auto_move)
        for cb in (self.cb_import_lib, self.cb_auto_move):
            cb.setStyleSheet("QCheckBox { margin-top: 2px; }")
        heading("Photo folders", lay)
        self.folders = QListWidget()
        self.folders.addItems(self.s["folders"])
        self.folders.setMaximumHeight(110)
        lay.addWidget(self.folders)
        fr = QHBoxLayout()
        add, rem = QPushButton("Add folder…"), QPushButton("Remove selected")
        add.clicked.connect(self.add_folder)
        rem.clicked.connect(lambda: [self.folders.takeItem(self.folders.row(i)) for i in self.folders.selectedItems()])
        fr.addWidget(add)
        fr.addWidget(rem)
        fr.addStretch()
        self.files = list(self.s.get("files", []))
        if self.files:
            self.files_label = QLabel(f"+ {len(self.files):,} single file{'s' if len(self.files) != 1 else ''} "
                                      "you dropped in")
            self.files_label.setStyleSheet("color: gray;")
            self.files_label.setToolTip("\n".join(self.files[:30]) + ("\n…" if len(self.files) > 30 else ""))
            forget = QPushButton("Remove them")
            forget.clicked.connect(lambda: (self.files.clear(), self.files_label.setText("(single files removed)"),
                                            forget.setEnabled(False)))
            fr.addWidget(self.files_label)
            fr.addWidget(forget)
        lay.addLayout(fr)
        general = lay

        import updater
        heading("Updates", lay)
        self.cb_updates = QCheckBox("Check for new versions (asks GitHub once a day - nothing about you or your "
                                    "photos is sent)")
        self.cb_updates.setChecked(bool(self.s.get("check_updates", True)))
        lay.addWidget(self.cb_updates)
        ur = QHBoxLayout()
        ur.addWidget(QLabel(f"You have FaceSort {APP_VERSION}. Updates come from GitHub project:"))
        self.update_repo = QLineEdit(self.s.get("update_repo") or updater.UPDATE_REPO)
        self.update_repo.setPlaceholderText("owner/repository")
        self.update_repo.setToolTip("Where FaceSort's releases are published, e.g. someone/FaceSort")
        ur.addWidget(self.update_repo, 1)
        check_now = QPushButton("Check now")
        check_now.clicked.connect(self.check_updates_now)
        ur.addWidget(check_now)
        lay.addLayout(ur)
        if not updater.is_installed():
            nb = QLabel("This copy wasn't installed with the installer (it runs from source or a Setup.bat build): "
                        "when an update is out, its download page opens instead of installing it here.")
            nb.setWordWrap(True)
            nb.setStyleSheet("color: gray;")
            lay.addWidget(nb)
        heading("Speed report", lay)
        sr = QHBoxLayout()
        sn = QLabel("FaceSort notes each moment the window is slow to respond, and what it was busy with, in "
                    "speed.log (on this PC only). If FaceSort feels sluggish, send that file along.")
        sn.setWordWrap(True)
        sn.setStyleSheet("color: gray;")
        sr.addWidget(sn, 1)
        show_sr = QPushButton("Show speed.log")
        show_sr.clicked.connect(lambda: show_in_folder(os.path.join(store.data_dir(), "speed.log"))
                                if os.path.exists(os.path.join(store.data_dir(), "speed.log"))
                                else QMessageBox.information(self, "Speed report", "No slow moments noted yet."))
        sr.addWidget(show_sr)
        lay.addLayout(sr)

        lay = pages["scan"][0]
        heading("Face recognition", lay)
        form = QFormLayout()
        self.engine_box = QComboBox()
        self.engine_box.addItem("Best accuracy - AdaFace (newest; best with a graphics card)", "adaface")
        self.engine_box.addItem("High accuracy - ArcFace (recommended)", "arcface")
        self.engine_box.addItem("Standard (fast)", "sface")
        self.engine_box.setCurrentIndex(max(0, self.engine_box.findData(self.s.get("engine", "sface"))))
        self.engine_box.setToolTip(
            "High accuracy recognises turned, partly hidden and cut-off faces far better than Standard and\n"
            "finds fewer things that aren't faces.\n"
            "Best accuracy uses the same face finder with a newer recognizer (AdaFace), trained to cope with\n"
            "poor pictures: in tests it told people apart better on very dark, tiny and blurry faces, and the same\n"
            "on clear ones. It does about 5x the work per face: fine with a graphics card, slow without one.\n"
            "Switching rescans your library once; names and everything you've sorted are kept.")
        form.addRow("Face engine:", self.engine_box)
        notes = []
        if not engine_hq.available("arcface"):
            notes.append(f"High accuracy needs a one-time {engine_hq.DOWNLOAD_MB} MB download")
        if not engine_hq.available("adaface"):
            notes.append("Best accuracy a one-time 260 MB download (done when you switch)")
        eng_note = QLabel("; ".join(notes) + ("." if notes else ""))
        eng_note.setStyleSheet("color: gray;")
        if eng_note.text():
            form.addRow("", eng_note)
        self.thr = QSlider(Qt.Horizontal)
        self.thr.setRange(30, 56)
        self.engine_now = self.s.get("engine", "sface")
        lo, hi = store.THRESHOLD_RANGE[self.engine_now]
        self.thr.setRange(round(lo * 100), round(hi * 100))
        self.thr.setValue(round(store.threshold(self.s) * 100))
        tl = QHBoxLayout()
        tl.addWidget(QLabel("Looser"))
        tl.addWidget(self.thr)
        tl.addWidget(QLabel("Stricter"))
        tw = QWidget()
        tw.setLayout(tl)
        tl.setContentsMargins(0, 0, 0, 0)
        form.addRow("Face matching:", tw)
        self.minface = QSpinBox()
        self.minface.setRange(16, 200)
        self.minface.setSuffix(" px")
        self.minface.setValue(self.s["min_face_px"])
        self.minface.setToolTip("Faces smaller than this (in a 1600 px version of the photo) are skipped.\n"
                                "Lower = finds tiny background faces but more mistakes. Applies to newly scanned photos.")
        form.addRow("Skip faces smaller than:", self.minface)
        self.det = QSlider(Qt.Horizontal)
        self.det.setRange(55, 90)
        self.det.setValue(round(self.s.get("detect_threshold", 0.75) * 100))
        self.det.setToolTip("How sure FaceSort must be that something is a face.\n"
                            "More faces = also finds blurry, side-on and partly hidden faces, "
                            "but sometimes picks up things that aren't faces (just press Delete on those).")
        dl = QHBoxLayout()
        dl.addWidget(QLabel("More faces"))
        dl.addWidget(self.det)
        dl.addWidget(QLabel("Fewer mistakes"))
        dw = QWidget()
        dw.setLayout(dl)
        dl.setContentsMargins(0, 0, 0, 0)
        form.addRow("Face detection:", dw)
        import neverfaces
        b_never = QPushButton(f"Never track list… ({len(neverfaces.load())})")
        b_never.setToolTip("Logos, watermarks, drawings… that are never faces: everything matching them is set aside "
                           "by itself, also in photos scanned later")
        b_never.clicked.connect(self.never_track_list)
        form.addRow("", b_never)
        self.threads = QSpinBox()
        self.threads.setRange(1, max(1, os.cpu_count() or 1))
        self.threads.setValue(self.s["threads"])
        self.threads.setToolTip("Threads that open and decode photos. With the GPU on, these feed it;\n"
                                "decoding JPEG/HEIC files always happens on the CPU.")
        form.addRow("CPU threads (opening photos):", self.threads)
        lay.addLayout(form)
        self.tat = QSlider(Qt.Horizontal)
        self.tat.setRange(10, 30)
        self.tat.setValue(int(self.s.get("tattoo_min_inliers", 14)))
        self.tat.setToolTip("How closely a tattoo must match before it's suggested.\n"
                            "Looser finds tattoos at harder angles but suggests more look-alikes.")
        tl2 = QHBoxLayout()
        tl2.addWidget(QLabel("Looser"))
        tl2.addWidget(self.tat)
        tl2.addWidget(QLabel("Stricter"))
        tw2 = QWidget()
        tw2.setLayout(tl2)
        tl2.setContentsMargins(0, 0, 0, 0)
        form.addRow("Tattoo matching:", tw2)
        self.cb_side = QCheckBox("Also look for faces in sideways photos (photos turned without a rotation flag)")
        self.cb_side.setChecked(self.s.get("look_sideways", True))
        self.cb_side.setToolTip("Photos where no face is found with confidence are also checked turned left and right.\n"
                                "Slightly slower on photos without people.")
        lay.addWidget(self.cb_side)
        self.cb_dark = QCheckBox("Take a second look at dark photos (dim, evening and backlit shots)")
        self.cb_dark.setChecked(self.s.get("dark_second_look", True))
        self.cb_dark.setToolTip("Dark photos are also searched in a brightened copy, which finds faces buried in "
                                "the dark,\nand a face in a dark photo is read from whichever version is clearer.\n"
                                "Only dark photos take a little longer.")
        lay.addWidget(self.cb_dark)
        self.cb_shoot = QCheckBox("Same-shoot suggestions: recognise people by their clothes at the same occasion")
        self.cb_shoot.setChecked(bool(self.s.get("same_shoot", False)))
        self.cb_shoot.setToolTip("For people whose face can't be read (turned away, blurry, hidden): someone wearing the "
                                 "same clothes\nas a person you confirmed in a photo taken at the same time and place is "
                                 "suggested in\nSmart Groups > Same shoot. Uses two small models from OpenCV (downloaded "
                                 "once, about 140 MB);\neverything runs on this PC.")
        lay.addWidget(self.cb_shoot)
        import face_attrs
        self.cb_details = QCheckBox("Face details: which unnamed faces look male (Smart Groups > Men / Unsure) "
                                    "and which way heads are turned"
                                    + ("" if face_attrs.ready() else f"   (gets {face_attrs.DOWNLOAD_MB} MB of models once)"))
        self.cb_details.setChecked(bool(self.s.get("face_details")))
        self.cb_details.setToolTip("Two InsightFace models (free for non-commercial use) read each face's thumbnail on "
                                   "this PC. Side-on faces are then matched more carefully, and front-on faces are "
                                   "picked for the people list. Men / women is an estimate: unclear faces go to Unsure.")
        lay.addWidget(self.cb_details)
        import content
        self.cb_content = QCheckBox("Content: recommended tags, Safe view and No people"
                                    + ("" if content.ready() else f"   (gets {content.DOWNLOAD_MB} MB of models once)"))
        self.cb_content.setChecked(bool(self.s.get("content_on")))
        self.cb_content.setToolTip("NudeNet (AGPL) and an NSFW classifier (MIT) read each photo once on this PC: how "
                                   "explicit it is, what tags fit it, and whether anyone's in it.")
        lay.addWidget(self.cb_content)
        import pose as _pose
        self.cb_pose = QCheckBox(f"Poses: Standing, Sitting, Lying down… as tags (RTMPose, about {_pose.DOWNLOAD_MB + 36} MB once)")
        self.cb_pose.setChecked(bool(self.s.get("pose_on")))
        self.cb_pose.setToolTip("A pose model (RTMPose by OpenMMLab, Apache licence) marks 17 points on each person, on "
                                "this PC; FaceSort works out the poses from them. Offered as tags, and rules can use them.")
        lay.addWidget(self.cb_pose)
        rr = QHBoxLayout()
        dark_again = QPushButton("Look again in dark photos")
        dark_again.setToolTip("Find the dark photos scanned before this existed and look at them again.\n"
                              "Names you gave are kept.")
        dark_again.clicked.connect(lambda: self.request_rescan("dark"))
        rr.addWidget(dark_again)
        again = QPushButton("Look again in photos with no faces")
        again.setToolTip("Run face detection again (with the settings above) on photos where none was found.\n"
                         "People you tagged on those photos are kept.")
        again.clicked.connect(lambda: self.request_rescan("nofaces"))
        full = QPushButton("Scan all photos again")
        full.setToolTip("Run face detection again on every photo. Names you gave are kept.")
        full.clicked.connect(lambda: self.request_rescan("all"))
        rr.addWidget(again)
        rr.addWidget(full)
        rr.addStretch()
        lay.addLayout(rr)
        heading("CPU and graphics card (GPU)", lay)
        cpu_note = QLabel(f"This PC has {os.cpu_count() or '?'} CPU threads. CPU threads (above) open and decode the "
                          "photos; the graphics card does the face detection and recognition when it's on.")
        cpu_note.setWordWrap(True)
        cpu_note.setStyleSheet("color: gray;")
        lay.addWidget(cpu_note)
        self.cb_gentle = QCheckBox("Keep background work gentle (recommended)")
        self.cb_gentle.setChecked(self.s.get("background_cpu", "gentle") != "full")
        self.cb_gentle.setToolTip(
            "Updating groups and suggestions after you tag, same-shoot matching and saving who's who run\n"
            "below normal priority on at most half the CPU, and suggestions wait until you've paused tagging\n"
            "for a few seconds. Turn off to update them as fast as the PC allows (takes effect after a restart\n"
            "for the CPU share).")
        lay.addWidget(self.cb_gentle)
        self.cb_gpu = QCheckBox("Use the graphics card (GPU) for face detection and recognition")
        self.cb_gpu.setChecked(self.s.get("use_gpu", True))
        lay.addWidget(self.cb_gpu)
        active, text = engine.gpu_status()
        self.gpu_label = QLabel(("<span style='color:#3cbf78'>●</span> " if active else "● ")
                                + f"Currently running on: {text}")
        self.gpu_label.setWordWrap(True)
        self.gpu_label.setContentsMargins(24, 0, 0, 0)
        lay.addWidget(self.gpu_label)
        try:                                      # (1.61) jobs moved to the CPU after crashing the card
            import helpers as _h
            pool = _h.pool()
            moved = pool.cpu_only_jobs()
        except Exception:
            pool, moved = None, []
        if moved:
            import time as _time
            nice = {"content_job": "content check", "clip_index_job": "search by description", "scan_job": "face scan",
                    "pose_job": "poses", "bodies_job": "same-shoot (figures)"}
            txt = ", ".join(f"{nice.get(k.split(':')[-1], k.split(':')[-1])} (until "
                            f"{_time.strftime('%b %d', _time.localtime(u))})" for k, u in moved)
            row = QHBoxLayout()
            row.setContentsMargins(24, 0, 0, 0)
            lbl = QLabel(f"<span style='color:#d08a2c'>●</span> On the CPU after crashing the graphics card: {txt}")
            lbl.setWordWrap(True)
            row.addWidget(lbl, 1)
            again = QPushButton("Try the graphics card again")
            again.setToolTip("Forget the crashes: these jobs use the graphics card again (if one crashes again it "
                             "goes back to the CPU)")

            def retry():
                pool.reset_gpu_crashes()
                lbl.setText("These jobs use the graphics card again.")
                again.setEnabled(False)
            again.clicked.connect(retry)
            row.addWidget(again)
            lay.addLayout(row)
        names = gpu_names(wait=False)             # (asked in the background when FaceSort starts)
        if names or sys.platform == "win32":
            g = QLabel("Graphics cards in this PC: " + (", ".join(names) if names else "looking…"))
            g.setStyleSheet("color: gray;")
            g.setWordWrap(True)
            g.setContentsMargins(24, 0, 0, 0)
            lay.addWidget(g)
            if not names:
                def fill(tries=[0]):
                    got = gpu_names(wait=False)
                    try:
                        if got:
                            g.setText("Graphics cards in this PC: " + ", ".join(got))
                        elif tries[0] < 60:
                            tries[0] += 1
                            QTimer.singleShot(250, fill)
                        else:
                            g.hide()
                    except RuntimeError:
                        pass                       # (Settings was closed meanwhile)
                QTimer.singleShot(250, fill)
        heading("Helper processes", lay)
        import helpers
        hn = QLabel("FaceSort's background work (what's in photos, descriptions, face details, duplicates, "
                    "suggestions…) runs in separate helper processes, so it can use several cores at once and "
                    "never slows the window down: one for the graphics card, and CPU helpers for the rest.")
        hn.setWordWrap(True)
        hn.setStyleSheet("color: gray;")
        lay.addWidget(hn)
        self.cb_helpers = QCheckBox("Run background work in helper processes (recommended)")
        self.cb_helpers.setChecked(self.s.get("helpers", "on") != "off")
        lay.addWidget(self.cb_helpers)
        hr = QHBoxLayout()
        hr.setContentsMargins(24, 0, 0, 0)
        hr.addWidget(QLabel("CPU helpers:"))
        self.helpers_box = QComboBox()
        auto = helpers.default_cpu_helpers()
        self.helpers_box.addItem(f"Automatic ({auto} on this PC)", 0)
        for k in range(1, max(2, min(16, os.cpu_count() or 2)) + 1):
            self.helpers_box.addItem(str(k), k)
        i = self.helpers_box.findData(int(self.s.get("cpu_helpers", 0) or 0))
        self.helpers_box.setCurrentIndex(max(0, i))
        self.helpers_box.setToolTip("Each helper uses about 200-400 MB of memory and up to 2 CPU threads; the "
                                    "graphics card helper also keeps its models loaded (about 1 GB).")
        hr.addWidget(self.helpers_box)
        hr.addStretch()
        lay.addLayout(hr)
        self.cb_fp16 = QCheckBox("Faster FP16 models on the graphics card (each checked against the full model first)")
        self.cb_fp16.setChecked(bool(self.s.get("fp16", True)))
        try:
            import fp16
            st = fp16.status()
        except Exception:
            st = []
        tip = ("RTX and other recent graphics cards run half-precision (FP16) maths on their tensor cores much "
               "faster. Each model is converted once and, the first time it runs, its answers are compared with "
               "the full-precision model's; it's only used if they match.")
        if st:
            tip += "\n\n" + "\n".join(f"{name} ({prov}): {'FP16' if ok else 'full precision'} - {detail}"
                                       for name, prov, ok, detail in st)
        self.cb_fp16.setToolTip(tip)
        lay.addWidget(self.cb_fp16)
        self.cb_helpers.toggled.connect(self.helpers_box.setEnabled)
        self.helpers_box.setEnabled(self.cb_helpers.isChecked())
        if helpers.has_pool():
            running = helpers.pool().helpers
            st = QLabel("Running now: " + (", ".join(sorted({f"{sum(1 for h in running if h.role == r)} {r.upper()}"
                                                             for r in ("gpu", "cpu")
                                                             if any(h.role == r for h in running)})) or "none (idle)"))
            st.setStyleSheet("color: gray;")
            st.setContentsMargins(24, 0, 0, 0)
            lay.addWidget(st)
        scan = lay

        lay = general
        heading("Known people", lay)
        np_, ns = known.counts()
        self.known_label = QLabel()
        self.set_known_label(np_, ns)
        self.known_label.setWordWrap(True)
        lay.addWidget(self.known_label)
        kr = QHBoxLayout()
        exp, imp, clr = QPushButton("Export people…"), QPushButton("Import people…"), QPushButton("Clear library…")
        exp.clicked.connect(self.export_people)
        imp.clicked.connect(self.import_people)
        clr.clicked.connect(self.clear_library)
        clr.setToolTip("Forget all scanned photos and faces and scan again. Known people are kept.")
        for b in (exp, imp):
            kr.addWidget(b)
        kr.addStretch()
        kr.addWidget(clr)
        lay.addLayout(kr)

        lay = pages["tags"][0]
        heading("Tags already on photos", lay)
        kwl = QLabel("Names in people tags (Lightroom, digiKam, Picasa, Windows Photo Gallery, FaceSort) are always "
                     "used. Plain keywords count as a person when they match someone FaceSort knows.")
        kwl.setWordWrap(True)
        kwl.setStyleSheet("color: gray;")
        lay.addWidget(kwl)
        self.cb_kw = QCheckBox("Treat every plain keyword as a person's name (only if your tags are just names)")
        self.cb_kw.setChecked(self.s.get("keywords_all_people", False))
        lay.addWidget(self.cb_kw)
        tags = lay
        lay = general
        heading("Thumbnails", lay)
        cr = QHBoxLayout()
        self.cache_label = QLabel("Thumbnail cache: counting… (saved so big libraries open instantly)")
        self._count_cache()
        self.cache_label.setStyleSheet("color: gray;")
        clear_cache = QPushButton("Clear")
        clear_cache.clicked.connect(self.clear_thumb_cache)
        cr.addWidget(self.cache_label, 1)
        cr.addWidget(clear_cache)
        lay.addLayout(cr)
        lay = scan
        heading("When to scan", lay)
        self.cb_start = QCheckBox("Look for new photos every time FaceSort opens")
        self.cb_start.setChecked(self.s.get("scan_on_start", False))
        lay.addWidget(self.cb_start)
        self.cb_ai = QCheckBox(f"AI detector (experimental): also judge the pictures themselves for the “Possibly AI” "
                               f"list - often wrong; one-time {aidetect.DETECTOR_MB} MB download, slower scans")
        self.cb_ai.setChecked(bool(self.s.get("ai_detector")))
        lay.addWidget(self.cb_ai)
        self.cb_raw_best = QCheckBox("Duplicates: keep the camera RAW file when a set has one (even if a JPEG is "
                                     "the same size or bigger)")
        self.cb_raw_best.setChecked(bool(self.s.get("dup_prefer_raw", True)))
        lay.addWidget(self.cb_raw_best)
        self.cb_raw_pairs = QCheckBox("Duplicates: a RAW and a JPEG with the same name in the same folder are kept on "
                                      "purpose (cameras save both) - not shown as duplicates")
        self.cb_raw_pairs.setChecked(bool(self.s.get("dup_raw_jpeg_pairs", True)))
        lay.addWidget(self.cb_raw_pairs)
        self.cb_ai_group = QCheckBox("Show an “AI images” group in Smart Groups (otherwise AI images just carry "
                                     "the AI badge; right-click › Not AI takes a wrong one off)")
        self.cb_ai_group.setChecked(bool(self.s.get("show_ai_group", False)))
        lay.addWidget(self.cb_ai_group)
        self.cb_copies = QCheckBox("Show exact copies of a photo once in a gallery (e.g. a group photo saved in each "
                                   "person's folder) - the copies stay where they are")
        self.cb_copies.setChecked(bool(self.s.get("hide_exact_copies", True)))
        lay.addWidget(self.cb_copies)
        lay = tags
        heading("Grouping and suggestions", lay)
        self.cb_folders = QCheckBox("Use my photo folders as hints (a folder often holds the same person: "
                                    "its faces group together more easily, and its name is suggested)")
        self.cb_folders.setChecked(self.s.get("folder_hints", True))
        lay.addWidget(self.cb_folders)
        heading("Generic names", lay)
        self.generic = GenericNamesEditor(generic_names(self.s))
        lay.addWidget(self.generic)
        heading("Tags", lay)
        from tagcolumn import tag_list
        self.cb_tagcol = QCheckBox("Show recommended tags beside the photo in the viewer")
        self.cb_tagcol.setChecked(bool(self.s.get("tag_column", True)))
        lay.addWidget(self.cb_tagcol)
        self.cb_safe_sugg = QCheckBox("Safe view also blurs suggestive photos (underwear, swimwear, partly undressed)")
        self.cb_safe_sugg.setChecked(bool(self.s.get("safe_suggestive")))
        lay.addWidget(self.cb_safe_sugg)
        self.tags_edit = TagListEditor(tag_list(self.s))
        lay.addWidget(self.tags_edit)
        b_learn = QPushButton("Share what FaceSort learned…")
        b_learn.setToolTip("Export your tag and Safe / Suggestive / Explicit learning (no names, faces or photos) "
                           "for another FaceSort, or import one")
        b_learn.clicked.connect(self.share_learned)
        lay.addWidget(b_learn, 0, Qt.AlignLeft)
        heading("Rules for new photos", lay)
        from rules_ui import RulesEditor
        win = self.parent()
        try:
            person_names = win.people_by_recent()
            folder_names = [" › ".join(store.category_paths(win.conn).get(k, [])) or v[0]
                       for k, v in store.folder_tree(win.conn).items() if v[2] is None]
            tag_names = [r[0] for r in win.conn.execute("SELECT name FROM albums ORDER BY tag DESC, name")]
        except Exception:
            person_names, folder_names, tag_names = [], [], []
        self.rules_edit = RulesEditor(win.conn, self.s, person_names, sorted(set(f for f in folder_names if f)),
                                      tag_names)
        lay.addWidget(self.rules_edit)
        lay = scan
        heading("Videos", lay)
        vl = QLabel("Faces are looked for in 7 moments spread through each video; each person is kept once. "
                    "Video tags go into an .xmp file next to the video.")
        vl.setWordWrap(True)
        vl.setStyleSheet("color: gray;")
        lay.addWidget(vl)
        self.video_box = QComboBox()
        self.video_modes = [("after", "Scan videos after all photos are done (recommended)"),
                            ("with", "Scan videos together with photos"),
                            ("separate", "Only when I click “Scan videos”")]
        for _, label in self.video_modes:
            self.video_box.addItem(label)
        self.video_box.setCurrentIndex(next((i for i, (k, _) in enumerate(self.video_modes)
                                             if k == self.s.get("video_mode", "after")), 0))
        vf = QFormLayout()
        vf.addRow("Videos:", self.video_box)
        lay.addLayout(vf)
        lay = tags
        heading("Writing tags into photos", lay)
        self.prefix = QLineEdit(self.s["keyword_prefix"])
        self.prefix.setPlaceholderText('optional, e.g. "People: "')
        pf = QFormLayout()
        pf.addRow("Tag prefix:", self.prefix)
        lay.addLayout(pf)
        self.cb_sugg = QCheckBox("Also tag suggested (not yet confirmed) matches")
        self.cb_sugg.setChecked(self.s["tag_suggestions"])
        self.cb_regions = QCheckBox("Write named face rectangles (shown by digiKam, Lightroom, ACDSee…)")
        self.cb_regions.setChecked(self.s["write_regions"])
        self.cb_sidecar = QCheckBox("Never modify photo files - write .xmp sidecar files instead")
        self.cb_sidecar.setToolTip("Windows Explorer does not read sidecar files, so tags won't show there.")
        self.cb_sidecar.setChecked(self.s["always_sidecar"])
        self.cb_mtime = QCheckBox("Keep each photo's 'Date modified' unchanged")
        self.cb_mtime.setChecked(self.s["keep_modified_date"])
        for cb in (self.cb_sugg, self.cb_regions, self.cb_sidecar, self.cb_mtime):
            lay.addWidget(cb)

        lay = general
        heading("Where FaceSort keeps its data", lay)
        info = QLabel("Your library, known people and settings are kept outside the app folder, so updating "
                      f"FaceSort never touches them:<br><code>{store.data_dir()}</code>")
        info.setWordWrap(True)
        info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        info.setStyleSheet("color: gray;")
        lay.addWidget(info)
        for pl, _ in pages.values():
            pl.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        outer.addWidget(bb)
        self.tabs.setCurrentIndex(int(settings.get("settings_tab", 0) or 0) % self.tabs.count())
        avail = self.screen().availableGeometry() if self.screen() else None
        want = max(b.sizeHint().height() for _, b in pages.values())
        h = want + 110
        if avail:
            h = min(h, int(avail.height() * 0.9))
        self.resize(max(700, max(b.sizeHint().width() for _, b in pages.values()) + 50), h)

    def set_known_label(self, people, samples):
        self.known_label.setText(
            f"FaceSort remembers <b>{people}</b> named {'person' if people == 1 else 'people'} in a separate file, "
            "so they're recognized again even if you clear the library or remove a folder. "
            "Export it as a backup or to use on another PC.")

    def export_people(self):
        dest, _ = QFileDialog.getSaveFileName(self, "Export known people", "FaceSort people.fspeople",
                                              "FaceSort people (*.fspeople)")
        if not dest:
            return
        try:
            win = self.parent()
            if hasattr(win, "remember_now"):
                win.remember_now()               # (everyone up to date first)
            known.export_to(dest)
            QMessageBox.information(self, "Exported", f"Saved to:\n{dest}\n\nKeep it private: it can recognize these people.")
        except Exception as e:
            QMessageBox.warning(self, "Export failed", str(e))

    def import_people(self):
        src, _ = QFileDialog.getOpenFileName(self, "Import known people", "", "FaceSort people (*.fspeople);;All files (*)")
        if not src:
            return
        try:
            n = known.import_from(src)
        except Exception as e:
            QMessageBox.warning(self, "Import failed", f"Couldn't read that file:\n{e}")
            return
        self.imported = True
        known._COUNTS.clear()
        self.set_known_label(*known.counts())
        QMessageBox.information(self, "Imported", f"Imported {n} people. Matching faces will show up as suggestions.")

    def check_updates_now(self):
        import updater
        r = self.update_repo.text().strip().strip("/")
        if not updater.repo({"update_repo": r}):
            QMessageBox.information(self, "Updates", "Fill in the GitHub project first (owner/repository).")
            return
        self.parent().settings["update_repo"] = r
        self.parent().start_update_check(force=True)

    def request_rescan(self, which):
        self.rescan_request = which
        self.accept()

    def clear_library(self):
        if QMessageBox.question(
                self, "Clear library",
                "Forget every scanned photo and face, then scan your folders again?\n\n"
                "Kept: known people (they'll be recognized again), names saved in photo tags, your settings.\n"
                "Your photos themselves are never touched.") == QMessageBox.Yes:
            self.clear_requested = True
            self.accept()

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Add a photo folder")
        if d:
            d = store.norm_path(d)
            if d not in [self.folders.item(i).text() for i in range(self.folders.count())]:
                self.folders.addItem(d)

    def choose_root(self):
        d = QFileDialog.getExistingDirectory(self, "Choose your Photos library folder",
                                             self.s.get("library_root") or "")
        if not d:
            return
        d = store.norm_path(d)
        self.s["library_root"] = d
        self.root_label.setText(d)
        self.rename_root_b.setEnabled(True)
        items = [self.folders.item(i).text() for i in range(self.folders.count())]
        if not any(d == f or d.startswith(f.rstrip("\\/") + os.sep) for f in items):
            self.folders.addItem(d)

    def rename_root(self):
        new = self.win.rename_library_root()
        if new:
            self.s["library_root"] = new
            self.root_label.setText(new)
            self.folders.clear()
            self.folders.addItems(self.win.settings["folders"])
            self.files = list(self.win.settings.get("files", []))

    def _count_cache(self):
        _count_in_background(self)

    def clear_thumb_cache(self):
        import shutil
        shutil.rmtree(thumb_cache_dir(), ignore_errors=True)
        self.cache_label.setText("Thumbnail cache: cleared (rebuilt as you browse)")

    def never_track_list(self):
        from neverfaces_ui import NeverTrackDialog
        win = self.parent()
        d = NeverTrackDialog(self, win.conn)
        d.exec()
        if d.changed:
            try:
                win.after_change()
            except Exception:
                pass

    def share_learned(self):
        from learned_ui import LearnedDialog
        win = self.parent()
        d = LearnedDialog(self, win.conn, self.s)
        d.exec()
        if d.changed:
            from tagcolumn import tag_list
            try:
                self.tags_edit.add_missing(tag_list(self.s))
            except Exception:
                pass
            try:
                win.learned_changed()
            except Exception:
                pass

    def result_settings(self):
        s = self.s
        s["folders"] = [self.folders.item(i).text() for i in range(self.folders.count())]
        s["files"] = list(self.files)
        s[store.threshold_key(self.engine_now)] = self.thr.value() / 100
        s["engine"] = self.engine_box.currentData()
        s["min_face_px"] = self.minface.value()
        s["threads"] = self.threads.value()
        s["use_gpu"] = self.cb_gpu.isChecked()
        s["helpers"] = "on" if self.cb_helpers.isChecked() else "off"
        s["cpu_helpers"] = int(self.helpers_box.currentData() or 0)
        s["fp16"] = self.cb_fp16.isChecked()
        s["detect_threshold"] = self.det.value() / 100
        s["look_sideways"] = self.cb_side.isChecked()
        s["dark_second_look"] = self.cb_dark.isChecked()
        s["same_shoot"] = self.cb_shoot.isChecked()
        s["face_details"] = self.cb_details.isChecked()
        s["content_on"] = self.cb_content.isChecked()
        s["pose_on"] = self.cb_pose.isChecked()
        s["background_cpu"] = "gentle" if self.cb_gentle.isChecked() else "full"
        s["tattoo_min_inliers"] = self.tat.value()
        s["keywords_all_people"] = self.cb_kw.isChecked()
        s["folder_hints"] = self.cb_folders.isChecked()
        s["import_into_library"] = self.cb_import_lib.isChecked()
        s["auto_move"] = self.cb_auto_move.isChecked()
        s["scan_on_start"] = self.cb_start.isChecked()
        s["ai_detector"] = self.cb_ai.isChecked()
        s["show_ai_group"] = self.cb_ai_group.isChecked()
        s["hide_exact_copies"] = self.cb_copies.isChecked()
        s["check_updates"] = self.cb_updates.isChecked()
        s["update_repo"] = self.update_repo.text().strip().strip("/")
        s["dup_prefer_raw"] = self.cb_raw_best.isChecked()
        s["dup_raw_jpeg_pairs"] = self.cb_raw_pairs.isChecked()
        s["video_mode"] = self.video_modes[self.video_box.currentIndex()][0]
        s["keyword_prefix"] = self.prefix.text()
        s["tag_suggestions"] = self.cb_sugg.isChecked()
        s["write_regions"] = self.cb_regions.isChecked()
        s["always_sidecar"] = self.cb_sidecar.isChecked()
        s["keep_modified_date"] = self.cb_mtime.isChecked()
        s["generic_names"] = self.generic.result_list()
        s["tag_list"] = self.tags_edit.result_list()
        self.rules_edit.save_to(s)
        self.backups_page.save_to(s)
        self.viewer_page.save_to(s)
        self.privacy_page.save_to(s)
        s["tag_column"] = self.cb_tagcol.isChecked()
        s["safe_suggestive"] = self.cb_safe_sugg.isChecked()
        s["settings_tab"] = self.tabs.currentIndex()       # opens on the same tab next time
        return s


_gpu_names = None


_gpu_lock = threading.Lock()


def gpu_names(wait=True):
    """Names of the graphics cards (Windows), for display only. Asking Windows takes from a fraction
    of a second to several seconds, so it's done once, in the background (start it with
    wait=False when FaceSort opens); until then wait=False returns []."""
    global _gpu_names
    if _gpu_names is not None:
        return _gpu_names
    if not wait:
        if _gpu_lock.acquire(blocking=False):
            threading.Thread(target=_ask_gpu_names, daemon=True).start()
        return []
    with _gpu_lock:
        pass
    if _gpu_names is None:
        _gpu_lock.acquire()
        _ask_gpu_names()
    return _gpu_names or []


def _ask_gpu_names():
    """(Holding _gpu_lock.) Ask Windows for the graphics cards' names."""
    global _gpu_names
    names = []
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }"],
                capture_output=True, text=True, timeout=20, creationflags=0x08000000)  # no console window
            names = [x.strip() for x in out.stdout.splitlines() if x.strip()]
    except Exception:
        pass
    finally:
        _gpu_names = names
        _gpu_lock.release()


def _count_in_background(dlg):
    """Count the thumbnail cache without holding up the Settings window (big caches take a while)."""
    import threading
    from PySide6.QtCore import QTimer
    box = {}
    threading.Thread(target=lambda: box.__setitem__("r", thumb_cache_size()), daemon=True).start()

    def check():
        if "r" not in box:
            QTimer.singleShot(200, check)
            return
        n, size = box["r"]
        try:
            dlg.cache_label.setText(f"Thumbnail cache: {n:,} pictures, {size / 1e6:,.0f} MB "
                                    "(saved so big libraries open instantly)")
        except RuntimeError:
            pass                                       # (the window was closed meanwhile)
    QTimer.singleShot(200, check)


class GenericNamesEditor(QWidget):
    """The list of generic names (“Model” -> Model 01, Model 02, …). Ticked = shown straight in the
    right-click menu of a face; the others are under “More generic names”."""

    NOTE = ("Right-click a face > Tag new “Model 04” names it with the lowest number nobody has, and "
            "puts that person in the folder “Unknown Model”. When you later rename them, their number "
            "is free again. Ticked names are in the right-click menu itself; the others are under "
            "“More generic names”.")
    TICK_TIP = "Ticked: in the right-click menu. Double-click to rename."
    DEFAULTS = PLACEHOLDER_NAMES
    WHAT = "Generic names"

    def __init__(self, items, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        note = QLabel(self.NOTE)
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        lay.addWidget(note)
        row = QHBoxLayout()
        self.list = QListWidget()
        self.list.setMinimumHeight(150)
        self.list.setMaximumHeight(240)
        self.list.itemChanged.connect(self._edited)
        row.addWidget(self.list, 1)
        col = QVBoxLayout()
        for text, fn, tip in (("Add…", self.add, "Add a generic name"),
                              ("Rename…", self.rename, "Rename the selected one (people already tagged keep their names)"),
                              ("Remove", self.remove, "Remove it from the list (people already tagged keep their names)"),
                              ("Move up", lambda: self.move(-1), ""), ("Move down", lambda: self.move(1), ""),
                              ("Defaults", self.defaults, "Add back the names FaceSort started with")):
            b = QPushButton(text)
            b.setAutoDefault(False)
            if tip:
                b.setToolTip(tip)
            b.clicked.connect(fn)
            col.addWidget(b)
        col.addStretch()
        row.addLayout(col)
        lay.addLayout(row)
        for it in items:
            self._add_item(it["name"], it["menu"])

    def _add_item(self, name, menu=True, select=False):
        it = QListWidgetItem(name)
        it.setFlags(it.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEditable)
        it.setCheckState(Qt.Checked if menu else Qt.Unchecked)
        it.setToolTip(self.TICK_TIP)
        it.setData(Qt.UserRole, name)
        self.list.addItem(it)
        if select:
            self.list.setCurrentItem(it)
        return it

    def names(self, skip=None):
        return [self.list.item(i).text() for i in range(self.list.count()) if self.list.item(i) is not skip]

    def _valid(self, text, skip=None):
        name = clean_generic(text)
        if not name:
            QMessageBox.information(self, self.WHAT, "Type a name without a number at the end.")
            return ""
        if name.casefold() in {n.casefold() for n in self.names(skip)}:
            QMessageBox.information(self, self.WHAT, f"“{name}” is already in the list.")
            return ""
        return name

    def _edited(self, it):
        old = it.data(Qt.UserRole)
        if it.text() == old:
            return                                   # just ticked / unticked
        name = self._valid(it.text(), skip=it) if it.text().strip() != old else old
        self.list.blockSignals(True)
        it.setText(name or old)
        it.setData(Qt.UserRole, name or old)
        self.list.blockSignals(False)

    def add(self):
        text, ok = QInputDialog.getText(self, "Add a generic name", "Name (a number is added for each person):")
        if ok:
            name = self._valid(text)
            if name:
                self._add_item(name, True, select=True)

    def rename(self):
        it = self.list.currentItem()
        if it:
            text, ok = QInputDialog.getText(self, "Rename", "New name:", text=it.text())
            if ok and text.strip() and text.strip() != it.text():
                name = self._valid(text, skip=it)
                if name:
                    self.list.blockSignals(True)
                    it.setText(name)
                    it.setData(Qt.UserRole, name)
                    self.list.blockSignals(False)

    def remove(self):
        r = self.list.currentRow()
        if r >= 0:
            self.list.takeItem(r)

    def move(self, d):
        r = self.list.currentRow()
        if r < 0 or not 0 <= r + d < self.list.count():
            return
        it = self.list.takeItem(r)
        self.list.insertItem(r + d, it)
        self.list.setCurrentRow(r + d)

    def defaults(self):
        have = {n.casefold() for n in self.names()}
        for n in self.DEFAULTS:
            if n.casefold() not in have:
                self._add_item(n, True)

    def result_list(self):
        return [{"name": self.list.item(i).text(), "menu": self.list.item(i).checkState() == Qt.Checked}
                for i in range(self.list.count())]

    def add_missing(self, items):
        have = {n.casefold() for n in self.names()}
        for it in items:
            if it["name"].casefold() not in have:
                have.add(it["name"].casefold())
                self._add_item(it["name"], it.get("menu", True))


class TagListEditor(GenericNamesEditor):
    """Settings > Tagging > Tags: the tags the viewer's tag column suggests."""
    NOTE = ("Beside each photo in the viewer, FaceSort suggests up to 10 of these tags (and your albums): what's in "
            "the photo (Explicit, Lingerie…), the scene (Beach, Studio…), and your own tags - learned from the "
            "photos you put in them. Click a tag to add it; each tag is also an album, and is written into the "
            "file as a keyword. Unticked tags aren't suggested.")
    TICK_TIP = "Ticked: suggested in the viewer. Double-click to rename."
    WHAT = "Tags"

    @property
    def DEFAULTS(self):
        import content
        import pose
        return content.CONTENT_TAGS + content.SCENE_TAGS + pose.POSES


class GenericNamesDialog(QDialog):
    """“Edit generic names…” from a right-click menu."""

    def __init__(self, parent, items):
        super().__init__(parent)
        self.setWindowTitle("Generic names")
        self.resize(560, 380)
        lay = QVBoxLayout(self)
        self.editor = GenericNamesEditor(items)
        lay.addWidget(self.editor)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def result_list(self):
        return self.editor.result_list()


class ExportDialog(QDialog):
    def __init__(self, win, settings):
        super().__init__(win)
        self.setWindowTitle("Sort photos into folders")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Make one folder per person in:"))
        layout_note = QLabel("The layout follows the Folders list on the left: Folder\\Sub-folder\\Person\\"
                             "Inner folder. Drag people and folders there to change it.")
        layout_note.setWordWrap(True)
        layout_note.setStyleSheet("color: gray;")
        lay.addWidget(layout_note)
        row = QHBoxLayout()
        self.path = QLineEdit(settings.get("library_root") or settings.get("export_dir", ""))
        self.path.setPlaceholderText("Choose a folder…")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse)
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        lay.addLayout(row)
        lay.addSpacing(8)
        self.copy_rb = QRadioButton("Copy photos   (your originals stay where they are)")
        self.move_rb = QRadioButton("Move photos   (originals are moved out of their current folders)")
        (self.move_rb if settings.get("export_move") else self.copy_rb).setChecked(True)
        lay.addWidget(self.copy_rb)
        lay.addWidget(self.move_rb)
        self.ai_cb = QCheckBox("Put photos marked as AI-made in “~AI generated” (instead of the people's folders)")
        self.ai_cb.setChecked(bool(settings.get("export_ai_folder")))
        lay.addWidget(self.ai_cb)
        self.conv_cb = QCheckBox("Convert other formats so they open everywhere")
        self.conv_cb.setChecked(settings.get("export_convert", True))
        lay.addWidget(self.conv_cb)
        cn = QLabel("WebP / HEIC / AVIF / BMP / TIFF photos → JPEG (quality 95); see-through pictures and screenshots → "
                    "PNG (lossless) - also when a file has the wrong name. JPEG, PNG, RAW and animated GIFs stay as they "
                    "are. When moving, the originals go to “~To Be Discarded”.")
        cn.setWordWrap(True)
        cn.setContentsMargins(22, 0, 0, 0)
        cn.setStyleSheet("color: gray;")
        lay.addWidget(cn)
        ndrop = win.conn.execute("SELECT COUNT(*) FROM photos WHERE dropped IS NOT NULL AND error IS NULL").fetchone()[0]
        self.drop_cb = QCheckBox(f"Also sort the {ndrop:,} photo{'s' if ndrop != 1 else ''} in Recently dropped "
                                 "(not checked yet)")
        lay.addWidget(self.drop_cb)
        self.drop_cb.setVisible(ndrop > 0)
        # (1.53) new file names as they're sorted - offered every time, never ticked to begin with
        import rename_ui
        self.rename_cb = QCheckBox("Rename the files as they're sorted")
        self.rename_cb.setChecked(False)
        lay.addWidget(self.rename_cb)
        self.rename_box = rename_ui.PatternBox(settings.get("sort_rename_pattern") or "{person} {date} {n}")
        self.rename_box.setContentsMargins(22, 0, 0, 0)
        self.rename_box.setVisible(False)
        lay.addWidget(self.rename_box)
        self.rename_cb.toggled.connect(self.rename_box.setVisible)
        note = QLabel(
            "When moving: a photo with several people is moved to the person with the biggest face in it, "
            "and the others get a copy. Photos marked to discard go to “~To Be Discarded”, and Sort Later "
            "photos to “~Sort Later”. "
            "Photos with nobody named stay where they are. Names stay attached, and a list of every move "
            "is saved in the destination folder.")
        note.setWordWrap(True)
        note.setContentsMargins(22, 0, 0, 0)
        note.setStyleSheet("color: gray;")
        lay.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = bb.button(QDialogButtonBox.Ok)
        bb.accepted.connect(self.check)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.move_rb.toggled.connect(self.update_ok)
        self.path.textChanged.connect(self.update_ok)
        self.update_ok()

    def browse(self):
        d = QFileDialog.getExistingDirectory(self, "Choose where to create the person folders", self.path.text())
        if d:
            self.path.setText(os.path.normpath(d))

    def update_ok(self, *_):
        self.ok.setText("Move photos" if self.move_rb.isChecked() else "Copy photos")
        self.ok.setEnabled(bool(self.path.text().strip()))

    def dest(self):
        return os.path.normpath(self.path.text().strip())

    def move(self):
        return self.move_rb.isChecked()

    def ai_folder(self):
        return self.ai_cb.isChecked()

    def include_dropped(self):
        return self.drop_cb.isChecked()

    def convert(self):
        return self.conv_cb.isChecked()

    def rename(self):
        """The rename pattern, or None (files keep their names)."""
        return (self.rename_box.text() or None) if self.rename_cb.isChecked() else None

    def check(self):
        d = self.dest()
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "Folder", f"Can't use that folder:\n{e}")
            return
        if self.move() and QMessageBox.question(
                self, "Move photos",
                f"Move your named photos into person folders in:\n{d}\n\n"
                "They will no longer be in their current folders. Continue?") != QMessageBox.Yes:
            return
        self.accept()


# =========================================================================== main window
class EnhanceCopyDialog(QDialog):
    """Save enhanced copy: low light, Steady, frame rate (24 - 60), how new frames are made,
    and the whole video or just the marked section."""

    def __init__(self, viewer, path, low_light, marks):
        super().__init__(viewer)
        self.setWindowTitle("Save an enhanced copy")
        self.path = path
        st = viewer.win.settings
        try:
            vs = engine.VideoSource(path)
            self.src_fps, self.n = (vs.fps or 25.0), vs.n
            self.W, self.H = vs.W, vs.H
            vs.release()
        except Exception:
            self.src_fps, self.n, self.W, self.H = 25.0, 0, 0, 0
        self.length = self.n / self.src_fps if self.n > 0 else None
        lay = QVBoxLayout(self)
        head = QLabel(f"<b>{html.escape(os.path.basename(path))}</b><br><span style='color:gray'>"
                      f"{self.W} × {self.H} · {jobs.fps_label(self.src_fps)} fps"
                      + (f" · {engine.clock(self.length)}" if self.length else "") + "</span>")
        head.setTextFormat(Qt.RichText)
        lay.addWidget(head)
        form = QFormLayout()
        self.ll = QComboBox()
        self.ll.addItems(["Off", "On", "Strong"])
        self.ll.setCurrentIndex(max(0, min(2, int(low_light or 0))))
        form.addRow("Low light:", self.ll)
        self.steady = QCheckBox("Steady - calm the flicker and crawling grain between frames")
        self.steady.setToolTip("Evens out brightness flicker and averages the grain over neighbouring frames "
                               "where nothing moves (moving things stay sharp). Best for dark or noisy video.")
        self.steady.setChecked(bool(st.get("copy_steady", False)))
        form.addRow("Smoothing:", self.steady)
        self.fps = QComboBox()
        if 24 - 0.01 <= self.src_fps <= 60 + 0.01:
            self.fps.addItem(f"Same as the video ({jobs.fps_label(self.src_fps)} fps)", None)
        names = {24.0: "24 fps (cinema)", 25.0: "25 fps (PAL / Europe TV)", 30000 / 1001: "29.97 fps (NTSC / US TV)",
                 30.0: "30 fps (phones, web)", 48.0: "48 fps (high frame rate cinema)", 50.0: "50 fps (PAL HD)",
                 60000 / 1001: "59.94 fps (NTSC HD)", 60.0: "60 fps (smooth, gaming)"}
        for v, _r in jobs.FPS_CHOICES:
            self.fps.addItem(names[v], v)
        want = st.get("copy_fps")
        i = next((k for k in range(self.fps.count()) if self.fps.itemData(k) is not None and want is not None
                  and abs(self.fps.itemData(k) - want) < 0.005), 0 if self.fps.itemData(0) is None else
                 next(k for k in range(self.fps.count()) if self.fps.itemData(k) == 30.0))
        self.fps.setCurrentIndex(i)
        form.addRow("Frame rate:", self.fps)
        self.method = QComboBox()
        for txt, key in (("Motion interpolation - new in-between frames (smoothest, slow)", "motion"),
                         ("Blend neighbouring frames (quick, soft on fast movement)", "blend"),
                         ("Repeat / drop frames (quickest, no extra smoothness)", "repeat")):
            self.method.addItem(txt, key)
        self.method.setCurrentIndex(max(0, self.method.findData(st.get("copy_method", "motion"))))
        form.addRow("New frames:", self.method)
        a, b = marks
        self.whole = QRadioButton("Whole video" + (f"  ({engine.clock(self.length)})" if self.length else ""))
        self.part = QRadioButton(f"Only between the markers  ({engine.clock(a or 0)} – "
                                 f"{engine.clock(b) if b is not None else (engine.clock(self.length) if self.length else 'end')})")
        self.marks = (a, b)
        has = a is not None or b is not None
        self.part.setEnabled(has)
        (self.part if has else self.whole).setChecked(True)
        if not has:
            self.part.setText("Only between the markers  (set them with ⇤ ⇥ or { } on the position bar)")
        sec = QVBoxLayout()
        sec.addWidget(self.whole)
        sec.addWidget(self.part)
        form.addRow("Section:", sec)
        lay.addLayout(form)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setTextFormat(Qt.RichText)
        lay.addWidget(self.note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = bb.button(QDialogButtonBox.Ok)
        self.ok.setText("Save copy")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        for w in (self.ll, self.fps, self.method):
            w.currentIndexChanged.connect(self.update_note)
        for w in (self.steady, self.whole, self.part):
            w.toggled.connect(self.update_note)
        self.st = st
        self.setMinimumWidth(560)
        self.update_note()

    def options(self):
        fps = self.fps.currentData()
        a, b = self.marks if self.part.isChecked() else (None, None)
        o = {"strength": self.ll.currentIndex(), "steady": self.steady.isChecked(),
             "fps": fps, "method": self.method.currentData(), "start": a, "end": b}
        return o

    def accept(self):
        self.st["copy_steady"] = self.steady.isChecked()
        self.st["copy_fps"] = self.fps.currentData()
        self.st["copy_method"] = self.method.currentData()
        super().accept()

    def update_note(self, *_):
        o = self.options()
        change = o["fps"] is not None and abs(o["fps"] - self.src_fps) > 0.005
        self.method.setEnabled(change)
        a, b = o["start"], o["end"]
        dur = ((b if b is not None else (self.length or 0)) - (a or 0)) if self.length else None
        nothing = not (o["strength"] or o["steady"] or change or a is not None or b is not None)
        self.ok.setEnabled(not nothing)
        if nothing:
            self.note.setText("<span style='color:gray'>Nothing to change - pick low light, Steady, another "
                              "frame rate or a section.</span>")
            return
        name = os.path.basename(jobs.copy_name(self.path, o, self.src_fps))
        lines = [f"Saved next to the video as <b>{html.escape(name)}</b> (H.264 MP4, with the sound), tagged like it."]
        if change and o["method"] == "motion":
            mins = (dur or 0) * (self.W * self.H) / (1280 * 720) * 12 / max(1, min(8, (os.cpu_count() or 2) - 1)) / 60
            lines.append("<span style='color:#d97706'>Motion interpolation is slow" +
                         (f" - roughly {max(1, round(mins))} min for this length" if dur else "") +
                         ". On dark, grainy video it can smear or warp moving things; turning on Steady first "
                         "helps. Try it on a short marked section first.</span>")
        if change and o["fps"] < self.src_fps - 0.005:
            lines.append("<span style='color:gray'>A lower frame rate than the video's drops frames.</span>")
        self.note.setText("<br>".join(lines))


# =========================================================================== scan
class ScanDialog(QDialog):
    """What the next scan does: new files and/or checking the ones already in the library, which
    folders, how many, videos, other files, and what follows the scan. self.opts after OK."""

    def __init__(self, win, settings, chosen_folders=None, waiting=0, dark_unchecked=0, nofaces=0):
        super().__init__(win)
        self.setWindowTitle("Scan")
        self.setMinimumWidth(620)
        o = jobs.scan_options(settings, settings.get("scan_opts") if settings.get("scan_remember", True) else None)
        lay = QVBoxLayout(self)

        def heading(text):
            lb = QLabel(text)
            f = lb.font()
            f.setBold(True)
            lb.setFont(f)
            lb.setStyleSheet(f"color: {HEAD_COL.name()};")
            if lay.count():
                lay.addSpacing(6)
            lay.addWidget(lb)

        def note(text):
            lb = QLabel(text)
            lb.setWordWrap(True)
            lb.setStyleSheet("color: gray;")
            lay.addWidget(lb)
            return lb

        heading("What to do")
        self.rb_new = QRadioButton("Import new files only")
        self.rb_verify = QRadioButton("Check the files already in the library (changed, moved or deleted)")
        self.rb_both = QRadioButton("Both")
        grp = QButtonGroup(self)
        for rb in (self.rb_both, self.rb_new, self.rb_verify):
            grp.addButton(rb)
        {"new": self.rb_new, "verify": self.rb_verify}.get(o["mode"], self.rb_both).setChecked(True)
        for rb in (self.rb_new, self.rb_verify, self.rb_both):
            lay.addWidget(rb)

        heading("Folders")
        self.folder_list = QListWidget()
        self.folder_list.setMaximumHeight(110)
        pick = {os.path.normcase(f) for f in (chosen_folders or o["folders"] or [])}
        from PySide6.QtWidgets import QListWidgetItem
        for f in settings.get("folders", []):
            it = QListWidgetItem(f)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if not pick or os.path.normcase(store.norm_path(f)) in pick
                             or os.path.normcase(f) in pick else Qt.Unchecked)
            self.folder_list.addItem(it)
        lay.addWidget(self.folder_list)
        row = QHBoxLayout()
        row.addWidget(QLabel("Leave out folders named:"))
        self.exclude = QLineEdit("; ".join(o["exclude"]))
        self.exclude.setPlaceholderText("e.g. Thumbnails; Old backups; D:\\Photos\\Scans")
        self.exclude.setToolTip("Folder names (left out wherever they are) or full folder paths, separated by ; \n"
                                "Recycle bins, system folders and thumbnail caches are always left out.\n"
                                "Files already in the library from those folders stay in it.")
        row.addWidget(self.exclude, 1)
        lay.addLayout(row)

        heading("New files")
        row = QHBoxLayout()
        row.addWidget(QLabel("Import up to"))
        self.limit = QSpinBox()
        self.limit.setRange(0, 10_000_000)
        self.limit.setSingleStep(100)
        self.limit.setSpecialValueText("no limit")
        self.limit.setValue(o["limit"])
        row.addWidget(self.limit)
        row.addWidget(QLabel("photos and videos,"))
        self.order = QComboBox()
        for key, text in (("folder", "folder by folder"), ("newest", "newest first"), ("oldest", "oldest first")):
            self.order.addItem(text, key)
        self.order.setCurrentIndex(max(0, self.order.findData(o["order"])))
        row.addWidget(self.order)
        row.addStretch(1)
        lay.addLayout(row)
        if waiting:
            note(f"{waiting:,} new files are waiting from the last scan.")
        row = QHBoxLayout()
        self.cb_videos = QCheckBox("Include videos,")
        self.cb_videos.setChecked(o["videos"])
        self.video_mode = QComboBox()
        self.video_mode.addItem("after all the photos", "after")
        self.video_mode.addItem("together with the photos", "with")
        self.video_mode.setCurrentIndex(max(0, self.video_mode.findData(o["video_mode"])))
        self.cb_videos.toggled.connect(self.video_mode.setEnabled)
        self.video_mode.setEnabled(o["videos"])
        row.addWidget(self.cb_videos)
        row.addWidget(self.video_mode)
        row.addStretch(1)
        lay.addLayout(row)
        self.cb_others = QCheckBox("Include other files (documents, music, archives… - everything except system files)")
        self.cb_others.setChecked(o["others"])
        self.cb_others.setToolTip("They're only recorded - never opened (archives are never looked inside).\n"
                                  "They show in Smart Groups > Unassociated files until you tag them with people\n"
                                  "or put them in a folder; then they move with Sort into folders like photos do.")
        lay.addWidget(self.cb_others)
        row = QHBoxLayout()
        self.cb_small = QCheckBox("Skip pictures smaller than")
        self.small_kb = QSpinBox()
        self.small_kb.setRange(1, 10_000)
        self.small_kb.setSuffix(" KB")
        self.small_kb.setValue(o["min_kb"] or 20)
        self.cb_small.setChecked(bool(o["min_kb"]))
        self.small_kb.setEnabled(bool(o["min_kb"]))
        self.cb_small.toggled.connect(self.small_kb.setEnabled)
        self.cb_small.setToolTip("Icons, web thumbnails and app pictures - they fill the Unknown list with nothing useful.")
        row.addWidget(self.cb_small)
        row.addWidget(self.small_kb)
        row.addWidget(QLabel("(icons, web thumbnails)"))
        row.addStretch(1)
        lay.addLayout(row)

        heading("Files already in the library")
        row = QHBoxLayout()
        row.addWidget(QLabel("Files that are no longer there:"))
        self.missing = QComboBox()
        self.missing.addItem("take them out of the library", "remove")
        self.missing.addItem("keep them, and show me the list", "keep")
        self.missing.setCurrentIndex(max(0, self.missing.findData(o["missing"])))
        self.missing.setToolTip("Files on a drive that isn't plugged in are always kept.")
        row.addWidget(self.missing)
        row.addStretch(1)
        lay.addLayout(row)
        self.cb_nofaces = QCheckBox(f"Look again in photos where no face was found ({nofaces:,})")
        self.cb_nofaces.setChecked(False)
        self.cb_nofaces.setEnabled(nofaces > 0)
        lay.addWidget(self.cb_nofaces)
        self.cb_dark = QCheckBox(f"Look again in dark photos scanned before the second look existed "
                                 f"({dark_unchecked:,} to check)")
        self.cb_dark.setChecked(False)
        self.cb_dark.setEnabled(dark_unchecked > 0 and settings.get("dark_second_look", True))
        lay.addWidget(self.cb_dark)

        heading("After the scan")
        self.cb_dupes = QCheckBox("Find duplicates right away")
        self.cb_dupes.setChecked(o["dupes_after"])
        lay.addWidget(self.cb_dupes)
        self.cb_shoot = QCheckBox("Same-shoot suggestions for the new photos")
        self.cb_shoot.setChecked(o["shoot_after"])
        self.cb_shoot.setVisible(bool(settings.get("same_shoot")))
        lay.addWidget(self.cb_shoot)
        self.cb_light = QCheckBox("Lighter scan: one CPU thread, so the PC stays quick while you work (slower)")
        self.cb_light.setChecked(o["light"])
        lay.addWidget(self.cb_light)
        lay.addSpacing(6)
        self.cb_remember = QCheckBox("Remember these choices (also used when FaceSort scans on opening)")
        self.cb_remember.setChecked(settings.get("scan_remember", True))
        lay.addWidget(self.cb_remember)

        for rb in (self.rb_new, self.rb_verify, self.rb_both):
            rb.toggled.connect(self.sync)
        self.sync()
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Scan")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.opts = None
        self.remember = True

    def sync(self):
        new_ok = not self.rb_verify.isChecked()
        old_ok = not self.rb_new.isChecked()
        for w in (self.limit, self.order, self.cb_small):
            w.setEnabled(new_ok)
        self.small_kb.setEnabled(new_ok and self.cb_small.isChecked())
        for w in (self.missing,):
            w.setEnabled(old_ok)
        self.cb_nofaces.setEnabled(old_ok and "(0)" not in self.cb_nofaces.text())
        self.cb_dark.setEnabled(old_ok and "(0 to check)" not in self.cb_dark.text())

    def accept(self):
        folders = [self.folder_list.item(i).text() for i in range(self.folder_list.count())
                   if self.folder_list.item(i).checkState() == Qt.Checked]
        if not folders:
            QMessageBox.information(self, "Scan", "Tick at least one folder.")
            return
        all_f = self.folder_list.count()
        self.opts = {
            "mode": "new" if self.rb_new.isChecked() else "verify" if self.rb_verify.isChecked() else "both",
            "folders": None if len(folders) == all_f else [store.norm_path(f) for f in folders],
            "limit": self.limit.value(), "order": self.order.currentData(),
            "videos": self.cb_videos.isChecked(), "video_mode": self.video_mode.currentData(),
            "others": self.cb_others.isChecked(),
            "min_kb": self.small_kb.value() if self.cb_small.isChecked() else 0,
            "exclude": [e.strip() for e in self.exclude.text().split(";") if e.strip()],
            "missing": self.missing.currentData(),
            "nofaces_again": self.cb_nofaces.isChecked() and self.cb_nofaces.isEnabled(),
            "dark_again": self.cb_dark.isChecked() and self.cb_dark.isEnabled(),
            "dupes_after": self.cb_dupes.isChecked(), "shoot_after": self.cb_shoot.isChecked(),
            "light": self.cb_light.isChecked()}
        self.remember = self.cb_remember.isChecked()
        super().accept()
