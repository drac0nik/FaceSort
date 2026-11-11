"""The photo / video viewer (PhotoViewer)."""
import html
import os
import cv2
import numpy as np
import photo_tools
import engine
import store
from PySide6.QtCore import QEvent, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QFont, QImage, QKeySequence, QMovie, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QMenu, QMessageBox, QPushButton, QSlider,
    QStackedWidget, QToolButton, QVBoxLayout, QWidget)
from icons import app_icon
from ui_common import (
    ElidedLabel, key_html, LEGEND_GRAY, MEDIA_OK, open_file, path_label, QAudioOutput,
    QMediaMetaData, QMediaPlayer, QVideoSink, QVideoWidget, show_in_folder, SPEEDS, VIDEO_COL)
from viewer_parts import (
    chip, CompareView, EnhancedView, Filmstrip, frame_planes, LowLightWorker, MarkSlider, PhotoCanvas,
    qimage_to_rgb, rgb_to_qimage, TagEdit, ToolWorker)
from dialogs import EnhanceCopyDialog


class PhotoViewer(QWidget):
    """Full photo with every face boxed, and two tag bars along the bottom.

    People bar: names the highlighted face, or (no face highlighted) tags the person on the
    whole photo. Category bar: sets the photo's one category. Shortcut keys (C R D Delete,
    ← →) work whenever neither bar has the keyboard."""

    def __init__(self, win, items, index=0, context="", person_id=None):
        super().__init__(win)
        self.win, self.items, self.pos, self.context = win, list(items), index, context
        self.person_id = person_id   # set when opened from a person (R then removes that person)
        self.tags = []               # people tagged on the photo itself: [(person_id, name)]
        self.category = None
        self.tattoos, self.tsugg = [], []   # tattoo marks / tattoo matches to check on this photo
        self.marking = False                # drawing a box: M = tattoo, T = a face the scan missed
        self.mark_kind = "tattoo"
        self.pending_tattoo = None
        self.pending_face = None
        self.image, self.error, self.faces, self.path, self.focus_id = None, None, [], None, None
        self.sel_faces = set()               # faces picked with a drag (I / N / right-click act on all)
        self.slideshow = QTimer(self, interval=3000)       # Space: slideshow
        self.slideshow.timeout.connect(self.slide_next)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.back_b = QToolButton()
        self.back_b.setIcon(app_icon("back"))
        self.back_b.setIconSize(QSize(33, 33))
        self.back_b.setAutoRaise(True)
        self.back_b.setToolTip("Back to the gallery   (Esc or Backspace)")
        self.back_b.clicked.connect(self.close_viewer)
        top.addWidget(self.back_b)
        self.counter = QLabel()
        f = self.counter.font()
        f.setBold(True)
        self.counter.setFont(f)
        self.state = ElidedLabel()
        top.addWidget(self.counter)
        top.addSpacing(12)
        top.addWidget(self.state, 1)
        self.toast = QLabel()
        self.toast.setStyleSheet("color: #3cbf78; font-weight: bold;")
        top.addWidget(self.toast)
        # viewing tools: zoom, 1:1, fit, slideshow, full screen
        for icon, tip, fn in (("rotate_left", "Turn left   ( [ )", lambda: self.rotate(False)),
                              ("rotate_right", "Turn right   ( ] )", lambda: self.rotate(True)),
                              ("zoom_out", "Zoom out   ( - )", lambda: self.canvas.zoom_by(1 / 1.25)),
                              ("zoom_in", "Zoom in   ( + or the mouse wheel)", lambda: self.canvas.zoom_by(1.25)),
                              ("fit", "Fit the whole photo   ( 0 )", self.fit),
                              ("search", "Actual size, 1:1   ( 1 )", lambda: self.canvas.actual_size()),
                              ("play", "Slideshow   (Space)", self.toggle_slideshow),
                              ("fullscreen", "Full screen   (F11)", self.win.toggle_fullscreen)):
            b = QToolButton()
            b.setIcon(app_icon(icon))
            b.setIconSize(QSize(22, 22))
            b.setAutoRaise(True)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            top.addWidget(b)
            if icon == "play":
                self.play_b = b
        self.hide_boxes = bool(win.settings.get("viewer_hide_boxes", False))
        self.boxes_b = QToolButton()               # face (and tattoo) boxes on / off, to see the photo clean
        self.boxes_b.setCheckable(True)
        self.boxes_b.setChecked(self.hide_boxes)
        self.boxes_b.setIcon(app_icon("person"))
        self.boxes_b.setIconSize(QSize(22, 22))
        self.boxes_b.setAutoRaise(True)
        self.boxes_b.toggled.connect(self.set_hide_boxes)
        self._boxes_tip()
        top.addWidget(self.boxes_b)
        self.compare_b = QToolButton()             # duplicates side by side
        self.compare_b.setCheckable(True)
        self.compare_b.setIcon(app_icon("compare"))
        self.compare_b.setIconSize(QSize(22, 22))
        self.compare_b.setAutoRaise(True)
        self.compare_b.toggled.connect(self.set_compare)
        top.addWidget(self.compare_b)
        self.tools_b = QToolButton()               # photo tools: red eye, upscale, enhance, restore, colourize
        self.tools_b.setIcon(app_icon("wand"))
        self.tools_b.setIconSize(QSize(22, 22))
        self.tools_b.setAutoRaise(True)
        self.tools_b.setToolTip("Photo tools   (W): remove red eye, upscale, auto enhance, restore an old faded "
                                "photo, colourize black & white / sepia.\nYou see a preview first; the original is "
                                "never changed - Save copy puts the new picture next to it.")
        self.tools_b.setPopupMode(QToolButton.InstantPopup)
        self.tools_menu = QMenu(self.tools_b)
        self.tools_menu.aboutToShow.connect(self.build_tools_menu)
        self.tools_b.setMenu(self.tools_menu)
        top.addWidget(self.tools_b)
        self.not_ai_b = QToolButton()                # only on photos with the AI badge
        self.not_ai_b.setText("Not AI")
        self.not_ai_b.setIcon(app_icon("ai"))
        self.not_ai_b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.not_ai_b.setAutoRaise(True)
        self.not_ai_b.setToolTip("This photo is marked as (possibly) AI-made. Click if it isn't - the AI badge "
                                 "comes off.")
        self.not_ai_b.clicked.connect(self.key_not_ai)
        self.not_ai_b.hide()
        top.addWidget(self.not_ai_b)
        self.zoom_label = QLabel("")
        self.zoom_label.setStyleSheet("color: gray;")
        self.zoom_label.setMinimumWidth(44)
        top.addWidget(self.zoom_label)
        db = QToolButton()                     # the Details panel, from here too
        db.setDefaultAction(win.a_details)
        db.setIconSize(QSize(22, 22))
        db.setAutoRaise(True)
        db.setToolButtonStyle(Qt.ToolButtonIconOnly)
        top.addWidget(db)
        lay.addLayout(top)
        self.path_label = path_label()
        lay.addWidget(self.path_label)
        self.name_label = QLabel()             # the file name, bold white (on a dark pill so it shows on light themes too)
        nf = self.name_label.font()
        nf.setBold(True)
        nf.setPointSizeF(nf.pointSizeF() * 1.15)
        self.name_label.setFont(nf)
        self.name_label.setStyleSheet("QLabel { color: white; background: rgba(20, 24, 32, 0.82); "
                                      "border-radius: 6px; padding: 2px 8px; }")
        self.name_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        nr = QHBoxLayout()
        nr.addWidget(self.name_label)
        nr.addStretch(1)
        lay.addLayout(nr)
        # photo tools: the preview of the edited picture (not saved until Save copy)
        self.edit_bar = QFrame()
        self.edit_bar.setStyleSheet("QFrame { background: rgba(245,140,30,0.16); border: 1px solid rgba(245,140,30,0.7);"
                                    " border-radius: 6px; }  QLabel { border: none; background: transparent; }")
        eb = QHBoxLayout(self.edit_bar)
        eb.setContentsMargins(8, 3, 8, 3)
        self.edit_label = QLabel()
        self.edit_label.setTextFormat(Qt.RichText)
        eb.addWidget(self.edit_label, 1)
        self.edit_hold = QPushButton("Hold to see the original")
        self.edit_hold.pressed.connect(lambda: self.show_edit(False))
        self.edit_hold.released.connect(lambda: self.show_edit(True))
        eb.addWidget(self.edit_hold)
        self.edit_more = QPushButton("More tools ▾")
        self.edit_more.setMenu(self.tools_menu)
        eb.addWidget(self.edit_more)
        self.edit_save = QPushButton("Save copy")
        self.edit_save.setIcon(app_icon("save", "orange"))
        self.edit_save.clicked.connect(self.save_edit)
        eb.addWidget(self.edit_save)
        self.edit_cancel = QPushButton("Discard")
        self.edit_cancel.clicked.connect(lambda: self.discard_edit())
        eb.addWidget(self.edit_cancel)
        self.edit_bar.hide()
        lay.addWidget(self.edit_bar)
        self.edit_rgb = self.edit_exif = self.edit_qimg = self.edit_orig = None
        self.edit_ops = []
        self.tool = ToolWorker()
        self.tool.done.connect(self._tool_done)
        self.tool.step.connect(self._tool_step)
        self._tool_after = None
        self.frames_row = QHBoxLayout()        # videos: one button per moment with faces
        self.frames_row.setSpacing(4)
        lay.addLayout(self.frames_row)
        self.is_video, self.frame_t, self.all_faces, self._shown = False, None, [], None
        self.is_anim, self.movie = False, None          # an animated GIF playing
        self.canvas = PhotoCanvas(self)
        self.canvas.setFocusPolicy(Qt.StrongFocus)
        # shortcut list on the left, its bottom level with the bottom of the photo (grows upward)
        self.legend = QWidget()
        self.legend_lay = QVBoxLayout(self.legend)
        self.legend_lay.setContentsMargins(0, 0, 10, 0)
        self.legend_lay.setSpacing(3)
        # above the shortcut list: a confirmed photo of whoever is suggested for the highlighted
        # face, to compare with (handy for people you only know as “Model 07”)
        self.ref_box = QWidget()
        rl = QVBoxLayout(self.ref_box)
        rl.setContentsMargins(0, 2, 0, 10)
        rl.setSpacing(4)
        self.ref_title = QLabel()
        self.ref_title.setTextFormat(Qt.RichText)
        self.ref_title.setWordWrap(True)
        self.ref_img = QLabel()
        self.ref_img.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.ref_img.setStyleSheet("QLabel { border: 2px solid rgba(59,130,246,0.9); border-radius: 6px; }")
        self.ref_note = QLabel(f"<span style='color:{LEGEND_GRAY}'>Same person? <b>C</b> = yes · <b>R</b> = no</span>")
        self.ref_note.setTextFormat(Qt.RichText)
        for wdg in (self.ref_title, self.ref_img, self.ref_note):
            rl.addWidget(wdg)
        self.ref_box.hide()
        self.ref_face = None                   # (face id, path, box) of the picture shown
        self.legend_lay.addWidget(self.ref_box)
        self.legend_lay.addStretch(1)
        for item in ("[Enter] People Bar", "C[a]tegory Bar", "[C]onfirm", "[R]emove Face", "[T]ag Person",
                     "[M]ark Tattoo", "[D]iscard", "Sort [L]ater", "[N]ot a Face", "[I]gnore",
                     "[Shift+I] Ignore All Unnamed", "[Drag] Select Several Faces", "[← →] Browse", "[Wheel / + −] Zoom", "[0] Fit · [1] Actual Size", "[[ / ]] Turn Left / Right",
                     "[Space] Slideshow / Play Video or GIF", "[S]creenshot (Video / GIF)", "[E] Low Light (Video)", "[{ / }] Start / End Marker (Video)",
                     "[F11] Full Screen", "[W] Photo Tools (Red Eye, Upscale, Enhance…)", "[B] Hide / Show Face Boxes", "[Alt+1–5] Quick Category", "[Ctrl+Drag] Tag Person", "[Shift+Drag] Mark Tattoo",
                     "[Right-click] Unassign / Remove", "[Esc] Back to Gallery"):
            lb = QLabel(key_html(item))
            lb.setTextFormat(Qt.RichText)
            self.legend_lay.addWidget(lb)
        self.legend.setFixedWidth(self.legend.sizeHint().width() + 6)
        self.legend.installEventFilter(self)
        win.hires.ready.connect(self._ref_ready)
        mid = QHBoxLayout()
        mid.setSpacing(0)
        mid.addWidget(self.legend)
        self.media = QStackedWidget()          # the photo (with face boxes), or the video playing
        self.media.addWidget(self.canvas)
        self.player = None              # made the first time you play a video (sound devices are opened then)
        if MEDIA_OK:
            self.video_widget = QVideoWidget()
            self.video_widget.setFocusPolicy(Qt.StrongFocus)
            self.media.addWidget(self.video_widget)
            self.enh_view = EnhancedView()      # page 2: the video with low-light recovery
            self.media.addWidget(self.enh_view)
            self.sink = QVideoSink(self)
            self.sink.videoFrameChanged.connect(self.on_sink_frame)
        else:
            for _ in range(2):                  # (keeps the page numbers the same)
                self.media.addWidget(QWidget())
        self.compare = CompareView(self)        # page 3: two duplicates side by side
        self.media.addWidget(self.compare)
        self.low_light = int(win.settings.get("low_light", 0))
        self._ll_level, self._ll_frame, self._ll_busy = None, None, False
        self._ll_worker = LowLightWorker()
        self._ll_worker.done.connect(self._ll_done)
        mid.addWidget(self.media, 1)
        lay.addLayout(mid, 1)
        # video controls: play / pause, position, speed, screenshot, sound
        self.video_bar = QWidget()
        vb = QHBoxLayout(self.video_bar)
        vb.setContentsMargins(0, 2, 0, 2)
        self.play_video_b = QToolButton()
        self.play_video_b.setIcon(app_icon("play", "orange"))
        self.play_video_b.setIconSize(QSize(26, 26))
        self.play_video_b.setAutoRaise(True)
        self.play_video_b.setToolTip("Play / pause   (Space)")
        self.play_video_b.clicked.connect(self.toggle_video)
        vb.addWidget(self.play_video_b)
        self.seek = MarkSlider(Qt.Horizontal)
        self.seek.setToolTip("Drag to move through the video")
        self.seek.sliderMoved.connect(self.seek_to)
        vb.addWidget(self.seek, 1)
        self.marks = [None, None]           # start / end of a section (seconds)
        self.mark_bs = []
        for txt, tip, fn in (("⇤", "Start marker here   ( { )\nEnhanced copies, best frames and collages then use "
                                   "only the section between the markers", lambda: self.set_mark(0)),
                             ("⇥", "End marker here   ( } )", lambda: self.set_mark(1)),
                             ("✕", "Clear the markers   (Shift+X)", self.clear_marks)):
            b = QToolButton()
            b.setText(txt)
            b.setAutoRaise(True)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(fn)
            vb.addWidget(b)
            self.mark_bs.append(b)
        self.mark_bs[2].hide()
        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setMinimumWidth(96)
        vb.addWidget(self.time_label)
        self.speed_box = QComboBox()
        for sp in SPEEDS:
            self.speed_box.addItem(f"{sp:g}×", sp)
        self.speed_box.setCurrentIndex(SPEEDS.index(1.0))
        self.speed_box.setToolTip("Playback speed")
        self.speed_box.setFocusPolicy(Qt.NoFocus)
        self.speed_box.currentIndexChanged.connect(self.set_speed)
        vb.addWidget(self.speed_box)
        self.ll_box = QComboBox()
        for txt in ("Low light: off", "Low light: on", "Low light: strong"):
            self.ll_box.addItem(txt)
        self.ll_box.setCurrentIndex(max(0, min(2, int(self.win.settings.get("low_light", 0)))))
        self.ll_box.setToolTip("Low-light recovery   (E)\nBrightens dark videos while they play: lifts the shadows, "
                               "brings back detail and calms the colour noise. Screenshots are saved the way you see it.")
        self.ll_box.setFocusPolicy(Qt.NoFocus)
        self.ll_box.currentIndexChanged.connect(self.set_low_light)
        vb.addWidget(self.ll_box)
        self.ll_save_b = QToolButton()
        self.ll_save_b.setIcon(app_icon("save", "orange"))
        self.ll_save_b.setIconSize(QSize(22, 22))
        self.ll_save_b.setAutoRaise(True)
        self.ll_save_b.setToolTip("Save an enhanced copy of this video next to it, with its sound, tagged like "
                                  "the original: low light, Steady (less flicker), a new frame rate (24 - 60 fps) "
                                  "and / or just the section between the markers")
        self.ll_save_b.clicked.connect(self.save_enhanced_copy)
        vb.addWidget(self.ll_save_b)
        self.ll_frames_b = QToolButton()
        self.ll_frames_b.setIcon(app_icon("image", "orange"))
        self.ll_frames_b.setIconSize(QSize(22, 22))
        self.ll_frames_b.setAutoRaise(True)
        self.ll_frames_b.setToolTip("Save the best moments of this video as brightened photos next to it "
                                    "(sharpest frames with the most in them, spread through the video), "
                                    "tagged like the video and added to the library")
        self.ll_frames_b.clicked.connect(lambda: self.win.lowlight_best_frames(self.path, self.low_light or 1,
                                                                               *self.section())
                                         if self.is_video else None)
        vb.addWidget(self.ll_frames_b)
        self.shot_b = QPushButton("  Screenshot (S)")
        self.shot_b.setIcon(app_icon("image", "orange"))
        self.shot_b.setToolTip("Save this frame as a photo next to the video, tagged like the video")
        self.shot_b.clicked.connect(self.screenshot)
        vb.addWidget(self.shot_b)
        self.collage_b = QPushButton("  12-shot collage")
        self.collage_b.setIcon(app_icon("grid", "orange"))
        self.collage_b.setToolTip("12 frames spread evenly through the whole video (blank / black screens skipped), "
                                  "saved as one 3 × 4 collage next to the video, tagged like it")
        self.collage_b.clicked.connect(lambda: self.win.make_collage(self.path, *self.section())
                                       if self.is_video else None)
        vb.addWidget(self.collage_b)
        self.mute_b = QToolButton()
        self.mute_b.setText("🔊")
        self.mute_b.setCheckable(True)
        self.mute_b.setAutoRaise(True)
        self.mute_b.setToolTip("Sound on / off")
        self.mute_b.toggled.connect(lambda m: (self.mute_b.setText("🔇" if m else "🔊"),
                                               self.player and self.audio.setMuted(m)))
        vb.addWidget(self.mute_b)
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setFixedWidth(90)
        self.volume.valueChanged.connect(lambda v: self.player and self.audio.setVolume(v / 100))
        vb.addWidget(self.volume)
        self.video_bar.hide()
        lay.addWidget(self.video_bar)

        # ---- the two tag bars
        bold = QFont(self.font())
        bold.setBold(True)
        self.people_placeholder = ("Type a name…   (Tab completes · Enter applies · "
                                   "Ctrl+Enter applies and goes to the next photo)")
        self.people_bar = TagEdit(self, "people", self.people_placeholder)
        self.cat_bar = TagEdit(self, "category", "Type the photo's folder…   (“Wedding 2024”, or “Jane Doe › Beach” "
                                                 "for a folder of Jane's)")
        prow, crow = QHBoxLayout(), QHBoxLayout()
        for row, label, bar in ((prow, "People", self.people_bar), (crow, "Category", self.cat_bar)):
            lb = QLabel(label)
            lb.setFont(bold)
            lb.setFixedWidth(70)
            row.addWidget(lb)
            holder = QHBoxLayout()
            holder.setSpacing(4)
            row.addLayout(holder)
            row.addWidget(bar, 1)
            row.addWidget(bar.new_label)
            if bar is self.people_bar:
                self.people_chips = holder
            else:
                self.cat_chip = holder
        self.top5 = QHBoxLayout()
        self.top5.setSpacing(4)
        crow.addLayout(self.top5)
        lay.addLayout(prow)
        lay.addLayout(crow)

        btns = QHBoxLayout()
        self.prev_b, self.next_b = QPushButton("◀  Previous"), QPushButton("Next  ▶")
        self.prev_b.clicked.connect(lambda: self.go(-1))
        self.next_b.clicked.connect(lambda: self.go(1))
        btns.addWidget(self.prev_b)
        btns.addWidget(self.next_b)
        btns.addSpacing(16)
        self.tag_b = QPushButton("Tag person (T)")
        self.tag_b.setCheckable(True)
        self.tag_b.setToolTip("Drag a box around a face the scan missed, then type who it is.")
        self.tag_b.toggled.connect(lambda on: self.set_marking(on, "face"))
        btns.addWidget(self.tag_b)
        self.mark_b = QPushButton("Mark tattoo (M)")
        self.mark_b.setCheckable(True)
        self.mark_b.setToolTip("Drag a box around a tattoo, then type whose it is.\n"
                               "Shift + drag works any time. Right-click a mark to remove it.")
        self.mark_b.toggled.connect(lambda on: self.set_marking(on, "tattoo"))
        btns.addWidget(self.mark_b)
        self.find_b = QPushButton("Find tattoo matches")
        self.find_b.setToolTip("Search your photos for the tattoos marked so far")
        self.find_b.clicked.connect(lambda: (self.win.start_tattoo_search(), self.toast.setText(
            "Searching… (results appear under Tattoo matches)")))
        btns.addWidget(self.find_b)
        btns.addStretch()
        for text, fn in (("Open in default app", lambda: self.path and open_file(self.path)),
                         ("Open window to this image", lambda: self.path and show_in_folder(self.path))):
            b = QPushButton(text)
            b.clicked.connect(fn)
            btns.addWidget(b)
        for b in self.findChildren(QPushButton):
            b.setFocusPolicy(Qt.NoFocus)  # keep the keyboard for browsing and the bars
            b.setAutoDefault(False)
        lay.addLayout(btns)
        self.strip = Filmstrip(self)
        lay.addWidget(self.strip)
        # letter keys: typing in a bar takes priority (the bar keeps the letters)
        for key, fn in (("Left", lambda: self.go(-1)), ("Right", lambda: self.go(1)),
                        ("PgUp", lambda: self.go(-1)), ("PgDown", lambda: self.go(1)),
                        ("C", self.key_confirm), ("R", self.key_remove), ("D", self.key_discard),
                        ("N", self.key_notface), ("L", self.key_sort_later),
                        ("T", lambda: self.tag_b.setChecked(not self.tag_b.isChecked())),
                        ("A", self.cat_bar.setFocus),
                        ("M", lambda: self.mark_b.setChecked(not self.mark_b.isChecked())),
                        ("I", self.key_ignore),
                        ("+", lambda: self.canvas.zoom_by(1.25)), ("=", lambda: self.canvas.zoom_by(1.25)),
                        ("-", lambda: self.canvas.zoom_by(1 / 1.25)), ("0", self.fit),
                        ("1", lambda: self.canvas.actual_size()), ("Space", self.space_key),
                        ("S", self.screenshot), ("[", lambda: self.rotate(False)), ("]", lambda: self.rotate(True)),
                        (",", lambda: self.step_frame(-1)), (".", lambda: self.step_frame(1)),
                        ("B", lambda: self.set_hide_boxes(not self.hide_boxes)),
                        ("Shift+I", self.key_ignore_unnamed),
                        ("{", lambda: self.set_mark(0)), ("}", lambda: self.set_mark(1)),
                        ("W", self.open_tools_menu),
                        ("Shift+X", self.clear_marks),
                        ("V", lambda: self.set_compare(not self.compare_on())),
                        ("E", lambda: self.set_low_light((self.low_light + 1) % 3) if self.is_video else None),
                        ("Home", lambda: self.go(-self.pos)), ("End", lambda: self.go(len(self.items) - 1 - self.pos))):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)     # only while the viewer is open
            sc.activated.connect(fn)
        for i in range(5):
            sc = QShortcut(QKeySequence(f"Alt+{i + 1}"), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(lambda i=i: self.quick_category(i))
        self.show_item()
        self.canvas.setFocus()

    def open_items(self, items, index=0, context="", person_id=None):
        """Reuse this viewer for another set of photos (it's kept, just hidden, between uses -
        nothing that arrives late can find it gone)."""
        self.slideshow.stop()
        self.play_b.setIcon(app_icon("play"))
        for b in (self.tag_b, self.mark_b):
            b.blockSignals(True)
            b.setChecked(False)
            b.blockSignals(False)
        self.marking, self.mark_kind = False, "tattoo"
        self.pending_tattoo = self.pending_face = None
        self.people_bar.clear()
        self.cat_bar.clear()
        self.people_bar.setPlaceholderText(self.people_placeholder)
        self.toast.setText("")
        self.stop_video()
        self.items, self.pos, self.context, self.person_id = list(items), index, context, person_id
        self.path, self._shown, self.image = None, None, None
        self.show_item()
        self.canvas.setFocus()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.people_bar.setFocus()   # Enter on the photo = start typing a name
            return
        if e.key() in (Qt.Key_Escape, Qt.Key_Backspace):
            if self.marking:
                self.tag_b.setChecked(False)
                self.mark_b.setChecked(False)
            elif self.pending_tattoo or self.pending_face:
                self.cancel_tattoo()
            elif self.sel_faces:
                self.clear_selection()
            elif self.compare_on():
                self.set_compare(False)
            elif self.edit_rgb is not None or self.tool.busy:
                self.discard_edit()
            else:
                self.close_viewer()          # back to the gallery
            return
        super().keyPressEvent(e)

    def close_viewer(self):
        self.slideshow.stop()
        self.stop_video()
        self.set_compare(False)
        self.win.close_viewer()

    # ---- animated GIFs: they play, looping, right on the photo canvas
    def setup_gif(self, path):
        self.stop_gif()
        self.is_anim = False
        if os.path.splitext(path)[1].lower() != ".gif":
            return
        mv = QMovie(path)
        if not mv.isValid() or mv.frameCount() == 1:
            return
        self.is_anim = True
        self.movie = mv
        mv.setCacheMode(QMovie.CacheAll)
        mv.setSpeed(int(SPEEDS[self.speed_box.currentIndex()] * 100))
        mv.frameChanged.connect(self.gif_frame)
        mv.stateChanged.connect(lambda st: self.play_video_b.setIcon(
            app_icon("pause" if st == QMovie.Running else "play", "orange")))
        n = mv.frameCount()
        self.seek.setRange(0, max(0, n - 1) if n > 0 else 0)
        QTimer.singleShot(0, mv.start)          # plays (looping) as soon as it's open

    def stop_gif(self):
        mv = getattr(self, "movie", None)
        if mv is not None:
            mv.stop()
            try:
                mv.frameChanged.disconnect()
            except (RuntimeError, TypeError):
                pass
            mv.deleteLater()
        self.movie = None

    def gif_frame(self, n):
        mv = self.movie
        if mv is None:
            return
        img = mv.currentImage()
        if not img.isNull():
            self.image = img
            self.canvas.update()
        if not self.seek.isSliderDown():
            self.seek.setValue(n)
        total = mv.frameCount()
        self.time_label.setText(f"frame {n + 1}" + (f" / {total}" if total > 0 else ""))

    def boxes_hidden(self):
        """Boxes switched off (B), or a GIF past its first frame (its boxes were found there)."""
        return self.hide_boxes or bool(getattr(self, "is_anim", False) and self.movie is not None
                                       and self.movie.currentFrameNumber() > 0)

    def set_hide_boxes(self, on):
        self.hide_boxes = bool(on)
        if self.boxes_b.isChecked() != self.hide_boxes:
            self.boxes_b.setChecked(self.hide_boxes)
        self.win.settings["viewer_hide_boxes"] = self.hide_boxes
        self._boxes_tip()
        self.toast.setText("Face boxes hidden (B shows them)" if self.hide_boxes else "Face boxes shown")
        self.canvas.update()

    def _boxes_tip(self):
        self.boxes_b.setToolTip(("Show" if self.hide_boxes else "Hide") + " the face boxes   (B)")

    # ---- video playback
    def space_key(self):
        if getattr(self, "is_anim", False):
            self.toggle_video()
        elif self.is_video and MEDIA_OK:
            self.toggle_video()
        else:
            self.toggle_slideshow()

    def seek_to(self, v):
        if getattr(self, "is_anim", False) and self.movie is not None:
            if self.movie.state() == QMovie.Running:
                self.movie.setPaused(True)
            self.movie.jumpToFrame(v)
        elif self.player:
            self.player.setPosition(v)

    def set_speed(self, i):
        if getattr(self, "movie", None) is not None:
            self.movie.setSpeed(int(SPEEDS[i] * 100))
        if self.player:
            self.player.setPlaybackRate(SPEEDS[i])

    def make_player(self):
        if self.player is not None or not MEDIA_OK:
            return self.player
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(self.volume.value() / 100)
        self.audio.setMuted(self.mute_b.isChecked())
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.sink if self.low_light else self.video_widget)
        self.player.positionChanged.connect(self.video_position)
        self.player.durationChanged.connect(lambda d: self.seek.setRange(0, max(0, d)))
        self.player.playbackStateChanged.connect(self.video_state)
        self.player.errorOccurred.connect(lambda _e, msg: self.toast.setText(f"Can't play this video: {msg}"))
        return self.player

    def toggle_video(self):
        if getattr(self, "is_anim", False) and self.movie is not None:
            mv = self.movie
            if mv.state() == QMovie.Running:
                mv.setPaused(True)
            elif mv.state() == QMovie.Paused:
                mv.setPaused(False)
            else:
                mv.start()
            return
        if not self.is_video or not self.make_player():
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            return
        if getattr(self, "_video_loaded", None) != self.path:
            self.player.setSource(QUrl.fromLocalFile(self.path))
            self._video_loaded = self.path
            if self.frame_t:
                self.player.setPosition(int(self.frame_t * 1000))      # start at the moment you're on
        self.player.setPlaybackRate(SPEEDS[self.speed_box.currentIndex()])
        self.media.setCurrentIndex(self.video_page())
        self.player.play()
        (self.enh_view if self.media.currentIndex() == 2 else self.video_widget).setFocus()

    def stop_video(self):
        if getattr(self, "is_anim", False):
            self.stop_gif()
            self.is_anim = False
        if self.player:
            self.player.stop()
            self.player.setSource(QUrl())        # let go of the file
            self._video_loaded = None
        self.media.setCurrentIndex(0)

    def video_state(self, state):
        playing = state == QMediaPlayer.PlayingState
        self.play_video_b.setIcon(app_icon("pause" if playing else "play", "orange"))

    def video_position(self, ms):
        if not self.seek.isSliderDown():
            self.seek.setValue(ms)
        total = self.player.duration() if self.player else 0

        def mmss(v):
            v = int(v // 1000)
            return f"{v // 3600}:{v // 60 % 60:02d}:{v % 60:02d}" if v >= 3600 else f"{v // 60}:{v % 60:02d}"
        self.time_label.setText(f"{mmss(ms)} / {mmss(total)}")

    def current_frame(self):
        """(QImage, seconds) of what's on screen in the video (brightened, if low light is on)."""
        if self.player and self.media.currentIndex() in (1, 2):
            fr = (self.sink if self.media.currentIndex() == 2 else self.video_widget.videoSink()).videoFrame()
            planes = frame_planes(fr) if fr.isValid() and self.low_light else None
            if planes is not None and float(planes[0].mean()) < 0.40 * 255:
                return (rgb_to_qimage(engine.low_light_yuv(*planes, self.low_light)),
                        self.player.position() / 1000)
            img = fr.toImage() if fr.isValid() else QImage()
            if not img.isNull():
                if self.low_light:
                    img = rgb_to_qimage(engine.low_light(qimage_to_rgb(img), self.low_light))
                return img, self.player.position() / 1000
            t = self.player.position() / 1000
        else:
            t = self.frame_t or 0
        bgr = engine.video_frame(self.path, t, max_side=100000)
        rgb = bgr[:, :, ::-1].copy()
        if self.low_light:
            rgb = engine.low_light(rgb, self.low_light)
        return rgb_to_qimage(rgb), t

    # ---- low-light recovery
    def video_page(self):
        return 2 if self.low_light and MEDIA_OK else 1

    def set_low_light(self, level):
        level = max(0, min(2, int(level)))
        if self.ll_box.currentIndex() != level:
            self.ll_box.setCurrentIndex(level)       # comes back here once
            return
        self.low_light = level
        self.win.settings["low_light"] = level
        self._ll_level = None
        if self.player:
            playing = self.media.currentIndex() in (1, 2)
            self.player.setVideoOutput(self.sink if level else self.video_widget)
            if playing:
                self.media.setCurrentIndex(self.video_page())
                if self.player.playbackState() != QMediaPlayer.PlayingState:
                    self.player.setPosition(self.player.position())     # show the paused frame the new way
        if self.is_video and self.media.currentIndex() == 0:
            self._shown = None
            self.load_picture()
            self.canvas.update()
        self.toast.setText(("Low light: " + ("on", "strong")[level - 1]) if level else "Low light: off")

    def on_sink_frame(self, frame):
        self._ll_frame = frame                     # only the newest frame is worked on (never falls behind)
        if not self._ll_busy:
            self._ll_next()

    def _ll_next(self):
        frame, self._ll_frame = self._ll_frame, None
        if frame is None or not frame.isValid() or self.media.currentIndex() != 2 or not self.low_light:
            self._ll_busy = False
            return
        dpr = self.enh_view.devicePixelRatioF()
        W, H = int(self.enh_view.width() * dpr), int(self.enh_view.height() * dpr)
        planes = frame_planes(frame)
        if planes is not None and float(planes[0].mean()) < 0.40 * 255:
            Y, U, V = planes
            k = min(1.0, W / max(1, Y.shape[1]), H / max(1, Y.shape[0]))
            if k < 1:                               # work at the size it's shown
                Y = cv2.resize(Y, (max(16, int(Y.shape[1] * k)), max(16, int(Y.shape[0] * k))), interpolation=cv2.INTER_AREA)
                U = cv2.resize(U, (max(8, int(U.shape[1] * k)), max(8, int(U.shape[0] * k))), interpolation=cv2.INTER_AREA)
                V = cv2.resize(V, (U.shape[1], U.shape[0]), interpolation=cv2.INTER_AREA)
            data = (Y, U, V)
            lv = engine.dark_levels(Y.astype(np.float32))
        else:
            img = frame.toImage()
            if img.isNull():
                self._ll_busy = False
                return
            if img.width() > W or img.height() > H:     # work at the size it's shown: quick even for 4K
                img = img.scaled(max(16, W), max(16, H), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            data = qimage_to_rgb(img)
            lv = engine.frame_level(data)
        old = self._ll_level
        self._ll_level = lv if old is None else (old[0] * 0.85 + lv[0] * 0.15, old[1] * 0.85 + lv[1] * 0.15)  # no pumping
        self._ll_busy = True
        self._ll_worker.submit(data, self.low_light, self._ll_level, dpr)

    def _ll_done(self, img):
        if img is not None and self.media.currentIndex() == 2:
            self.enh_view.set_image(img)
        self._ll_next()

    def step_frame(self, d):
        """, / . : one frame back / forward (the video pauses)."""
        if getattr(self, "is_anim", False) and self.movie is not None:
            mv = self.movie
            mv.setPaused(True)
            mv.jumpToFrame((mv.currentFrameNumber() + d) % max(1, mv.frameCount()))
            return
        if not self.is_video or not self.make_player():
            return
        if getattr(self, "_video_loaded", None) != self.path:
            self.player.setSource(QUrl.fromLocalFile(self.path))
            self._video_loaded = self.path
            self.player.setPosition(int((self.frame_t or 0) * 1000))
            self.media.setCurrentIndex(self.video_page())
            self.player.pause()
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        if getattr(self, "_fps_for", None) != self.path:
            fps = 0
            try:
                fps = float(self.player.metaData().value(QMediaMetaData.VideoFrameRate) or 0)
            except Exception:
                fps = 0
            self._fps = fps if 1 <= fps <= 240 else engine.video_fps(self.path)
            self._fps_for = self.path
        self.media.setCurrentIndex(self.video_page())
        frame_ms = 1000.0 / (self._fps or 30.0)
        target = max(0, min(self.player.duration() or 10 ** 9, round(self.player.position() + d * frame_ms)))
        self.player.setPosition(int(target))
        n = int(round(target / frame_ms))
        self.toast.setText(f"Frame {n + 1:,}  ·  , back  · . forward")

    def update_ai_button(self):
        pid, _ = self.photo_id()
        ai = self.win.conn.execute("SELECT ai, ai_info FROM photos WHERE id=?", (pid,)).fetchone() if pid else None
        on = bool(ai and ai[0])
        self.not_ai_b.setVisible(on)
        if on:
            self.not_ai_b.setToolTip(("Marked as AI by the file itself" if ai[0] == 1 else "Possibly AI (the detector's "
                                      "guess - often wrong)") + (f": {ai[1]}" if ai[1] else "") +
                                     "\nClick if it isn't - the AI badge comes off.")

    def key_not_ai(self):
        pid, _ = self.photo_id()
        if pid:
            self.win.act_not_ai([pid])
            self.not_ai_b.hide()
            self.toast.setText("AI badge taken off")

    # ---- photo tools (red eye, upscale, enhance, restore, colourize)
    def open_tools_menu(self):
        if self.tools_b.isVisible():
            self.tools_b.showMenu()

    def build_tools_menu(self):
        m = self.tools_menu
        m.clear()
        busy = self.tool.busy
        if self.edit_rgb is not None:
            h, w = self.edit_rgb.shape[:2]
            hdr = m.addAction(f"On the preview ({w:,} × {h:,}) - tools add up")
        else:
            w, h = getattr(self, "_full_w", 0), getattr(self, "_full_h", 0)
            hdr = m.addAction(f"This photo: {w:,} × {h:,}")
        hdr.setEnabled(False)
        m.addSeparator()
        a = m.addAction("Remove red eye", lambda: self.run_tool("redeye"))
        a.setToolTip("Finds the eyes and turns red pupils dark")
        up = m.addMenu("Upscale (saved lossless, as PNG)")
        for f, nw, nh, ok in photo_tools.upscale_choices(w, h):
            act = up.addAction(f"×{f}   →   {nw:,} × {nh:,}" + ("" if ok else "   (too big)"),
                               lambda f=f: self.run_tool("upscale", f))
            act.setEnabled(ok and not busy and w > 0)
        up.setToolTipsVisible(True)
        m.addSeparator()
        m.addAction("Auto enhance (light)", lambda: self.run_tool("enhance"))
        m.addAction("Restore old / faded photo", lambda: self.run_tool("restore"))
        sm = m.addMenu("Smooth dark blotches")
        sm.setToolTipsVisible(True)
        a = sm.addAction("Smooth the shadows", lambda: self.run_tool("shadows"))
        a.setToolTip("Patchy, ringed shadows (a dark JPEG): rebuilt as smooth gradients; lit areas untouched")
        a = sm.addAction("Smooth and lift the shadows a little", lambda: self.run_tool("shadows", True))
        a.setToolTip("The same, and the shadows brightened slightly to show what's in them (black stays black)")
        sm.setEnabled(not busy)
        m.addAction("Colourize black & white / sepia" + ("" if photo_tools.color_ready() else
                                                          f"   (gets a {photo_tools.COLOR_MB} MB model once)"),
                    lambda: self.run_tool("colorize"))
        for act in m.actions():
            if act is not hdr and act.menu() is None:
                act.setEnabled(not busy)
        if busy:
            m.addSeparator()
            m.addAction("Stop the tool that's working", lambda: self.discard_edit())

    TOOL_NAMES = {"redeye": ("Removing red eye", "red eye fixed"), "upscale": ("Upscaling", "x{}"),
                  "enhance": ("Enhancing", "enhanced"), "restore": ("Restoring", "restored"),
                  "colorize": ("Colourizing", "colourized"), "shadows": ("Smoothing the shadows", "shadows smoothed")}

    def run_tool(self, kind, arg=None):
        if self.is_video or self.is_anim or not self.path or self.tool.busy:
            return
        if kind == "colorize" and not photo_tools.color_ready():
            if QMessageBox.question(self, "Colourize", f"Colourizing uses a model of about {photo_tools.COLOR_MB} MB "
                                    "(DeOldify), downloaded once from GitHub. After that it works on this PC only - "
                                    "your photos are never uploaded.\n\nDownload it now?") != QMessageBox.Yes:
                return

            def got(ok):
                if ok and photo_tools.color_ready():
                    self.run_tool("colorize")
                else:
                    self.toast.setText("The colourizing model wasn't downloaded.")
            if not self.win.run_job("colordl", lambda conn, progress, cancelled: photo_tools.download_color(progress, cancelled),
                                    on_done=got):
                QMessageBox.information(self, "Colourize", "Wait for the current job to finish first.")
            return
        base = self.edit_rgb
        path = self.path
        hints = [(f[1], f[2], f[3], f[4]) for f in self.faces if f[1] is not None]
        doing, label = self.TOOL_NAMES[kind]
        if kind == "upscale":
            label = label.format(arg)
        elif kind == "shadows" and arg:
            label = "shadows smoothed and lifted"

        def work(step, cancelled):
            exif = None
            src = base
            if src is None:
                src, exif = photo_tools.load_full(path)
            step(0.02)
            info = ""
            if kind == "colorize" and not photo_tools.is_monochrome(src):
                info = "this photo already had colour - the model's colours replace it"
            if kind == "redeye":
                out, n = photo_tools.red_eye(src, hints)
                if not n:
                    raise ValueError("no red eyes were found on this photo")
                info = f"{n} eye{'s' if n != 1 else ''} fixed"
            elif kind == "upscale":
                out = photo_tools.upscale(src, arg, step, cancelled)
            elif kind == "enhance":
                out = photo_tools.auto_enhance(src, step, cancelled)
            elif kind == "restore":
                out = photo_tools.restore_faded(src, step, cancelled)
            elif kind == "shadows":
                out = photo_tools.smooth_shadows(src, bool(arg), step, cancelled)
            else:
                out = photo_tools.colorize(src, step, cancelled)
            # what's shown: at most 4096 across (the saved copy is full size)
            h, w = out.shape[:2]
            k = min(1.0, 4096 / max(w, h))
            shown = cv2.resize(out, (max(1, round(w * k)), max(1, round(h * k))), interpolation=cv2.INTER_AREA) if k < 1 else out
            qimg = QImage(np.ascontiguousarray(shown).data, shown.shape[1], shown.shape[0], 3 * shown.shape[1],
                          QImage.Format_RGB888).copy()
            return {"rgb": out, "exif": exif, "qimg": qimg, "label": label, "info": info, "path": path}
        self._tool_kind = doing
        self.toast.setText(f"{doing}…")
        self.edit_label.setText(f"<b>{doing}…</b>")
        self.edit_bar.show()
        for b in (self.edit_save, self.edit_hold, self.edit_more):
            b.setEnabled(False)
        self.tool.submit(work)

    def _tool_step(self, f):
        if self.tool.busy:
            self.edit_label.setText(f"<b>{getattr(self, '_tool_kind', 'Working')}… {int(f * 100)}%</b>")

    def _tool_done(self, res, err):
        for b in (self.edit_save, self.edit_hold, self.edit_more):
            b.setEnabled(True)
        if res is None or res.get("path") != self.path:
            if err and "cancelled" not in err:
                self.toast.setText("Couldn't do that: " + err)
            if self.edit_rgb is None:
                self.edit_bar.hide()
            else:
                self._edit_label()
            return
        if self.edit_rgb is None:
            self.edit_exif = res["exif"]
            self.edit_orig = self.image
        self.edit_rgb = res["rgb"]
        self.edit_qimg = res["qimg"]
        self.edit_ops.append(res["label"])
        self.show_edit(True)
        self._edit_label(res["info"])
        self.toast.setText("Preview - not saved yet")

    def _edit_label(self, info=""):
        h, w = self.edit_rgb.shape[:2]
        done = ", ".join(op.replace("x", "upscaled ×") if op.startswith("x") else op for op in self.edit_ops)
        self.edit_label.setText(f"<b>Preview:</b> {html.escape(done)} &nbsp;·&nbsp; {w:,} × {h:,}"
                                + (f" &nbsp;·&nbsp; <span style='color:gray'>{html.escape(info)}</span>" if info else "")
                                + " &nbsp;<span style='color:gray'>(not saved yet)</span>")

    def show_edit(self, edited):
        if self.edit_rgb is None:
            return
        self.image = self.edit_qimg if edited else self.edit_orig
        self.canvas.update()

    def discard_edit(self, quiet=False):
        if self.tool.busy:
            self.tool.cancel = True
        had = self.edit_rgb is not None
        if had and self.edit_orig is not None:
            self.image = self.edit_orig
            self.canvas.update()
        self.edit_rgb = self.edit_exif = self.edit_qimg = self.edit_orig = None
        self.edit_ops = []
        self.edit_bar.hide()
        if not quiet:
            self.toast.setText("Edits discarded - the photo is as it was")

    def save_edit(self):
        if self.edit_rgb is None or self.tool.busy:
            return
        rgb, exif, src = self.edit_rgb, self.edit_exif, self.path
        # upscaled, or smoothed shadows (JPEG would put the blocks back): saved losslessly
        png = any(op.startswith("x") or op.startswith("shadows") for op in self.edit_ops)
        dest = photo_tools.out_path(src, " ".join(self.edit_ops), png=png)
        try:
            photo_tools.save(rgb, dest, exif)
        except Exception as e:
            QMessageBox.warning(self, "Save copy", f"Couldn't save the copy in:\n{os.path.dirname(dest)}\n\n{e}")
            return
        out = self.win.add_derived(src, dest, copy_of=True)
        self.discard_edit(quiet=True)
        msg = f"✓ Saved next to the original: {os.path.basename(out or dest)}"
        self.toast.setText(msg)
        self.win.statusBar().showMessage(msg, 10000)

    # ---- start / end markers
    def video_now(self):
        """Seconds into the video you're at (playing or paused), or the moment shown."""
        if self.player and getattr(self, "_video_loaded", None) == self.path:
            return self.player.position() / 1000.0
        return float(self.frame_t or 0)

    def video_length(self):
        if self.player and getattr(self, "_video_loaded", None) == self.path and self.player.duration() > 0:
            return self.player.duration() / 1000.0
        try:
            vs = engine.VideoSource(self.path)
            n, fps = vs.n, vs.fps
            vs.release()
            return n / fps if fps and n > 0 else None
        except Exception:
            return None

    def set_mark(self, which):
        if not self.is_video:
            return
        t = self.video_now()
        a, b = self.marks
        if which == 0:
            a = t
            if b is not None and b <= a:
                b = None
        else:
            b = t
            if b <= 0.05:
                self.toast.setText("Move a little into the video first - the end marker can't be at 0:00")
                return
            if a is not None and a >= b:
                a = None
        self.marks = [a, b]
        if self.seek.maximum() <= 0:                  # not played yet: the bar learns the length now
            L = self.video_length()
            if L:
                self.seek.setRange(0, int(L * 1000))
                self.seek.setValue(int(t * 1000))
        self._marks_changed()

    def clear_marks(self, quiet=False):
        self.marks = [None, None]
        self._marks_changed(quiet)

    def section(self):
        """(start, end) seconds of the marked section, or (None, None) for the whole video."""
        return tuple(self.marks)

    def _marks_changed(self, quiet=False):
        a, b = self.marks
        self.seek.set_marks(a, b)
        on = a is not None or b is not None
        self.mark_bs[2].setVisible(on and self.is_video)
        if quiet:
            return
        if on:
            self.toast.setText(f"Section: {engine.clock(a or 0)} – {engine.clock(b) if b is not None else 'end'}"
                               "   (copies, best frames and collages use only this part · Shift+X clears)")
        else:
            self.toast.setText("Markers cleared - the whole video is used")

    def save_enhanced_copy(self):
        if not self.is_video:
            return
        if self.player and self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        dlg = EnhanceCopyDialog(self, self.path, self.low_light, self.marks)
        if dlg.exec() == QDialog.Accepted:
            self.win.make_lowlight_copy(self.path, dlg.options())

    def screenshot(self):
        """S: save the frame as a photo in the video's folder, tagged like the video."""
        if getattr(self, "is_anim", False) and self.movie is not None:
            n = self.movie.currentFrameNumber()
            out = self.win.save_video_screenshot(self.path, self.movie.currentImage(), 0, label=f"frame {n + 1}")
            if out:
                self.done_action(f"✓ Frame saved: {os.path.basename(out)}", advance=False)
            return
        if not self.is_video:
            return
        try:
            img, t = self.current_frame()
        except Exception as e:
            self.toast.setText(f"Couldn't take a screenshot: {e}")
            return
        out = self.win.save_video_screenshot(self.path, img, t)
        if out:
            self.done_action(f"✓ Screenshot saved: {os.path.basename(out)}", advance=False)

    # ---- turning
    def rotate(self, clockwise):
        pid, _ = self.photo_id()
        if pid is None:
            return
        if self.is_video:
            self.toast.setText("Videos can't be turned")
            return
        if self.edit_rgb is not None or self.tool.busy:
            self.toast.setText("Save or discard the photo-tools preview first")
            return
        done, skipped = self.win.rotate_photos([pid], clockwise)
        if skipped:
            self.toast.setText(f"Couldn't turn this one: {skipped[0][1]}")
            return
        self._shown = None
        self.load_picture()
        self.canvas.reset_zoom()
        self.reload_faces()
        self.strip.update()
        self.toast.setText("Turned " + ("right" if clockwise else "left"))

    # ---- viewing
    def fit(self):
        self.canvas.reset_zoom()
        self.zoom_changed()

    def zoom_changed(self):
        img = self.image
        if img is None or img.isNull():
            self.zoom_label.setText("")
            return
        ir = self.canvas.image_rect()
        full = max(img.width(), 1)
        real = getattr(self, "_full_w", full) or full
        self.zoom_label.setText(f"{ir.width() / real:.0%}")

    def toggle_slideshow(self):
        if self.slideshow.isActive():
            self.slideshow.stop()
            self.play_b.setIcon(app_icon("play"))
            self.toast.setText("Slideshow stopped")
        else:
            self.slideshow.start()
            self.play_b.setIcon(app_icon("pause"))
            self.toast.setText("Slideshow - Space stops it")

    def slide_next(self):
        if self.pos < len(self.items) - 1:
            self.go(1)
        else:
            self.toggle_slideshow()

    # ---- suggestions for the bars
    def candidates(self, kind):
        if kind == "people":
            return [(n, None) for n in self.win.people_by_recent()]
        out, seen = [], set()
        pid, _ = self.photo_id()
        people = store.people_on_photo(self.win.conn, pid) if pid else []
        for folder in self.win.folder_suggestions([self.path] if self.path else []):
            out.append((folder, "folder name"))
            seen.add(folder.casefold())
        for n in store.folder_labels_by_use(self.win.conn, people) + self.win.categories_by_use():
            if n.casefold() not in seen:
                out.append((n, None))
                seen.add(n.casefold())
        # typing a new name: offer it as each person's own folder too
        typed = " ".join(self.cat_bar.text().split())
        if typed and store.PATH_SEP.strip() not in typed:
            names = dict(self.win.conn.execute("SELECT id, name FROM persons"))
            for p in people:
                lbl = f"{names.get(p, '?')}{store.PATH_SEP}{typed}"
                if lbl.casefold() not in seen:
                    out.append((lbl, f"new folder of {names.get(p, '?')}"))
        return out

    def apply_bar(self, kind, name, advance=False):
        pid, _ = self.photo_id()
        if pid is None:
            return
        if self.win.conn.execute("SELECT sort_later FROM photos WHERE id=?", (pid,)).fetchone() == (1,) \
                and not self.context.startswith("Sort Later"):
            store.set_sort_later(self.win.conn, [pid], False)   # you're sorting it now
            self.reload_faces()
        if kind == "people" and self.pending_face:
            box, self.pending_face = self.pending_face, None
            self.people_bar.setPlaceholderText(self.people_placeholder)
            try:
                fid = self.win.add_manual_face(pid, self.path, box, name, self.frame_t if self.is_video else None)
            except Exception as e:
                self.done_action(f"Couldn't add the face: {e}", advance=False)
                return
            self.focus_id = fid
            self.done_action(f"✓ Face added: {name}", advance=False)
            return
        if kind == "people" and self.pending_tattoo:
            person = store.get_or_create_person(self.win.conn, name)
            store.add_tattoo(self.win.conn, person, pid, self.pending_tattoo)
            self.pending_tattoo = None
            self.people_bar.setPlaceholderText(self.people_placeholder)
            self.done_action(f"✓ Tattoo marked: {name} - click “Find tattoo matches” to search", advance=False)
            return
        if kind == "people":
            f = self.focused()
            if f and f[7] != 2:
                if f[7] in (3, 4):                  # "not a face" after all: FaceSort learns that too
                    store.set_notface(self.win.conn, [f[0]], 0)
                self.win.assign_ids([f[0]], name, refresh=False)
                msg = f"✓ Named: {name}"
                nxt = next((x for x in self.faces if x[0] != f[0] and not x[5] and x[7] == 0), None)
                if nxt and not advance:
                    self.focus_id = nxt[0]   # next unnamed face in this photo
            else:
                store.tag_photos(self.win.conn, [pid], store.get_or_create_person(self.win.conn, name))
                msg = f"✓ Tagged: {name}"
        else:
            store.set_category(self.win.conn, [pid], name, store.people_on_photo(self.win.conn, pid))
            msg = f"✓ Folder: {name}"
        self.done_action(msg, advance=advance)

    # ---- tattoos
    def set_marking(self, on, kind="tattoo"):
        other = self.tag_b if kind == "tattoo" else self.mark_b
        if on and kind == "tattoo" and self.is_video:
            self.mark_b.setChecked(False)
            self.toast.setText("Tattoos can only be marked on photos")
            return
        if on and other.isChecked():
            other.blockSignals(True)
            other.setChecked(False)
            other.blockSignals(False)
        if not on and kind != self.mark_kind:
            return
        self.marking = on
        self.mark_kind = kind
        self.canvas.setCursor(Qt.CrossCursor if on else Qt.ArrowCursor)
        if on:
            self.toast.setText("Drag a box around the face" if kind == "face" else "Drag a box around the tattoo")
        self.canvas.setFocus()
        self.canvas.update()

    def box_drawn(self, box, shift=False, ctrl=False):
        if ctrl or (self.marking and self.mark_kind == "face" and not shift):
            return self.face_drawn(box)
        return self.tattoo_drawn(box)

    def face_menu(self, f, where):
        """Right-click on a face box in the preview."""
        if f[0] in self.sel_faces and len(self.sel_faces) > 1:
            n = len(self.sel_faces)
            m = QMenu(self)
            m.addAction(f"Ignore these {n} faces (strangers)\tI", self.ignore_selected)
            m.addAction(f"Not faces ({n})\tN", self.notface_selected)
            m.addSeparator()
            m.addAction("Clear the selection\tEsc", lambda: self.clear_selection())
            m.exec(where)
            return
        fid, name, conf, ign = f[0], f[5], f[6], f[7]
        manual = (self.win.conn.execute("SELECT manual FROM faces WHERE id=?", (fid,)).fetchone() or [0])[0]
        m = QMenu(self)
        if name and ign == 0:
            m.addAction(f"Unassign {name}" + ("" if manual else "   (won't be suggested as them again)"),
                        lambda: self.unassign_face(fid, name, manual))
            if not conf:
                m.addAction(f"Yes, it's {name} (confirm)", lambda: (self.focus_face(f), self.key_confirm()))
            m.addAction("Change the name…", lambda: self.focus_face(f))
            self.win.generic_menu(m.addMenu("Change to a generic name"),
                                  lambda new, pre: self.tag_placeholder(f, new, pre))
        else:
            undo = {1: "Undo “ignore” (back to an unnamed face)", 3: "Undo “not a face” (it is a face)",
                    4: "Undo “probably not a face” (it is a face)", 2: "Undo “discard”"}.get(ign)
            if undo:
                m.addAction(undo, lambda: self.unmark_face(f))
                m.addSeparator()
            if ign != 2:
                m.addAction("Name this face…", lambda: self.focus_face(f))
                m.addSeparator()
                self.win.generic_menu(m, lambda new, pre: self.tag_placeholder(f, new, pre))
        if manual:
            m.addAction("Delete this face box", lambda: self.delete_face_box(fid))
        else:
            m.addSeparator()
            if ign != 3:
                m.addAction("Not a face\tN", lambda: (self._focus(fid), self.key_notface()))
            if ign != 1:
                m.addAction("Ignore (a stranger)\tI", lambda: (self._focus(fid), self.key_ignore()))
        if sum(1 for x in self.all_faces if not x[5] and x[7] == 0) > 1:
            m.addAction("Ignore all unnamed faces here\tShift+I", self.key_ignore_unnamed)
        m.exec(where)

    def unmark_face(self, f):
        """Undo what was decided about a face box: ignored / not a face / discarded -> an ordinary
        unnamed face again (it can be named, and grouped with look-alikes)."""
        fid, ign = f[0], f[7]
        if ign == 2:
            store.set_discarded(self.win.conn, [fid], False)
        elif ign in (3, 4):
            store.set_notface(self.win.conn, [fid], 0)
        else:
            store.set_ignored(self.win.conn, [fid], False)
        self.focus_id = fid
        self.win.schedule_regroup()
        self.done_action({1: "Not ignored any more", 2: "Discard undone"}.get(ign, "It's a face - kept"),
                         advance=False)

    def _focus(self, fid):
        self.focus_id = fid

    def tag_placeholder(self, f, name, prefix):
        """A new numbered person for a face (each number is a different person), saved in the folder
        “Unknown <prefix>”. Also for a face marked ignored / not a face: that marking is undone."""
        if f[7] in (3, 4):
            store.set_notface(self.win.conn, [f[0]], 0)
        self.win.assign_placeholder([f[0]], name, prefix, refresh=False)
        self.focus_id = f[0]
        nxt = next((x for x in self.faces if x[0] != f[0] and not x[5] and x[7] == 0), None)
        if nxt:
            self.focus_id = nxt[0]
        self.done_action(f"✓ Named: {name} (rename it any time)", advance=False)

    def unassign_face(self, fid, name, manual=0):
        if manual:
            return self.delete_face_box(fid, f"Unassigned {name} (face box removed)")
        store.reject(self.win.conn, [fid])
        self.focus_id = fid
        self.done_action(f"Unassigned {name}", advance=False)

    def delete_face_box(self, fid, msg="Face box removed"):
        store.delete_face(self.win.conn, fid)
        if self.focus_id == fid:
            self.focus_id = None
        self.done_action(msg, advance=False)

    def face_drawn(self, box):
        self.pending_face = box
        self.tag_b.setChecked(False)
        self.people_bar.setPlaceholderText("Who is this?   (Enter saves · Esc cancels)")
        self.people_bar.setFocus()
        self.canvas.update()

    def align_legend(self):
        ir = self.canvas.image_rect()
        gap = int(self.canvas.height() - ir.bottom()) if ir.isValid() and not ir.isEmpty() else 0
        self.legend_lay.setContentsMargins(0, 0, 10, max(0, gap))

    def tattoo_drawn(self, box):
        if self.is_video:
            self.toast.setText("Tattoos can only be marked on photos")
            self.canvas.update()
            return
        self.pending_tattoo = box
        self.mark_b.setChecked(False)
        self.people_bar.setPlaceholderText("Whose tattoo is this?   (Enter saves · Esc cancels)")
        self.people_bar.setFocus()
        self.canvas.update()

    def cancel_tattoo(self):
        self.pending_tattoo = None
        self.pending_face = None
        self.people_bar.setPlaceholderText(self.people_placeholder)
        self.canvas.setFocus()
        self.canvas.update()

    def remove_tattoo(self, tid):
        store.remove_tattoo(self.win.conn, tid)
        self.done_action("Tattoo mark removed", advance=False)

    def quick_category(self, i):
        top = store.top_categories(self.win.conn)
        if i < len(top):
            self.apply_bar("category", top[i])

    # ---- navigation
    def go(self, step):
        n = self.pos + step
        if 0 <= n < len(self.items):
            self.pos = n
            self.show_item()

    def show_item(self):
        fid, path = self.items[self.pos]
        self.focus_id = fid if fid > 0 else None
        self.sel_faces = set()
        if self.pending_tattoo or self.pending_face:
            self.cancel_tattoo()
        if path != self.path:
            self.stop_video()
            if self.edit_rgb is not None or self.tool.busy:
                self.discard_edit(quiet=True)
            self.path = path
            self.is_video = engine.is_video(path)
            self.frame_t = None
            self.setup_gif(path)
            self.video_bar.setVisible(self.is_video or self.is_anim)
            self.tools_b.setVisible(not (self.is_video or self.is_anim))
            self.update_ai_button()
            for wdg in (self.mute_b, self.volume, self.mark_bs[0], self.mark_bs[1]):
                wdg.setVisible(self.is_video)
            self.clear_marks(quiet=True)
            self.play_video_b.setEnabled(MEDIA_OK or self.is_anim)
            if self.is_video and not MEDIA_OK:
                self.play_video_b.setToolTip("Video playback isn't available in this copy of FaceSort")
            if self.is_video:
                r = self.win.conn.execute("""SELECT f.frame_t FROM faces f JOIN photos ph ON ph.id=f.photo_id
                                             WHERE ph.path=? AND f.id=?""", (path, fid)).fetchone()
                c = self.win.conn.execute("SELECT cover_t FROM photos WHERE path=?", (path,)).fetchone()
                self.frame_t = (r[0] if r and r[0] is not None else (c[0] if c else None))
        elif self.is_video and fid > 0:
            r = self.win.conn.execute("SELECT frame_t FROM faces WHERE id=?", (fid,)).fetchone()
            if r and r[0] is not None:
                self.frame_t = r[0]
        self.load_picture()
        self.canvas.reset_zoom()
        self.mark_b.setEnabled(not self.is_video)
        self.strip.update()
        QTimer.singleShot(0, self.zoom_changed)
        self.setWindowTitle(os.path.basename(path))
        self.path_label.setText(os.path.normpath(path))
        self.name_label.setText(os.path.basename(path))
        self.prev_b.setEnabled(self.pos > 0)
        self.next_b.setEnabled(self.pos < len(self.items) - 1)
        self.reload_faces()
        self.update_compare()

    # ---- duplicates side by side
    def dup_partners(self):
        """(this photo, its duplicates that are in this gallery, the best copy) or None."""
        pid, _ = self.photo_id()
        if pid is None or self.is_video:
            return None
        here = self.win.gallery_photo_ids()
        for d in self.win.dup_sets():
            if pid in d["ids"]:
                others = [p for p in d["ids"] if p != pid and p in here]
                if others:
                    return pid, others, d["best"]
        return None

    def compare_on(self):
        return self.media.currentIndex() == 3

    def update_compare(self):
        part = self.dup_partners()
        self.compare_b.setEnabled(part is not None)
        self.compare_b.setToolTip(("Compare with its duplicate, side by side   (V)" if part else
                                   "Compare duplicates side by side   (V)\nNo copy of this picture in this gallery"))
        if self.compare_on():
            if part:
                self.compare.load(*part)
            else:
                self.set_compare(False)

    def set_compare(self, on):
        on = bool(on)
        part = self.dup_partners() if on else None
        if on and not part:
            self.toast.setText("No duplicate of this picture in this gallery")
            on = False
        if on:
            self.stop_video()
            self.compare.load(*part)
            self.media.setCurrentIndex(3)
        elif self.compare_on():
            self.media.setCurrentIndex(0)
            self.canvas.setFocus()
        if self.compare_b.isChecked() != on:
            self.compare_b.blockSignals(True)
            self.compare_b.setChecked(on)
            self.compare_b.blockSignals(False)

    def load_picture(self):
        """The photo - or, for a video, the frame being looked at."""
        want = (self.path, self.frame_t)
        if want == self._shown:
            return
        QTimer.singleShot(0, self.align_legend)
        self._shown = want
        self.image, self.error = None, None
        try:
            li = engine.load_image(self.path, max_side=4096, t=self.frame_t)
            img = li.bgr
            self._full_w = li.width          # for the zoom % (100% = the photo's real pixels)
            self._full_h = li.height
            rgb = img[:, :, ::-1].copy()
            if self.is_video and self.low_light:
                rgb = engine.low_light(rgb, self.low_light)
            h, w = rgb.shape[:2]
            self.image = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        except Exception as e:
            self.error = f"Couldn't open this {'video' if self.is_video else 'photo'}:\n{e}"

    def show_frame(self, t):
        if self.player and self.media.currentIndex() in (1, 2):
            self.player.pause()
            self.media.setCurrentIndex(0)
        self.frame_t = t
        self.load_picture()
        first = next((f for f in self.all_faces if f[8] is not None and abs(f[8] - t) < 0.05), None)
        self.focus_id = first[0] if first else None
        self.reload_faces()
        self.canvas.setFocus()

    def rebuild_frames(self):
        while self.frames_row.count():
            w = self.frames_row.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        if not self.is_video:
            return
        times = sorted({f[8] for f in self.all_faces if f[8] is not None})
        lb = QLabel("Video - moments with faces:")
        lb.setStyleSheet(f"color: {VIDEO_COL.name()}; font-weight: bold;")
        self.frames_row.addWidget(lb)
        for t in times:
            who = [f[5] or "?" for f in self.all_faces if f[8] == t]
            b = QPushButton(f"{int(t // 60)}:{int(t % 60):02d}")
            b.setToolTip(", ".join(who))
            b.setCheckable(True)
            b.setChecked(self.frame_t is not None and abs(t - self.frame_t) < 0.05)
            b.setFocusPolicy(Qt.NoFocus)
            b.setAutoDefault(False)
            b.clicked.connect(lambda _=False, t=t: self.show_frame(t))
            self.frames_row.addWidget(b)
        self.frames_row.addStretch()

    def photo_id(self):
        r = self.win.conn.execute("SELECT id, discarded FROM photos WHERE path=?", (self.path,)).fetchone()
        return r if r else (None, 0)

    def reload_faces(self):
        self.all_faces = self.win.conn.execute(
            """SELECT f.id, f.x, f.y, f.w, f.h, p.name, f.confirmed,
                      CASE WHEN f.ignored=5 THEN COALESCE(f.later_prev, 0) ELSE f.ignored END, f.frame_t FROM faces f
               LEFT JOIN persons p ON p.id=f.person_id
               WHERE f.photo_id=(SELECT id FROM photos WHERE path=?)""", (self.path,)).fetchall()
        if self.is_video:
            # only the faces in the frame on screen (each video moment has its own boxes)
            self.faces = [f for f in self.all_faces
                          if f[8] is None or self.frame_t is None or abs(f[8] - self.frame_t) < 0.05]
        else:
            self.faces = self.all_faces
        self.rebuild_frames()
        pid, discarded = self.photo_id()
        self.tags = self.win.conn.execute(
            """SELECT p.id, p.name FROM photo_people pp JOIN persons p ON p.id=pp.person_id
               WHERE pp.photo_id=? ORDER BY p.name""", (pid,)).fetchall()
        r = self.win.conn.execute("""SELECT c.name FROM photos ph JOIN categories c ON c.id=ph.category_id
                                     WHERE ph.id=?""", (pid,)).fetchone()
        self.category = r[0] if r else None
        self.tattoos = self.win.conn.execute(
            """SELECT t.id, t.x, t.y, t.w, t.h, p.name, t.src FROM tattoos t JOIN persons p ON p.id=t.person_id
               WHERE t.photo_id=?""", (pid,)).fetchall()
        self.tsugg = self.win.conn.execute(
            """SELECT s.person_id, p.name, s.x, s.y, s.w, s.h, s.inliers FROM tattoo_suggestions s
               JOIN persons p ON p.id=s.person_id WHERE s.photo_id=?""", (pid,)).fetchall()
        self.rebuild_chips()
        self.counter.setText(f"{self.pos + 1} / {len(self.items)}" + (f"   ·   {self.context}" if self.context else ""))
        f = self.focused()
        if f is None and self.tsugg:
            self.state.setText("Tattoo match: " + ", ".join(t[1] for t in self.tsugg)
                               + "?   C = yes, it's them · R = not them")
        elif f is None:
            if self.win.conn.execute("SELECT sort_later FROM photos WHERE id=?", (pid,)).fetchone() == (1,):
                self.state.setText("This photo is in Sort Later (L to take it out)")
            elif discarded:
                self.state.setText("This photo is marked to discard (D to undo)")
            elif not [x for x in self.faces if x[7] != 1]:
                self.state.setText("No face found in this photo - type who's in it in the People bar")
            else:
                self.state.setText("No face highlighted - the People bar tags the whole photo (click a face to name it)")
        elif f[7] == 5:
            self.state.setText("This photo is in Sort Later (L to take it out)")
        elif f[7] == 2:
            self.state.setText("Highlighted face: marked to discard")
        elif f[7] == 1:
            self.state.setText("Highlighted face: ignored - typing a name brings it back")
        elif f[5]:
            self.state.setText(f"Highlighted face: {f[5]}" + ("" if f[6] else " (suggested - press C to confirm)"))
        else:
            self.state.setText("Highlighted face: not named yet - type the name in the People bar")
        self.update_reference(f)
        self.canvas.update()

    # ---- a confirmed photo of the suggested person, beside the photo
    def update_reference(self, f):
        if f is None or not f[5] or f[6] or f[7] != 0:
            f = next((x for x in self.faces if x[5] and not x[6] and x[7] == 0), None)   # any suggestion here
        if f is None:
            self.ref_face = None
            self.ref_box.hide()
            return
        pid, _ = self.photo_id()
        row = self.win.conn.execute(
            """SELECT f.id, ph.path, f.x, f.y, f.w, f.h, f.frame_t FROM faces f JOIN photos ph ON ph.id=f.photo_id
               JOIN persons p ON p.id=f.person_id
               WHERE p.name=? AND f.confirmed=1 AND f.ignored=0 AND ph.id<>? AND ph.error IS NULL
               ORDER BY ph.is_video, COALESCE(f.quality, 0) DESC, f.px * f.score DESC LIMIT 1""",
            (f[5], pid or -1)).fetchone()
        if not row:
            self.ref_face = None
            self.ref_box.hide()
            return
        box = tuple(row[2:6]) + ((row[6],) if row[6] is not None else ())
        self.ref_face = (row[0], row[1], box)
        self.ref_title.setText(f"Confirmed photo of <b>{html.escape(f[5])}</b>")
        self.ref_box.show()
        self._ref_paint()
        QTimer.singleShot(0, self._ref_paint)        # again once the list has settled: use all the room

    def _ref_size(self):
        """As wide as the shortcut list; smaller if the window is too short to fit it above the list."""
        w = self.legend.width() - 16
        cur = self.ref_img.height() if self.ref_img.isVisible() else 0
        gap = self.legend_lay.itemAt(1).geometry().height()      # the empty space above the shortcut list
        if gap <= 0 and not cur:     # not laid out yet: estimate
            used = sum(self.legend_lay.itemAt(i).widget().sizeHint().height() + 3
                       for i in range(self.legend_lay.count())
                       if self.legend_lay.itemAt(i).widget() not in (None, self.ref_box))
            gap = self.legend.height() - used - self.ref_title.sizeHint().height() - self.ref_note.sizeHint().height() - 24
        return max(0, min(w, cur + gap - 4))

    def _ref_paint(self):
        if not self.ref_face:
            return
        side = self._ref_size()
        if side < 70:
            self.ref_img.hide()
            return
        fid, path, box = self.ref_face
        dpr = self.devicePixelRatioF()
        img = self.win.hires.get(fid, path, box, 320)
        if img is None:          # loading in the background: the small thumbnail meanwhile
            pm = self.win.face_pixmap(fid)
        else:
            pm = QPixmap.fromImage(img)
        pm = pm.scaled(int(side * dpr), int(side * dpr), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pm.setDevicePixelRatio(dpr)
        self.ref_img.setPixmap(pm)
        self.ref_img.setFixedSize(int(pm.width() / dpr) + 4, int(pm.height() / dpr) + 4)
        self.ref_img.show()

    def _ref_ready(self, fid):
        if self.ref_face and fid == self.ref_face[0] and self.isVisible():
            self._ref_paint()

    def eventFilter(self, obj, ev):
        if obj is self.legend and ev.type() == QEvent.Resize and self.ref_face:
            QTimer.singleShot(0, self._ref_paint)
        return super().eventFilter(obj, ev)

    def focused(self):
        return next((f for f in self.faces if f[0] == self.focus_id), None)

    def focus_face(self, f):
        """Clicking a face highlights it and puts the cursor in the People bar."""
        self.focus_id = f[0]
        self.reload_faces()
        self.focus_people_bar()

    def focus_people_bar(self):
        """The cursor in the People bar, ready to type - also after a right-click menu (which
        hands the keyboard back to the photo when it closes, so this is done again just after)."""
        def go():
            self.window().activateWindow()
            self.people_bar.setFocus(Qt.OtherFocusReason)
        go()
        QTimer.singleShot(0, go)
        QTimer.singleShot(120, go)

    def rebuild_chips(self):
        for holder in (self.people_chips, self.cat_chip, self.top5):
            while holder.count():
                w = holder.takeAt(0).widget()
                if w:
                    w.hide()
                    w.setParent(None)
                    w.deleteLater()
        for pid, name in self.tags:
            self.people_chips.addWidget(chip(name, f"Remove the {name} tag from this photo",
                                             lambda _=False, pid=pid, name=name: self.untag(pid, name)))
        if self.category:
            self.cat_chip.addWidget(chip(self.category, "Remove this photo's folder", self.clear_category))
        for i, name in enumerate(store.top_categories(self.win.conn)):
            b = QPushButton(name)
            b.setToolTip(f"Set folder: {name}   (Alt+{i + 1})")
            b.setFocusPolicy(Qt.NoFocus)
            b.setAutoDefault(False)
            b.setMaximumWidth(150)
            if name == self.category:
                b.setEnabled(False)
            b.clicked.connect(lambda _=False, name=name: self.apply_bar("category", name))
            self.top5.addWidget(b)

    def untag(self, person_id, name):
        pid, _ = self.photo_id()
        store.untag_photos(self.win.conn, [pid], person_id)
        self.done_action(f"Removed tag: {name}", advance=False)

    def clear_category(self):
        pid, _ = self.photo_id()
        store.set_category(self.win.conn, [pid], None)
        self.done_action("Folder removed", advance=False)

    def done_action(self, msg, advance=True):
        self.toast.setText(msg)
        QTimer.singleShot(1800, self.toast, lambda: self.toast.setText("") if self.toast.text() == msg else None)
        self.win.after_change(defer=True)     # the main window catches up when you pause
        if advance and self.pos < len(self.items) - 1:
            self.go(1)
        else:
            self.reload_faces()

    # ---- actions on the highlighted face
    def tattoo_targets(self):
        return [t for t in self.tsugg if self.person_id is None or t[0] == self.person_id]

    def key_confirm(self):
        f = self.focused()
        if not f and self.tattoo_targets():
            pid, _ = self.photo_id()
            t = self.tattoo_targets()
            for person_id, *_ in t:
                store.confirm_tattoo_matches(self.win.conn, [pid], person_id)
            self.done_action("✓ Tattoo match: " + ", ".join(x[1] for x in t))
            return
        if not f:
            self.done_action("✓ Kept")
            return
        if f[5] and not f[6] and f[7] == 0:
            store.confirm(self.win.conn, [f[0]])
            self.done_action(f"✓ Confirmed: {f[5]}")
        elif f[5] and f[6]:
            self.done_action(f"✓ {f[5]}")
        else:
            self.done_action("✓ Kept", advance=True)

    def key_remove(self):
        f = self.focused()
        if not f and self.tattoo_targets():
            pid, _ = self.photo_id()
            t = self.tattoo_targets()
            for person_id, *_ in t:
                store.reject_tattoo_matches(self.win.conn, [pid], person_id)
            self.done_action("Not their tattoo: " + ", ".join(x[1] for x in t))
            return
        if not f:
            # whole photo: remove the tag of the person you're browsing (or the only tag)
            target = [t for t in self.tags if t[0] == self.person_id] or (self.tags if len(self.tags) == 1 else [])
            if target:
                pid, _ = self.photo_id()
                store.untag_photos(self.win.conn, [pid], target[0][0])
                self.done_action(f"Removed tag: {target[0][1]}")
            return
        if f[7]:
            return
        if f[5]:
            store.reject(self.win.conn, [f[0]])
            self.done_action(f"Not {f[5]}")
        else:
            store.ungroup(self.win.conn, [f[0]])
            self.done_action("Taken out of the group")

    def key_discard(self):
        f = self.focused()
        if not f:
            pid, discarded = self.photo_id()
            if pid is None:
                return
            ok = self.win.dup_cache_valid()
            store.set_photos_discarded(self.win.conn, [pid], not discarded)
            if not discarded:
                self.win.prune_dups([pid], ok)
            self.done_action("Discard undone" if discarded else "Marked to discard", advance=not discarded)
            return
        if f[7] == 2:
            store.set_discarded(self.win.conn, [f[0]], False)
            self.done_action("Discard undone", advance=False)
        else:
            store.set_discarded(self.win.conn, [f[0]], True)
            self.done_action("Marked to discard")

    def key_sort_later(self):
        pid, _ = self.photo_id()
        if pid is None:
            return
        on = not self.win.conn.execute("SELECT sort_later FROM photos WHERE id=?", (pid,)).fetchone()[0]
        store.set_sort_later(self.win.conn, [pid], on)
        self.done_action("→ Sort Later" if on else "Taken out of Sort Later", advance=on)

    # ---- several faces at once (drag a box round them)
    def select_faces(self, faces, add=False):
        ids = {f[0] for f in faces}
        self.sel_faces = (self.sel_faces | ids) if add else ids
        if len(self.sel_faces) == 1 and not add:
            f = faces[0]
            self.sel_faces = set()
            self.focus_face(f)
            return
        self.focus_id = None
        self._sel_toast()
        self.canvas.update()

    def toggle_selected(self, f):
        if f[0] in self.sel_faces:
            self.sel_faces.discard(f[0])
        else:
            if self.focus_id and self.focus_id != f[0] and not self.sel_faces:
                self.sel_faces.add(self.focus_id)
            self.sel_faces.add(f[0])
        self.focus_id = None
        self._sel_toast()
        self.canvas.update()

    def _sel_toast(self):
        n = len(self.sel_faces)
        self.toast.setText(f"{n} face{'s' if n != 1 else ''} selected - I ignores them · N not faces · "
                           "right-click for more · Esc clears" if n else "")

    def clear_selection(self, quiet=False):
        if not self.sel_faces:
            return
        self.sel_faces = set()
        if not quiet:
            self.toast.setText("Selection cleared")
        self.canvas.update()

    def selected_faces(self):
        return [f for f in self.faces if f[0] in self.sel_faces]

    def ignore_selected(self):
        fs = self.selected_faces()
        named = [f for f in fs if f[5] and f[6]]
        ids = [f[0] for f in fs if not (f[5] and f[6]) and f[7] != 1]
        self.sel_faces = set()
        if ids:
            store.set_ignored(self.win.conn, ids, True)
        msg = f"Ignored {len(ids)} face{'s' if len(ids) != 1 else ''}"
        if named:
            msg += f" · kept {len(named)} confirmed ({', '.join(sorted({f[5] for f in named}))})"
        self.done_action(msg, advance=False)

    def notface_selected(self):
        ids = [f[0] for f in self.selected_faces() if not (f[5] and f[6]) and f[7] != 3]
        self.sel_faces = set()
        if ids:
            store.set_notface(self.win.conn, ids, 3)
        self.done_action(f"{len(ids)} marked “not a face”", advance=False)

    def key_ignore(self):
        if self.sel_faces:
            self.ignore_selected()
            return
        f = self.focused()
        if f and f[7] != 1:
            store.set_ignored(self.win.conn, [f[0]], True)
            self.done_action("Ignored")

    def key_ignore_unnamed(self):
        """Shift+I: ignore every face on this photo (every moment of a video) that has no name -
        strangers in the background - and go on to the next photo. Named and suggested faces stay."""
        ids = [f[0] for f in self.all_faces if not f[5] and f[7] == 0]
        if not ids:
            self.toast.setText("No unnamed faces left on this one")
            return
        store.set_ignored(self.win.conn, ids, True)
        self.done_action(f"Ignored {len(ids)} unnamed face{'s' if len(ids) != 1 else ''}")

    def key_notface(self):
        if self.sel_faces:
            self.notface_selected()
            return
        f = self.focused()
        if f and f[7] != 3:
            store.set_notface(self.win.conn, [f[0]], 3)
            self.done_action("Not a face - lookalikes will be set aside")
