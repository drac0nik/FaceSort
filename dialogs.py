"""Dialogs: Settings, Export, Save an enhanced copy, and the drop dialog."""
import html
import os
import subprocess
import sys
import aidetect
import engine
import engine_hq
import jobs
import known
import store
from PySide6.QtCore import Qt
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
        names = gpu_names()
        if names:
            g = QLabel("Graphics cards in this PC: " + ", ".join(names))
            g.setStyleSheet("color: gray;")
            g.setWordWrap(True)
            g.setContentsMargins(24, 0, 0, 0)
            lay.addWidget(g)
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
        n, size = thumb_cache_size()
        self.cache_label = QLabel(f"Thumbnail cache: {n:,} pictures, {size / 1e6:,.0f} MB "
                                  "(saved so big libraries open instantly)")
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

    def clear_thumb_cache(self):
        import shutil
        shutil.rmtree(thumb_cache_dir(), ignore_errors=True)
        self.cache_label.setText("Thumbnail cache: cleared (rebuilt as you browse)")

    def result_settings(self):
        s = self.s
        s["folders"] = [self.folders.item(i).text() for i in range(self.folders.count())]
        s["files"] = list(self.files)
        s[store.threshold_key(self.engine_now)] = self.thr.value() / 100
        s["engine"] = self.engine_box.currentData()
        s["min_face_px"] = self.minface.value()
        s["threads"] = self.threads.value()
        s["use_gpu"] = self.cb_gpu.isChecked()
        s["detect_threshold"] = self.det.value() / 100
        s["look_sideways"] = self.cb_side.isChecked()
        s["dark_second_look"] = self.cb_dark.isChecked()
        s["same_shoot"] = self.cb_shoot.isChecked()
        s["background_cpu"] = "gentle" if self.cb_gentle.isChecked() else "full"
        s["tattoo_min_inliers"] = self.tat.value()
        s["keywords_all_people"] = self.cb_kw.isChecked()
        s["folder_hints"] = self.cb_folders.isChecked()
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
        s["settings_tab"] = self.tabs.currentIndex()       # opens on the same tab next time
        return s


_gpu_names = None


def gpu_names():
    """Names of the graphics cards (Windows), for display only."""
    global _gpu_names
    if _gpu_names is None:
        _gpu_names = []
        if sys.platform == "win32":
            try:
                out = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }"],
                    capture_output=True, text=True, timeout=8, creationflags=0x08000000)  # no console window
                _gpu_names = [x.strip() for x in out.stdout.splitlines() if x.strip()]
            except Exception:
                pass
    return _gpu_names


