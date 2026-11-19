"""The viewer's edit mode (1.54): E opens it for the photo or video on screen.

Photos: the edit panel replaces the tag column; the preview is worked out in the background on a
smaller copy, and Save copy (always the default) or Replace original works on the full picture.
Videos: trim, cut out parts, turn, low light - saved as a new clip (videoedit.py)."""
import os
import shutil

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QKeySequence, QShortcut
from PySide6.QtWidgets import QMenu, QMessageBox

import engine
import photoedit
import photo_tools
from edit_panel import AutoLight, EditPanel, RenderWorker, VideoEditPanel
from viewer_parts import ToolWorker, qimage_to_rgb, rgb_to_qimage

PREVIEW_SIDE = 1200


def _small(rgb, side=PREVIEW_SIDE):
    h, w = rgb.shape[:2]
    k = side / max(w, h)
    if k >= 1:
        return np.ascontiguousarray(rgb)
    return cv2.resize(rgb, (max(1, round(w * k)), max(1, round(h * k))), interpolation=cv2.INTER_AREA)


def copy_path(src, pattern, png=False):
    folder = os.path.dirname(src)
    stem, ext = os.path.splitext(os.path.basename(src))
    ext = ".png" if png or ext.lower() == ".png" else ".jpg"
    import renamer
    base = renamer.clean((pattern or "{name} (edited)").replace("{name}", stem)) or f"{stem} (edited)"
    out, n = os.path.join(folder, base + ext), 2
    while os.path.exists(out):
        out = os.path.join(folder, f"{base} ({n}){ext}")
        n += 1
    return out


