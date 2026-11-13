"""MainWindow, part: drag and drop from Explorer, folders and Settings."""
import os
import aidetect
import companions
import engine
import known
import bodies
import store
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox
from ui_common import APP_VERSION, bodies_download_job, HELP
from dialogs import DropDialog, SettingsDialog


class SettingsMixin:
    """Part of MainWindow (see facesort.py): drag and drop from Explorer, folders and Settings."""

    # ------------------------------------------------------------------ folders & settings
    # ---- drag & drop from Explorer
    @staticmethod
    def _dropped_paths(e):
        md = e.mimeData()
        if not md.hasUrls():
            return []
        return [store.norm_path(u.toLocalFile()) for u in md.urls() if u.isLocalFile() and u.toLocalFile()]

    def dragEnterEvent(self, e):
        if self._dropped_paths(e):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if self._dropped_paths(e):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        paths = self._dropped_paths(e)
        if paths:
            e.acceptProposedAction()
            QTimer.singleShot(0, lambda: self.handle_drop(paths))   # don't block Explorer while the dialog is open

    def handle_drop(self, paths, ask=True, answers=None):
        """Photos, videos and folders dropped on the window: ask how to tag them, add them,
        and scan. answers=(people, category, sort_later, how) skips the dialog (tests)."""
        dirs = [p for p in paths if os.path.isdir(p)]
        files = [p for p in paths if os.path.isfile(p)
                 and (os.path.splitext(p)[1].lower() in engine.IMAGE_EXTS or engine.is_video(p))]
        skipped = len(paths) - len(dirs) - len(files)
        if not dirs and not files:
            self.statusBar().showMessage("Nothing to add - drop photos, videos or folders.", 8000)
            return False
        known_paths = {r[0] for r in self.conn.execute("SELECT path FROM photos")}
        prefixes = tuple(d.rstrip("\\/") + os.sep for d in dirs)
        inside = {p for p in known_paths if prefixes and p.startswith(prefixes)} | {f for f in files if f in known_paths}
        if answers is None:
            dlg = DropDialog(self, dirs, files, len(inside))
            if dlg.exec() != QDialog.Accepted:
                return False
            people, cat, later, structure = dlg.result_tags()
        else:
            people, cat, later, structure = (tuple(answers) + (None,))[:4]
        # add to what FaceSort scans: folders, or single files outside your folders
        folders = self.settings["folders"]
        covered = lambda p: any(p == f or p.startswith(f.rstrip("\\/") + os.sep) for f in folders)
        for d in dirs:
            if not covered(d):
                folders[:] = [f for f in folders if not f.startswith(d.rstrip("\\/") + os.sep)] + [d]
        extra = self.settings.setdefault("files", [])
        for f in files:
            if not covered(f) and f not in extra:
                extra.append(f)
        extra[:] = [f for f in extra if not covered(f)]
        store.save_settings(self.settings)
        # everything dropped in goes to "Recently dropped" first (laid out the way it'll be saved),
        # so you can check it's tagged right before it's sorted into the library
        names = [os.path.basename(p.rstrip("\\/")) for p in dirs + files]
        label = names[0] + (f" + {len(names) - 1} more" if len(names) > 1 else "")
        batch = store.new_drop_batch(self.conn, label)
        for p in dirs + files:
            store.add_drop_rule(self.conn, p, p in dirs, people, cat, later, structure if p in dirs else None, batch)
        # everything else that came along (notes, links, documents, archives...): kept with the photos
        others = self.collect_other_files(dirs, [p for p in paths if os.path.isfile(p) and p not in files])
        if others:
            store.add_drop_files(self.conn, batch, others)
        rules = store.drop_rules(self.conn)
        for pid, path in self.conn.execute("SELECT id, path FROM photos WHERE error IS NULL").fetchall():
            if path in inside:
                store.apply_drop_rules(self.conn, pid, path, rules)
        store.settle_photo_tags(self.conn)
        self.conn.commit()
        self.view = ("dropped", batch)
        self._goto_after_scan = ("dropped", batch)       # open it once it's scanned
        msg = "Added " + ", ".join(x for x in (
            f"{len(dirs)} folder{'s' if len(dirs) != 1 else ''}" if dirs else "",
            f"{len(files)} file{'s' if len(files) != 1 else ''}" if files else "") if x)
        if people:
            msg += " · tagged " + ", ".join(people)
        if cat:
            msg += f" · folder “{cat}”"
        if later:
            msg += " · Sort Later"
        if structure is not None:
            msg += (" · folder name kept for whoever is tagged" if structure == -2 else
                    " · keeping the sub-folders" if structure == -1 else
                    " · keeping the folder structure" + (f" (level {structure} = person)" if structure else ""))
        if others:
            msg += f" · {len(others)} other file{'s' if len(others) != 1 else ''} kept with them (~links / ~archives / ~files)"
        self.statusBar().showMessage(msg, 12000)
        self.refresh_all()
        if self.busy():
            self._scan_after = True          # scans as soon as the current job ends
        else:
            self.start_scan(videos_only=False)
        return True

    @staticmethod
    def collect_other_files(dirs, loose, limit=50000):
        """[(path, kind, dropped folder)] of the non-photo files in a drop. A photo's own extra
        files (.xmp, Takeout .json, .aae, .thm) aren't listed - they travel with their photo."""
        def media(name):
            ext = os.path.splitext(name)[1].lower()
            return ext in engine.IMAGE_EXTS or ext in engine.VIDEO_EXTS
        out = []
        for d in dirs:
            for root, subdirs, names in os.walk(d):
                subdirs[:] = [x for x in subdirs if not x.startswith(("$", ".")) and x.lower() not in
                              ("~links", "~archives", "~files")]
                media_lower = [n.lower() for n in names if media(n)]
                for n in names:
                    if media(n) or n.lower() in companions.JUNK or n.startswith("."):
                        continue
                    p = os.path.join(root, n)
                    if companions.is_companion(p, media_lower):
                        continue
                    out.append((p, companions.kind_of(p), d))
                    if len(out) >= limit:
                        return out
        for p in loose:
            n = os.path.basename(p)
            if not media(n) and n.lower() not in companions.JUNK:
                out.append((p, companions.kind_of(p), None))
        return out

    def enable_ai_detector(self):
        if aidetect.detector_available():
            return self.start_scan()

        def done(ok):
            if ok:
                self.statusBar().showMessage("AI detector ready - checking your photos", 8000)
                self.start_scan()
            else:
                QMessageBox.warning(self, "AI detector", "The AI detector couldn't be downloaded. Check your internet "
                                    "connection and turn it on again in Settings.")

        def job(conn, progress, cancelled):
            try:
                return aidetect.download_detector(progress, cancelled)
            except Exception:
                return False
        if not self.run_job("download", job, on_done=done):
            QMessageBox.information(self, "Busy", "Wait for the current task to finish, then turn it on again.")

    def rename_library_root(self):
        """Rename the Photos library folder in Windows; FaceSort follows it (nothing to redo)."""
        self.remember_now()
        old = self.settings.get("library_root")
        if not old or not os.path.isdir(old):
            QMessageBox.information(self, "Rename", "Choose your Photos library folder first.")
            return None
        name, ok = QInputDialog.getText(self, "Rename Photos library", "New folder name:", text=os.path.basename(old))
        name = name.strip() if ok else ""
        if not name or name == os.path.basename(old):
            return None
        if any(ch in name for ch in '\\/:*?"<>|'):
            QMessageBox.warning(self, "Rename", "A folder name can't contain \\ / : * ? \" < > |")
            return None
        new = store.norm_path(os.path.join(os.path.dirname(old), name))
        if os.path.exists(new):
            QMessageBox.warning(self, "Rename", f"“{name}” already exists there.")
            return None
        for w in (self.worker, self.regroup_worker):
            if w and w.isRunning():
                w.stop()
                w.wait(30000)
        self.regroup_timer.stop()
        try:
            os.rename(old, new)
        except OSError as e:
            QMessageBox.warning(self, "Rename", f"Windows couldn't rename the folder (is a file in it open in "
                                f"another program?):\n{e}")
            return None
        store.move_prefix(self.conn, old, new)
        known.move_prefix(old, new)

        def fix(p):
            return new + p[len(old):] if p == old or p.startswith(old.rstrip("\\/") + os.sep) else p
        s = self.settings
        s["library_root"] = new
        s["folders"] = [fix(f) for f in s["folders"]]
        s["files"] = [fix(f) for f in s.get("files", [])]
        if s.get("export_dir"):
            s["export_dir"] = fix(s["export_dir"])
        store.save_settings(s)
        self.pix_cache.clear()
        self.hi_cache.clear()
        self.hires.clear()
        self.refresh_all()
        self.statusBar().showMessage(f"Photos library renamed to “{name}”", 8000)
        return new

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Choose a folder with photos")
        if not d:
            return
        d = store.norm_path(d)
        if d not in self.settings["folders"]:
            self.settings["folders"].append(d)
            store.save_settings(self.settings)
        self.refresh_all()
        self.start_scan()

    def open_settings(self):
        self.remember_now()
        dlg = SettingsDialog(self, self.settings)
        if dlg.exec() != QDialog.Accepted:
            return
        old = dict(self.settings)
        new = dlg.result_settings()
        QTimer.singleShot(0, self.sync_match_slider)       # (the header slider follows Settings)
        QTimer.singleShot(0, lambda: self.update_scope_button())
        removed = [f for f in old["folders"] if f not in new["folders"]]
        if removed:
            if self.busy():
                self.stop_worker()
                self.worker.wait()
            for f in removed:
                store.remove_folder_photos(self.conn, f)
            self.hi_cache.clear()
            self.hires.clear()
        gone_files = [f for f in old.get("files", []) if f not in new.get("files", [])]
        if gone_files:
            self.conn.executemany("DELETE FROM photos WHERE path=?", [(f,) for f in gone_files])
            self.conn.commit()
        self.settings = new
        self.a_scan_videos.setVisible(new.get("video_mode", "after") == "separate")
        store.save_settings(new)
        if new.get("use_gpu") != old.get("use_gpu"):
            engine.reset_gpu()
        if new.get("ai_detector") and not old.get("ai_detector"):
            self.enable_ai_detector()
        if new.get("library_root") and new.get("library_root") != old.get("library_root"):
            n = known.import_root(new["library_root"])       # what it remembers comes straight back
            if n:
                self.statusBar().showMessage(f"Photos library: remembered details for {n:,} photos loaded", 10000)
            self.start_scan()
        if new.get("update_repo") != old.get("update_repo"):
            self._update_info = None                         # (another project: ask it afresh)
        self.show_update_state(getattr(self, "_update_info", None))
        if dlg.clear_requested:
            return self.clear_library()
        if new.get("engine") != old.get("engine"):
            new_engine = new["engine"]
            self.settings["engine"] = old.get("engine", "sface")   # switch_engine sets it once ready
            store.save_settings(self.settings)
            if new_engine == "adaface" and not self.confirm_adaface():
                return
            return self.switch_engine(new_engine)
        if new.get("keywords_all_people") and not old.get("keywords_all_people"):
            self.conn.execute("UPDATE photos SET tags_read=0")   # re-read keywords with the new rule
            self.conn.commit()
            if not dlg.rescan_request:
                self.refresh_all()
                return self.start_scan()
        if new.get("same_shoot") and not old.get("same_shoot"):
            QTimer.singleShot(0, self.enable_same_shoot)
        elif old.get("same_shoot") and not new.get("same_shoot"):
            w = getattr(self, "bodies_worker", None)
            if w and w.isRunning():
                w.stop()
        if dlg.rescan_request == "all":
            self.refresh_all()
            return self.rescan_all()
        if dlg.rescan_request == "dark":
            self.refresh_all()
            return self.rescan_dark()
        detect_changed = any(new.get(k) != old.get(k) for k in ("detect_threshold", "look_sideways", "min_face_px"))
        if new.get("dark_second_look", True) and not old.get("dark_second_look", True):
            self.refresh_all()
            return self.rescan_dark()
        nof = self.conn.execute("SELECT COUNT(*) FROM photos WHERE error IS NULL AND nfaces=0 "
                                "AND COALESCE(is_other, 0)=0").fetchone()[0]
        if dlg.rescan_request == "nofaces" or (detect_changed and nof and QMessageBox.question(
                self, "Look again?",
                f"Face detection settings changed. Look again for faces in the {nof:,} photos where none were "
                "found?\n(People you tagged on them are kept.)") == QMessageBox.Yes):
            self.refresh_all()
            return self.rescan_nofaces()
        self.pix_cache.clear()
        self.refresh_all()
        if dlg.imported:
            self.schedule_regroup(now=True)
        if any(f not in old["folders"] for f in new["folders"]):
            self.start_scan()
        elif store.threshold(new) != store.threshold(old) or new.get("folder_hints") != old.get("folder_hints"):
            self.schedule_regroup(now=True)
        if (new.get("dup_prefer_raw"), new.get("dup_raw_jpeg_pairs")) != (old.get("dup_prefer_raw", True),
                                                                          old.get("dup_raw_jpeg_pairs", True)):
            self._dup_key = None                  # the duplicate sets are worked out again with the new rules
            self.check_duplicates()

    def confirm_adaface(self):
        """Best accuracy does about 5x the work per face: say what that means without a graphics card."""
        try:
            import onnxruntime as ort
            gpu = bool(self.settings.get("use_gpu", True)) and any(
                p in ort.get_available_providers() for p in ("DmlExecutionProvider", "CUDAExecutionProvider"))
        except Exception:
            gpu = False
        faces = self.conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0]
        if gpu:
            return True
        hours = faces * 1.6 / max(1, int(self.settings.get("threads", 2))) / 3600
        return QMessageBox.question(
            self, "Best accuracy without a graphics card",
            "Best accuracy (AdaFace) does about five times the work per face of High accuracy, and FaceSort can't "
            "use a graphics card here, so it would run on the processor alone"
            + (f" - re-reading your {faces:,} faces could take around {hours:.0f} hours" if hours >= 1 else "")
            + ".\n\nSwitch anyway? (You can stop the scan and carry on later; it continues where it left off.)"
        ) == QMessageBox.Yes

    def enable_same_shoot(self):
        """Same-shoot suggestions switched on: get the two models if needed, then start reading."""
        if bodies.available():
            self.start_bodies()
            self.statusBar().showMessage("Same-shoot suggestions: finding the people in your photos (in the "
                                         "background)…", 8000)
            return

        def done(ok):
            if ok:
                self.start_bodies()
            else:
                self.statusBar().showMessage("Same-shoot suggestions: download stopped.", 8000)
        if not self.run_job("bodiesdl", bodies_download_job, on_done=done):
            QTimer.singleShot(3000, self.enable_same_shoot)      # after the current task

    def clear_library(self):
        """Forget all scanned photos/faces (known people, tags and settings stay) and rescan."""
        for w in (self.worker, self.regroup_worker):
            if w and w.isRunning():
                w.stop()
                w.wait(15000)
        self.remember_now()
        for w in (self.worker, self.regroup_worker):
            if w and w.isRunning():
                w.stop()
                w.wait(30000)
        self.regroup_timer.stop()
        self.conn.close()
        base = os.path.join(store.data_dir(), "library.db")
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(base + suffix)
            except FileNotFoundError:
                pass
        self.conn = store.connect()
        try:
            known.apply_structure(self.conn)   # your category tree and where people are placed
        except Exception:
            pass
        self.pix_cache.clear()
        self.hi_cache.clear()
        self.hires.clear()
        self.view = ("none", None)
        self.refresh_all()
        self.start_scan()

    def show_help(self):
        box = QMessageBox(self)
        box.setWindowTitle("FaceSort help")
        box.setTextFormat(Qt.RichText)
        box.setText(HELP + f"<p style='color:gray'>FaceSort {APP_VERSION}</p>")
        box.exec()
