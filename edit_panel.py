"""Edit mode's side panels (1.54): photos (crop & rotate, light, color, color pop, effects, detail,
fix & restore) and videos (trim, cut out parts, rotate, low light)."""
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMenu, QPushButton, QScrollArea, QSlider, QSpinBox, QToolButton,
                               QVBoxLayout, QWidget)

import photoedit

ASPECTS = [("Free", None), ("Original", "orig"), ("1:1", 1.0), ("4:5", 4 / 5), ("3:2", 3 / 2), ("16:9", 16 / 9)]
SECTION_CSS = "QToolButton { font-weight: bold; text-align: left; border: none; padding: 8px 4px; }"


class RenderWorker(QObject):
    """Works out the preview in the background: always the newest settings, one at a time."""
    done = Signal(object, object)                 # (token, result)

    def __init__(self):
        super().__init__()
        self._job = None
        self._lock = threading.Lock()
        self._busy = False

    def submit(self, token, fn):
        with self._lock:
            self._job = (token, fn)
            if self._busy:
                return
            self._busy = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            with self._lock:
                job, self._job = self._job, None
                if job is None:
                    self._busy = False
                    return
            token, fn = job
            try:
                res = fn()
            except Exception as e:                # (a bad setting shouldn't kill the thread)
                res = e
            self.done.emit(token, res)


class SliderRow(QWidget):
    """Label · value, with a slider under it. Double-click the slider: back to 0."""
    moved = Signal()
    released = Signal()

    def __init__(self, label, lo=-100, hi=100, fmt=None, scale=1.0, parent=None):
        super().__init__(parent)
        self.scale, self.fmt = scale, fmt
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(1)
        top = QHBoxLayout()
        self.label = QLabel(label)
        self.value_l = QLabel("0")
        self.value_l.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        top.addWidget(self.label)
        self.extra = QHBoxLayout()
        top.addLayout(self.extra)
        top.addStretch()
        top.addWidget(self.value_l)
        lay.addLayout(top)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(int(lo / scale), int(hi / scale))
        self.slider.setFocusPolicy(Qt.NoFocus)
        self.slider.valueChanged.connect(self._changed)
        self.slider.sliderReleased.connect(self.released.emit)
        self.slider.mouseDoubleClickEvent = lambda e: (self.set_value(0), self.released.emit())
        lay.addWidget(self.slider)
        self._show()

    def value(self):
        return self.slider.value() * self.scale

    def set_value(self, v, quiet=False):
        self.slider.blockSignals(quiet)
        self.slider.setValue(int(round(v / self.scale)))
        self.slider.blockSignals(False)
        self._show()

    def _show(self):
        v = self.value()
        if self.fmt:
            self.value_l.setText(self.fmt(v))
        else:
            self.value_l.setText(f"{v:+.0f}" if v else "0")

    def _changed(self, _v):
        self._show()
        self.moved.emit()
        if not self.slider.isSliderDown():
            self.released.emit()