class ViewerEditMixin:
    def setup_edit(self, mid):
        self.editing = None                      # None / "photo" / "video"
        self.ed_panel = EditPanel()
        self.ed_panel.hide()
        mid.addWidget(self.ed_panel)
        self.ved_panel = VideoEditPanel()
        self.ved_panel.hide()
        mid.addWidget(self.ved_panel)
        ep = self.ed_panel
        ep.changed.connect(self._ed_rerender)
        ep.committed.connect(self._ed_commit)
        ep.pick.connect(self._ed_pick_mode)
        ep.crop_mode.connect(self._ed_crop_mode)
        ep.tool.connect(self._ed_tool)
        ep.save.connect(self.ed_save)
        ep.cancel.connect(lambda: self.exit_edit(ask=True))
        ep.undo.connect(self.ed_undo)
        ep.redo.connect(self.ed_redo)
        ep.compare.connect(self._ed_compare)
        ep.auto.connect(lambda: self.ed_base is not None and ep.set_auto(AutoLight.of(self.ed_base)))
        vp = self.ved_panel
        vp.start_b.clicked.connect(lambda: vp.set_start(self.video_now()))
        vp.end_b.clicked.connect(lambda: vp.set_end(self.video_now()))
        vp.cut_from_b.clicked.connect(lambda: vp.cut_here(self.video_now()))
        vp.changed.connect(self._ved_changed)
        vp.ll_group.idClicked.connect(self.set_low_light)
        vp.save.connect(self.ved_save)
        vp.cancel.connect(lambda: self.exit_edit())
        vp.frames.connect(self._ved_frames)
        self.ed_render = RenderWorker()
        self.ed_render.done.connect(self._ed_rendered)
        self.ed_saver = ToolWorker()
        self.ed_saver.done.connect(self._ed_saved)
        self.ed_token = 0
        self.ed_pick = ""
        self.ed_crop = False
        self.ed_geo = self.ed_base = self.ed_qimg = self.ed_orig_qimg = None
        self.ed_undo_stack, self.ed_redo_stack = [], []
        for key, fn in (("Ctrl+Z", self.ed_undo), ("Ctrl+Y", self.ed_redo), ("Ctrl+Shift+Z", self.ed_redo)):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)

    # ---------------------------------------------------------------- in and out
    def enter_edit(self):
        if self.editing or not self.path:
            return
        if self.is_video:
            return self.enter_video_edit()
        if getattr(self, "is_anim", False):
            self.toast.setText("Animated GIFs can't be edited here")
            return
        if self.tool.busy:
            self.toast.setText("Wait for the photo tool to finish first")
            return
        self.slideshow.stop()
        self.ensure_picture()
        if self.edit_rgb is not None:                 # the photo tools were used first: edit on top of that
            base = self.edit_rgb
        elif self.image is not None:
            base = qimage_to_rgb(self.image)
        else:
            return
        self.ed_base = _small(base)
        self.ed_orig_qimg = rgb_to_qimage(self.ed_base)
        self.ed_view_qimg = self.image
        self.ed_undo_stack, self.ed_redo_stack = [photoedit.defaults()], []
        h, w = self.ed_base.shape[:2]
        self.ed_panel.load(photoedit.defaults(), (w, h))
        self.ed_panel.reset_aspect = None
        self.editing = "photo"
        self._edit_chrome(True)
        self.edit_bar.hide()
        self._ed_rerender()
        self._ed_status()

    def enter_video_edit(self):
        import jobs
        if not engine.ffmpeg_exe():
            self.toast.setText("Video editing needs FFmpeg, which isn't available")
            return
        self.slideshow.stop()
        if self.player:
            self.player.pause()
        length = self.video_length() or 0
        try:
            fps = engine.video_fps(self.path) or 25.0
        except Exception:
            fps = 25.0
        self.ved_panel.setup(length, fps, [(v, jobs.fps_label(v)) for v, _r in jobs.FPS_CHOICES], self.low_light)
        self.editing = "video"
        self._edit_chrome(True)
        self._ved_changed()
        self.state.setText("EDITING VIDEO - [ / ] trim, Cut from here, turn, low light · Save as new clip")

    def _edit_chrome(self, on):
        panel = self.ed_panel if self.editing == "photo" else self.ved_panel
        panel.setVisible(on)
        for w in (self.tag_col, self.legend):
            w.setVisible(not on)
        if hasattr(self, "edit_b"):
            self.edit_b.setEnabled(not on)
        self.canvas.update()

    def exit_edit(self, ask=False):
        if not self.editing:
            return
        if self.editing == "photo":
            if ask and len(self.ed_undo_stack) > 1 and photoedit.changed(self.ed_panel.p):
                if QMessageBox.question(self, "Edit", "Leave edit mode without saving your changes?") != QMessageBox.Yes:
                    return
            self.ed_token += 1
            self._ed_pick_mode("")
            self.ed_crop = False
            self.ed_panel.crop_b.setChecked(False)
            self.ed_panel.hide()
            if self.edit_rgb is not None:
                self.discard_edit(quiet=True)
            self.image = self.ed_view_qimg
            self.ed_base = self.ed_geo = self.ed_qimg = None
        else:
            self.ved_panel.hide()
            self.seek.set_marks(None, None)
            self.seek.set_cuts([])
        self.editing = None
        for w in (self.tag_col, self.legend):
            w.show()
        if hasattr(self, "edit_b"):
            self.edit_b.setEnabled(True)
        self.state.setText("")
        self.reload_faces()
        self.canvas.update()

    # ---------------------------------------------------------------- the photo preview
    def _ed_rerender(self):
        if self.editing != "photo" or self.ed_base is None:
            return
        self.ed_token += 1
        p, base, crop = dict(self.ed_panel.p), self.ed_base, not self.ed_crop

        def work():
            geo = photoedit.geometry(base, p, crop=crop)
            out = photoedit.tone_color(geo, p)
            if p.get("noise") or p.get("sharpen"):
                out = photoedit.detail(out, p, scale=max(0.3, max(out.shape[:2]) / 1600))
            return geo, out
        self.ed_render.submit(self.ed_token, work)

    def _ed_rendered(self, token, res):
        if token != self.ed_token or self.editing != "photo":
            return
        if isinstance(res, Exception):
            self.toast.setText(f"Couldn't show that: {res}")
            return
        self.ed_geo, out = res
        self.ed_qimg = rgb_to_qimage(out)
        self.image = self.ed_qimg
        self.canvas.update()

    def _ed_commit(self):
        p = dict(self.ed_panel.p)
        if self.ed_undo_stack and self.ed_undo_stack[-1] == p:
            return
        self.ed_undo_stack.append(p)
        self.ed_redo_stack = []
        self._ed_status()

    def _ed_status(self):
        n = len(photoedit.changed(self.ed_panel.p)) + (len(self.edit_ops) if self.edit_rgb is not None else 0)
        self.state.setText(f"EDITING · {n} change{'s' if n != 1 else ''} · Save copy keeps the original")
        self.ed_panel.undo_b.setEnabled(len(self.ed_undo_stack) > 1)
        self.ed_panel.redo_b.setEnabled(bool(self.ed_redo_stack))

    def ed_undo(self):
        if self.editing != "photo" or len(self.ed_undo_stack) <= 1:
            return
        self.ed_redo_stack.append(self.ed_undo_stack.pop())
        self.ed_panel.load(self.ed_undo_stack[-1], self.ed_panel.size_wh)
        self._ed_rerender()
        self._ed_status()

    def ed_redo(self):
        if self.editing != "photo" or not self.ed_redo_stack:
            return
        self.ed_undo_stack.append(self.ed_redo_stack.pop())
        self.ed_panel.load(self.ed_undo_stack[-1], self.ed_panel.size_wh)
        self._ed_rerender()
        self._ed_status()

    def _ed_compare(self, on):
        if self.editing != "photo":
            return
        self.image = self.ed_orig_qimg if on else (self.ed_qimg or self.ed_orig_qimg)
        self.canvas.update()

    def _ed_pick_mode(self, what):
        self.ed_pick = what or ""
        self.canvas.setCursor(Qt.CrossCursor if self.ed_pick else Qt.ArrowCursor)
        if what:
            self.toast.setText("Click the photo to pick the color" if what == "pop" else
                               "Click something that should be white or grey")

    def ed_picked(self, x, y):
        """A click on the photo with the eyedropper (x, y: fractions of the picture shown)."""
        what, self.ed_pick = self.ed_pick, ""
        self.canvas.setCursor(Qt.ArrowCursor)
        g = self.ed_geo
        if g is None or not what:
            return
        h, w = g.shape[:2]
        cx, cy = int(min(w - 1, max(0, x * w))), int(min(h - 1, max(0, y * h)))
        r = max(2, round(max(w, h) / 300))
        patch = g[max(0, cy - r):cy + r + 1, max(0, cx - r):cx + r + 1].reshape(-1, 3)
        self.ed_panel.picked(what, tuple(float(v) for v in patch.mean(axis=0)))

    def _ed_crop_mode(self, on):
        self.ed_crop = bool(on)
        self.toast.setText("Drag a box on the photo - what's outside is cut off" if on else "")
        self._ed_rerender()

    def ed_crop_drawn(self, box):
        g = self.ed_geo
        if g is None:
            return
        h, w = g.shape[:2]
        self.ed_panel.crop_drawn(box, w, h)

    def _ed_tool(self, kind, arg):
        if kind in ("touchup", "clarity"):
            self.open_touchup("heal" if kind == "touchup" else "clarity")
        elif kind == "upscale":
            m = QMenu(self)
            w, h = getattr(self, "_full_w", 0), getattr(self, "_full_h", 0)
            for f, nw, nh, ok in photo_tools.upscale_choices(w, h):
                a = m.addAction(f"×{f}   →   {nw:,} × {nh:,}" + ("" if ok else "   (too big)"),
                                lambda f=f: self.run_tool("upscale", f))
                a.setEnabled(ok)
            from PySide6.QtGui import QCursor
            m.exec(QCursor.pos())
        else:
            self.run_tool(kind, arg)

    def ed_tool_applied(self, res):
        """A photo tool finished while editing: its picture is the new starting point."""
        self.edit_bar.hide()
        self.ed_base = _small(res["rgb"])
        self.ed_orig_qimg = rgb_to_qimage(self.ed_base)
        h, w = self.ed_base.shape[:2]
        self.ed_panel.size_wh = (w, h)
        self._ed_rerender()
        self._ed_status()
        self.toast.setText(f"{res.get('label', 'Done')} - not saved yet")

    # ---------------------------------------------------------------- saving a photo
    def ed_save(self, replace=False):
        if self.editing != "photo" or self.ed_saver.busy:
            return
        p = dict(self.ed_panel.p)
        if not photoedit.changed(p) and self.edit_rgb is None:
            self.toast.setText("Nothing has changed yet")
            return
        src = self.path
        st = self.win.settings
        if replace and QMessageBox.question(
                self, "Replace original",
                f"Replace {os.path.basename(src)} with the edited picture?\n\nThe original is kept as "
                f"“{os.path.splitext(os.path.basename(src))[0]} (original)” and marked to discard, so it goes to "
                "~To Be Discarded the next time you Sort into folders.") != QMessageBox.Yes:
            return
        base, exif = self.edit_rgb, self.edit_exif
        png = any(op.startswith("x") or op.startswith("shadows") for op in self.edit_ops) if base is not None else False
        quality = int(st.get("edit_jpeg_quality", 95))
        pattern = st.get("edit_copy_name") or "{name} (edited)"

        def work(step, cancelled):
            full, ex = (base, exif) if base is not None else photo_tools.load_full(src)
            step(0.3)
            out = photoedit.render(full, p)
            step(0.8)
            if replace:
                ext = os.path.splitext(src)[1].lower()
                keep_png = ext == ".png" or png
                tmp = os.path.join(os.path.dirname(src), f".fsedit-{os.getpid()}" + (".png" if keep_png else ".jpg"))
                _save(out, tmp, ex, quality)
                return {"replace": True, "tmp": tmp, "src": src, "geometry": photoedit.geometry_changed(p)}
            dest = copy_path(src, pattern, png)
            _save(out, dest, ex, quality)
            return {"replace": False, "dest": dest, "src": src}
        self.ed_panel.setEnabled(False)
        self.toast.setText("Saving…")
        self.ed_saver.submit(work)

    def _ed_saved(self, res, err):
        self.ed_panel.setEnabled(True)
        if err or not res:
            QMessageBox.warning(self, "Save", f"Couldn't save the edited picture:\n{err}")
            return
        win = self.win
        src = res["src"]
        if not res["replace"]:
            out = win.add_derived(src, res["dest"], copy_of=True)
            if not win.settings.get("edit_keep_people", True):
                row = win.conn.execute("SELECT id FROM photos WHERE path=?", (out,)).fetchone()
                if row:
                    win.conn.execute("DELETE FROM photo_people WHERE photo_id=?", (row[0],))
                    win.conn.commit()
            msg = f"✓ Saved a copy next to the original: {os.path.basename(out or res['dest'])}"
        else:
            msg = self._replace_original(src, res["tmp"], res["geometry"])
            if msg is None:
                return
        self.exit_edit()
        self._shown = None
        win.pix_cache.clear()
        win.hi_cache.clear()
        self.load_picture()
        self.toast.setText(msg)
        win.statusBar().showMessage(msg, 10000)

    def _replace_original(self, src, tmp, geometry):
        win = self.win
        stem, ext = os.path.splitext(src)
        backup, n = f"{stem} (original){ext}", 2
        while os.path.exists(backup):
            backup = f"{stem} (original {n}){ext}"
            n += 1
        try:
            shutil.move(src, backup)
            os.replace(tmp, src)
        except OSError as e:
            if not os.path.exists(src) and os.path.exists(backup):
                shutil.move(backup, src)
            try:
                os.remove(tmp)
            except OSError:
                pass
            QMessageBox.warning(self, "Replace original", f"Couldn't replace the original:\n{e}")
            return None
        out = win.add_derived(src, backup, copy_of=True, scan=False)
        c = win.conn
        c.execute("UPDATE photos SET discarded=1 WHERE path=?", (out,))
        st = os.stat(src)
        c.execute("UPDATE photos SET mtime=?, size=? WHERE path=?", (round(st.st_mtime, 3), st.st_size, src))
        c.commit()
        pid = c.execute("SELECT id FROM photos WHERE path=?", (src,)).fetchone()
        if geometry and pid:
            win.rescan_photos([pid[0]])           # cropped / turned: the faces are looked for again
        return f"✓ Replaced - the original is kept as {os.path.basename(backup)} (marked to discard)"

    # ---------------------------------------------------------------- videos
    def _ved_changed(self):
        vp = self.ved_panel
        self.seek.set_marks(vp.start, vp.end)
        self.seek.set_cuts(vp.cuts)

    def ved_save(self):
        if self.editing != "video":
            return
        import videoedit
        vp = self.ved_panel
        opts = vp.options()
        if not opts["keep"]:
            self.toast.setText("Nothing would be left of the video")
            return
        whole = len(opts["keep"]) == 1 and opts["keep"][0][0] < 0.01 and \
            (vp.length is None or opts["keep"][0][1] > vp.length - 0.01)
        if whole and not opts["turn"] and not (opts["strength"] or opts["steady"] or opts["fps"]):
            self.toast.setText("Nothing has changed yet")
            return
        st = self.win.settings
        opts["quality"] = st.get("edit_clip_quality", "high")
        opts["name"] = st.get("edit_copy_name") or "{name} (edited)"
        src = self.path

        def done(out):
            if not out:
                self.win.statusBar().showMessage("Saving the clip stopped.", 6000)
                return
            self.win.add_derived(src, out, copy_of=True)
            msg = f"✓ New clip saved: {os.path.basename(out)}"
            self.win.statusBar().showMessage(msg, 12000)
            if self.isVisible():
                self.toast.setText(msg)
        if not self.win.run_job("videoedit", videoedit.edit_video, src, opts, on_done=done):
            QMessageBox.information(self, "Save", "Wait for the current job to finish first.")
            return
        self.exit_edit()
        self.toast.setText("Saving the new clip - you can keep working; Stop cancels it.")

    def _ved_frames(self):
        vp = self.ved_panel
        self.win.lowlight_best_frames(self.path, vp.ll_group.checkedId() or 1, vp.start, vp.end)


def _save(rgb, path, exif, quality=95):
    from PIL import Image
    im = Image.fromarray(rgb)
    kw = {"exif": exif} if exif else {}
    if path.lower().endswith(".png"):
        im.save(path, "PNG", compress_level=6, **kw)
    else:
        im.save(path, "JPEG", quality=quality, subsampling=0 if quality >= 90 else 2, **kw)
