"""MainWindow, part: background jobs, startup checks and closing."""
import os
import sys
import time
import traceback
import engine
import jobs
import known
import bodies
import face_attrs
import updater
import store
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QMessageBox
from ui_common import (APP_VERSION, bodies_job, clarity_job, log, log_path, open_file, regroup_job, remember_job,
                       scan_and_group, stats_job, Worker)
from dialogs import ExportDialog, ScanDialog

LOOK_ONLY_JOBS = {"discardlook", "colordl", "facedl"}   # jobs that only read or download (no redraw after)
REGROUP_IDLE = 3.0       # (gentle background mode) seconds without a key / click before suggestions update


class JobsMixin:
    """Part of MainWindow (see facesort.py): background jobs, startup checks and closing."""

    # ------------------------------------------------------------------ background jobs
    def _save_if_idle(self):
        try:
            self.conn.save_if_idle()
        except Exception as e:
            log(f"saving left-open changes: {e}")

    def busy(self):
        return self.worker is not None and self.worker.isRunning()

    def bg_running(self):
        """Any background job at work (scan, regroup, duplicate / copies / place checks, indexing)."""
        if self.busy():
            return True
        for name in ("regroup_worker", "remember_worker", "dup_worker", "copies_worker", "mis_worker",
                     "clip_worker", "stats_worker", "clarity_worker", "bodies_worker", "attrs_worker"):
            wk = getattr(self, name, None)
            if wk is not None and wk.isRunning():
                return True
        return bool(getattr(self, "regroup_timer", None) and self.regroup_timer.isActive())

    def bg_tick(self):
        """Start the next startup check once everything else has finished."""
        if getattr(self, "_closing", False) or not self._bg_queue:
            self._bg_timer.stop()
            return
        if self.bg_running():
            return
        fn = self._bg_queue.pop(0)
        try:
            fn()
        except Exception:
            log(traceback.format_exc())

    # ------------------------------------------------------------------ updates
    def start_update_check(self, force=False):
        """Ask GitHub (in the background, at most once a day) whether a newer FaceSort is out."""
        if not (force or updater.due(self.settings)):
            return
        w = getattr(self, "update_worker", None)
        if w and w.isRunning():
            return
        self.update_worker = Worker(updater.check_job, dict(self.settings))

        def done(info):
            self.settings["update_checked"] = time.time()
            store.save_settings(self.settings)
            self._update_info = info
            self.show_update_state(info)
            if not info and force:
                self.statusBar().showMessage(f"FaceSort {APP_VERSION} is the newest version.", 8000)

        def failed(tb):
            log(tb)
            self.show_update_state(getattr(self, "_update_info", None))
            if force:
                self.statusBar().showMessage("Couldn't check for updates: " + tb.strip().splitlines()[-1][:160], 12000)
        self.update_worker.ok.connect(done)
        self.update_worker.failed.connect(failed)
        if force:
            self.update_btn.setText("Checking…")
            self.update_btn.setEnabled(False)
        self.update_worker.start()

    def show_update_state(self, info):
        """The toolbar button: "Check for updates", or a green "Update to x.y.z" once one is out."""
        b = self.update_btn
        b.setEnabled(True)
        if info:
            b.setText(f"Update to {info['version']}")
            b.setStyleSheet("QToolButton { color: #22c55e; font-weight: bold; }")
            b.setToolTip(f"FaceSort {info['version']} is out (you have {APP_VERSION}) - click to see what's new "
                         "and install it")
        else:
            b.setText("Check for updates")
            b.setStyleSheet("")
            b.setToolTip(f"You have FaceSort {APP_VERSION}. Click to ask GitHub whether a newer version is out "
                         "(FaceSort also checks by itself once a day).")
        self.a_update.setVisible(bool(info) or bool(updater.repo(self.settings)))

    def update_clicked(self):
        if getattr(self, "_update_info", None):
            self.show_update()
        else:
            self.start_update_check(force=True)

    def show_update(self):
        info = getattr(self, "_update_info", None)
        if not info:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Update available")
        box.setIcon(QMessageBox.Information)
        box.setText(f"FaceSort {info['version']} is out. You have {APP_VERSION}.")
        notes = info.get("notes", "").strip()
        box.setInformativeText(("What's new:\n\n" + notes[:1500] + ("…" if len(notes) > 1500 else "")) if notes else "")
        installed = updater.is_installed()
        go = box.addButton("Download and install" if installed else "Open the download page", QMessageBox.AcceptRole)
        box.addButton("Later", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not go:
            return
        if not installed:
            # (running from source / a Setup.bat build: the installer would make a second copy)
            open_file(info["page"])
            return

        def downloaded(path):
            if not path:
                return
            if QMessageBox.question(self, "Install the update",
                                    f"FaceSort {info['version']} is downloaded and checked. FaceSort will close, update "
                                    "and start again (your library and settings stay as they are). Install now?"
                                    ) != QMessageBox.Yes:
                return
            updater.run_installer(path)
            self.close()

        def job(conn, progress, cancelled):
            return updater.download(info, progress, cancelled)
        if not self.run_job("update", job, on_done=downloaded):
            QMessageBox.information(self, "Busy", "Wait for the current task to finish, then try again.")

    def start_clarity(self):
        """Faces scanned before 1.36: read how clearly each can be recognised (from its thumbnail,
        in the background), then group again with that in mind."""
        w = getattr(self, "clarity_worker", None)
        if w and w.isRunning():
            return
        if not self.conn.execute("SELECT 1 FROM faces WHERE clarity IS NULL AND manual<2 LIMIT 1").fetchone():
            return
        self.clarity_worker = Worker(clarity_job)
        self.clarity_worker.progress.connect(lambda d, t, m: self.statusBar().showMessage(m, 4000))
        self.clarity_worker.ok.connect(lambda n: n and self.schedule_regroup())
        self.clarity_worker.failed.connect(lambda tb: log(tb))
        self.clarity_worker.start_background(self.settings)

    def start_face_details(self):
        """Men / Unsure and head angle for faces that don't have them yet (from their thumbnails,
        in the background); the lists catch up when it's done."""
        if not self.settings.get("face_details") or not face_attrs.ready():
            return
        w = getattr(self, "attrs_worker", None)
        if w and w.isRunning():
            return
        if not self.conn.execute("SELECT 1 FROM faces WHERE male IS NULL AND thumb IS NOT NULL LIMIT 1").fetchone():
            return
        self.attrs_worker = Worker(lambda conn, progress, cancelled: face_attrs.read_missing(
            conn, lambda d, t: progress(d, t, f"Reading face details… {d:,} / {t:,}"), cancelled))
        self.attrs_worker.progress.connect(lambda d, t, m: self.statusBar().showMessage(m, 4000))
        self.attrs_worker.ok.connect(lambda n: n and (self.schedule_regroup(), self.refresh_timer.start()))
        self.attrs_worker.failed.connect(lambda tb: log(tb))
        self.attrs_worker.start_background(self.settings)

    def start_bodies(self):
        """Same-shoot suggestions: find the people in photos not looked at yet (in the background,
        carries on where it left off), then work out who they probably are."""
        if not (self.settings.get("same_shoot") and bodies.available()):
            return
        w = getattr(self, "bodies_worker", None)
        if w and w.isRunning():
            return
        if not self.conn.execute("""SELECT 1 FROM photos WHERE COALESCE(body_done, 0)=0 AND is_video=0
                                    AND error IS NULL AND COALESCE(sort_later, 0)<>2 LIMIT 1""").fetchone():
            return
        self.bodies_worker = Worker(bodies_job)
        self.bodies_worker.progress.connect(lambda d, t, m: self.statusBar().showMessage(m, 4000))

        def done(n):
            self._data_gen += 1
            self.refresh_timer.start()
        self.bodies_worker.ok.connect(done)
        self.bodies_worker.failed.connect(lambda tb: (log(tb), self.statusBar().showMessage(
            "Same-shoot suggestions: couldn't read the photos - " + tb.strip().splitlines()[-1][:160], 15000)))
        self.bodies_worker.start_background(self.settings)

    def start_stats(self):
        w = getattr(self, "stats_worker", None)
        if w and w.isRunning():
            return
        self.stats_worker = Worker(stats_job)
        self.stats_worker.start()

    def set_busy(self, on, text=""):
        self.prog_strip.setVisible(on)
        self.prog_label.setText(text)
        self.a_tags.setEnabled(not on)
        if hasattr(self, "a_review"):
            self.a_review.setEnabled(not on)
        self.a_export.setEnabled(not on)
        self.a_scan.setText("Stop scan" if on and getattr(self, "_job", "") == "scan" else "Scan")

    def run_job(self, name, fn, *args, on_done=None, **kw):
        if self.busy() and self._job == "dupes" and name != "dupes":
            # the background duplicate check gives way (it carries on next time)
            self._after_dupes = lambda: self.run_job(name, fn, *args, on_done=on_done, **kw)
            self.worker.stop()
            return True
        if self.busy():
            return False
        if name != "dupes" and self._remember_dirty:
            # jobs read who's who from the known-people file: it's brought up to date first - in the
            # job's own thread (on a big library that takes seconds the window shouldn't wait for)
            self.remember_timer.stop()
            self._remember_dirty = False
            job = fn

            def fn(conn, *a, _job=job, **k):
                try:
                    known.sync_from_library(conn)
                except Exception as ex:
                    log(f"saving known people: {ex}")
                return _job(conn, *a, **k)
        self._job = name
        self.worker = Worker(fn, *args, **kw)
        self.worker.progress.connect(self.on_progress)
        self.worker.ok.connect(lambda res: self.job_done(name, res, on_done))
        self.worker.failed.connect(lambda tb: self.job_failed(name, tb))
        self.prog.setRange(0, 0)
        self.set_busy(True, "Working…")
        self.worker.start()
        return True

    def on_progress(self, done, total, msg):
        if total <= 0:
            self.prog.setRange(0, 0)
            self.prog_label.setText(msg)
        else:
            self.prog.setRange(0, total)
            self.prog.setValue(done)
            label = {"scan": "Scanning", "tags": "Writing tags", "export": "Copying",
                     "tattoo": "Tattoo search", "download": "", "dupes": "Duplicates", "recycle": "Recycle Bin", "rawcopy": "JPEG copies"}.get(self._job, "")
            if self._job == "download":
                self.prog_label.setText(msg)
                return
            if msg.startswith(("Grouping", "Updating")):
                self.prog_label.setText(msg)
            else:
                self.prog_label.setText(f"{label} {done:,} / {total:,}  ·  {msg}")
        if self._job == "scan" and done and done % 200 == 0:
            self.refresh_stats()
        if self._job == "scan" and done:
            self.update_engine_label()

    def update_engine_label(self):
        active, text = engine.gpu_status()
        if text != "not started yet":
            short = text if (active or text.startswith("High accuracy")) else "Standard · CPU"
            if text.startswith("GPU"):
                short = "Standard · " + text
            self.engine_label.setText("Face engine: " + short)
            self.engine_label.setToolTip(text)

    def job_done(self, name, res, on_done):
        self.update_engine_label()
        self.set_busy(False)
        self.worker = None
        look_only = name in LOOK_ONLY_JOBS      # changed nothing in the library: no redraw needed
        if not look_only:
            self._job_refresh(name)
        if on_done:
            on_done(res)
        if self.regroup_pending:
            self.schedule_regroup(now=True)
        self._after_job(name)

    def _job_refresh(self, name):
        self.pix_cache.clear()
        try:
            store.apply_vault_hiding(self.conn)      # newly scanned photos that belong in a locked vault
        except Exception as e:
            log(f"vaults: {e}")
        # whole-photo pictures (Viewer tab) were cached by photo number: after a scan or a move a
        # number can stand for a different / changed file, so reload them (from the disk cache: quick)
        self.hi_cache.clear()
        self.hires.clear()
        if name == "scan" and self.view[0] == "unknown":
            self.view = ("none", None)  # land on the biggest unnamed group
        goto = getattr(self, "_goto_after_scan", None)
        if name == "scan" and goto:
            self._goto_after_scan = None
            if self.conn.execute("SELECT 1 FROM photos WHERE dropped=?", (goto[1],)).fetchone():
                self.view = goto
        self.refresh_all()

    def _after_job(self, name):
        if self._scan_after:
            self._scan_after = False
            QTimer.singleShot(200, self.start_scan)
        queued = getattr(self, "_scan_queue", None)
        if queued:
            self._scan_queue = []
            QTimer.singleShot(100, lambda: self.scan_paths(queued))
        if name in ("scan", "export", "rescan"):
            QTimer.singleShot(1500, self.start_misplaced)      # anything now in the wrong folder?
            QTimer.singleShot(4000, self.start_clip_index)     # new photos: searchable by description
            if getattr(self, "_shoot_after", True):
                QTimer.singleShot(6000, self.start_bodies)     # ... and the people in them (same shoot)
            QTimer.singleShot(3000, self.start_face_details)   # men / unsure and head angle for new faces
        nxt = getattr(self, "_after_dupes", None)
        if name == "dupes" and nxt:
            self._after_dupes = None
            QTimer.singleShot(0, nxt)

    def job_failed(self, name, tb):
        self.set_busy(False)
        self.worker = None
        self.refresh_all()
        QMessageBox.critical(self, "Something went wrong", tb.strip().splitlines()[-1] +
                             f"\n\nDetails were saved to:\n{log_path()}")

    def stop_worker(self):
        if self.worker:
            self.worker.stop()
            self.prog_label.setText("Stopping…")

    def toggle_scan(self):
        if self.busy() and self._job == "scan":
            self.stop_worker()
        else:
            self.open_scan_window()

    def open_scan_window(self):
        """The Scan button: choose what this scan does, then scan."""
        if not self.settings["folders"]:
            return self.add_folder()
        if self.busy():
            QMessageBox.information(self, "Busy", "Wait for the current task to finish (or stop it), then scan.")
            return
        c = self.conn
        nofaces = c.execute("SELECT COUNT(*) FROM photos WHERE error IS NULL AND nfaces=0 "
                            "AND COALESCE(is_other, 0)=0").fetchone()[0]
        dark = c.execute("SELECT COUNT(*) FROM photos WHERE COALESCE(dark_seen, 0)=0 AND is_video=0 "
                         "AND error IS NULL").fetchone()[0]
        scope = [f for f in self.scope_folders() if f != self.OTHER_PLACES] if self.scope_on() else None
        dlg = ScanDialog(self, self.settings, chosen_folders=scope, waiting=getattr(self, "waiting", (0, 0))[0],
                         dark_unchecked=dark, nofaces=nofaces)
        if dlg.exec() != QDialog.Accepted or not dlg.opts:
            return
        o = dlg.opts
        self.settings["scan_remember"] = dlg.remember
        if dlg.remember:
            self.settings["scan_opts"] = {k: v for k, v in o.items()
                                          if k not in ("nofaces_again", "dark_again", "folders")}
            self.settings["import_limit"] = o["limit"]
            self.limit_edit.setText(str(o["limit"]))
        store.save_settings(self.settings)
        self.start_scan(opts=o)

    def remembered_scan_opts(self):
        return self.settings.get("scan_opts") if self.settings.get("scan_remember", True) else None

    def import_limit(self):
        try:
            return max(0, int(self.settings.get("import_limit", 0) or 0))
        except (TypeError, ValueError):
            return 0

    def save_import_limit(self):
        try:
            v = max(0, int(self.limit_edit.text() or 0))
        except ValueError:
            v = 0
        self.limit_edit.setText(str(v))
        if v != self.import_limit():
            self.settings["import_limit"] = v
            store.save_settings(self.settings)
            self.statusBar().showMessage("No import limit - Scan imports everything new." if not v else
                                         f"Each Scan now imports up to {v:,} new files.", 8000)

    def start_scan(self, videos_only=False, check_only=False, opts=None):
        if not self.settings["folders"]:
            return self.add_folder()
        o = opts or {}
        self._shoot_after = o.get("shoot_after", True)
        if self.regroup_worker and self.regroup_worker.isRunning():
            self.regroup_worker.stop()
            self.regroup_worker.wait()

        def done(stats):
            n = stats.get("scanned", 0)
            v = stats.get("videos", 0)
            pl = lambda k, word: f"{k:,} {word}" + ("" if k == 1 else "s")
            what = pl(n - v, "photo") + (f" and {pl(v, 'video')}" if v else "") if v != n else pl(v, "video")
            msg = (f"Imported {what}, found {stats.get('faces', 0):,} faces." if n else
                   "Nothing new imported." if check_only and stats.get("waiting") else "No new photos found.")
            waiting = stats.get("waiting", 0)
            sep_v = stats.get("waiting_videos", 0) if self.settings.get("video_mode") == "separate" \
                and not videos_only else 0
            self.waiting = (waiting, sep_v)
            if waiting:
                lim = self.import_limit()
                msg += (f"  {waiting:,} more waiting - click Scan to import the next "
                        f"{min(lim, waiting):,}." if lim else f"  {waiting:,} more waiting - click Scan to import them.")
            if sep_v and not waiting:
                msg += f"  {sep_v:,} new videos - click Scan videos when you want them."
            self.refresh_stats()
            if stats.get("errors"):
                msg += f" {stats['errors']} files couldn't be read."
            if stats.get("removed"):
                msg += f" {stats['removed']} deleted photos removed."
            if stats.get("others"):
                msg += f" {stats['others']:,} other files recorded (Smart Groups › Unassociated files)."
            if stats.get("skipped_small"):
                msg += f" {stats['skipped_small']:,} small pictures skipped."
            self.statusBar().showMessage(msg, 12000)
            if o.get("dupes_after") and not stats.get("cancelled"):
                QTimer.singleShot(500, self.start_dup_sets)
            if stats.get("missing"):
                self.show_missing(stats["missing"])

        self.run_job("scan", scan_and_group, self.settings, on_done=done, videos_only=videos_only,
                     check_only=check_only, opts=opts)

    def show_missing(self, paths):
        """Scan with "keep them, and show me the list": the files that weren't there any more."""
        p = os.path.join(store.data_dir(), "Missing files.txt")
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write(f"{len(paths):,} files in the library weren't found by the scan on "
                        f"{time.strftime('%Y-%m-%d %H:%M')} (kept in the library):\n\n")
                f.write("\n".join(paths) + "\n")
        except OSError:
            p = None
        box = QMessageBox(self)
        box.setWindowTitle("Missing files")
        box.setText(f"{len(paths):,} file{'s' if len(paths) != 1 else ''} in the library weren't found "
                    "(moved, renamed or deleted outside FaceSort). They're still in the library.")
        box.setDetailedText("\n".join(paths[:2000]) + ("\n…" if len(paths) > 2000 else ""))
        show = box.addButton("Open the list", QMessageBox.ActionRole) if p else None
        box.addButton(QMessageBox.Close)
        box.exec()
        if show is not None and box.clickedButton() is show:
            open_file(p)

    def remember_now(self):
        """Save who's who right away (before anything that reads it back)."""
        self.remember_timer.stop()
        w = self.remember_worker
        if w and w.isRunning():
            w.wait(30000)
        if not self._remember_dirty:
            return
        self._remember_dirty = False
        try:
            known.sync_from_library(self.conn)
        except Exception as ex:
            log(f"saving known people: {ex}")

    def start_remember(self):
        if not self._remember_dirty or getattr(self, "_closing", False):
            return
        if self.busy() or (self.regroup_worker and self.regroup_worker.isRunning()) or \
                (self.remember_worker and self.remember_worker.isRunning()):
            self.remember_timer.start(3000)
            return
        self._remember_dirty = False
        self.remember_worker = Worker(remember_job)
        self.remember_worker.failed.connect(lambda tb: log(tb))
        self.remember_worker.start_background(self.settings)

    def sync_match_slider(self):
        """The header's Face matching slider shows the setting (its range follows the face engine)."""
        sl = self.match_slider
        sl.blockSignals(True)
        lo, hi = store.THRESHOLD_RANGE[self.settings.get("engine", "sface")]
        sl.setRange(round(lo * 100), round(hi * 100))
        sl.setValue(round(store.threshold(self.settings) * 100))
        sl.blockSignals(False)
        self.match_timer.stop()

    def apply_match_slider(self):
        key = store.threshold_key(self.settings.get("engine", "sface"))
        v = self.match_slider.value() / 100
        if abs(float(self.settings.get(key, 0)) - v) < 1e-6:
            return
        self.settings[key] = v
        store.save_settings(self.settings)
        self.statusBar().showMessage(f"Face matching set to {self.match_slider.value()} - updating suggestions…", 6000)
        self.schedule_regroup(now=True)

    def schedule_regroup(self, now=False):
        self._remember_dirty = True
        self.remember_timer.start()
        if now:
            self.regroup_timer.start(10)
        else:
            self.regroup_timer.start()

    def start_regroup(self):
        if getattr(self, "_closing", False):
            return
        if self.busy() or (self.regroup_worker and self.regroup_worker.isRunning()):
            self.regroup_pending = True
            if self.regroup_worker and self.regroup_worker.isRunning():
                self.regroup_timer.start(800)
            return
        wait = self.regroup_wait()
        if wait > 0:                     # still tagging: update the suggestions once you pause
            self.regroup_pending = True
            self.regroup_timer.start(int(wait * 1000) + 50)
            return
        self.regroup_pending = False
        self.regroup_worker = Worker(regroup_job, store.threshold(self.settings),
                                     same_shoot=bool(self.settings.get("same_shoot")))
        self.regroup_worker.ok.connect(self.regroup_done)
        self.regroup_worker.failed.connect(lambda tb: log(tb))
        self.statusBar().showMessage("Updating suggestions…")
        self.regroup_worker.start_background(self.settings)

    def regroup_wait(self):
        """Seconds to hold off updating the suggestions: in gentle mode they wait until you've
        stopped tagging for a few seconds, so the work never competes with your clicks."""
        if self.settings.get("background_cpu", "gentle") == "full":
            return 0.0
        quiet = time.monotonic() - getattr(self, "_last_input", 0.0)
        return max(0.0, REGROUP_IDLE - quiet)

    def regroup_done(self, changed):
        self._data_gen += 1
        self.statusBar().clearMessage()
        if not changed and not self.refresh_timer.isActive():
            return          # nothing changed: don't redraw (no stutter)
        self.refresh_timer.start()         # redrawn once you pause (never under your fingers)

    def write_tags(self):
        s = self.settings
        named = self.conn.execute("SELECT COUNT(DISTINCT photo_id) FROM faces WHERE person_id IS NOT NULL AND ignored=0"
                                  + ("" if s["tag_suggestions"] else " AND confirmed=1")).fetchone()[0]
        where = ("as .xmp sidecar files next to the photos" if s["always_sidecar"] else
                 "into JPEG/PNG files (losslessly - the image itself is not touched) and as .xmp sidecar "
                 "files for other formats")
        if QMessageBox.question(
                self, "Write tags",
                f"Save people's names as tags for {named:,} photos?\n\n"
                f"Names are written {where}. Other tags you already have are kept."
                + ("" if s["tag_suggestions"] else "\n\nOnly confirmed faces are tagged (suggestions are skipped).")
        ) != QMessageBox.Yes:
            return

        def done(st):
            msg = (f"Updated {st['written']:,} photos" + (f" and {st['sidecars']:,} sidecar files" if st['sidecars'] else "")
                   + f". {st['unchanged']:,} were already up to date.")
            if st["errors"]:
                msg += f"\n\n{len(st['errors'])} files couldn't be updated:\n" + "\n".join(
                    f"• {os.path.basename(p)}: {e}" for p, e in st["errors"][:10])
            QMessageBox.information(self, "Tags written", msg)

        self.run_job("tags", jobs.write_tags, self.settings, on_done=done)

    def export_folders(self):
        dlg = ExportDialog(self, self.settings)
        if dlg.exec() != QDialog.Accepted:
            return
        d, move = dlg.dest(), dlg.move()
        self.settings["export_dir"], self.settings["export_move"] = d, move
        store.save_settings(self.settings)
        inc = self.settings["tag_suggestions"]

        def done(st):
            verb = "Moved" if move else "Copied"
            if move:
                msg = f"Moved {st['moved']:,} photos into person folders in:\n{d}"
                if st["copied"]:
                    msg += f"\n\nPlus {st['copied']:,} copies for photos with more than one person."
            else:
                msg = f"Copied {st['copied']:,} photos into person folders in:\n{d}"
            if st.get("discarded"):
                msg += f"\n\n{st['discarded']:,} discarded photos went into '{jobs.DISCARD_FOLDER}'."
            if st.get("webp"):
                parts = [f"{st[k]:,} as {k[3:].upper()}" for k in ("as_jpeg", "as_png") if st.get(k)]
                msg += (f"\n\n{st['webp']:,} photo{'s' if st['webp'] != 1 else ''} converted so they open everywhere ("
                        + ", ".join(parts) + ")"
                        + (f" - the originals went into '{jobs.DISCARD_FOLDER}'." if move else "."))
            if st.get("folders_cleaned"):
                msg += (f"\n\nCleanup: {st['folders_cleaned']:,} empty folder"
                        f"{'s' if st['folders_cleaned'] != 1 else ''} left behind went into '{jobs.DISCARD_FOLDER}'. "
                        "Folders with any other files in them were left where they are.")
            if st["skipped"]:
                msg += f"\n\n{st['skipped']:,} were already there."
            if st.get("log"):
                msg += f"\n\nA list of every move was saved in FaceSort's data folder:\n{st['log']}"
            if st["errors"]:
                msg += f"\n\n{len(st['errors'])} couldn't be {verb.lower()}:\n" + "\n".join(
                    f"• {os.path.basename(p)}: {e}" for p, e in st["errors"][:8])
            if move and st["moved"]:
                # keep the moved photos in the library: watch the destination folder too
                nd = store.norm_path(d)
                inside = any(nd == f or nd.startswith(f.rstrip("\\/") + os.sep) for f in self.settings["folders"])
                if not inside:
                    self.settings["folders"].append(nd)
                    store.save_settings(self.settings)
                    msg += f"\n\n“{nd}” was added to your photo folders so the moved photos stay in FaceSort."
                self.pix_cache.clear()
                self.refresh_all()
            box = QMessageBox(self)
            box.setWindowTitle("Done")
            box.setText(msg)
            op = box.addButton("Open folder", QMessageBox.ActionRole)
            box.addButton(QMessageBox.Ok)
            box.exec()
            if box.clickedButton() is op:
                open_file(d)

        self.settings["export_ai_folder"] = dlg.ai_folder()
        self.settings["export_convert"] = dlg.convert()
        store.save_settings(self.settings)
        self.run_job("export", jobs.export_folders, d, inc, move, on_done=done,
                     roots=list(self.settings["folders"]), ai_folder=dlg.ai_folder(),
                     include_dropped=dlg.include_dropped(), convert_webp=dlg.convert())

    def closeEvent(self, e):
        self._closing = True              # (a timer or job signal still on its way must not start anything)
        getattr(self, "stallwatch", None) and self.stallwatch.stop()
        try:
            store.save_settings(self.settings)  # remembers the thumbnail size
        except Exception:
            pass
        # no timer may fire after the library is closed (they'd hit a closed database)
        for t in self.findChildren(QTimer):
            t.stop()
        if getattr(self, "_bg_timer", None):
            self._bg_timer.stop()
        workers = [w for w in (self.worker, self.regroup_worker, self.remember_worker, getattr(self, "dup_worker", None),
                               getattr(self, "mis_worker", None), getattr(self, "collage_worker", None),
                               getattr(self, "copies_worker", None), getattr(self, "clip_worker", None),
                               getattr(self, "stats_worker", None), getattr(self, "lowlight_worker", None),
                               getattr(self, "clarity_worker", None), getattr(self, "bodies_worker", None),
                               getattr(self, "dest_worker", None), getattr(self, "attrs_worker", None))
                   if w is not None]
        for w in workers:
            if w.isRunning():
                w.stop()
        for w in workers:
            if w.isRunning():
                w.wait(15000)
        still = [w for w in workers if w.isRunning()]
        if self._remember_dirty:
            try:
                known.sync_from_library(self.conn)
            except Exception as ex:
                log(f"saving known people on close: {ex}")
        self.conn.close()
        e.accept()
        if still:
            # a background job that wouldn't stop in time (deep inside a long video, say): Qt aborts
            # the whole program if its thread is destroyed while running ("Fatal Python error:
            # Aborted" in the log). Everything is saved by now, so just end here.
            log(f"closing with {len(still)} background job(s) still busy - ending without waiting")
            logging_flush = getattr(sys.stderr, "flush", None)
            if logging_flush:
                logging_flush()
            os._exit(0)
