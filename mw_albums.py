"""MainWindow, part: albums and search by description."""
import os
import clip_search
import engine
import jobs
import store
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QInputDialog, QMenu, QMessageBox
from icons import app_icon
from ui_common import (
    clip_download_job, clip_index_job, collage_job, key_of, log, open_folder_window, ROLE_DROP,
    scan_and_group, show_in_folder, Worker)


class AlbumsMixin:
    """Part of MainWindow (see facesort.py): albums and search by description."""

    # ---------------------------------------------------------------- albums
    def new_album(self, photos=None):
        name, ok = QInputDialog.getText(self, "New album", "Album name (e.g. “Best of 2024”, “Portfolio”):")
        name = " ".join(name.split()) if ok else ""
        if not name:
            return None
        aid = store.create_album(self.conn, name)
        if photos:
            store.add_to_album(self.conn, aid, photos)
        self.rebuild_tree()
        self.statusBar().showMessage(f"Album “{name}” made" + (f" with {len(photos):,} photos" if photos else
                                     " - drag photos onto it to add them") + ".", 8000)
        return aid

    def photos_to_album(self, ids, key):
        photos = sorted({-k for k in ids if k < 0} | set(self.photos_of_faces([k for k in ids if k > 0])))
        if not photos:
            return
        if key[0] == "album_root":
            self.new_album(photos)
            return
        self.add_photos_to_album(key[1], photos)

    def add_photos_to_album(self, aid, photos):
        n = store.add_to_album(self.conn, aid, photos)
        name = (self.conn.execute("SELECT name FROM albums WHERE id=?", (aid,)).fetchone() or ["?"])[0]
        self.statusBar().showMessage(f"{n:,} photo{'s' if n != 1 else ''} added to “{name}”"
                                     + (f" ({len(photos) - n:,} were already in it)" if n < len(photos) else ""), 6000)
        self.rebuild_tree()
        if self.view == ("album", aid):
            self.refresh_grid(keep_selection=True)

    def remove_photos_from_album(self, aid, photos):
        store.remove_from_album(self.conn, aid, photos)
        self.statusBar().showMessage(f"{len(photos):,} photo{'s' if len(photos) != 1 else ''} taken out of the album "
                                     "(nothing was deleted).", 6000)
        self.rebuild_tree()
        self.refresh_grid()

    def rename_album(self, aid):
        old = (self.conn.execute("SELECT name FROM albums WHERE id=?", (aid,)).fetchone() or [""])[0]
        name, ok = QInputDialog.getText(self, "Rename album", "New name (the name of another album merges them):",
                                        text=old)
        name = " ".join(name.split()) if ok else ""
        if not name or name == old:
            return
        new = store.rename_album(self.conn, aid, name)
        if self.view == ("album", aid):
            self.view = ("album", new)
        self.rebuild_tree()
        self.refresh_grid()

    def delete_album(self, aid):
        name = (self.conn.execute("SELECT name FROM albums WHERE id=?", (aid,)).fetchone() or ["?"])[0]
        if QMessageBox.question(self, "Delete album", f"Delete the album “{name}”?\n\nThe photos aren't touched - "
                                "they stay in the library and in their folders.") != QMessageBox.Yes:
            return
        store.delete_album(self.conn, aid)
        if self.view == ("album", aid):
            self.view = ("none", None)
        self.rebuild_tree()
        self.refresh_grid()

    def album_menu(self, menu, photos):
        """'Add to album' for the selected photos (ticked: all of them are in it already)."""
        sub = menu.addMenu(app_icon("album", "purple"), "Add to album")
        have = store.albums_of(self.conn, photos)
        for aid, name, n in store.albums(self.conn):
            a = sub.addAction(name + ("   ✓" if have.get(aid, 0) >= len(photos) else ""),
                              lambda aid=aid: self.add_photos_to_album(aid, photos))
        if not sub.isEmpty():
            sub.addSeparator()
        sub.addAction("New album…", lambda: self.new_album(photos))
        return sub

    # ---------------------------------------------------------------- search by description
    def describe_search(self):
        text = " ".join(self.search_box.text().split())
        if not text:
            return
        if not clip_search.available():
            if QMessageBox.question(
                    self, "Search by description",
                    "Searching by description uses a small AI model (CLIP) that runs entirely on this PC.\n\n"
                    "It's downloaded once - about 150 MB - and after that nothing leaves your computer. "
                    "Your photos are then prepared for searching in the background (this takes a while the "
                    "first time, and you can keep working).\n\nDownload it now?") != QMessageBox.Yes:
                return

            def done(ok):
                if ok:
                    self.settings["clip_on"] = True
                    store.save_settings(self.settings)
                    self.start_clip_index()
                    self.describe_search()
            if not self.run_job("clipdl", clip_download_job, on_done=done):
                QMessageBox.information(self, "Search", "Wait for the current job to finish first.")
            return
        if not self.settings.get("clip_on"):
            self.settings["clip_on"] = True
            store.save_settings(self.settings)
        have, total = clip_search.index_counts(self.conn)
        if have == 0:
            self.start_clip_index()
            self.statusBar().showMessage("Getting your photos ready to search - try again in a moment.", 8000)
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            hits = clip_search.search(self.conn, text)
        except Exception as e:
            log(f"search: {e}")
            QMessageBox.warning(self, "Search", f"The search didn't work:\n{e}")
            return
        finally:
            QApplication.restoreOverrideCursor()
        self._search = (text, hits)
        self.close_viewer(refresh=False)
        for t in self.trees:
            t.blockSignals(True)
            t.clearSelection()
            t.setCurrentItem(None)
            t.blockSignals(False)
        self.view = ("search", text)
        self.refresh_grid()
        self.start_clip_index()          # anything new since

    def start_clip_index(self):
        """Prepare photos for searching by description, in the background (carries on where it
        left off; new photos are added after each scan)."""
        if not (self.settings.get("clip_on") and clip_search.available()):
            return
        w = getattr(self, "clip_worker", None)
        if w and w.isRunning():
            return
        have, total = clip_search.index_counts(self.conn)
        if have >= total:
            return
        self.clip_worker = Worker(clip_index_job)
        self.clip_worker.progress.connect(lambda d, t, m: self.statusBar().showMessage(m, 4000))
        self.clip_worker.ok.connect(lambda n: n and self.statusBar().showMessage(
            f"Search by description: {n:,} more photo{'s' if n != 1 else ''} ready.", 6000))
        self.clip_worker.failed.connect(lambda tb: self.statusBar().showMessage(
            "Search by description: couldn't prepare the photos - " + tb.strip().splitlines()[-1][:160], 15000))
        self.clip_worker.start()

    def make_lowlight_copy(self, video_path, opts=1):
        """An enhanced copy of the video (or of the marked section), next to it (in the background)."""
        if not isinstance(opts, dict):
            opts = {"strength": int(opts or 1)}

        def done(res):
            out, n, total = res
            if not out:
                self.statusBar().showMessage("Enhanced copy stopped.", 6000)
                return
            self.add_derived(video_path, out, copy_of=True)
            msg = f"✓ Enhanced copy saved: {os.path.basename(out)}"
            self.statusBar().showMessage(msg, 12000)
            if self.viewer_open():
                self.viewer.toast.setText(msg)
        if not self.run_job("lowlight", jobs.enhance_video, video_path, opts, on_done=done):
            QMessageBox.information(self, "Enhanced copy", "Wait for the current job to finish first.")
            return
        self.statusBar().showMessage("Making an enhanced copy - you can keep working; Stop cancels it.", 8000)

    def lowlight_best_frames(self, video_path, strength=1, t0=None, t1=None):
        """The best moments of a dark video, brightened, saved as photos next to it (tagged like
        the video and scanned into the library)."""
        n, ok = QInputDialog.getInt(self, "Best frames, brightened",
                                    "How many of the best moments should be saved as photos?",
                                    int(self.settings.get("lowlight_frames", 12)), 1, 60)
        if not ok:
            return
        self.settings["lowlight_frames"] = n

        def done(res):
            if not res:
                self.statusBar().showMessage("No frames saved.", 6000)
                return
            outs = [self.add_derived(video_path, p, scan=False) for p, _t in res]
            outs = [o for o in outs if o]
            msg = f"✓ {len(outs)} brightened frame{'s' if len(outs) != 1 else ''} saved next to the video"
            self.statusBar().showMessage(msg, 12000)
            if self.viewer_open():
                self.viewer.toast.setText(msg)
            if outs:
                self.scan_paths(outs)
        if not self.run_job("lowlight", jobs.best_lowlight_frames, video_path, n, int(strength), t0=t0, t1=t1,
                            on_done=done):
            QMessageBox.information(self, "Best frames", "Wait for the current job to finish first.")

    def make_collage(self, video_path, t0=None, t1=None):
        """12 frames from through the whole video in one 3 x 4 picture, saved next to the video
        (made in the background - long videos take a few seconds)."""
        w = getattr(self, "collage_worker", None)
        if w and w.isRunning():
            self.statusBar().showMessage("Still making the last collage…", 4000)
            return
        self.statusBar().showMessage(f"Making a collage of {os.path.basename(video_path)}…")
        self.collage_worker = Worker(collage_job, video_path, int(self.settings.get("low_light", 0)), t0, t1)
        self.collage_worker.ok.connect(lambda res, p=video_path: self.collage_done(p, res))
        self.collage_worker.failed.connect(lambda tb: (log(tb), self.statusBar().showMessage(
            "Couldn't make the collage: " + tb.strip().splitlines()[-1].split(":", 1)[-1].strip(), 10000)))
        self.collage_worker.start()

    def collage_done(self, video_path, res):
        data, times = res
        stem = os.path.splitext(os.path.basename(video_path))[0]
        folder = os.path.dirname(video_path)
        out = os.path.join(folder, f"{stem} - collage.jpg")
        n = 2
        while os.path.exists(out):
            out = os.path.join(folder, f"{stem} - collage ({n}).jpg")
            n += 1
        try:
            with open(out, "wb") as f:
                f.write(data)
        except OSError as e:
            QMessageBox.warning(self, "Collage", f"Couldn't save the collage in:\n{folder}\n\n{e}")
            return
        out = self.add_derived(video_path, out)
        msg = f"✓ Collage saved ({len(times)} frames): {os.path.basename(out or '')}"
        self.statusBar().showMessage(msg, 10000)
        if self.viewer_open():
            self.viewer.toast.setText(msg)

    def add_derived(self, src_path, out, copy_of=False, scan=True):
        """A new picture made from another (a video screenshot, a JPEG copy of a RAW): it gets the
        same people tags and folders, and is scanned like any new photo. copy_of: the Duplicates
        list leaves it out (it's meant to be the same picture)."""
        out = store.norm_path(out)
        video_path = src_path
        c = self.conn
        vid = c.execute("SELECT id, category_id, sort_later, dropped FROM photos WHERE path=?",
                        (store.norm_path(video_path),)).fetchone()
        st = os.stat(out)
        c.execute("INSERT OR IGNORE INTO photos(path, mtime, size, dropped) VALUES(?,?,?,?)",
                  (out, None, st.st_size, vid[3] if vid else None))      # mtime NULL: the next scan reads it
        pid = c.execute("SELECT id FROM photos WHERE path=?", (out,)).fetchone()[0]
        if copy_of and vid:
            c.execute("UPDATE photos SET copy_of=? WHERE id=?", (vid[0], pid))
        if vid:
            people = {r[0] for r in c.execute("""SELECT DISTINCT person_id FROM faces WHERE photo_id=? AND confirmed=1
                                                 AND ignored=0 AND person_id IS NOT NULL""", (vid[0],))}
            people |= {r[0] for r in c.execute("SELECT person_id FROM photo_people WHERE photo_id=?", (vid[0],))}
            c.executemany("INSERT OR IGNORE INTO photo_people(photo_id, person_id, src) VALUES(?,?,'shot')",
                          [(pid, p) for p in people])
            if vid[1]:
                c.execute("UPDATE photos SET category_id=? WHERE id=?", (vid[1], pid))
            c.executemany("INSERT OR IGNORE INTO photo_folders(photo_id, folder_id) VALUES(?,?)",
                          [(pid, f) for (f,) in c.execute("SELECT folder_id FROM photo_folders WHERE photo_id=?", (vid[0],))])
        c.commit()
        # a video you dropped in on its own: make sure FaceSort keeps its screenshots too
        covered = any(out.startswith(f.rstrip("\\/") + os.sep) for f in self.settings["folders"])
        if not covered and out not in self.settings.setdefault("files", []):
            self.settings["files"].append(out)
            store.save_settings(self.settings)
        if scan:
            self.scan_paths([out])
        return out

    def raw_jpeg_copies(self, photo_ids):
        """Right-click > Make a JPEG copy: a full-quality JPEG next to each RAW, tagged like it."""
        q = ",".join("?" * len(photo_ids))
        raws = [p for (p,) in self.conn.execute(f"SELECT path FROM photos WHERE id IN ({q})", list(photo_ids))
                if engine.is_raw(p)]
        if not raws:
            return

        def done(made):
            outs = [self.add_derived(src, out, copy_of=True, scan=False) for src, out in made.get("made", [])]
            if outs:
                self.scan_paths(outs)
            msg = f"Made {len(outs)} JPEG cop{'ies' if len(outs) != 1 else 'y'} next to the RAW file{'s' if len(outs) != 1 else ''}"
            if made.get("errors"):
                msg += f" · {len(made['errors'])} couldn't be made ({made['errors'][0][1]})"
            self.statusBar().showMessage(msg, 10000)
        if not self.run_job("rawcopy", jobs.raw_jpeg_copies, raws, on_done=done):
            QMessageBox.information(self, "Busy", "Wait for the current task to finish, then try again.")

    def scan_paths(self, paths):
        """Look at just these files (new screenshots, rescans) - now, or right after the current task."""
        if self.busy() and self._job != "dupes":
            self._scan_queue = list(dict.fromkeys(getattr(self, "_scan_queue", []) + list(paths)))
            return
        self.run_job("scan", scan_and_group, self.settings, only=list(paths),
                     on_done=lambda st: self.statusBar().showMessage(
                         f"Scanned {len(paths)} new file{'s' if len(paths) != 1 else ''}", 5000))

    def rescan_photos(self, photo_ids):
        """Right-click > Rescan: look for faces again in just these photos (names are kept)."""
        q = ",".join("?" * len(photo_ids))
        paths = [r[0] for r in self.conn.execute(f"SELECT path FROM photos WHERE id IN ({q})", list(photo_ids))]
        if not paths:
            return
        if self.busy():
            QMessageBox.information(self, "Busy", "Wait for the current task to finish, then try again.")
            return

        def done(stats):
            self.statusBar().showMessage(f"Rescanned {len(paths)} photo{'s' if len(paths) != 1 else ''} · "
                                         f"{stats.get('faces', 0)} face{'s' if stats.get('faces', 0) != 1 else ''} found", 8000)
        self.run_job("scan", scan_and_group, self.settings, on_done=done, only=paths)

    def folder_window(self, photo_ids):
        """The folder that holds these photos (their deepest shared folder)."""
        paths = [r[0] for r in self.conn.execute(
            f"SELECT path FROM photos WHERE id IN ({','.join('?' * len(photo_ids))})", list(photo_ids))] if photo_ids else []
        if not paths:
            return None
        try:
            d = os.path.commonpath([os.path.dirname(p) for p in paths])
        except ValueError:          # on different drives
            d = os.path.dirname(paths[0])
        return d if os.path.isdir(d) else None

    def open_window_to(self, photo_ids, fallback=None):
        """Open a folder in Explorer - with the photos you have selected in the gallery picked out
        when they're in it (or else the first of these photos), so you land on them."""
        d = self.folder_window(photo_ids) or (fallback if fallback and os.path.isdir(fallback) else None)
        if d:
            norm = os.path.normcase(os.path.normpath(d))
            picked = []
            sel = self.sel_photos() if hasattr(self, "grid") else []
            pool = list(sel) + [p for p in (photo_ids or [])[:1] if p not in sel]
            if pool:
                q = ",".join("?" * len(pool))
                paths = dict(self.conn.execute(f"SELECT id, path FROM photos WHERE id IN ({q})", pool))
                inside = [paths[p] for p in pool if p in paths and os.path.exists(paths[p])
                          and os.path.normcase(os.path.dirname(os.path.normpath(paths[p]))) == norm]
                picked = [x for x in inside if any(x == paths.get(p) for p in sel)] or inside[:1]
            if picked:
                show_in_folder(picked[0], picked[1:])
            else:
                open_folder_window(d)
        else:
            self.statusBar().showMessage("No folder on disk for that yet (nothing's been sorted into it).", 6000)

    def tree_menu(self, pos, tree=None):
        tree = tree or self.tree
        it = tree.itemAt(pos)
        if it is not None and it.isSelected() and len(tree.selected_keys()) > 1:
            m = self.build_multi_menu(tree.selected_keys())
        else:
            m = self.build_tree_menu(it)
        if m is not None and not m.isEmpty():
            m.exec(tree.viewport().mapToGlobal(pos))

    def build_multi_menu(self, keys):
        """Right-click on several selected rows."""
        m = QMenu(self)
        kinds = {k[0] for k in keys}
        if kinds <= {"group", "later_group"}:
            m.addAction(f"Name these {len(keys)} groups as one person…", self.name_group)
            m.addAction(f"Ignore these {len(keys)} groups", self.ignore_group)
        elif kinds == {"person"}:
            ids = [k[1] for k in keys]
            m.addAction(f"Confirm all suggestions for these {len(ids)} people",
                        lambda: [self.confirm_person(p) for p in ids])
            inner = {k for k, v in store.folder_tree(self.conn).items() if v[2]}
            self.category_menu(m, f"Place these {len(ids)} people in folder",
                               lambda cid: self.place_people(ids, cid), exclude=inner, new_parent=True)
            m.addAction("Take them out of their folders", lambda: self.place_people(ids, None))
        elif kinds == {"category"}:
            ids = [k[1] for k in keys]
            m.addAction(f"Delete these {len(ids)} folders…", lambda: self.delete_categories(ids))
        return m

    def delete_categories(self, ids):
        if QMessageBox.question(self, "Delete folders", f"Remove these {len(ids)} folders? Their photos, people "
                                "and folders inside them move up one level.") == QMessageBox.Yes:
            for cid in ids:
                store.delete_category(self.conn, cid)
            self.view = ("none", None)
            self.structure_changed()

    def build_tree_menu(self, it):
        """Right-click menu for a sidebar row (separate so it can be tested)."""
        if it is None:
            return None
        m = QMenu(self)
        drop = it.data(0, ROLE_DROP)
        if drop and tuple(drop) == ("cat_root",):
            m.addAction("New folder…\tCtrl+Shift+N", self.new_category)
            m.addAction("New vault…", self.new_vault)
            return m
        if drop and tuple(drop) == ("album_root",):
            m.addAction("New album…", self.new_album)
            return m
        key = key_of(it)
        if not key:
            return None
        if key[0] == "album":
            aid = key[1]
            m.addAction("Rename / merge…", lambda: self.rename_album(aid))
            m.addAction("New album…", self.new_album)
            m.addSeparator()
            m.addAction("Delete album… (photos stay)", lambda: self.delete_album(aid))
            return m
        if key[0] in ("person", "category", "group"):
            self.select_item(it)
        c = self.conn
        if key[0] == "person":
            pid = key[1]
            cat = c.execute("SELECT category_id FROM persons WHERE id=?", (pid,)).fetchone()
            cat = cat[0] if cat else None
            m.addAction("Rename / merge…", self.rename_person)
            nsug = c.execute("SELECT COUNT(*) FROM faces WHERE person_id=? AND confirmed=0 AND ignored=0",
                             (pid,)).fetchone()[0]
            ntat = c.execute("SELECT COUNT(*) FROM tattoo_suggestions WHERE person_id=?", (pid,)).fetchone()[0]
            if nsug or ntat:
                m.addAction(f"Confirm all {nsug + ntat} suggestion{'s' if nsug + ntat != 1 else ''}",
                            lambda: self.confirm_person(pid))
            m.addSeparator()
            inner = {k for k, v in store.folder_tree(c).items() if v[2]}
            self.category_menu(m, "Move to folder" if cat else "Place in folder",
                               lambda cid: self.place_people([pid], cid), exclude={cat} | inner, new_parent=True)
            m.addAction(f"New folder for {self.person_name(pid)}…\tCtrl+Shift+N", lambda: self.new_category(owner=pid))
            if cat:
                m.addAction(f"Take out of “{self.cat_name(cat)}”", lambda: self.place_people([pid], None))
            m.addAction("Set folder on all their photos…", self.act_category)
            if c.execute("SELECT 1 FROM tattoos WHERE person_id=? AND src='mark'", (pid,)).fetchone():
                m.addAction("Find tattoo matches", lambda: self.start_tattoo_search([pid]))
            m.addSeparator()
            m.addAction("Delete person", self.delete_person)
        elif key[0] == "category":
            cid = key[1]
            parent = c.execute("SELECT parent_id FROM categories WHERE id=?", (cid,)).fetchone()
            parent = parent[0] if parent else None
            m.addAction("Rename / merge…", lambda: self.rename_category(cid))
            m.addAction("New folder inside…\tCtrl+Shift+N", lambda: self.new_category(cid))
            da = m.addAction("Leave out of duplicate checks")
            da.setCheckable(True)
            da.setChecked(bool((c.execute("SELECT dup_skip FROM categories WHERE id=?", (cid,)).fetchone() or [0])[0]))
            da.toggled.connect(lambda on: self.set_dup_skip_folder(cid, on))
            ftree = store.folder_tree(c)
            owner = ftree.get(cid, ("", None, None))[2]
            inside = owner is not None
            same_kind = {k for k, v in ftree.items() if v[2] == owner}
            self.category_menu(m, "Move into", lambda p: self.move_cat(cid, p, owner if p is None else False),
                               exclude=({cid, parent} | store.category_descendants(c, cid)
                                        | (set(ftree) - same_kind)),
                               top_level="Top level" if parent else None)
            if inside:
                m.addAction("Make it a normal folder (not a person's)", lambda: self.move_cat(cid, None, owner=None))
            m.addSeparator()
            if not inside:
                m.addAction("Add person…", lambda: self.add_person_to(cid))
            here = c.execute("SELECT id, name FROM persons WHERE category_id=? ORDER BY name COLLATE NOCASE",
                             (cid,)).fetchall()
            if here:
                rm = m.addMenu("Take person out")
                for pid, name in here:
                    rm.addAction(name, lambda pid=pid: self.place_people([pid], None))
                if len(here) > 1:
                    rm.addSeparator()
                    rm.addAction(f"Everyone ({len(here)})", lambda: self.place_people([p for p, _ in here], None))
            m.addSeparator()
            if not store.is_main_vault(c, cid):          # the Vault itself always stays
                m.addAction("Delete vault" if store.vault_of(c, cid) == cid else "Delete folder",
                            lambda: self.delete_category(cid))
        elif key[0] == "group":
            m.addAction("Name this group…", self.name_group)
            m.addAction("Ignore group", self.ignore_group)
            m.addAction("Move these photos to Sort Later", lambda: (
                store.set_sort_later(c, self.photos_of_faces(self.all_ids())), self.after_change()))
        elif key[0] == "sortlater":
            m.addAction("Take all out of Sort Later…", self.sort_later_all_out)
        elif key[0] == "dropped":
            b = key[1]
            m.addAction("Done checking - it's tagged right", lambda: self.done_checking(b))
            if len(store.drop_batches(c)) > 1:
                m.addAction("Done checking all of Recently dropped", lambda: self.done_checking(None))
            m.addSeparator()
            m.addAction("Open window to this folder", lambda: self.open_window_to(
                [r[0] for r in c.execute("SELECT id FROM photos WHERE dropped=?", (b,))]))
        elif key[0] == "discarded":
            m.addAction("Move everything here to the Recycle Bin…", self.recycle_discarded)
        # vaults (a locked one, or an unlocked one shown as a folder)
        vid = key[1] if key[0] == "vault" else (key[1] if key[0] == "category" and
                                                  key[1] in {v[0] for v in store.vaults(c)} else None)
        if vid is not None:
            m.clear() if key[0] == "vault" else m.addSeparator()
            vinfo = next((v for v in store.vaults(c) if v[0] == vid), None)
            if vinfo:
                _i, vname, locked, has_pw, vpath = vinfo
                if locked:
                    m.addAction("Unlock…", lambda: self.unlock_vault(vid))
                else:
                    m.addAction(f"Lock “{vname}”", lambda: self.lock_vault(vid))
                    m.addAction("Change password…" if has_pw else "Set a password…", lambda: self.vault_password(vid))
                    if has_pw and not store.is_main_vault(c, vid):
                        m.addAction("Remove the password", lambda: self.vault_password(vid, remove=True))
                    m.addAction("Set where its files go…" if not vpath else f"Files go to: {vpath}  (change…)",
                                lambda: self.vault_location(vid))
                    if vpath:
                        m.addAction("Use the normal destination again", lambda: (store.set_vault_path(c, vid, None),
                                                                                self.rebuild_tree()))
                m.addSeparator()
                m.addAction("New vault…", self.new_vault)
            return m
        # where it is on disk
        if key[0] == "person":
            m.addSeparator()
            m.addAction("Open window to this folder", lambda: self.open_window_to(self.person_photo_ids(key[1])))
        elif key[0] == "category":
            m.addSeparator()
            m.addAction("Open window to this folder", lambda: self.open_window_to(
                self.places()[0].get(key[1], set()) | {r[0] for r in c.execute(
                    "SELECT id FROM photos WHERE category_id=?", (key[1],))}))
        return m