class GenericNamesEditor(QWidget):
    """The list of generic names (“Model” -> Model 01, Model 02, …). Ticked = shown straight in the
    right-click menu of a face; the others are under “More generic names”."""

    def __init__(self, items, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        note = QLabel("Right-click a face > Tag new “Model 04” names it with the lowest number nobody has, and "
                      "puts that person in the folder “Unknown Model”. When you later rename them, their number "
                      "is free again. Ticked names are in the right-click menu itself; the others are under "
                      "“More generic names”.")
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
        it.setToolTip("Ticked: in the right-click menu. Double-click to rename.")
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
            QMessageBox.information(self, "Generic names", "Type a name without a number at the end - "
                                    "the number is added for each new person.")
            return ""
        if name.casefold() in {n.casefold() for n in self.names(skip)}:
            QMessageBox.information(self, "Generic names", f"“{name}” is already in the list.")
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
        for n in PLACEHOLDER_NAMES:
            if n.casefold() not in have:
                self._add_item(n, True)

    def result_list(self):
        return [{"name": self.list.item(i).text(), "menu": self.list.item(i).checkState() == Qt.Checked}
                for i in range(self.list.count())]


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


class DropDialog(QDialog):
    """Asked right away when photos, videos or folders are dropped on the window: who's in
    them / which category - applied to every photo as it is scanned."""

    def __init__(self, win, dirs, files, already):
        super().__init__(win)
        self.setWindowTitle("Add and tag")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        nvid = sum(1 for f in files if engine.is_video(f))
        parts = []
        if dirs:
            parts.append(f"{len(dirs)} folder{'s' if len(dirs) != 1 else ''}")
        if len(files) - nvid:
            parts.append(f"{len(files) - nvid:,} photo{'s' if len(files) - nvid != 1 else ''}")
        if nvid:
            parts.append(f"{nvid:,} video{'s' if nvid != 1 else ''}")
        head = QLabel(f"<b>Adding {' + '.join(parts)}</b>")
        lay.addWidget(head)
        names = [os.path.basename(p.rstrip("\\/")) or p for p in dirs + files]
        sub = ElidedLabel(", ".join(names[:12]) + ("…" if len(names) > 12 else ""))
        sub.setStyleSheet("color: gray;")
        lay.addWidget(sub)
        if already:
            al = QLabel(f"{already:,} of these {'is' if already == 1 else 'are'} already in your library - "
                        "they get the tags right away.")
            al.setStyleSheet("color: gray;")
            lay.addWidget(al)
        lay.addSpacing(6)
        form = QFormLayout()
        people = win.people_by_recent()
        self.people = QLineEdit()
        self.people.setPlaceholderText("Who's in them? Separate names with commas - leave empty if mixed")
        comp = QCompleter(people, self.people)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setFilterMode(Qt.MatchContains)
        self.people.setCompleter(comp)
        # completer works on the name after the last comma
        comp.activated[str].connect(lambda t: self.people.setText(
            ", ".join([x.strip() for x in self.people.text().split(",")[:-1] if x.strip()] + [t])))
        self.people.textEdited.connect(lambda t: comp.setCompletionPrefix(t.split(",")[-1].strip()))
        form.addRow("People:", self.people)
        self.category = QComboBox()
        self.category.setEditable(True)
        sugg = []
        if len(dirs) == 1 and not files:
            sugg.append(os.path.basename(dirs[0].rstrip("\\/")))
        for c in win.categories_by_use():
            if c.casefold() not in {x.casefold() for x in sugg}:
                sugg.append(c)
        self.category.addItems([""] + sugg)
        self.category.lineEdit().setPlaceholderText("Folder (an event or group) - optional")
        form.addRow("Folder:", self.category)
        lay.addLayout(form)
        # a folder named like someone you know: offer them
        if len(dirs) == 1 and not files:
            fname = os.path.basename(dirs[0].rstrip("\\/"))
            match = next((n for n in people if n.casefold() == fname.casefold()), None)
            if match:
                self.people.setText(match)
            elif not store.is_generic_folder(fname):
                use = QPushButton(f"Use the folder name “{fname}” as the person")
                use.setFlat(True)
                use.setStyleSheet("text-align: left; color: palette(link);")
                use.setCursor(Qt.PointingHandCursor)
                use.clicked.connect(lambda: (self.people.setText(fname), self.people.setFocus()))
                form.addRow("", use)
        # ---- folder trees: use the folder names (which level is the person?)
        self.structure_group = None
        self.levels = self.folder_levels(dirs) if dirs else []
        if self.levels and (len(self.levels) > 1 or any(len(n) for n in self.levels)):
            lay.addSpacing(6)
            lay.addWidget(QLabel("<b>Use the folder names?</b>"))
            self.structure_group = QButtonGroup(self)
            has_subs = len(self.levels) > 1
            top_name = os.path.basename(dirs[0].rstrip("\\/")) if dirs else "the folder"
            opts = [(-2, f"Remember “{top_name}” for whoever gets tagged - now or later "
                         f"(saved as Person\\{top_name}" + ("\\…" if has_subs else "") + ")")] + \
                ([(-1, "Keep the sub-folders - inside the folder of each person named above")] if has_subs else []) + [
                (None, "No - just tag them as above" + (" (sub-folders aren't kept)" if has_subs else "")),
                (0, "Keep the folder structure (folders become folders, no person)")]
            for i, names in enumerate(self.levels, 1):
                shown = ", ".join(names[:4]) + (f"  (+{len(names) - 4} more)" if len(names) > 4 else "")
                opts.append((i, f"Level {i} is the person:  {shown}"))
            for j, (val, text) in enumerate(opts):
                rb = QRadioButton(text)
                rb.setProperty("level", -9 if val is None else val)
                self.structure_group.addButton(rb, j)
                lay.addWidget(rb)
                if j == 0:
                    rb.setChecked(True)
                rb.toggled.connect(self.update_preview)
            self.preview = QLabel()
            self.preview.setWordWrap(True)
            self.preview.setStyleSheet("color: gray;")
            self.preview.setTextFormat(Qt.RichText)
            lay.addWidget(self.preview)
            self.example = self.example_file(dirs)
            self.update_preview()
            self.people.textChanged.connect(self.update_preview)
        self.later = QCheckBox("Put them in Sort Later (out of the way until you're ready)")
        lay.addWidget(self.later)
        note = QLabel("People are tagged on every photo and video (a photo with one face gets that face named). "
                      "Faces are still found and grouped as usual.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        lay.addWidget(note)
        bb = QDialogButtonBox()
        self.ok = bb.addButton("Tag and scan", QDialogButtonBox.AcceptRole)
        self.plain = bb.addButton("Just scan", QDialogButtonBox.AcceptRole)
        bb.addButton(QDialogButtonBox.Cancel)
        self.ok.setDefault(True)
        self.choice = None
        self.ok.clicked.connect(lambda: self._done("tag"))
        self.plain.clicked.connect(lambda: self._done("plain"))
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.people.setFocus()

    @staticmethod
    def folder_levels(dirs, max_levels=6, max_dirs=20000):
        """Folder names at each depth of the dropped folders (level 1 = the folders themselves)."""
        levels, seen = [], 0
        todo = [(d, 1) for d in dirs]
        while todo and seen < max_dirs:
            d, lvl = todo.pop(0)
            seen += 1
            if lvl > max_levels:
                continue
            while len(levels) < lvl:
                levels.append([])
            name = os.path.basename(d.rstrip("\\/"))
            if name not in levels[lvl - 1]:
                levels[lvl - 1].append(name)
            try:
                subs = sorted(e.path for e in os.scandir(d) if e.is_dir() and not e.name.startswith((".", "$")))
            except OSError:
                subs = []
            todo += [(x, lvl + 1) for x in subs]
        return levels

    @staticmethod
    def example_file(dirs):
        """The deepest photo/video found (for the preview)."""
        best = None
        for d in dirs:
            for p in jobs.iter_photos(d):
                depth = p.count(os.sep)
                if best is None or depth > best[0]:
                    best = (depth, p, d)
                if depth - d.count(os.sep) >= 6:
                    break
        return best

    def level(self):
        if not self.structure_group:
            return None
        b = self.structure_group.checkedButton()
        v = b.property("level") if b else -9
        return None if v is None or v == -9 else int(v)

    def update_preview(self, *_):
        lvl = self.level()
        self.category.setEnabled(lvl is None or lvl in (-1, -2))
        if not self.example:
            self.preview.setText("")
            return
        _, path, d = self.example
        rel = os.path.relpath(path, os.path.dirname(d.rstrip("\\/")))
        if lvl is None:
            self.preview.setText(f"e.g. {html.escape(rel)}")
            return
        if lvl == -2:
            _, top, below = store.structure_of(d, path, 1)
            names = [n for n in (" ".join(x.split()) for x in self.people.text().split(",")) if n]
            who = html.escape(names[0]) if names else "<i>each person tagged</i>"
            parts = [f"<b>{who}</b>"] + [html.escape(x) for x in [top] + below if x]
            self.preview.setText(f"e.g. {html.escape(rel)}<br>→ saved as  "
                                 + "\\".join(parts + [html.escape(os.path.basename(path))])
                                 + "<br>Nobody named yet? The folder name is kept with the photos until "
                                   "someone is tagged on them.")
            return
        if lvl == -1:
            _, top, below = store.structure_of(d, path, 1)
            names = [n for n in (" ".join(x.split()) for x in self.people.text().split(",")) if n]
            who = names[0] if names else None
            parts = ([f"<b>{html.escape(who)}</b> (person)"] if who else [html.escape(top or "")]) + \
                [html.escape(x) for x in below]
            self.preview.setText(f"e.g. {html.escape(rel)}<br>→ saved as  "
                                 + "\\".join(parts + [html.escape(os.path.basename(path))])
                                 + ("" if who else "  (type a name above to put them inside that person)"))
            return
        above, person, below = store.structure_of(d, path, lvl)
        parts = [html.escape(x) for x in above]
        if person:
            parts.append(f"<b>{html.escape(person)}</b> (person)")
        parts += [html.escape(x) for x in below]
        dest = "\\".join(parts + [html.escape(os.path.basename(path))])
        self.preview.setText(f"e.g. {html.escape(rel)}<br>→ saved as  {dest}"
                             + ("" if person else "  (not deep enough for a person - folders only)"
                                if lvl else ""))

    def _done(self, how):
        self.choice = how
        self.accept()

    def result_tags(self):
        if self.choice != "tag":
            return [], None, False, None
        seen, names = set(), []
        for n in self.people.text().split(","):
            n = " ".join(n.split())
            if n and n.casefold() not in seen:
                seen.add(n.casefold())
                names.append(n)
        lvl = self.level()
        cat = (" ".join(self.category.currentText().split()) or None) if lvl is None or lvl == -1 else None
        return names, cat, self.later.isChecked(), lvl


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