class Section(QWidget):
    def __init__(self, title, open_=True, right=None, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = QHBoxLayout()
        self.btn = QToolButton()
        self.btn.setText(title)
        self.btn.setStyleSheet(SECTION_CSS)
        self.btn.setCheckable(True)
        self.btn.setChecked(open_)
        self.btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        from PySide6.QtWidgets import QSizePolicy
        self.btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.btn.setArrowType(Qt.DownArrow if open_ else Qt.RightArrow)
        self.btn.toggled.connect(self.toggle)
        head.addWidget(self.btn, 1)
        if right is not None:
            head.addWidget(right)
        lay.addLayout(head)
        self.body = QWidget()
        self.inner = QVBoxLayout(self.body)
        self.inner.setContentsMargins(14, 0, 4, 8)
        self.inner.setSpacing(4)
        lay.addWidget(self.body)
        self.body.setVisible(open_)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: rgba(128,128,128,0.35);")
        lay.addWidget(line)

    def toggle(self, on):
        self.btn.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
        self.body.setVisible(on)

    def add(self, w):
        if isinstance(w, QWidget):
            self.inner.addWidget(w)
        else:
            self.inner.addLayout(w)
        return w


def _btn(text, tip=None, checkable=False):
    b = QPushButton(text)
    b.setFocusPolicy(Qt.NoFocus)
    b.setCheckable(checkable)
    if tip:
        b.setToolTip(tip)
    return b


class EditPanel(QWidget):
    """Photo edit settings. changed(): the preview should be redrawn; committed(): one step for Undo."""
    changed = Signal()
    committed = Signal()
    pick = Signal(str)                             # "wb" / "pop": the next click on the photo picks a color
    crop_mode = Signal(bool)
    tool = Signal(str, object)                     # Fix & restore: (kind, arg)
    save = Signal(bool)                            # True = replace the original
    cancel = Signal()
    undo = Signal()
    redo = Signal()
    compare = Signal(bool)
    auto = Signal()
    gif_changed = Signal()                         # (1.57.2) the GIF's frames / speed / direction
    gif_mark = Signal(str)                         # "start" / "end": use the frame on screen
    gif_frames = Signal()                          # save every frame as a picture

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(340)
        self.p = photoedit.defaults()
        self.gif = None                            # None = a photo; else the GIF's own settings
        self.gif_n = 0
        self._quiet = False
        self.ratio = None
        self.size_wh = (1, 1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 0, 0, 0)
        # undo / redo / hold to compare
        top = QHBoxLayout()
        self.undo_b = _btn("↶ Undo", "Undo (Ctrl+Z)")
        self.redo_b = _btn("↷ Redo", "Redo (Ctrl+Y)")
        self.cmp_b = _btn("Hold to compare", "Shows the original while you hold it (or hold \\)")
        self.undo_b.clicked.connect(self.undo.emit)
        self.redo_b.clicked.connect(self.redo.emit)
        self.cmp_b.pressed.connect(lambda: self.compare.emit(True))
        self.cmp_b.released.connect(lambda: self.compare.emit(False))
        for b in (self.undo_b, self.redo_b):
            top.addWidget(b)
        top.addStretch()
        top.addWidget(self.cmp_b)
        outer.addLayout(top)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 6, 0)
        bl.setSpacing(0)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        self.rows = {}

        def slider(sec, key, label, lo=-100, hi=100, fmt=None, scale=1.0):
            r = SliderRow(label, lo, hi, fmt, scale)
            r.moved.connect(lambda k=key, r=r: self._set(k, r.value()))
            r.released.connect(self.committed.emit)
            sec.add(r)
            self.rows[key] = r
            return r

        # crop & rotate
        # (1.57.2) animated GIFs: everything below is done to every frame; these are the GIF's own
        self.gif_sec = Section("Animated GIF")
        self.gif_info = QLabel("")
        self.gif_info.setWordWrap(True)
        self.gif_sec.add(self.gif_info)
        self.gif_spins = {}
        for key, label in (("start", "First frame:"), ("end", "Last frame:")):
            row = QHBoxLayout()
            lab = QLabel(label)
            lab.setMinimumWidth(72)
            sp = QSpinBox()
            sp.setRange(1, 1)
            sp.valueChanged.connect(self._gif_spin)
            here = _btn("◀ Here", "Use the frame on screen (pick it with the bar under the picture)")
            here.clicked.connect(lambda _=False, k=key: self.gif_mark.emit(k))
            row.addWidget(lab)
            row.addWidget(sp, 1)
            row.addWidget(here)
            self.gif_sec.add(row)
            self.gif_spins[key] = sp
        row = QHBoxLayout()
        lab = QLabel("Speed:")
        lab.setMinimumWidth(72)
        self.gif_speed = QComboBox()
        import gifedit
        for v, label in gifedit.SPEEDS:
            self.gif_speed.addItem(label, v)
        self.gif_speed.currentIndexChanged.connect(self._gif_opts)
        row.addWidget(lab)
        row.addWidget(self.gif_speed, 1)
        self.gif_sec.add(row)
        row = QHBoxLayout()
        self.gif_rev = QCheckBox("Play backwards")
        self.gif_bounce = QCheckBox("Bounce")
        self.gif_bounce.setToolTip("Plays forwards, then back again")
        for c in (self.gif_rev, self.gif_bounce):
            c.toggled.connect(self._gif_opts)
            row.addWidget(c)
        self.gif_sec.add(row)
        fb = _btn("Save every frame as a picture…",
                  "Each kept frame as its own PNG, with your edits, in a folder next to the GIF")
        fb.clicked.connect(self.gif_frames.emit)
        self.gif_sec.add(fb)
        note = QLabel("Crop, turn, light, color and the rest are done to every frame. The bar under the picture "
                      "picks which frame you see. (Fix & restore works on single photos - save the frames to use it.)")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray; font-size: 11px;")
        self.gif_sec.add(note)
        self.gif_sec.hide()
        bl.addWidget(self.gif_sec)
        sec = Section("Crop && rotate", open_=False)
        row = QHBoxLayout()
        for text, tip, fn in (("⟲ 90°", "Turn left", lambda: self._turn(-1)), ("⟳ 90°", "Turn right", lambda: self._turn(1)),
                              ("⇋ Flip", "Flip left to right", lambda: self._toggle("flip_h")),
                              ("⇵ Flip", "Flip upside down", lambda: self._toggle("flip_v"))):
            b = _btn(text, tip)
            b.clicked.connect(fn)
            row.addWidget(b)
        sec.add(row)
        slider(sec, "straighten", "Straighten", -45, 45, lambda v: f"{v:+.1f}°" if v else "0°", 0.1)
        sec.add(QLabel("Crop shape:"))
        arow = QHBoxLayout()
        arow.setSpacing(3)
        self.aspect_group = QButtonGroup(self)
        for i, (label, val) in enumerate(ASPECTS):
            b = _btn(label, checkable=True)
            b.setMinimumWidth(10)
            self.aspect_group.addButton(b, i)
            arow.addWidget(b)
        self.aspect_group.button(0).setChecked(True)
        self.aspect_group.idClicked.connect(self._aspect)
        sec.add(arow)
        crow = QHBoxLayout()
        self.crop_b = _btn("Draw the crop on the photo", "Drag a box on the photo: what's outside it is cut off",
                           checkable=True)
        self.crop_b.toggled.connect(self.crop_mode.emit)
        nocrop = _btn("No crop")
        nocrop.clicked.connect(self._no_crop)
        crow.addWidget(self.crop_b, 1)
        crow.addWidget(nocrop)
        sec.add(crow)
        bl.addWidget(sec)
        self.crop_sec = sec
        # light
        auto = _btn("Auto", "Sets the light from the photo itself")
        auto.clicked.connect(self.auto_light)
        sec = Section("Light", right=auto)
        for key, label in (("exposure", "Exposure"), ("brightness", "Brightness"), ("contrast", "Contrast"),
                           ("highlights", "Highlights"), ("shadows", "Shadows")):
            slider(sec, key, label)
        bl.addWidget(sec)
        # color
        self.bw = QCheckBox("B&&W")
        self.bw.setFocusPolicy(Qt.NoFocus)
        self.bw.toggled.connect(lambda on: (self._set("bw", on), self.committed.emit()))
        sec = Section("Color", right=self.bw)
        wb = slider(sec, "temp", "White balance", fmt=lambda v: f"{photoedit.kelvin(v):,} K")
        self.wb_pick = _btn("✎", "Eyedropper: click something white or grey in the photo", checkable=True)
        self.wb_pick.setFixedWidth(30)
        self.wb_pick.toggled.connect(lambda on: self._picking("wb", on))
        wb.extra.addWidget(self.wb_pick)
        for key, label in (("tint", "Tint"), ("saturation", "Saturation"), ("vibrance", "Vibrance")):
            slider(sec, key, label)
        bl.addWidget(sec)
        # color pop
        self.pop_on = QCheckBox("On")
        self.pop_on.setFocusPolicy(Qt.NoFocus)
        self.pop_on.toggled.connect(self._pop_toggled)
        sec = Section("Color pop", open_=False, right=self.pop_on)
        note = QLabel("One color is kept; everything else turns grey.")
        note.setStyleSheet("color: gray;")
        note.setWordWrap(True)
        sec.add(note)
        srow = QHBoxLayout()
        srow.setSpacing(3)
        self.swatches = QButtonGroup(self)
        for i, (name, hue) in enumerate(photoedit.SWATCHES):
            b = QPushButton()
            b.setCheckable(True)
            b.setFixedSize(26, 26)
            b.setToolTip(name)
            b.setFocusPolicy(Qt.NoFocus)
            c = QColor.fromHsv(hue % 360, 200, 220)
            b.setStyleSheet(f"QPushButton {{ background: {c.name()}; border-radius: 13px; border: 2px solid #222; }}"
                            "QPushButton:checked { border: 3px solid white; }")
            self.swatches.addButton(b, i)
            srow.addWidget(b)
        self.swatches.idClicked.connect(self._swatch)
        self.pop_pick = _btn("✎", "Eyedropper: click the color to keep", checkable=True)
        self.pop_pick.setFixedWidth(30)
        self.pop_pick.toggled.connect(lambda on: self._picking("pop", on))
        srow.addWidget(self.pop_pick)
        srow.addStretch()
        sec.add(srow)
        self.pop_label = QLabel("")
        self.pop_label.setStyleSheet("color: gray;")
        sec.add(self.pop_label)
        self.sens = SliderRow("Sensitivity (how many shades are kept)", 0, 100, lambda v: f"{v:.0f}")
        self.sens.set_value(45, quiet=True)
        self.sens.moved.connect(self._sens)
        self.sens.released.connect(self.committed.emit)
        sec.add(self.sens)
        bl.addWidget(sec)
        self.pop_sec = sec
        # effects
        sec = Section("Effects", open_=False)
        slider(sec, "vignette", "Vignette")
        lab = QLabel("−100 black edges · 0 none · +100 white edges")
        lab.setStyleSheet("color: gray; font-size: 11px;")
        sec.add(lab)
        bl.addWidget(sec)
        # detail
        sec = Section("Detail", open_=False)
        slider(sec, "sharpen", "Sharpen", 0, 100, lambda v: f"{v:.0f}")
        slider(sec, "noise", "Smooth noise", 0, 100, lambda v: f"{v:.0f}")
        bl.addWidget(sec)
        # fix & restore (the photo tools)
        sec = Section("Fix && restore", open_=False)
        for kind, label, tip in (("redeye", "Remove red eye", None), ("enhance", "Auto enhance", None),
                                 ("restore", "Restore old / faded photo", None),
                                 ("shadows", "Smooth dark blotches", "Patchy shadows in a dark JPEG"),
                                 ("colorize", "Colorize black & white / sepia", None),
                                 ("touchup", "Touch-up brush…", "Paint over a spot or scratch"),
                                 ("clarity", "Face clarity brush…", "Paint over a soft face"),
                                 ("upscale", "Upscale…", "Bigger, saved lossless as PNG")):
            b = _btn(label, tip)
            b.clicked.connect(lambda _=False, k=kind: self.tool.emit(k, None))
            sec.add(b)
        bl.addWidget(sec)
        self.fix_sec = sec
        bl.addStretch()
        # bottom
        bot = QHBoxLayout()
        reset = _btn("Reset", "Back to the original (Undo brings it back)")
        reset.clicked.connect(self.reset)
        cancel = _btn("Cancel", "Leave edit mode without saving (Esc)")
        cancel.clicked.connect(self.cancel.emit)
        self.save_b = QToolButton()
        self.save_b.setText("Save copy")
        self.save_b.setToolTip("Saves a copy next to the original - always the default")
        self.save_b.setPopupMode(QToolButton.MenuButtonPopup)
        self.save_b.setStyleSheet("QToolButton { background: #4c8dff; color: #0b1220; font-weight: bold; "
                                  "padding: 6px 12px; border-radius: 6px; }")
        m = QMenu(self.save_b)
        m.addAction("Save copy  ·  always the default", lambda: self.save.emit(False))
        m.addAction("Replace original…", lambda: self.save.emit(True))
        self.save_b.setMenu(m)
        self.save_b.clicked.connect(lambda: self.save.emit(False))
        bot.addWidget(reset)
        bot.addWidget(cancel)
        bot.addStretch()
        bot.addWidget(self.save_b)
        outer.addLayout(bot)

    # ---------------------------------------------------------------- animated GIFs
    def set_gif(self, n, text=""):
        """n frames (None = editing a photo, not a GIF)."""
        import gifedit
        self.gif_n = n or 0
        self.gif = gifedit.gif_defaults(n) if n else None
        self.gif_sec.setVisible(bool(n))
        self.fix_sec.setVisible(not n)
        if not n:
            return
        self.gif_info.setText(text)
        self._quiet = True
        for sp in self.gif_spins.values():
            sp.setRange(1, max(1, n))
        self.gif_spins["start"].setValue(1)
        self.gif_spins["end"].setValue(n)
        self.gif_speed.setCurrentIndex(self.gif_speed.findData(1.0))
        self.gif_rev.setChecked(False)
        self.gif_bounce.setChecked(False)
        self._quiet = False

    def set_gif_mark(self, which, frame):
        if self.gif is not None:
            self.gif_spins[which].setValue(frame + 1)

    def _gif_spin(self, _v):
        if self._quiet or self.gif is None:
            return
        a, b = self.gif_spins["start"].value(), self.gif_spins["end"].value()
        if self.sender() is self.gif_spins["start"] and a > b:
            self._quiet = True
            self.gif_spins["end"].setValue(a)
            self._quiet = False
        elif self.sender() is self.gif_spins["end"] and b < a:
            self._quiet = True
            self.gif_spins["start"].setValue(b)
            self._quiet = False
        self._gif_opts()

    def _gif_opts(self, *_a):
        if self._quiet or self.gif is None:
            return
        self.gif = {"start": self.gif_spins["start"].value() - 1, "end": self.gif_spins["end"].value() - 1,
                    "speed": float(self.gif_speed.currentData()), "reverse": self.gif_rev.isChecked(),
                    "bounce": self.gif_bounce.isChecked()}
        self.gif_changed.emit()

    # ---------------------------------------------------------------- values
    def _set(self, key, v):
        if self._quiet:
            return
        self.p[key] = v
        self.changed.emit()

    def _toggle(self, key):
        self.p[key] = not self.p.get(key)
        self.p["crop"] = None
        self.changed.emit()
        self.committed.emit()

    def _turn(self, d):
        self.p["turn"] = (self.p.get("turn", 0) + d) % 4
        self.p["crop"] = None                     # (a crop made on the old shape wouldn't fit)
        self.changed.emit()
        self.committed.emit()

    def _aspect(self, i):
        val = ASPECTS[i][1]
        w, h = self.size_wh
        if self.p.get("turn", 0) % 2:
            w, h = h, w
        self.ratio = (w / h) if val == "orig" else val
        self.p["crop"] = photoedit.aspect_crop(w, h, self.ratio) if self.ratio else None
        self.changed.emit()
        self.committed.emit()

    def _no_crop(self):
        self.p["crop"] = None
        self.aspect_group.button(0).setChecked(True)
        self.ratio = None
        self.changed.emit()
        self.committed.emit()

    def crop_drawn(self, box, w, h):
        """A box drawn on the (uncropped) photo, made to the chosen shape."""
        self.p["crop"] = photoedit.fit_aspect(box, w, h, self.ratio)
        self.changed.emit()
        self.committed.emit()

    def _picking(self, what, on):
        other = self.pop_pick if what == "wb" else self.wb_pick
        if on:
            other.blockSignals(True)
            other.setChecked(False)
            other.blockSignals(False)
        self.pick.emit(what if on else "")

    def picked(self, what, rgb):
        """The eyedropper's color (RGB 0-255, from the photo before color changes)."""
        (self.wb_pick if what == "wb" else self.pop_pick).setChecked(False)
        if what == "wb":
            t, tint = photoedit.wb_from_sample(rgb)
            self.p["temp"], self.p["tint"] = t, tint
            self.sync()
        else:
            c = QColor(*[int(v) for v in rgb])
            hue = c.hsvHue() if c.hsvHue() >= 0 else 0
            self.swatches.setExclusive(False)
            for b in self.swatches.buttons():
                b.setChecked(False)
            self.swatches.setExclusive(True)
            self.p["pop"] = {"hue": hue, "sens": self.sens.value()}
            self.pop_label.setText(f"Keeping: the picked color (hue {hue}°)")
            self.pop_on.blockSignals(True)
            self.pop_on.setChecked(True)
            self.pop_on.blockSignals(False)
        self.changed.emit()
        self.committed.emit()

    def _swatch(self, i):
        name, hue = photoedit.SWATCHES[i]
        self.p["pop"] = {"hue": hue, "sens": self.sens.value()}
        self.pop_label.setText(f"Keeping: {name.lower()}")
        self.pop_on.blockSignals(True)
        self.pop_on.setChecked(True)
        self.pop_on.blockSignals(False)
        self.changed.emit()
        self.committed.emit()

    def _pop_toggled(self, on):
        if on:
            if not self.p.get("pop"):
                self.swatches.button(5).setChecked(True)          # blues, to begin with
                self._swatch(5)
                return
        else:
            self.p["pop"] = None
            self.pop_label.setText("")
        self.changed.emit()
        self.committed.emit()

    def _sens(self):
        if self.p.get("pop"):
            self.p["pop"] = dict(self.p["pop"], sens=self.sens.value())
            self.changed.emit()

    def auto_light(self):
        """Light settings from the preview's brightness spread."""
        self.auto.emit()

    def set_auto(self, vals):
        self.p.update(vals)
        self.sync()
        self.changed.emit()
        self.committed.emit()

    def reset(self):
        self.p = photoedit.defaults()
        self.ratio = None
        self.aspect_group.button(0).setChecked(True)
        self.sync()
        self.changed.emit()
        self.committed.emit()

    def load(self, p, size_wh):
        self.p = dict(p)
        self.size_wh = size_wh
        self.sync()

    def sync(self):
        """The controls show self.p."""
        self._quiet = True
        for k, r in self.rows.items():
            r.set_value(self.p.get(k, 0) or 0, quiet=True)
        self.bw.blockSignals(True)
        self.bw.setChecked(bool(self.p.get("bw")))
        self.bw.blockSignals(False)
        pop = self.p.get("pop")
        self.pop_on.blockSignals(True)
        self.pop_on.setChecked(bool(pop))
        self.pop_on.blockSignals(False)
        if pop:
            self.sens.set_value(pop.get("sens", 45), quiet=True)
        else:
            self.pop_label.setText("")
        self._quiet = False


