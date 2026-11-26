"""MainWindow, part: drag and drop from Explorer, folders and Settings."""
import os
import aidetect
import companions
import engine
import known
import bodies
import libmove
import store
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QInputDialog, QMessageBox
from ui_common import APP_VERSION, bodies_download_job, HELP, log
from dialogs import SettingsDialog


def cryptbox_scrub(x):
    import cryptbox
    return cryptbox.scrub(str(x))


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

    def _droppable(self, e):
        import webdrop
        return bool(self._dropped_paths(e)) or webdrop.has_web(e.mimeData())

    def dragEnterEvent(self, e):
        if self._droppable(e):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if self._droppable(e):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        paths = self._dropped_paths(e)
        if paths:
            e.acceptProposedAction()
            QTimer.singleShot(0, lambda: self.handle_drop(paths))   # don't block Explorer while the dialog is open
            return
        import webdrop
        if webdrop.has_web(e.mimeData()):
            pl = webdrop.payload(e.mimeData())           # (read now: the browser's data is gone after the drop)
            e.acceptProposedAction()
            QTimer.singleShot(0, lambda: self.web_drop(pl))

    def web_drop(self, pl):
        """(1.54) A picture dragged from a web browser: saved in the Web drops folder (downloaded by its
        address if the browser didn't hand over the file), then added like any drop."""
        import webdrop
        from ui_common import Worker
        if not (pl["files"] or pl["urls"] or pl["image"]):
            self.statusBar().showMessage("Nothing usable came with that - try dragging the picture itself.", 8000)
            return
        dest = webdrop.folder(self.settings)
        self.statusBar().showMessage("Getting the picture from the web…")
        w = Worker(lambda conn, progress=None, cancelled=None: webdrop.save_all(pl, dest))

        def done(res):
            self._web_worker = None
            if res["saved"]:
                how = {"file": "", "download": " (downloaded)", "picture": " (as the browser showed it)"}[res["how"][0]]
                self.statusBar().showMessage(f"Saved {os.path.basename(res['saved'][0])}{how} in {dest}", 10000)
                self.handle_drop(res["saved"])
            else:
                why = res["errors"][0][1] if res["errors"] else "nothing that came with it was a picture"
                self.statusBar().clearMessage()
                QMessageBox.information(self, "Dropped from the web", f"Couldn't get the picture: {why}.")

        def failed(tb):
            self._web_worker = None
            self.statusBar().clearMessage()
            QMessageBox.information(self, "Dropped from the web",
                                    "Couldn't get the picture: " + str(tb).strip().splitlines()[-1])
        w.ok.connect(done)
        w.failed.connect(failed)
        self._web_worker = w
        w.start()

    def handle_drop(self, paths, ask=True, answers=None, watch=False, defaults=None, source=""):
        """Photos, videos and folders dropped on the window (or chosen with Add folder): the Add
        photos window (importer.py) asks how to tag them; they're added and scanned.
        answers: skips the window (tests) - a dict like ImportDialog.choices(), or the older
        (people, category, sort_later[, structure]) tuple."""
        dirs = [store.norm_path(p) for p in paths if os.path.isdir(p)]
        files = [store.norm_path(p) for p in paths if os.path.isfile(p)
                 and (os.path.splitext(p)[1].lower() in engine.IMAGE_EXTS or engine.is_video(p))]
        if not dirs and not files:
            self.statusBar().showMessage("Nothing to add - drop photos, videos or folders.", 8000)
            return False
        known_paths = {r[0] for r in self.conn.execute("SELECT path FROM photos")}
        prefixes = tuple(d.rstrip("\\/") + os.sep for d in dirs)
        inside = {p for p in known_paths if prefixes and p.startswith(prefixes)} | {f for f in files if f in known_paths}
        if answers is None:
            # (1.57.6) one Add photos window at a time: anything else coming in waits its turn
            if getattr(self, "_import_open", False):
                self._import_queue = getattr(self, "_import_queue", []) + [
                    (list(paths), {"watch": watch, "defaults": defaults, "source": source})]
                self.statusBar().showMessage("More to add - its Add photos window comes next.", 6000)
                return True
            from importer import ImportDialog
            self._import_open = True
            try:
                dlg = ImportDialog(self, dirs, files, len(inside), watch=watch, defaults=defaults, source=source)
                ok = dlg.exec() == QDialog.Accepted
            finally:
                self._import_open = False
                if getattr(self, "_import_queue", None):
                    nxt_paths, nxt_kw = self._import_queue.pop(0)
                    QTimer.singleShot(300, lambda: self.handle_drop(nxt_paths, **nxt_kw))
            if not ok:
                return False
            ch = dlg.choices()
        elif isinstance(answers, dict):
            ch = {"how": "tag", "watch": watch, "people": [], "category": None, "later": False, "folders": []}
            ch.update(answers)
        else:
            people, cat, later, structure = (tuple(answers) + (None,))[:4]
            ch = {"how": "tag", "watch": watch, "people": list(people or []), "category": cat, "later": later,
                  "folders": [(d, "their", os.path.basename(d.rstrip("\\/"))) for d in dirs] if structure == -2 else []}
        people, cat, later = ch["people"], ch["category"], ch["later"]
        uses = {store.norm_path(p): (u, n) for p, u, n in ch["folders"]}
        skip = [p for p, (u, n) in uses.items() if u == "skip"]
        orig_dirs, moved_note = list(dirs), ""
        loose = [store.norm_path(p) for p in paths if os.path.isfile(p) and store.norm_path(p) not in files
                 and not (os.path.splitext(p)[1].lower() in engine.IMAGE_EXTS or engine.is_video(p))]
        if not ch["watch"] and self.settings.get("import_into_library", True):
            # (1.57.3) new imports go straight into the library: <library>\~Unsorted\<where they were>
            # (1.58.1) - and the other files with them (notes, links, documents, archives)
            res = self.import_into_library(dirs, files, ask=answers is None, skip=skip, extras=loose)
            if res is not None:
                remap, n_moved, errors = res
                orig_dirs = list(dict.fromkeys(remap(d) for d in dirs))
                loose = [remap(p) for p in loose]
                dirs = list(dict.fromkeys(remap(d) for d in dirs))
                files = list(dict.fromkeys(remap(f) for f in files))
                uses = {remap(p): v for p, v in uses.items()}
                skip = [remap(p) for p in skip]
                known_paths = {r[0] for r in self.conn.execute("SELECT path FROM photos")}
                prefixes = tuple(d.rstrip("\\/") + os.sep for d in dirs)
                inside = {p for p in known_paths if prefixes and p.startswith(prefixes)} | \
                    {f for f in files if f in known_paths}
                if n_moved:
                    moved_note = f" · {n_moved:,} moved into {libmove.UNSORTED}"
                if errors:
                    moved_note += f" · {len(errors)} couldn't be moved (added where they are)"
                    files += [store.norm_path(src) for src, _why in errors
                              if os.path.splitext(src)[1].lower() in engine.IMAGE_EXTS or engine.is_video(src)]
        # where they live from now on: watched (a library folder) or imported once (forgotten -
        # just these photos are kept; the folder isn't looked at again)
        folders = self.settings["folders"]
        covered = lambda p: any(p == f or p.startswith(f.rstrip("\\/") + os.sep) for f in folders)
        imported = self.settings.setdefault("imported", [])
        for d in dirs:
            if covered(d):
                continue
            if ch["watch"]:
                folders[:] = [f for f in folders if not f.startswith(d.rstrip("\\/") + os.sep)] + [d]
                imported[:] = [f for f in imported if f != d]
                if skip:
                    ex = self.settings.setdefault("exclude", [])
                    ex += [p for p in skip if p.startswith(d.rstrip("\\/") + os.sep) and p not in ex]
            elif d not in imported:
                imported.append(d)
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
        if ch["how"] == "tag":
            for p in dirs + files:
                u, n = uses.get(p, ("", None))
                store.add_drop_rule(self.conn, p, p in dirs, people + ([n] if u == "person" else []),
                                    cat if u not in ("folder",) else None, later,
                                    -2 if u == "their" else None, batch, album=n if u == "album" else None,
                                    skip=u == "skip")
                if u == "folder":
                    store.add_drop_rule(self.conn, p, True, [], store.CHAIN + n, False, None, batch)
            for p, (u, n) in uses.items():
                if p in dirs:
                    continue
                if u == "folder":
                    chain = self._folder_chain(p, uses, dirs)
                    store.add_drop_rule(self.conn, p, True, [], store.CHAIN + store.PATH_SEP.join(chain), False,
                                        None, batch)
                else:
                    store.add_drop_rule(self.conn, p, True, [n] if u == "person" else [], None, False,
                                        -2 if u == "their" else None, batch, album=n if u == "album" else None,
                                        skip=u == "skip")
            store.remember_import_choices(self.conn, [(p, u, n) for p, (u, n) in uses.items()])
        else:
            for p in dirs + files:
                store.add_drop_rule(self.conn, p, p in dirs, [], None, False, None, batch)
        # everything else that came along (notes, links, documents, archives...): kept with the photos
        others = self.collect_other_files(orig_dirs, [p for p in loose if os.path.isfile(p)])
        if others:
            store.add_drop_files(self.conn, batch, others)
            libmove.record_drop_extras(self.conn, batch, [p for p, _k, _r in others])   # (1.58.3) items you can tag
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
        msg += " · watching for new photos" if ch["watch"] and dirs else (" · imported once" if dirs else "")
        msg += moved_note
        if people:
            msg += " · tagged " + ", ".join(people)
        if cat:
            msg += f" · folder “{cat}”"
        if later:
            msg += " · Sort Later"
        if skip:
            msg += f" · {len(skip)} folder{'s' if len(skip) != 1 else ''} left out"
        if others:
            msg += f" · {len(others)} other file{'s' if len(others) != 1 else ''} kept with them (~links / ~archives / ~files)"
        self.statusBar().showMessage(msg, 12000)
        self.refresh_all()
        # only what you dropped is scanned (not the rest of your folders - that's for the next Scan)
        # (1.56.1) all of what you dropped, whatever the toolbar's Import limit says (a 900-picture gallery
        # stopped at the limit and the rest waited, untagged, for the next Scan)
        drop = {"roots": dirs + files, "mode": "both", "shoot_after": True, "exclude": skip, "limit": 0}
        if self.busy():
            prev = self._scan_after if isinstance(self._scan_after, dict) else None
            if prev and prev.get("roots"):   # (two drops while busy: both scanned, not just the last one)
                drop["roots"] = list(dict.fromkeys(list(prev["roots"]) + drop["roots"]))
                drop["exclude"] = list(dict.fromkeys(list(prev.get("exclude") or []) + list(skip)))
            self._scan_after = drop          # scans as soon as the current job ends
        else:
            self.start_scan(videos_only=False, opts=drop)
        return True

    def import_into_library(self, dirs, files, ask=True, skip=(), extras=()):
        r"""(1.57.3) Move what's being imported into <library>\~Unsorted\<where it was>.
        -> (remap(path) -> new path, how many moved, [(path, why)]), or None (imported where it is)."""
        s = self.settings
        lib = libmove.library(s)
        if not lib:
            if not ask:
                return None
            QMessageBox.information(self, "Your FaceSort library",
                                    "What you import now goes straight into your FaceSort library (into "
                                    f"“{libmove.UNSORTED}”, and on to each person's folder once they're tagged).\n\n"
                                    "Choose the folder for your library.")
            d = QFileDialog.getExistingDirectory(self, "Choose your FaceSort library folder")
            if not d:
                self.statusBar().showMessage("No library folder chosen - imported where they are.", 10000)
                return None
            lib = store.norm_path(d)
            s["library_root"] = lib
        folders = s["folders"]
        if not any(lib == f or lib.startswith(f.rstrip("\\/") + os.sep) for f in folders):
            folders.append(lib)
        store.save_settings(s)
        moves = libmove.plan(dirs, files, lib, skip=skip, keep=list(folders), extras=extras)
        done, errors = {}, []
        if moves:
            from PySide6.QtWidgets import QProgressDialog
            dlg = None
            if len(moves) > 30:
                dlg = QProgressDialog(f"Moving into {libmove.UNSORTED}…", "Stop", 0, len(moves), self)
                dlg.setWindowTitle("Import")
                dlg.setMinimumDuration(400)

            def progress(i, n):
                if dlg is not None:
                    dlg.setValue(i)
                    QApplication.processEvents()
            done, errors = libmove.run(self.conn, moves, progress, (lambda: dlg.wasCanceled()) if dlg else None)
            if dlg is not None:
                dlg.close()
        keep = [lib] + list(folders)

        def remap(p):
            p = store.norm_path(p)
            if p in done:
                return done[p]
            if any(libmove.inside(p, k) for k in keep):
                return p
            if os.path.isdir(p) or not os.path.exists(p):
                return store.norm_path(libmove.target(p, lib))
            return p                                   # (a file that couldn't be moved stays put)
        for d in dirs:
            nd = remap(d)
            if nd != d:
                os.makedirs(nd, exist_ok=True)
        return remap, len(done), errors

    def library_or_ask(self, why):
        """The library folder; asks for it once if there isn't one yet (None = not chosen)."""
        s = self.settings
        lib = libmove.library(s)
        if not lib:
            QMessageBox.information(self, "Your FaceSort library", why + "\n\nChoose the folder for your library.")
            d = QFileDialog.getExistingDirectory(self, "Choose your FaceSort library folder")
            if not d:
                return None
            lib = store.norm_path(d)
            s["library_root"] = lib
        if not any(lib == f or lib.startswith(f.rstrip("\\/") + os.sep) for f in s["folders"]):
            s["folders"].append(lib)
        store.save_settings(s)
        return lib

    def bring_into_library(self, answers=None):
        """(1.57.5) Photos added before imports went into the library: moved into ~Unsorted\\<where they
        were>, like a new import - tagged ones then go on to their person's folder by themselves.
        answers: skips the window (tests) - {"watched": bool}."""
        from PySide6.QtWidgets import QCheckBox, QDialogButtonBox, QLabel, QVBoxLayout
        if self.busy():
            QMessageBox.information(self, "Bring into the library", "Wait for the current task to finish first.")
            return False
        lib = self.library_or_ask("Older photos are brought into your FaceSort library, into "
                                  f"“{libmove.UNSORTED}”.") if answers is None else libmove.library(self.settings)
        if not lib:
            return False
        found = libmove.strays(self.conn, self.settings, lib)
        imported = [p for p, g in found if g == "imported"]
        watched = [p for p, g in found if g == "watched"]
        if not found:
            if answers is None:
                QMessageBox.information(self, "Bring into the library",
                                        "Everything FaceSort knows is already in your library.")
            return False
        if answers is None:
            dlg = QDialog(self)
            dlg.setWindowTitle("Bring everything into the library")
            dlg.setMinimumWidth(560)
            lay = QVBoxLayout(dlg)
            head = QLabel(f"<b>{len(imported):,}</b> photo{'s' if len(imported) != 1 else ''} and videos you added "
                          "before (dropped, imported once, single files) are still outside your library. They're "
                          f"moved into <b>{libmove.UNSORTED}\\&lt;where they were&gt;</b> in<br>{lib}")
            head.setWordWrap(True)
            lay.addWidget(head)
            cb = QCheckBox(f"Also the {len(watched):,} in the photo folders you keep watching (your whole collection)")
            cb.setEnabled(bool(watched))
            lay.addWidget(cb)
            note = QLabel("")
            note.setWordWrap(True)
            note.setStyleSheet("color: gray;")
            lay.addWidget(note)
            bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            lay.addWidget(bb)

            def update():
                paths = imported + (watched if cb.isChecked() else [])
                far = libmove.other_drive(paths, lib)
                txt = ("Then, like anything new, photos with someone confirmed move on to their person's folder, "
                       f"and the rest wait in {libmove.UNSORTED} until they're tagged. A photo's own extra files "
                       "(.xmp, .json…) go with it; other files stay where they are. ")
                if cb.isChecked():
                    txt += ("The watched folders stay in your list, so new photos put there later are still "
                            "found. ")
                txt += (f"{far:,} are on another drive, so they're copied, checked, then removed - that takes "
                        "longer. " if far else "Everything's on the library's drive, so it's quick. ")
                txt += "Undo is in History."
                note.setText(txt)
                bb.button(QDialogButtonBox.Ok).setText(f"Move {len(paths):,} into {libmove.UNSORTED}")
                bb.button(QDialogButtonBox.Ok).setEnabled(bool(paths))
            cb.toggled.connect(update)
            update()
            bb.accepted.connect(dlg.accept)
            bb.rejected.connect(dlg.reject)
            if dlg.exec() != QDialog.Accepted:
                return False
            with_watched = cb.isChecked()
        else:
            with_watched = bool(answers.get("watched"))
        paths = imported + (watched if with_watched else [])
        if not paths:
            return False

        def done(st):
            if not isinstance(st, dict):
                return
            msg = f"Brought {st.get('moved', 0):,} older photos into {libmove.UNSORTED}"
            if st.get("errors"):
                msg += f" · {len(st['errors'])} couldn't be moved (left where they are)"
            if st.get("missing"):
                msg += f" · {st['missing']:,} weren't there any more"
            msg += " · tagged ones move on to their folders next · Undo in History"
            self.statusBar().showMessage(msg, 15000)
            self.pix_cache.clear()
            self.refresh_all()
            QTimer.singleShot(1500, self, lambda: self.automove_tick(force=True))
        return self.run_job("bringin", libmove.bring_in_job, lib, paths, on_done=done)

    # ------------------------------------------------------------ (1.60) reconnect files to their galleries
    def reconnect_galleries(self, answers=None):
        """Files in ~Unsorted matched back to the gallery folder they belong to, by shoot evidence (where
        they came from, name and number, when taken, scene, outfit at that shoot, tattoo / jewelry).
        answers (tests): {"pick": "sure" | "all", "tag": bool} - no window."""
        import reconnect
        if self.busy():
            QMessageBox.information(self, "Reconnect to galleries", "Wait for the current task to finish first.")
            return False
        lib = libmove.library(self.settings) if answers is not None else self.library_or_ask(
            "Files are reconnected to galleries inside your FaceSort library.")
        if not lib:
            return False

        def found(res):
            if not isinstance(res, tuple):
                return
            hits, gals = res
            if not hits:
                if answers is None:
                    QMessageBox.information(self, "Reconnect to galleries",
                                            f"Nothing in {libmove.UNSORTED} has enough shoot evidence to place it in a "
                                            "gallery - those files stay where they are.")
                self._reconnect_result = 0
                return
            if answers is None:
                from reconnect_ui import ReconnectDialog
                dlg = ReconnectDialog(self, hits, gals, lib)
                if dlg.exec() != QDialog.Accepted:
                    return
                picks, tag = dlg.picks(), dlg.tag.isChecked()
            else:
                every = answers.get("pick") == "all"
                picks = [(m["id"], gd, gals[gd].person) for gd, ms in hits.items() for m in ms if every or m["sure"]]
                tag = bool(answers.get("tag", True))
            if not picks:
                return

            def moved(st):
                if not isinstance(st, dict):
                    return
                self._reconnect_result = st.get("moved", 0)
                msg = f"Reconnected {st.get('moved', 0):,} files to their galleries"
                if st.get("tagged"):
                    msg += f" · {st['tagged']:,} tagged with the gallery's person"
                if st.get("extra_files"):
                    msg += f" · {st['extra_files']:,} other files followed"
                if st.get("errors"):
                    msg += f" · {len(st['errors'])} couldn't be moved"
                self.statusBar().showMessage(msg + " · Undo in History", 15000)
                self.pix_cache.clear()
                self.refresh_all()
            self.run_job("reconnect", reconnect.reconnect_job, lib, picks, tag, on_done=moved)

        return self.run_job("reconnect_find", reconnect.find, lib, on_done=found)

    # ------------------------------------------------------------ (1.58) encrypted photo and video files
    def seal_soon(self):
        """Encrypt what isn't yet (new photos, edits, copies) - when that's on and nothing else runs."""
        import cryptbox
        import fsx
        if getattr(self, "_closing", False) or not fsx.enabled() or not cryptbox.unlocked():
            return None
        if self.busy() and getattr(self, "_job", None) != "dupes":      # (the duplicate check gives way)
            return False
        n = self.conn.execute("""SELECT COUNT(*) FROM photos p LEFT JOIN vault v ON v.photo_id = p.id
                                 WHERE v.photo_id IS NULL""").fetchone()[0] + len(fsx.made_paths(self.conn))
        if not n:
            return None

        def done(st):
            if not isinstance(st, dict):
                return
            parts = []
            if st.get("sealed"):
                parts.append(f"Encrypted {st['sealed']:,} photo{'s' if st['sealed'] != 1 else ''} and videos")
            if st.get("waiting"):
                parts.append(f"{st['waiting']:,} original{'s' if st['waiting'] != 1 else ''} waiting for you to "
                             "remove them (Settings › Privacy › Review originals)")
            if st.get("errors"):
                parts.append(f"{len(st['errors'])} couldn't be encrypted (see the log)")
                log(f"encrypting: {len(st['errors'])} failed, e.g. {cryptbox_scrub(st['errors'][:3])}")
            if parts:
                self.statusBar().showMessage(" · ".join(parts), 15000)
        return self.run_job("seal", fsx.seal_job, fsx.library_roots(self.settings), on_done=done)

    def seal_when_free(self, tries=240):
        """Encrypt as soon as nothing else is running (tried every 5 seconds, for up to 20 minutes)."""
        r = self.seal_soon()
        if r is False and tries > 0:
            QTimer.singleShot(5000, self, lambda: self.seal_when_free(tries - 1))
        return r

    def review_originals(self, parent=None):
        """The originals of encrypted photos, still on disk: remove them (once you're sure)."""
        import fsx
        from PySide6.QtWidgets import QDialogButtonBox, QLabel, QListWidget, QVBoxLayout
        items = fsx.originals(self.conn)
        if not items:
            QMessageBox.information(parent or self, "Originals", "No originals are waiting - every encrypted "
                                    "photo's original has been removed.")
            return False
        total = 0
        for p, _i in items:
            try:
                total += os.path.getsize(p)
            except OSError:
                pass
        dlg = QDialog(parent or self)
        dlg.setWindowTitle("Review originals")
        dlg.resize(720, 480)
        lay = QVBoxLayout(dlg)
        head = QLabel(f"<b>{len(items):,} original{'s' if len(items) != 1 else ''}</b> ({total / 1e9:,.2f} GB) "
                      "are encrypted and checked. Removing them leaves only the encrypted copies, readable in "
                      "FaceSort with your password or recovery key. Their .xmp / .json extras go too, and so do "
                      "the folders they leave empty.")
        head.setWordWrap(True)
        lay.addWidget(head)
        lst = QListWidget()
        for p, _i in items[:2000]:
            lst.addItem(p)
        if len(items) > 2000:
            lst.addItem(f"… and {len(items) - 2000:,} more")
        lay.addWidget(lst, 1)
        warn = QLabel("Make sure you have your recovery key saved: without the password or the key, the "
                      "encrypted copies can't be opened by anyone - including you.")
        warn.setWordWrap(True)
        warn.setStyleSheet("color: #b3261e;")
        lay.addWidget(warn)
        bb = QDialogButtonBox()
        go = bb.addButton(f"Remove {len(items):,} original{'s' if len(items) != 1 else ''}", QDialogButtonBox.AcceptRole)
        bb.addButton("Not yet", QDialogButtonBox.RejectRole)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        go.setStyleSheet("font-weight: 600;")
        if dlg.exec() != QDialog.Accepted:
            return False

        def done(st):
            if not isinstance(st, dict):
                return
            msg = f"Removed {st['removed']:,} originals - only the encrypted copies are left"
            if st.get("kept"):
                msg += f" · {len(st['kept'])} kept (still in use, or their encrypted copy is missing)"
            self.statusBar().showMessage(msg, 15000)
        return self.run_job("originals", fsx.originals_job, items, fsx.library_roots(self.settings), on_done=done)

    def start_unseal(self):
        """Encryption of the files turned off: every photo back to a normal file."""
        import fsx

        def done(st):
            if not isinstance(st, dict):
                return
            if not st.get("left"):
                fsx.set_enabled(False)
                self.statusBar().showMessage(f"Decrypted {st['unsealed']:,} photos and videos - they're normal "
                                             "files again", 15000)
            else:
                QMessageBox.warning(self, "Decrypting", f"{st['left']:,} couldn't be decrypted yet (see the log) - "
                                    "they stay encrypted; try again from Settings › Privacy.")
                log(f"decrypting: {cryptbox_scrub(st.get('errors', [])[:3])}")
        return self.run_job("unseal", fsx.unseal_job, on_done=done)

    def automove_tick(self, force=False):
        """(1.57.3) Photos in ~Unsorted that now have someone confirmed on them move to where Sort into
        folders would put them - once you've stopped using FaceSort for a moment (so a quick fix to a
        tag doesn't move a file twice), never while a photo is open or a job is running."""
        import time
        s = self.settings
        if not s.get("auto_move", True):
            return False
        lib = libmove.library(s)
        if not lib or not self.isVisible() or self.busy() or not os.path.isdir(libmove.unsorted_dir(lib)):
            return False
        if not getattr(self, "_extras_migrated", False):
            self._extras_migrated = True
            try:
                libmove.migrate_drop_extras(self.conn)
            except Exception as e:
                log(f"other files: {e}")
        libmove.sync_follow(self.conn, lib)          # (1.58.3) other files go with their photos
        if not force:
            if time.monotonic() - (getattr(self, "_last_input", None) or 0) < libmove.PAUSE:
                return False
            v = getattr(self, "viewer", None)
            if v is not None and v.isVisible():
                return False
            if getattr(getattr(self, "locker", None), "locked", False):
                return False
        pids = libmove.tagged_waiting(self.conn, lib)
        if not pids:
            return False

        def done(st):
            if not isinstance(st, dict):
                return
            n = st.get("moved", 0)
            if n:
                msg = f"Moved {n:,} tagged photo{'s' if n != 1 else ''} out of {libmove.UNSORTED} into their " \
                      "person folders" + (f" (+{st['copied']:,} copies for group photos)" if st.get("copied") else "") + \
                      " · Undo in History"
                self.statusBar().showMessage(msg, 12000)
            if st.get("errors"):
                log(f"auto-move: {len(st['errors'])} couldn't be moved: {st['errors'][:3]}")
            self.pix_cache.clear()
            self.refresh_all()
        return self.run_job("automove", libmove.auto_move_job, lib, pids, on_done=done)

    @staticmethod
    def _folder_chain(path, uses, dirs):
        """A folder row's chain: the folder rows above it (inside what was dropped) and itself."""
        chain = []
        p = path.rstrip("\\/")
        tops = {d.rstrip("\\/") for d in dirs}
        while True:
            u, n = uses.get(p, ("", None))
            if u == "folder":
                chain.append(n)
            if p in tops:
                break
            q = os.path.dirname(p)
            if q == p:
                break
            p = q
        return list(reversed(chain))

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
        """Add folder…: the same Add photos window as a drop, with Keep watching ticked (it's
        how you add a library folder)."""
        d = QFileDialog.getExistingDirectory(self, "Choose a folder with photos")
        if not d:
            return
        self.handle_drop([store.norm_path(d)], watch=True)

    def open_settings(self):
        # (who's who is saved in the background now - opening Settings used to wait for it, seconds
        # on a big library; Export known people saves it first itself)
        self.start_remember()
        dlg = SettingsDialog(self, self.settings)
        if dlg.exec() != QDialog.Accepted:
            return
        old = dict(self.settings)
        new = dlg.result_settings()
        QTimer.singleShot(0, lambda: dlg.privacy_page.apply_after(self))   # (1.55) encrypt / decrypt at a restart
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
        if any(new.get(k) != old.get(k) for k in ("use_gpu", "helpers", "cpu_helpers", "background_cpu", "fp16")):
            import helpers
            if helpers.has_pool():           # helpers start again with the new settings when next needed
                from ui_common import ProcessWorker
                helpers.pool().shutdown(3.0)
                ProcessWorker.abandon_all()
        if new.get("ai_detector") and not old.get("ai_detector"):
            self.enable_ai_detector()
        if new.get("library_root") and new.get("library_root") != old.get("library_root"):
            self.remember_now()
            n = known.import_root(new["library_root"])       # what it remembers comes straight back
            if n:
                self.statusBar().showMessage(f"Photos library: remembered details for {n:,} photos loaded", 10000)
            self.start_scan()
        if new.get("content_on") and not old.get("content_on"):
            import content
            QTimer.singleShot(400, self.get_content_models if not content.ready() else self.start_content)
        if new.get("pose_on") and not old.get("pose_on"):
            import pose
            QTimer.singleShot(600, self.get_pose_models if not pose.ready() else self.start_pose)
        if bool(new.get("tag_column", True)) != bool(old.get("tag_column", True)) or \
                new.get("safe_suggestive") != old.get("safe_suggestive"):
            self.safe_view_changed()
        if new.get("face_details") and not old.get("face_details"):
            import face_attrs
            QTimer.singleShot(300, self.get_face_details if not face_attrs.ready() else self.start_face_details)
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
