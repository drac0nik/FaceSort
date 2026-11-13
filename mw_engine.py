"""MainWindow, part: the face engine, scanning and grouping."""
import os
import rotate
import engine
import face_attrs
import engine_hq
import jobs
import bodies
import store
from PySide6.QtCore import QItemSelectionModel, QRect, Qt, QTimer
from PySide6.QtWidgets import QApplication, QInputDialog, QMenu, QMessageBox, QToolBar
from icons import app_icon
from ui_common import find_dark_job, log, open_file, show_in_folder
from viewer import PhotoViewer
from quick_review import QuickReview


class EngineMixin:
    """Part of MainWindow (see facesort.py): the face engine, scanning and grouping."""

    # ------------------------------------------------------------------ face engine
    def startup(self):
        s = self.settings
        if not s.get("engine_chosen") and not os.environ.get("FACESORT_NO_PROMPT"):
            s["engine_chosen"] = True
            store.save_settings(s)
            has_faces = self.conn.execute("SELECT 1 FROM faces LIMIT 1").fetchone() is not None
            box = QMessageBox(self)
            box.setWindowTitle("Face recognition")
            box.setText("<b>Use high-accuracy face recognition?</b>")
            box.setInformativeText(
                "It recognises turned, partly hidden and cut-off faces far better, and picks up fewer things "
                "that aren't faces (knees, elbows, patterns).<br><br>"
                f"• one-time download of {engine_hq.DOWNLOAD_MB} MB<br>"
                "• slower on computers without a graphics card<br>"
                + ("• your library is rescanned once - names, tags, folders and everything you've "
                   "sorted are kept<br>" if has_faces else "")
                + "<br>You can change this any time in Settings.")
            hq = box.addButton("Use high accuracy", QMessageBox.AcceptRole)
            box.addButton("Keep standard", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is hq:
                return self.switch_engine("arcface")
            s["engine"] = "sface"
            store.save_settings(s)
        if s.get("engine") in ("arcface", "adaface") and not engine_hq.available(s.get("engine")):
            s["engine"] = "arcface" if engine_hq.available("arcface") else "sface"   # models removed: fall back
            store.save_settings(s)
            self.statusBar().showMessage("High-accuracy models not found - using the standard face engine "
                                         "(switch back in Settings).", 15000)
        # the sorted library (and vaults with their own place) are always part of the scan, so
        # photos left there by older versions or moved by hand come back into FaceSort
        lib = self.library_dest()
        extra = [lib] if lib else []
        try:
            extra += [p for p in store.vault_root_paths(self.conn).values() if os.path.isdir(p)]
        except Exception:
            pass
        changed = False
        for d in extra:
            d = store.norm_path(d)
            if not any(d == f or d.startswith(f.rstrip("\\/") + os.sep) for f in s["folders"]):
                s["folders"].append(d)
                changed = True
        if changed:
            store.save_settings(s)
        # background checks run one after another (not all at once fighting over the disk), each
        # only when nothing else is working: database statistics, copies, things in the wrong
        # place, then search-by-description indexing
        self._bg_queue = [self.start_stats, self.start_clarity, self.start_copies_check, self.start_misplaced,
                          self.start_clip_index, self.start_bodies, self.offer_folder_cleanup, self.start_update_check,
                          self.offer_dark_look, self.offer_face_details, self.start_face_details,
                          self.start_sex_learning]
        self._bg_timer = QTimer(self)
        self._bg_timer.timeout.connect(self.bg_tick)
        QTimer.singleShot(2500, lambda: None if getattr(self, "_closing", False) else self._bg_timer.start(1200))
        # videos older versions couldn't read are tried again (FFmpeg can read many of them now)
        if not s.get("video_retry_ffmpeg"):
            s["video_retry_ffmpeg"] = True
            store.save_settings(s)
            bad = [(i, p) for i, p in self.conn.execute("SELECT id, path FROM photos WHERE error IS NOT NULL")
                   if engine.is_video(p)]
            if bad:
                self.conn.executemany("DELETE FROM photos WHERE id=?", [(i,) for i, _ in bad])
                self.conn.commit()
                log(f"{len(bad)} unreadable videos will be tried again")
                QTimer.singleShot(1500, lambda paths=[p for _, p in bad]: self.scan_paths(paths))
        if s["folders"] and s.get("scan_on_start", False):
            # with an import limit, nothing new is imported until you click Scan
            self.start_scan(check_only=self.import_limit() > 0, opts=self.remembered_scan_opts())
        elif s["folders"]:
            self.statusBar().showMessage("Click Scan to look for new photos (Settings can do this every time "
                                         "FaceSort opens).", 15000)

    def switch_engine(self, model):
        """Change face engine: download its models if needed, then rescan everything once.
        Names, tags, categories, ignores and 'not them' answers carry over to the same faces."""
        if model in ("arcface", "adaface") and not engine_hq.available(model):
            def done(ok):
                if ok:
                    self.switch_engine(model)
                else:
                    QMessageBox.warning(self, "Download", "The face models couldn't be downloaded. "
                                        "Check your internet connection and try again in Settings.")
                    if self.settings["folders"]:
                        self.start_scan()

            def download_job(conn, progress, cancelled):
                if model == "adaface":
                    return engine_hq.download_adaface(progress, cancelled)
                return engine_hq.download_models(progress, cancelled)
            if not self.run_job("download", download_job, on_done=done):
                QMessageBox.information(self, "Busy", "Wait for the current task to finish first.")
            return
        self.settings["engine"] = model
        self.settings["engine_chosen"] = True
        store.save_settings(self.settings)
        if store.get_model(self.conn) != model:
            store.set_model(self.conn, model)
            self.conn.execute("UPDATE photos SET mtime=NULL")   # everything gets measured again
            self.conn.commit()
        self.pix_cache.clear()
        self.refresh_all()
        if self.settings["folders"]:
            self.start_scan()
        self.statusBar().showMessage({"arcface": "High-accuracy", "adaface": "Best-accuracy (AdaFace)"}.get(model, "Standard")
                                     + " face engine on - rescanning your library once.", 15000)

    def quick_review(self):
        if self.busy():
            QMessageBox.information(self, "Busy", "Wait for the current task to finish first.")
            return
        if self.regroup_worker and self.regroup_worker.isRunning():
            self.regroup_worker.wait()
        self.regroup_timer.stop()
        self.close_viewer(refresh=False)
        r = getattr(self, "_review_widget", None)
        if r is None:                      # made once, then reused (only hidden in between)
            r = QuickReview(self)
            self._review_widget = r
            self.main_stack.addWidget(r)
        else:
            r.restart()
        self.review = r
        self.place_details(False)
        self.main_stack.setCurrentWidget(r)
        for sc in self._main_shortcuts:
            sc.setEnabled(False)
        r.setFocus()

    def close_review(self):
        if getattr(self, "review", None) is None:
            return
        self.review = None
        self.main_stack.setCurrentIndex(0)
        self.place_details(True)
        for sc in self._main_shortcuts:
            sc.setEnabled(True)
        self.pix_cache.clear()
        self.after_change()
        self.grid.setFocus()

    def start_tattoo_search(self, person_ids=None):
        if not self.conn.execute("SELECT 1 FROM tattoos LIMIT 1").fetchone():
            QMessageBox.information(self, "Tattoo matches",
                                    "No tattoos are marked yet.\n\nOpen a photo (double-click), press M and drag a box "
                                    "around a tattoo, then type whose it is.")
            return

        def done(st):
            msg = (f"Checked {st['photos']:,} photo{'s' if st['photos'] != 1 else ''} for "
                   f"{st['tattoos']} tattoo{'s' if st['tattoos'] != 1 else ''}: "
                   f"{st['found']} possible match{'es' if st['found'] != 1 else ''} found.")
            if st.get("unusable"):
                msg += f" {st['unusable']} mark(s) were too plain or small to search for - mark a bigger area."
            self.statusBar().showMessage(msg, 15000)
            if st["found"] and self.view[0] not in ("person", "tattoos"):
                self.view = ("tattoos", None)
                self.refresh_all()

        if not self.run_job("tattoo", jobs.tattoo_search, self.settings, person_ids, on_done=done):
            QMessageBox.information(self, "Busy", "Wait for the current task to finish first.")

    def confirm_all(self):
        ids = [r[0] for r in self.model.rows if not r[3]]
        if ids:
            store.confirm(self.conn, ids)
            self.after_change()

    def act_remove(self):
        keys = self.selected_ids()
        ids = [k for k in keys if k > 0]
        photos = [-k for k in keys if k < 0]
        if not keys:
            return
        if self.view[0] == "shoot":
            sb = self.sel_shoot_bodies()
            if sb:
                bodies.reject(self.conn, sb)
                self.after_change(regroup=False)
            return
        tp = self.sel_tattoo_photos()
        if self.view[0] == "tattoos":
            if tp:
                store.reject_tattoo_matches(self.conn, tp)
                self.after_change(regroup=False)
            return
        if self.view[0] == "person":
            if tp:
                store.reject_tattoo_matches(self.conn, tp, self.view[1])
            tagged = [p for p in photos if p not in tp]
            if tagged:
                store.untag_photos(self.conn, tagged, self.view[1])
            if ids:
                store.reject(self.conn, ids)
            self.quick_change(regroup=bool(ids), drop=ids + [-p for p in photos])
        elif self.view[0] == "group":
            store.ungroup(self.conn, ids)
            self.quick_change(regroup=False, drop=ids)
        elif self.view[0] == "multi" and ids:
            kinds = {k[0] for k in self.view[1]}
            if kinds <= {"person", "later_person"}:
                store.reject(self.conn, ids)
                self.quick_change(drop=ids)
            elif kinds <= {"group"}:
                store.ungroup(self.conn, ids)
                self.quick_change(regroup=False, drop=ids)
        elif self.view[0] == "later_person" and ids:
            store.reject(self.conn, ids)
            self.quick_change(drop=ids)
        elif self.view[0] == "later_group" and ids:
            self.conn.execute(f"UPDATE faces SET later_cluster=NULL WHERE id IN ({','.join('?' * len(ids))})", ids)
            self.conn.commit()
            self.quick_change(regroup=False, drop=ids)
        elif self.view[0] == "nofaces" and photos:
            q = ",".join("?" * len(photos))
            n = self.conn.execute(f"SELECT COUNT(*) FROM photo_people WHERE photo_id IN ({q})", photos).fetchone()[0]
            if n and QMessageBox.question(self, "Remove tags",
                                          f"Remove all people tags from {len(photos)} photo"
                                          f"{'s' if len(photos) != 1 else ''}?") == QMessageBox.Yes:
                self.conn.execute(f"DELETE FROM photo_people WHERE photo_id IN ({q})", photos)
                self.conn.commit()
                self.after_change(regroup=False)

    def act_ignore(self):
        ids = self.sel_faces()
        if ids and self.view[0] != "ignored":
            store.set_ignored(self.conn, ids, True)
            self.quick_change(regroup=self.view[0] == "person", drop=ids)

    def retry_unreadable(self):
        """Forget the failures so the next scan tries those files again."""
        self.conn.execute("DELETE FROM photos WHERE error IS NOT NULL")
        self.conn.commit()
        self.view = ("none", None)
        self.start_scan()

    def act_discard(self):
        keys = self.selected_ids()
        if keys and self.view[0] != "discarded":
            ids, photos = [k for k in keys if k > 0], [-k for k in keys if k < 0]
            ok = self.dup_cache_valid()
            if ids:
                store.set_discarded(self.conn, ids, True)
            if photos:
                store.set_photos_discarded(self.conn, photos, True)
            if photos and not ids:
                # whole photos (Duplicates, folders, albums…): this gallery now, the rest when you pause
                self.prune_dups(photos, ok)
                self.quick_refresh()
                return
            self.quick_change(regroup=self.view[0] == "person", drop=ids + [-p for p in photos])

    def act_undiscard(self):
        keys = self.selected_ids() if self.view[0] == "discarded" else []
        if keys:
            ids, photos = [k for k in keys if k > 0], [-k for k in keys if k < 0]
            if ids:
                store.set_discarded(self.conn, ids, False)
            if photos:
                store.set_photos_discarded(self.conn, photos, False)
            self.after_change()

    def rescan_nofaces(self):
        """Look for faces again in photos where none were found (e.g. after changing the
        detection settings). People you tagged on those photos are kept."""
        n = self.conn.execute("SELECT COUNT(*) FROM photos WHERE error IS NULL AND nfaces=0 "
                              "AND COALESCE(is_other, 0)=0").fetchone()[0]
        if not n:
            return
        self.conn.execute("UPDATE photos SET mtime=NULL WHERE error IS NULL AND nfaces=0 AND COALESCE(is_other, 0)=0")
        self.conn.commit()
        self.start_scan()

    def rescan_dark(self, quiet=False):
        """Find the dark photos scanned before the second look existed and scan them again (names
        are kept: faces are matched by position)."""
        if self.busy():
            if not quiet:
                QMessageBox.information(self, "Busy", "Wait for the current task to finish, then try again.")
            return

        def done(n):
            if n:
                self.statusBar().showMessage(f"Looking again at {n:,} dark photo{'s' if n != 1 else ''}…", 8000)
                self.start_scan()
            else:
                self.statusBar().showMessage("No dark photos to look at again.", 6000)
        self.run_job("darkcheck", find_dark_job, on_done=done)

    def offer_folder_cleanup(self):
        """Once, after updating to 1.37.3: older versions copied a person's folder to everyone else
        on a group photo. Offer to take those copies out."""
        if self.settings.get("folder_copies_checked"):
            return
        self.settings["folder_copies_checked"] = True
        store.save_settings(self.settings)
        copies = store.copied_folder_placements(self.conn)
        if not copies or os.environ.get("FACESORT_NO_PROMPT"):
            return
        nphotos = len({p for p, _f in copies})
        if QMessageBox.question(
                self, "Copied folders",
                "Earlier versions put a group photo into the same folder for everyone on it (Jane's "
                "“Beach 2023” made a “Beach 2023” for Bob too). Folders are now only for the person whose "
                f"gallery you're in.\n\n{nphotos:,} photo{'s are' if nphotos != 1 else ' is'} still in such "
                "copied folders. Take them out of the copies (and delete copied folders left empty)?\n\n"
                "The original folders, and the files on disk, aren't touched.") == QMessageBox.Yes:
            n, gone = store.remove_copied_folders(self.conn)
            self.structure_changed(f"Copied folders: {n:,} entr{'y' if n == 1 else 'ies'} taken out, "
                                   f"{gone:,} empty folder{'s' if gone != 1 else ''} deleted")

    def offer_dark_look(self):
        """Once, after updating to 1.36: offer the second look at the dark photos already scanned."""
        if self.settings.get("dark_offer_done") or not self.settings.get("dark_second_look", True):
            return
        n = self.conn.execute("SELECT COUNT(*) FROM photos WHERE COALESCE(dark_seen, 0)=0 AND is_video=0 "
                              "AND error IS NULL").fetchone()[0]
        self.settings["dark_offer_done"] = True
        store.save_settings(self.settings)
        if not n or os.environ.get("FACESORT_NO_PROMPT"):
            return
        if QMessageBox.question(
                self, "Second look at dark photos",
                "New: FaceSort takes a second look at dark photos (dim, evening and backlit shots) with a "
                "brightened copy, and finds faces it missed before.\n\n"
                f"Check the {n:,} photos already in your library for dark ones and look at those again now?\n"
                "Names you gave are kept. (Later: Settings > Look again in dark photos.)") == QMessageBox.Yes:
            self.rescan_dark(quiet=True)

    def looks_menu(self, menu, face_ids):
        """Right-click faces > Looks…: correct FaceSort's men / women estimate (and teach it)."""
        q = ",".join("?" * len(face_ids))
        said = {r[0] for r in self.conn.execute(f"SELECT male_set FROM faces WHERE id IN ({q})", face_ids)}
        n = f"   ({len(face_ids)} faces)" if len(face_ids) > 1 else ""
        sub = menu.addMenu("Looks…")
        for value, label in ((0, "Looks female"), (1, "Looks male")):
            a = sub.addAction(label + n, lambda v=value: self.set_looks(face_ids, v))
            a.setCheckable(True)
            a.setChecked(said == {value})
        if said - {None}:
            sub.addSeparator()
            sub.addAction("Let FaceSort judge again" + n, lambda: self.set_looks(face_ids, None))
        return sub

    def set_looks(self, face_ids, value):
        face_attrs.set_said(self.conn, face_ids, value)
        if self.view[0] in ("unknown", "partial") and self.view[1] in ("men", "unsure") and value is not None \
                and not (self.view[1] == "men" and value == 1):
            self.drop_from_view(face_ids)            # not in this list any more
        word = {0: "female", 1: "male"}.get(value)
        self.statusBar().showMessage((f"✓ Marked as looking {word}" if word else "✓ FaceSort will judge again")
                                     + " - FaceSort learns from this", 6000)
        self.after_change(regroup=False, defer=True)
        self.sex_timer.start()

    def set_person_sex(self, person_ids, value):
        face_attrs.set_person_sex(self.conn, person_ids, value)
        self.statusBar().showMessage("✓ Saved - FaceSort learns from this", 5000)
        self.sex_timer.start()

    def offer_face_details(self):
        """Once (1.42): offer the two small face-detail models (Men / Unsure groups, head angle)."""
        if self.settings.get("face_details") is not None or face_attrs.ready():
            if face_attrs.ready() and self.settings.get("face_details") is None:
                self.settings["face_details"] = True
                store.save_settings(self.settings)
            return
        if os.environ.get("FACESORT_NO_PROMPT"):
            return
        yes = QMessageBox.question(
            self, "Men / Unsure and head angle",
            "New: FaceSort can tell which unnamed faces look male (so you can set them aside in one go - "
            "Smart Groups > Men) and which way each head is turned (side-on faces are matched more carefully, "
            "and front-on faces are picked for the people list).\n\n"
            f"This needs two face models from InsightFace (about {face_attrs.DOWNLOAD_MB} MB, once; free for "
            "non-commercial use). They work on this PC only - your photos are never uploaded.\n\n"
            "Download them now? (Later: Settings > Scanning > Face details.)") == QMessageBox.Yes
        self.settings["face_details"] = yes
        store.save_settings(self.settings)
        if yes:
            self.get_face_details()

    def get_face_details(self):
        def got(ok):
            if ok and face_attrs.ready():
                self.start_face_details()
        if not self.run_job("facedl", lambda conn, progress, cancelled: face_attrs.download(progress, cancelled),
                            on_done=got):
            QTimer.singleShot(30000, self.get_face_details)      # busy: try again a little later

    def rescan_all(self):
        """Run face detection again on every photo. Names are kept (faces are matched by position)."""
        self.conn.execute("UPDATE photos SET mtime=NULL")
        self.conn.commit()
        self.start_scan()

    def act_notface(self):
        ids = self.sel_faces()
        if ids and self.view[0] != "notfaces":
            store.set_notface(self.conn, ids, 3)
            self.after_change()        # regroup: lookalikes get set aside
            self.statusBar().showMessage(f"Marked {len(ids)} as not a face - FaceSort will set aside lookalikes", 6000)

    def act_notface_all(self):
        ids = [r[0] for r in self.model.rows if r[0] > 0]
        if ids:
            store.set_notface(self.conn, ids, 3)
            self.after_change()

    def act_isface(self):
        ids = self.sel_faces()
        if ids:
            store.set_notface(self.conn, ids, 0)
            self.after_change()

    def act_unignore(self):
        ids = [k for k in self.selected_ids() if k > 0] if self.view[0] == "ignored" else []
        if ids:
            store.set_ignored(self.conn, ids, False)
            self.after_change()

    def rename_person(self):
        pid = self.view[1]
        old = self.conn.execute("SELECT name FROM persons WHERE id=?", (pid,)).fetchone()[0]
        new, ok = QInputDialog.getText(self, "Rename", "New name:", text=old)
        new = new.strip()
        if not ok or not new or new == old:
            return
        exists = self.conn.execute("SELECT id FROM persons WHERE name=? AND id<>?", (new, pid)).fetchone()
        if exists and QMessageBox.question(self, "Merge people",
                                           f"“{new}” already exists. Merge {old} into {new}?") != QMessageBox.Yes:
            return
        self.view = ("person", store.rename_person(self.conn, pid, new))
        self.after_change()

    def delete_person(self):
        pid = self.view[1]
        name = self.conn.execute("SELECT name FROM persons WHERE id=?", (pid,)).fetchone()[0]
        if QMessageBox.question(self, "Delete person",
                                f"Remove the name “{name}”? The faces go back to unnamed groups.\n"
                                "(Tags already written into photos are removed the next time you click Write tags.)"
                                ) == QMessageBox.Yes:
            store.delete_person(self.conn, pid)
            self.view = ("unknown", None)
            self.after_change()

    def open_viewer(self, index, tag_face=False):
        if not index.isValid():
            return
        self.close_review()
        if not self.model.rows[index.row()][0]:
            return
        if jobs.media_kind(self.model.rows[index.row()][1] or "") == "other":
            return self.open_other(self.model.rows[index.row()][1])
        # other files (documents, music…) aren't shown in the viewer: it steps over them
        real = [i for i, r in enumerate(self.model.rows) if r[0] and jobs.media_kind(r[1] or "") != "other"]
        items = [(self.model.rows[i][0], self.model.rows[i][1]) for i in real]
        person = self.view[1] if self.view[0] == "person" else None
        self.close_viewer(refresh=False)
        v = getattr(self, "_viewer_widget", None)
        if v is None:                    # made once, then reused (only hidden in between)
            v = PhotoViewer(self, items, real.index(index.row()), self.title.text(), person)
            self._viewer_widget = v
            self.main_stack.addWidget(v)
        else:
            v.open_items(items, real.index(index.row()), self.title.text(), person)
        self.viewer = v
        self.place_details(False)
        self.main_stack.setCurrentWidget(v)
        for sc in self._main_shortcuts:
            sc.setEnabled(False)          # the viewer's own keys take over
        v.canvas.setFocus()
        if tag_face:
            QTimer.singleShot(0, lambda: v.tag_b.setChecked(True))

    # never started from FaceSort, only shown in their folder
    RISKY_EXT = {".exe", ".bat", ".cmd", ".com", ".scr", ".msi", ".msp", ".ps1", ".psm1", ".vbs", ".vbe", ".js",
                 ".jse", ".wsf", ".wsh", ".hta", ".jar", ".lnk", ".url", ".website", ".html", ".htm", ".mht",
                 ".mhtml", ".reg", ".pif", ".cpl", ".dll", ".sys", ".inf", ".application", ".appref-ms", ".py",
                 ".pyw", ".sh", ".desktop", ".webloc", ".svg", ".xml", ".chm", ".iso", ".img", ".vhd", ".vhdx"}

    def open_other(self, path):
        """Double-click on an other file: opens it in its usual program - except programs, scripts,
        links and web pages, which are only shown in their folder."""
        if not os.path.exists(path):
            QMessageBox.information(self, "Not found", f"{path}\n\nisn't there any more.")
            return
        if os.path.splitext(path)[1].lower() in self.RISKY_EXT:
            show_in_folder(path)
            self.statusBar().showMessage("Programs, scripts, links and web pages aren't opened from FaceSort - "
                                         "shown in their folder instead.", 8000)
        else:
            open_file(path)

    def place_details(self, in_gallery):
        """The Details panel sits beside the pictures in the gallery (under the gallery's buttons),
        and beside the viewer / Quick review when one of those is open."""
        d = self.details
        vis = self.a_details.isChecked()
        target = self.gal_split if in_gallery else self.main_split
        if d.parent() is target:
            return
        w = d.width() if d.width() > 50 else 300
        target.addWidget(d)
        d.setVisible(vis)
        if target is self.main_split:
            sizes = self.main_split.sizes()
            if len(sizes) == 3:
                self.main_split.setSizes([sizes[0], max(300, sizes[1] + sizes[2] - w), w])
        else:
            sizes = self.gal_split.sizes()
            if len(sizes) == 2:
                self.gal_split.setSizes([max(300, sum(sizes) - w), w])

    def viewer_open(self):
        return self.viewer is not None or getattr(self, "review", None) is not None \
            or QApplication.activeModalWidget() is not None

    def close_viewer(self, refresh=True):
        """Back from the viewer to the gallery (Esc, Backspace or the back arrow)."""
        v = self.viewer
        if v is None:
            return
        at = v.items[v.pos][0] if v.items else None
        v.slideshow.stop()
        if v.pending_tattoo or v.pending_face:
            v.cancel_tattoo()
        self.viewer = None
        self.main_stack.setCurrentIndex(0)          # the viewer stays (hidden) for next time
        self.place_details(True)
        for sc in self._main_shortcuts:
            sc.setEnabled(True)
        if self.isFullScreen() or getattr(self, "_fs_on", False):
            self.toggle_fullscreen()
        if refresh:
            self.quick_change(regroup=False)
            # land on the photo you were looking at
            for i, r in enumerate(self.model.rows):
                if r[0] == at:
                    ix = self.model.index(i)
                    self.grid.selectionModel().setCurrentIndex(ix, QItemSelectionModel.SelectionFlag.ClearAndSelect)
                    self.grid.scrollTo(ix)
                    break
        self.grid.setFocus()

    def toggle_fullscreen(self):
        if self.isFullScreen() or getattr(self, "_fs_on", False):
            self._fs_on = False
            was_max, geo = getattr(self, "_before_fs", (False, None))
            if was_max:
                self.showMaximized()           # back the way it was: maximized...
            else:
                self.showNormal()
                if geo is not None:
                    self.setGeometry(geo)      # ...or the size and place you had
                    # (again once Windows has finished leaving full screen, which can reset it)
                    for ms in (0, 80, 250):
                        QTimer.singleShot(ms, lambda geo=geo: None if self.isFullScreen() or self.isMaximized()
                                          else self.setGeometry(geo))
            for w in (self.side_panel, self.statusBar()):
                w.show()
            for tb in self.findChildren(QToolBar):
                tb.show()
        else:
            self._before_fs = (self.isMaximized(), QRect(self.geometry()))
            self._fs_on = True
            for w in (self.side_panel,):
                w.hide()
            for tb in self.findChildren(QToolBar):
                tb.hide()
            self.statusBar().hide()
            self.showFullScreen()

    def grid_menu(self, pos):
        idx = self.grid.indexAt(pos)
        if idx.isValid() and self.model.rows[idx.row()][0] and not self.grid.selectionModel().isSelected(idx):
            self.grid.setCurrentIndex(idx)
        ids = self.selected_ids()
        if not ids:
            if self.view[0] in ("person", "category"):
                m = QMenu(self)
                m.addAction("Create Folder…", self.create_folder_here)
                m.exec(self.grid.viewport().mapToGlobal(pos))
            return
        kind = self.view[0]
        m = QMenu(self)
        faces = [k for k in ids if k > 0]
        photos = [-k for k in ids if k < 0]
        sp1 = self.sel_photos()
        if len(sp1) == 1:
            vrow = self.conn.execute("SELECT path, is_video FROM photos WHERE id=?", (sp1[0],)).fetchone()
            if vrow and vrow[1]:
                m.addAction("Play video", lambda i=self.grid.currentIndex(): self.play_from_gallery(i))
                m.addAction("Make a 12-shot collage", lambda p=vrow[0]: self.make_collage(p))
                m.addAction("Make a low light copy (brightened)", lambda p=vrow[0]: self.make_lowlight_copy(
                    p, int(self.settings.get("low_light", 0)) or 1))
                m.addAction("Save the best frames, brightened…", lambda p=vrow[0]: self.lowlight_best_frames(
                    p, int(self.settings.get("low_light", 0)) or 1))
                m.addSeparator()
        if faces:
            m.addAction(f"Name {len(faces)} face{'s' if len(faces) > 1 else ''}…\tEnter", self.act_name)
        if len(ids) == 1:
            m.addAction("Tag Person - draw a box around a missed face…\tT", self.act_tag_person)
        m.addAction(("Tag people on this photo…" if len(self.sel_photos()) == 1 else "Tag people on these photos…")
                    + "\tEnter", self.act_tag)
        if photos:
            q = ",".join("?" * len(photos))
            tagged = self.conn.execute(f"""SELECT DISTINCT p.id, p.name FROM photo_people pp JOIN persons p
                                           ON p.id=pp.person_id WHERE pp.photo_id IN ({q}) ORDER BY p.name""",
                                       photos).fetchall()
            if tagged:
                rm = m.addMenu("Remove tag")
                for pid, nm in tagged:
                    rm.addAction(nm, lambda pid=pid: (store.untag_photos(self.conn, photos, pid),
                                                      self.after_change(regroup=False)))
        if self.view[0] == "dupes" and len(photos) == 1:
            m.addAction("Keep this copy (discard the others in its set)", lambda p=photos[0]: self.keep_this_copy(p))
            m.addAction("Not a duplicate (take it out of this set)", lambda p=photos[0]: self.not_duplicate(p))
            m.addSeparator()
        if self.view[0] == "ai":
            m.addAction("Not AI (take off the list)", self.act_not_ai)
        if self.view[0] in ("person", "category"):
            m.addAction("Create Folder… (the selected photos go in it)", self.create_folder_here)
        if self.view[0] == "person":
            pf = [(k, store.PATH_SEP.join(p)) for k, p in store.category_paths(self.conn).items()
                  if store.folder_owner(self.conn, k) == self.view[1]]
            if pf:
                mm = m.addMenu("Move to folder")
                for k, lbl in sorted(pf, key=lambda x: x[1].casefold()):
                    mm.addAction(lbl, lambda k=k: self.file_into(self.selected_ids(), ("pf", k, self.view[1])))
                mm.addSeparator()
                mm.addAction("Not in a folder", lambda: self.file_into(self.selected_ids(), ("pf", None, self.view[1])))
        cm = m.addMenu("Set folder")
        for i, n in enumerate(store.top_categories(self.conn)):
            cm.addAction(n, lambda n=n: self.set_category_for(self.sel_photos(), n))
        cm.addAction("Other…\tA", self.act_category)
        cm.addSeparator()
        cm.addAction("Remove folder", lambda: self.set_category_for(self.sel_photos(), None))
        names = self.person_names() if faces else []
        if names:
            sub = m.addMenu("Assign to")
            for n in names[:40]:
                sub.addAction(n, lambda n=n: self.assign_ids(self.selected_ids(), n))
            if len(names) > 40:
                sub.addSeparator()
                sub.addAction("Everyone else…", self.act_name)
        named = [r for r in self.conn.execute(
            f"SELECT f.id, p.name FROM faces f JOIN persons p ON p.id=f.person_id WHERE f.id IN ({','.join('?' * len(faces))})",
            faces)] if faces else []
        if faces and self.settings.get("face_details"):
            self.looks_menu(m, list(faces))
        if faces and not named:          # faces with no name yet: one new numbered person for them
            self.generic_menu(m, lambda new, pre, ids=list(faces): self.assign_placeholder(ids, new, pre),
                              f"   ({len(faces)} faces)" if len(faces) > 1 else "")
        if named:
            who = sorted({n for _, n in named})
            m.addAction(f"Unassign {who[0] if len(who) == 1 else f'{len(who)} people'}", lambda: self.unassign_faces(
                [i for i, _ in named]))
        sp_dup = self.sel_photos()
        if sp_dup:
            q = ",".join("?" * len(sp_dup))
            n_ai = self.conn.execute(f"SELECT COUNT(*) FROM photos WHERE ai>0 AND id IN ({q})", sp_dup).fetchone()[0]
            if n_ai:
                m.addAction(app_icon("ai"), "Not AI - take off the AI badge" + (f"   ({n_ai})" if n_ai > 1 else ""),
                            lambda ids=list(sp_dup): self.act_not_ai(ids))
            self.album_menu(m, sp_dup)
            tm = m.addMenu(app_icon("rotate_right"), "Turn")
            tm.addAction(app_icon("rotate_left"), "Turn left (anticlockwise)\t[", lambda: self.rotate_selected(False))
            tm.addAction(app_icon("rotate_right"), "Turn right (clockwise)\t]", lambda: self.rotate_selected(True))
            if kind == "album":
                m.addAction("Remove from album", lambda aid=self.view[1]: self.remove_photos_from_album(aid, sp_dup))
            m.addSeparator()
            nskip = self.conn.execute(f"SELECT COUNT(*) FROM photos WHERE dup_skip=1 AND id IN "
                                      f"({','.join(map(str, sp_dup[:5000]))})").fetchone()[0]
            if nskip < len(sp_dup):
                m.addAction("Leave out of duplicate checks", lambda: self.set_dup_skip_photos(sp_dup, True))
            if nskip:
                m.addAction("Check for duplicates again", lambda: self.set_dup_skip_photos(sp_dup, False))
        if kind == "person" and faces:
            m.addAction("Yes, it's them (confirm)\tC", self.act_confirm)
        if kind == "person":
            m.addAction("Not this person\tR", self.act_remove)
        if kind == "group":
            m.addAction("Doesn't belong in this group\tR", self.act_remove)
        m.addSeparator()
        if kind == "ignored":
            m.addAction("Restore", self.act_unignore)
        elif kind == "discarded":
            m.addAction("Restore", self.act_undiscard)
        elif kind == "sortlater":
            m.addAction("Take out of Sort Later\tL", self.act_sort_later)
            m.addAction("Discard photo (to ~To Be Discarded)\tD", self.act_discard)
        else:
            m.addAction("Sort later\tL", self.act_sort_later)
            m.addAction("Discard photo (to ~To Be Discarded)\tD", self.act_discard)
            if faces:
                m.addAction("Not a face (knee, elbow, pattern…)\tN", self.act_notface)
                m.addAction("Ignore this face (stranger)\tI", self.act_ignore)
        sp = self.sel_photos()
        nraw = sum(1 for (p,) in self.conn.execute(
            f"SELECT path FROM photos WHERE id IN ({','.join('?' * len(sp))})", sp) if engine.is_raw(p)) if sp else 0
        if nraw:
            m.addAction(f"Make a JPEG copy of {'this RAW photo' if nraw == 1 else f'these {nraw} RAW photos'}",
                        lambda: self.raw_jpeg_copies(sp))
        if sp:
            m.addAction(f"Rescan {'this photo' if len(sp) == 1 else f'these {len(sp)} photos'} (look for faces again)",
                        lambda: self.rescan_photos(sp))
        if idx.isValid():
            path = self.model.rows[idx.row()][1]
            m.addSeparator()
            m.addAction("Open photo", lambda: self.open_viewer(idx))
            def reveal(path=path):
                ids = self.sel_photos()
                others = [r[0] for r in self.conn.execute(
                    f"SELECT path FROM photos WHERE id IN ({','.join('?' * len(ids))})", ids)] if len(ids) > 1 else []
                show_in_folder(path, [o for o in others if o != path])
            m.addAction("Open window to this image" + ("s" if len(self.sel_photos()) > 1 else ""), reveal)
        m.exec(self.grid.viewport().mapToGlobal(pos))

    def rotate_selected(self, clockwise):
        """[ / ] or the buttons above the gallery: turn the selected photos a quarter turn."""
        photos = self.sel_photos()
        if not photos:
            self.statusBar().showMessage("Select the photos to turn first.", 5000)
            return
        done, skipped = self.rotate_photos(photos, clockwise)
        msg = f"Turned {done} photo{'s' if done != 1 else ''} {'right' if clockwise else 'left'}" if done else ""
        if skipped:
            msg += ("  ·  " if msg else "") + f"{len(skipped)} couldn't be turned ({skipped[0][1]})"
        self.statusBar().showMessage(msg, 8000)

    def rotate_photos(self, photo_ids, clockwise):
        """Turn the files and keep names, boxes and thumbnails lined up. -> (count, [(path, why)])"""
        c = self.conn
        q = ",".join("?" * len(photo_ids))
        rows = c.execute(f"SELECT id, path, is_video FROM photos WHERE id IN ({q})", list(photo_ids)).fetchall()
        done, skipped = 0, []
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for pid, path, video in rows:
                if video or not rotate.can_rotate(path):
                    skipped.append((path, "videos and RAW files can't be turned"))
                    continue
                try:
                    rotate.rotate_file(path, clockwise)
                    st = os.stat(path)
                    try:
                        ph = engine.dhash(engine.load_image(path, max_side=256).bgr)
                    except Exception:
                        ph = None
                    store.rotate_photo(c, pid, clockwise, round(st.st_mtime, 3), st.st_size, ph)
                    done += 1
                except Exception as e:
                    skipped.append((path, str(e)))
        finally:
            QApplication.restoreOverrideCursor()
        if done:
            self.pix_cache.clear()
            self.hi_cache.clear()
            self.hires.clear()
            self._pid_cache = {}
            self.after_change(regroup=False)
        return done, skipped

    def save_video_screenshot(self, video_path, img, t, label=None):
        """A frame from a video (or animated GIF) saved as a JPEG next to it, with its people
        tagged on it and its folders, then scanned like any new photo."""
        stem = os.path.splitext(os.path.basename(video_path))[0]
        t = max(0.0, float(t or 0))
        stamp = label or (f"{int(t // 3600)}h{int(t // 60 % 60):02d}m{t % 60:05.2f}s" if t >= 3600
                          else f"{int(t // 60)}m{t % 60:05.2f}s")
        folder = os.path.dirname(video_path)
        out = os.path.join(folder, f"{stem} - {stamp.replace('.', '_')}.jpg")
        n = 2
        while os.path.exists(out):
            out = os.path.join(folder, f"{stem} - {stamp.replace('.', '_')} ({n}).jpg")
            n += 1
        if not img.save(out, "JPG", 95):
            QMessageBox.warning(self, "Screenshot", f"Couldn't save the screenshot in:\n{folder}")
            return None
        return self.add_derived(video_path, out)

    def play_from_gallery(self, index):
        if not index.isValid():
            return
        self.open_viewer(index)
        if self.viewer_open() and self.viewer.is_video:
            QTimer.singleShot(150, self.viewer.toggle_video)