class AutoLight:
    """Light settings that suit a picture (RGB uint8)."""

    @staticmethod
    def of(rgb):
        import numpy as np
        small = rgb[::4, ::4].astype(np.float32) / 255
        L = small[..., 0] * 0.2126 + small[..., 1] * 0.7152 + small[..., 2] * 0.0722
        p2, p50, p98 = np.percentile(L, [2, 50, 98])
        exposure = int(np.clip(np.log2(0.46 / max(p50, 0.02)) * 50, -60, 60))
        shadows = int(np.clip((0.08 - p2) * 300, 0, 40))
        highlights = int(np.clip((0.95 - p98) * 150, -40, 0)) if p98 > 0.97 else 0
        contrast = int(np.clip((0.75 - (p98 - p2)) * 60, -10, 25))
        return {"exposure": exposure, "shadows": shadows, "highlights": highlights, "contrast": contrast,
                "brightness": 0}


class VideoEditPanel(QWidget):
    """Trim, cut out parts, turn, low light, Steady, frame rate - saved as a new clip."""
    changed = Signal()
    save = Signal()
    cancel = Signal()
    frames = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(340)
        self.start = None
        self.end = None
        self.cuts = []
        self.turn = 0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 0, 0, 0)
        t = QLabel("<b>Trim</b>")
        lay.addWidget(t)
        row = QHBoxLayout()
        self.start_b = _btn("Start here  [")
        self.end_b = _btn("End here  ]")
        row.addWidget(self.start_b)
        row.addWidget(self.end_b)
        lay.addLayout(row)
        self.trim_l = QLabel("The whole video")
        self.trim_l.setStyleSheet("color: gray;")
        lay.addWidget(self.trim_l)
        hint = QLabel(", and . step one frame. Click the bar to jump anywhere.")
        hint.setStyleSheet("color: gray; font-size: 11px;")
        lay.addWidget(hint)
        lay.addSpacing(6)
        lay.addWidget(QLabel("<b>Cut out a part</b>"))
        self.cut_list = QListWidget()
        self.cut_list.setMaximumHeight(90)
        lay.addWidget(self.cut_list)
        crow = QHBoxLayout()
        self.cut_from_b = _btn("Cut from here", "Starts a part to cut out at this moment", checkable=True)
        self.cut_rm_b = _btn("Remove cut")
        crow.addWidget(self.cut_from_b, 1)
        crow.addWidget(self.cut_rm_b)
        lay.addLayout(crow)
        self.cut_rm_b.clicked.connect(self.remove_cut)
        lay.addSpacing(6)
        lay.addWidget(QLabel("<b>Rotate</b>"))
        rrow = QHBoxLayout()
        self.left_b = _btn("⟲ 90° left")
        self.right_b = _btn("⟳ 90° right")
        self.left_b.clicked.connect(lambda: self.set_turn(self.turn - 1))
        self.right_b.clicked.connect(lambda: self.set_turn(self.turn + 1))
        rrow.addWidget(self.left_b)
        rrow.addWidget(self.right_b)
        lay.addLayout(rrow)
        self.turn_l = QLabel("")
        self.turn_l.setStyleSheet("color: gray;")
        lay.addWidget(self.turn_l)
        lay.addSpacing(6)
        lay.addWidget(QLabel("<b>Low light</b> <span style='color:gray'>· shown while you edit, saved into the "
                             "clip</span>"))
        lrow = QHBoxLayout()
        self.ll_group = QButtonGroup(self)
        for i, text in enumerate(("Off", "On", "Strong")):
            b = _btn(text, checkable=True)
            self.ll_group.addButton(b, i)
            lrow.addWidget(b)
        self.ll_group.button(0).setChecked(True)
        lay.addLayout(lrow)
        self.steady = QCheckBox("Steady - calm the flicker and crawling grain")
        self.steady.setFocusPolicy(Qt.NoFocus)
        lay.addWidget(self.steady)
        frow = QHBoxLayout()
        frow.addWidget(QLabel("Frame rate"))
        self.fps = QComboBox()
        frow.addWidget(self.fps, 1)
        lay.addLayout(frow)
        self.frames_b = _btn("Best frames as photos…")
        self.frames_b.clicked.connect(self.frames.emit)
        lay.addWidget(self.frames_b)
        note = QLabel("Exact to the frame: the clip is re-encoded (H.264, same size) with the trim, cuts, rotation "
                      "and low light in one go - a progress bar shows. The original is never changed.")
        note.setWordWrap(True)
        note.setStyleSheet("background: rgba(76,141,255,0.14); border-radius: 6px; padding: 8px;")
        lay.addWidget(note)
        lay.addStretch()
        bot = QHBoxLayout()
        reset = _btn("Reset")
        reset.clicked.connect(self.reset)
        cancel = _btn("Cancel")
        cancel.clicked.connect(self.cancel.emit)
        self.save_b = _btn("Save as new clip")
        self.save_b.setStyleSheet("QPushButton { background: #4c8dff; color: #0b1220; font-weight: bold; "
                                  "padding: 6px 12px; border-radius: 6px; }")
        self.save_b.clicked.connect(self.save.emit)
        bot.addWidget(reset)
        bot.addWidget(cancel)
        bot.addStretch()
        bot.addWidget(self.save_b)
        lay.addLayout(bot)
        self.length = None
        self.pending_cut = None

    def setup(self, length, fps, fps_choices, low_light):
        self.length, self.src_fps = length, fps
        self.start = self.end = None
        self.cuts, self.turn, self.pending_cut = [], 0, None
        self.cut_from_b.setChecked(False)
        self.fps.clear()
        self.fps.addItem(f"Same as the video ({fps:.3g} fps)", None)
        for v, label in fps_choices:
            if abs(v - fps) > 0.01:
                self.fps.addItem(f"{label} fps", v)
        self.ll_group.button(max(0, min(2, int(low_light or 0)))).setChecked(True)
        self.steady.setChecked(False)
        self.refresh()

    def set_turn(self, t):
        self.turn = t % 4
        self.refresh()
        self.changed.emit()

    def set_start(self, t):
        if self.end is not None and t >= self.end:
            self.end = None
        self.start = t if t > 0.001 else None
        self.refresh()
        self.changed.emit()

    def set_end(self, t):
        if self.start is not None and t <= self.start:
            self.start = None
        self.end = t if not self.length or t < self.length - 0.001 else None
        self.refresh()
        self.changed.emit()

    def cut_here(self, t):
        """First press: a cut starts here; second: it ends here."""
        if self.pending_cut is None:
            self.pending_cut = t
            self.cut_from_b.setText("Cut to here")
        else:
            a, b = sorted((self.pending_cut, t))
            if b - a > 0.01:
                self.cuts.append((a, b))
                self.cuts.sort()
            self.pending_cut = None
            self.cut_from_b.setText("Cut from here")
            self.cut_from_b.setChecked(False)
        self.refresh()
        self.changed.emit()

    def remove_cut(self):
        i = self.cut_list.currentRow()
        if 0 <= i < len(self.cuts):
            del self.cuts[i]
        elif self.cuts:
            self.cuts.pop()
        self.refresh()
        self.changed.emit()

    def reset(self):
        self.start = self.end = None
        self.cuts, self.turn, self.pending_cut = [], 0, None
        self.cut_from_b.setText("Cut from here")
        self.cut_from_b.setChecked(False)
        self.ll_group.button(0).setChecked(True)
        self.steady.setChecked(False)
        self.fps.setCurrentIndex(0)
        self.refresh()
        self.changed.emit()

    def keep(self):
        """The parts kept: [(from, to)] in seconds."""
        import videoedit
        return videoedit.kept_parts(self.length or 0, self.start, self.end, self.cuts)

    def refresh(self):
        import engine
        clock = engine.clock
        a = clock(self.start or 0)
        b = clock(self.end if self.end is not None else (self.length or 0))
        kept = sum(y - x for x, y in self.keep()) if self.length else 0
        self.trim_l.setText(f"Start {a} · End {b}" + (f" · new clip {clock(kept)}" if self.length else ""))
        self.cut_list.clear()
        for x, y in self.cuts:
            self.cut_list.addItem(QListWidgetItem(f"{clock(x)} – {clock(y)}"))
        self.turn_l.setText({0: "", 1: "Turned 90° right", 2: "Upside down", 3: "Turned 90° left"}[self.turn])

    def options(self):
        return {"keep": self.keep(), "turn": self.turn, "strength": self.ll_group.checkedId(),
                "steady": self.steady.isChecked(), "fps": self.fps.currentData(), "method": "motion"}
