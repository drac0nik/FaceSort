"""MainWindow, part: actions on faces and photos (name, confirm, discard, duplicates…)."""
import os
import re
import time
import companions
import engine
import jobs
import known
import store
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QAbstractItemView, QInputDialog, QMenu, QMessageBox, QStyle, QTreeWidgetItem
from icons import app_icon
from ui_common import (
    copies_job, dup_check_job, dup_sets_job, FOLDER_BAR_MIN, generic_names, IDLE_REDRAW, key_of, log, misplaced_job,
    open_file, PEOPLE_LABEL, show_in_folder, std_icon, Worker)
from gallery import FolderButton


class ActionsMixin:
    """Part of MainWindow (see facesort.py): actions on faces and photos (name, confirm, discard, duplicates…)."""

    # ------------------------------------------------------------------ actions
    def _deferred_refresh(self):
        if self.viewer_open():
            self._refresh_on_close = True     # the preview is open: done when it closes, not under your fingers
            return
        quiet = time.monotonic() - self._last_input
        if quiet < IDLE_REDRAW:
            # you're still working (confirming, clicking): the full redraw waits for a real pause
            self.refresh_timer.start(int((IDLE_REDRAW - quiet) * 1000) + 50)
            return
        self.after_change(regroup=False)

    def suggestion_sig(self):
        """A cheap fingerprint of the suggestions and groups (to skip redrawing when a background
        update changed nothing)."""
        return self.conn.execute("""SELECT COUNT(*), TOTAL(person_id), TOTAL(cluster), TOTAL(later_cluster)
                                    FROM faces WHERE ignored IN (0, 5) AND confirmed=0""").fetchone()

    def after_change(self, regroup=True, defer=False):
        """Bring the window up to date after a change. defer=True: the change is already shown
        where you're working (e.g. a confirmed face); the rest catches up a moment later, once
        you pause, so quick keypresses never wait for it."""
        try:
            if store.apply_drop_folders(self.conn):     # dropped folder names, for people just tagged
                self._data_gen = getattr(self, "_data_gen", 0) + 1
        except Exception as e:
            log(f"drop folders: {e}")
        self._remember_dirty = True              # saved to the known-people file a little later
        self.remember_timer.start()
        if self.view[0] == "misplaced":
            QTimer.singleShot(600, self.start_misplaced)   # tagged one: it can be put back now
        if defer:
            self.refresh_timer.start()
            if regroup:
                self.schedule_regroup()
            return
        self.refresh_timer.stop()
        try:
            store.rehide_later(self.conn)            # sorting inside Sort Later keeps photos there
            self.conn.commit()
        except Exception:
            pass
        self.rebuild_tree()
        self.refresh_grid()
        self.refresh_stats()
        if regroup:
            self.schedule_regroup()

    def next_placeholder_name(self, prefix):
        """“Model 07”: the lowest number nobody uses (here, in the known-people file, or in a locked
        vault), so two unnamed people never share a name - and when “Model 03” is renamed to who she
        really is, 03 is free to be used again."""
        pat = re.compile(rf"^{re.escape(prefix)}\s*0*(\d+)$", re.I)
        names = [r[0] for r in self.conn.execute("SELECT name FROM persons")]
        try:
            names += list(known.names())
        except Exception:
            pass
        used = {int(m.group(1)) for m in map(pat.match, names) if m}
        n = 1
        while n in used:
            n += 1
        return f"{prefix} {n:02d}"

    def generic_menu(self, menu, on_pick, suffix=""):
        """Add the generic names to a right-click menu: the ticked ones straight in it ("Tag new
        “Model 04”"), the others under “More generic names”, and “Edit generic names…”.
        on_pick(new_name, prefix) is called with the name chosen."""
        items = generic_names(self.settings)
        shown = [g["name"] for g in items if g["menu"]]
        more = [g["name"] for g in items if not g["menu"]]
        for prefix in shown:
            new = self.next_placeholder_name(prefix)
            menu.addAction(f"Tag new “{new}”{suffix}", lambda new=new, pre=prefix: on_pick(new, pre))
        sub = menu.addMenu("More generic names" if shown else "Tag as a generic name")
        for prefix in more:
            new = self.next_placeholder_name(prefix)
            sub.addAction(f"Tag new “{new}”{suffix}", lambda new=new, pre=prefix: on_pick(new, pre))
        if more:
            sub.addSeparator()
        sub.addAction("Edit generic names…", self.edit_generic_names)
        return menu

    def edit_generic_names(self):
        from dialogs import GenericNamesDialog
        d = GenericNamesDialog(self, generic_names(self.settings))
        if d.exec():
            self.settings["generic_names"] = d.result_list()
            store.save_settings(self.settings)
            self.statusBar().showMessage("Generic names saved", 4000)

    def assign_placeholder(self, ids, name, prefix, refresh=True):
        """Tag faces as a new “Model 03” and put that person in the folder “Unknown Model”
        (made at the top of Folders the first time)."""
        if not ids:
            return
        self.assign_ids(ids, name, refresh=False)
        c = self.conn
        pid = c.execute("SELECT id FROM persons WHERE name=?", (name,)).fetchone()
        if pid:
            folder = f"Unknown {prefix}"
            row = c.execute("""SELECT id FROM categories WHERE name=? AND parent_id IS NULL AND owner_id IS NULL
                               AND COALESCE(vault, 0)=0""", (folder,)).fetchone()
            cid = row[0] if row else store.create_category(c, folder, None, None)
            store.place_person(c, [pid[0]], cid)
            c.commit()
            try:
                known.save_structure(c)
            except Exception:
                pass
        if refresh:
            self.after_change()
        self.statusBar().showMessage(f"✓ {name} - saved under “Unknown {prefix}” (rename any time)", 6000)

    def assign_ids(self, ids, name, refresh=True):
        if not ids or not name:
            return
        pid = store.get_or_create_person(self.conn, name)
        store.assign(self.conn, ids, pid)
        if refresh:
            # shown straight away where you're working; the lists, counts and the rest catch up once
            # you pause (a full redraw on a big library takes a noticeable moment)
            if self.view[0] == "person" and self.view[1] == pid:
                self.patch_rows(ids, confirmed=1)
            else:
                self.drop_from_view(ids)
            self.after_change(defer=True)

    def drop_from_view(self, ids):
        """Named faces leave a list of unnamed faces / someone else's gallery: take their tiles out
        now and select the next one, so you can carry on."""
        rows = self.model.rows
        sel = sorted(i.row() for i in self.grid.selectionModel().selectedIndexes())
        want = set(ids)
        if not any(r[0] in want for r in rows):
            return
        self.model.remove_ids(ids)
        if sel and self.model.rows:
            j = min(sel[0], len(self.model.rows) - 1)
            while j < len(self.model.rows) - 1 and not self.model.rows[j][0]:
                j += 1                                   # (not onto a heading)
            if self.model.rows[j][0]:
                self.grid.setCurrentIndex(self.model.index(j))
        self.update_hint()

    # ---- suggestion sources (most useful first)
    def people_by_recent(self):
        names = [r[0] for r in self.conn.execute(
            "SELECT name FROM persons ORDER BY last_used DESC, name COLLATE NOCASE")]
        seen = {n.casefold() for n in names}
        try:
            names += [n for n in known.names() if n.casefold() not in seen]
        except Exception:
            pass
        hidden = self.hidden_names()
        return [n for n in names if n.casefold() not in hidden]

    def hidden_names(self):
        """People in locked vaults: not offered anywhere while locked."""
        key = (self._data_gen, self.conn.total_changes)
        if getattr(self, "_hidden_key", None) != key:
            self._hidden = store.hidden_person_names(self.conn)
            self._hidden_key = key
        return self._hidden

    def categories_by_use(self):
        names = store.folder_labels_by_use(self.conn)
        seen = {n.casefold() for n in names}
        try:
            names += [n for n in known.category_names() if n.casefold() not in seen]
        except Exception:
            pass
        # nothing from a locked vault (its folders, or its people's folders)
        tops = {n.casefold() for _i, n, locked, _p, _pa in store.vaults(self.conn) if locked} | self.hidden_names()
        return [n for n in names if n.split(store.PATH_SEP)[0].strip().casefold() not in tops]

    @staticmethod
    def folder_suggestions(paths):
        """Folder names of the photos (e.g. "2019 Beach Trip"), as folder ideas."""
        out = []
        for p in paths:
            name = os.path.basename(os.path.dirname(p))
            if name and name.casefold() not in {o.casefold() for o in out} and not re.fullmatch(
                    r"(dcim|\d{3}[a-z]*|camera|pictures|photos|images|downloads|desktop|100apple|img)", name.casefold()):
                out.append(name)
        return out[:3]

    def act_name(self):
        if not self.sel_faces() and self.selected_ids():
            return self.act_tag()  # whole photos: tag who's in them
        ids = self.sel_faces()
        if not ids:
            if self.view[0] == "group":
                return self.name_group()
            return
        current = ""
        if self.view[0] == "person":
            r = self.conn.execute("SELECT name FROM persons WHERE id=?", (self.view[1],)).fetchone()
            current = r[0] if r else ""
        name = self.ask_name(f"Name {len(ids)} face{'s' if len(ids) > 1 else ''}", current)
        if name:
            self.assign_ids(ids, name)

    def name_group(self):
        ids = self.all_ids()
        if not ids:
            return
        folder = store.group_folder(self.conn, ids, self.unnamed_counts()) if self.settings.get("folder_hints", True) else None
        name = self.ask_name(f"Who is this? ({len(ids)} faces)", suggest=folder)
        if name:
            self.assign_ids(ids, name)

    def ignore_group(self):
        ids = self.all_ids()
        if ids and QMessageBox.question(self, "Ignore group",
                                        f"Ignore all {len(ids)} faces in this group?\n"
                                        "Use this for strangers or faces you don't want to sort.") == QMessageBox.Yes:
            store.set_ignored(self.conn, ids, True)
            self.after_change(regroup=False)

    def add_manual_face(self, photo_id, path, box, name, t=None):
        """Tag Person: measure the drawn box and save it as a named face."""
        model = store.get_model(self.conn)
        s = self.settings
        eng = engine.thread_engine(int(s.get("min_face_px", 20)), bool(s.get("use_gpu", True)),
                                   float(s.get("detect_threshold", engine.SCORE_THRESHOLD)), False, model)
        face = engine.face_from_box(eng, path, box, t)
        person = store.get_or_create_person(self.conn, name)
        fid = store.add_manual_face(self.conn, photo_id, face, person, model)
        try:
            known.sync_from_library(self.conn)    # remembered right away (survives Clear library)
        except Exception:
            pass
        self.pix_cache.pop(fid, None)
        self.pix_cache.pop(("rep", fid), None)
        self.statusBar().showMessage(f"Face added: {name}" + ("" if face.get("emb") is not None else
                                     " (no face could be measured in the box, so it won't be used to "
                                     "recognise them elsewhere)"), 8000)
        return fid

    def unassign_faces(self, ids):
        """Take the name off faces (drawn face boxes are removed; others won't be suggested as
        that person again)."""
        manual = {i for (i,) in self.conn.execute(
            f"SELECT id FROM faces WHERE manual>0 AND id IN ({','.join('?' * len(ids))})", ids)}
        for i in manual:
            store.delete_face(self.conn, i)
        rest = [i for i in ids if i not in manual]
        if rest:
            store.reject(self.conn, rest)
        self.after_change()
        self.statusBar().showMessage(f"Unassigned {len(ids)} face{'s' if len(ids) != 1 else ''}", 6000)

    def act_tag_person(self):
        """T: draw a box around a face the scan missed. One photo selected -> its preview opens
        ready to draw; several whole photos -> tag who's in them."""
        sel = self.grid.selectionModel().selectedIndexes()
        if len(sel) == 1 and jobs.media_kind(self.model.rows[sel[0].row()][1] or "") == "other":
            return self.act_tag()                        # (no picture to draw a box on)
        if len(sel) == 1:
            self.open_viewer(sel[0], tag_face=True)
        elif sel:
            self.act_tag()

    def act_tag(self):
        """Tag people on whole photos (no face box needed)."""
        photos = self.sel_photos()
        if not photos:
            return
        name = self.ask_name(f"Who's in {'this photo' if len(photos) == 1 else f'these {len(photos)} photos'}?")
        if name:
            store.tag_photos(self.conn, photos, store.get_or_create_person(self.conn, name))
            self.after_change(regroup=False)

    def ask_category(self, photo_ids, title):
        paths = [r[0] for r in self.conn.execute(
            f"SELECT path FROM photos WHERE id IN ({','.join('?' * len(photo_ids))}) LIMIT 50", photo_ids)]
        names = self.folder_suggestions(paths)
        names += [n for n in self.categories_by_use() if n.casefold() not in {x.casefold() for x in names}]
        name, ok = QInputDialog.getItem(self, title, "Folder (an event or group - type a new one or pick one):",
                                        [""] + names, 0, True)
        name = " ".join(name.split()) if ok else ""
        return name or None

    def act_category(self):
        """Set the category of the selected photos - or, with nothing selected in a person,
        category or 'No category' view, of every photo shown."""
        photos = self.sel_photos()
        if not photos and self.view[0] in ("person", "nocategory", "category"):
            photos = sorted({-k for k in self.all_ids() if k < 0} | set(self.photos_of_faces(
                [k for k in self.all_ids() if k > 0])))
            if not photos or QMessageBox.question(
                    self, "Set folder", f"Set the folder for all {len(photos):,} photos shown here?") != QMessageBox.Yes:
                return
        if not photos:
            return
        name = self.ask_category(photos, f"Folder for {'this photo' if len(photos) == 1 else f'{len(photos):,} photos'}")
        if name:
            self.set_category_for(photos, name)

    def set_category_for(self, photos, name):
        people = sorted({p for ph in photos[:200] for p in store.people_on_photo(self.conn, ph)})
        store.set_category(self.conn, photos, name, people)
        self.after_change(regroup=False)
        self.statusBar().showMessage(
            (f"Folder “{name}” set on " if name else "Folder removed from ") + f"{len(photos):,} photo"
            + ("s" if len(photos) != 1 else ""), 6000)

    def photos_of_faces(self, face_ids):
        if not face_ids:
            return []
        q = ",".join("?" * len(face_ids))
        return [r[0] for r in self.conn.execute(f"SELECT DISTINCT photo_id FROM faces WHERE id IN ({q})", face_ids)]

    def rename_category(self, cid=None):
        cid = cid if isinstance(cid, int) and not isinstance(cid, bool) else self.view[1]
        here = self.view == ("category", cid)
        old = self.conn.execute("SELECT name FROM categories WHERE id=?", (cid,)).fetchone()[0]
        new, ok = QInputDialog.getText(self, "Rename folder", "New name:", text=old)
        new = " ".join(new.split())
        if not ok or not new or new == old:
            return
        if self.conn.execute("SELECT id FROM categories WHERE name=? AND id<>?", (new, cid)).fetchone() and \
                QMessageBox.question(self, "Merge folders", f"“{new}” already exists. Merge “{old}” into it?") != QMessageBox.Yes:
            return
        new_id = store.rename_category(self.conn, cid, new)
        if here:
            self.view = ("category", new_id)
        self.structure_changed()

    def delete_category(self, cid=None, stay=False):
        cid = cid if isinstance(cid, int) and not isinstance(cid, bool) else self.view[1]
        stay = stay and self.view != ("category", cid)
        name = self.conn.execute("SELECT name FROM categories WHERE id=?", (cid,)).fetchone()[0]
        if QMessageBox.question(self, "Delete folder",
                                f"Remove the folder “{name}”? Its photos, people and folders inside it "
                                "stay - they move up one level.") == QMessageBox.Yes:
            store.delete_category(self.conn, cid)
            if not stay:
                self.view = ("none", None)
            self.structure_changed()

    def update_sort_button(self):
        key = self.settings.get("grid_sort", "default")
        label = dict(self.grid_sorts).get(key, self.grid_sorts[0][1])
        self.sort_btn.setText(f"Sort: {label if key != 'default' else 'Best first'}")
        for a in self.sort_menu.actions():
            a.setChecked(a.data() == key)

    def set_grid_sort(self, key):
        self.settings["grid_sort"] = key
        store.save_settings(self.settings)
        self.update_sort_button()
        self.refresh_grid(keep_selection=True)

    def photo_meta(self):
        """{path: (size, date taken, date downloaded/created)} for sorting (one read per change)."""
        key = (self._data_gen, id(self.conn), self.conn.total_changes)
        if getattr(self, "_meta_key", None) != key:
            if self.settings.get("grid_sort") == "downloaded":
                store.fill_created(self.conn)
            self._meta = {p: (sz or 0, tk, cr, w or 0, h or 0) for p, sz, tk, cr, w, h in
                          self.conn.execute("SELECT path, size, taken, created, width, height FROM photos")}
            self._meta_key = (self._data_gen, id(self.conn), self.conn.total_changes)
        return self._meta

    def smart_kinds(self):
        """The kinds of list row in the Smart Groups tab (groups, Unknown faces, Sort later…)."""
        kinds = set()
        stack = [self.tree2.topLevelItem(i) for i in range(self.tree2.topLevelItemCount())]
        while stack:
            it = stack.pop()
            k = key_of(it)
            if k:
                kinds.add(k[0])
            stack.extend(it.child(i) for i in range(it.childCount()))
        return kinds | {"unknown", "group"}

    def split_by_folder(self, rows):
        """One section per folder the pictures are in, headed by the folder's full path (A to Z) with
        the usual heading line - laid out like a person's gallery - so you can see where each face
        came from (the rest of a shoot helps with off-angle faces). A gallery that already has
        sections ("To confirm", "Other faces"…) keeps them: "To confirm  ›  D:\\Photos\\Beach".
        Inside a section the order stays as it was (the clearest faces first)."""
        sections, cur = [], [None, []]
        for r in rows:
            if r[0] == 0:
                if cur[0] is not None or cur[1]:
                    sections.append(cur)
                cur = [r, []]
            else:
                cur[1].append(r)
        if cur[0] is not None or cur[1]:
            sections.append(cur)
        out = []
        for head, items in sections:
            prefix = head[1].split("   ·   ")[0] + "  ›  " if head is not None else ""
            groups = {}
            for r in items:
                groups.setdefault(os.path.dirname(r[1]), []).append(r)
            if not groups and head is not None:
                out.append(head)
            for folder in sorted(groups, key=os.path.normcase):
                g = groups[folder]
                out.append((0, f"{prefix}{os.path.normpath(folder)}   ·   {len(g):,}", ("srcfolder", folder), 1))
                out.extend(g)
        return out

    def sort_rows(self, rows):
        return self.clump_ai(self._sort_rows(rows))

    def clump_ai(self, rows):
        """Photos marked as AI go together at the end of each section, under an "AI images"
        heading (not in the AI / Duplicates lists, which are about that already)."""
        if self.view[0] in ("ai", "dupes", "errors") or not rows:
            return rows
        out, block, section = [], [], [None]

        def flush():
            plain = [r for r in block if not self.photo_info(r[1])[3]]
            ai = [r for r in block if self.photo_info(r[1])[3]]
            out.extend(plain)
            if ai:
                where = f"{section[0]}  ›  " if section[0] else ""
                out.append((0, f"{where}AI images   ·   {len(ai):,}", ("ai_block",), 1))
                out.extend(ai)
            block.clear()
        for r in rows:
            if r[0] == 0:
                flush()
                out.append(r)
                section[0] = r[1].split("   ·   ")[0]
            else:
                block.append(r)
        flush()
        return out

    def _sort_rows(self, rows):
        """Order the pictures by the Sort button's choice, keeping each section together."""
        key = self.settings.get("grid_sort", "default")
        if key == "default" or not rows:
            return rows
        meta = self.photo_meta()
        far = float("-inf")

        def k(r):
            size, taken, created, w, h = meta.get(r[1], (0, None, None, 0, 0))
            name = os.path.basename(r[1]).casefold()
            if key in ("res_down", "res_up"):        # the smaller side first (1080p...), then total pixels
                return (min(w, h), w * h, name)
            if key == "downloaded":
                return (-(created or far), name)
            if key == "taken":
                return (taken is None, -(taken or 0), name)
            if key in ("name_az", "name_za"):
                return name
            return size
        out, block = [], []

        def flush():
            out.extend(sorted(block, key=k, reverse=key in ("name_za", "size_down", "res_down")))
            block.clear()
        for r in rows:
            if r[0] == 0:
                flush()
                out.append(r)
            else:
                block.append(r)
        flush()
        return out

    def dup_key(self):
        return (id(self.conn),) + tuple(self.conn.execute(
            """SELECT COUNT(*), TOTAL(phash), TOTAL(discarded), TOTAL(sort_later),
                      (SELECT COUNT(*) FROM dup_ignore), (SELECT COUNT(*) FROM faces WHERE ignored=2),
                      (SELECT COUNT(*) FROM dup_checked), (SELECT COUNT(*) FROM faces WHERE confirmed=1),
                      TOTAL(dup_skip), (SELECT TOTAL(dup_skip) FROM categories),
                      (SELECT COUNT(*) FROM photo_folders), TOTAL(COALESCE(category_id, 0))
               FROM photos WHERE error IS NULL""").fetchone())

    def dup_sets(self, wait=False):
        """The duplicate sets. Worked out in the background when something that matters changed
        (so confirming faces never waits for it); until then the last answer is shown.
        wait=True: work it out now if it's out of date (for actions on the sets)."""
        key = self.dup_key()
        if getattr(self, "_dup_key", None) != key:
            if wait or not hasattr(self, "_dups"):
                if wait:
                    self._dups = store.duplicate_sets(self.conn, check_new=False)
                    self._dup_key = key
                else:
                    self._dups = []
            if not wait:
                self.dup_timer.start()
        return self._dups

    def start_dup_sets(self):
        w = getattr(self, "dup_worker", None)
        if w and w.isRunning():
            self.dup_timer.start()
            return
        key = self.dup_key()
        if getattr(self, "_dup_key", None) == key:
            return
        self.dup_worker = Worker(dup_sets_job)
        self.dup_worker.ok.connect(lambda res, key=key: self.dup_sets_done(key, res))
        self.dup_worker.failed.connect(lambda tb: log(tb))
        self.dup_worker.start()

    def dup_sets_done(self, key, res):
        sets, unchecked = res
        before = len(getattr(self, "_dups", []))
        self._dups, self._dup_key = sets, key
        store.DUP_UNCHECKED[0] = unchecked
        if unchecked:
            QTimer.singleShot(300, self.check_duplicates)
        if self.viewer_open():
            if self.viewer is not None:          # (Quick review or a dialog open: nothing to update)
                self.viewer.update_compare()
            return
        if len(sets) != before:
            self.rebuild_tree()
        if self.view[0] == "dupes":
            self.refresh_grid()
        elif self.dupes_btn.isChecked():
            self.refresh_grid(keep_selection=True)
        elif getattr(self, "_all_rows", None) is not None:
            self.gallery_dupes_rows(self._all_rows, self.view[0])      # just the button's state
        if self._dup_key != self.dup_key():
            self.dup_timer.start()

    def sorted_photo_sql(self, alias="ph"):
        """SQL condition: the photo is already sorted - someone confirmed on it (face or whole-photo
        tag), it has a folder, or the file is already inside the sorted library / a vault's place."""
        a = alias
        # (IN lists, worked out once - not a look-up per photo: on a big library that's the
        # difference between a fraction of a second and half a minute)
        conds = [f"{a}.category_id IS NOT NULL",
                 f"{a}.id IN (SELECT photo_id FROM faces WHERE confirmed=1 AND person_id IS NOT NULL AND ignored=0)",
                 f"{a}.id IN (SELECT photo_id FROM photo_people)",
                 f"{a}.id IN (SELECT photo_id FROM photo_folders)"]
        roots = []
        lib = self.library_dest()
        if lib:
            roots.append(lib)
        try:
            roots += list(store.vault_root_paths(self.conn).values())
        except Exception:
            pass
        for r in roots:
            r = store.norm_path(r).rstrip("\\/") + os.sep
            esc = r.replace("'", "''").replace("!", "!!").replace("%", "!%").replace("_", "!_")
            disc = (r + jobs.DISCARD_FOLDER + os.sep).replace("'", "''").replace("!", "!!").replace("%", "!%").replace("_", "!_")
            conds.append(f"({a}.path LIKE '{esc}%' ESCAPE '!' AND {a}.path NOT LIKE '{disc}%' ESCAPE '!')")
        return "(" + " OR ".join(conds) + ")"

    # ------------------------------------------------- library consistency
    def library_dest(self):
        """The sorted library (where Sort into folders moves photos), or None when photos are copied."""
        d = self.settings.get("export_dir")
        return store.norm_path(d) if d and self.settings.get("export_move") and os.path.isdir(d) else None

    def start_copies_check(self):
        """Copies of group photos that lost their names (scanned before names were carried over)."""
        w = getattr(self, "copies_worker", None)
        if w and w.isRunning():       # (replacing a running thread makes Qt abort the program)
            return
        self.copies_worker = Worker(copies_job)
        self.copies_worker.ok.connect(lambda n: n and (log(f"named {n} faces on copies of group photos"),
                                                       self.after_change(),
                                                       self.statusBar().showMessage(
                                                           f"{n} face{'s' if n != 1 else ''} on copies of group "
                                                           "photos got their names back.", 10000)))
        self.copies_worker.failed.connect(lambda tb: log(tb))
        self.copies_worker.start()

    def start_misplaced(self):
        dest = self.library_dest()
        if not dest:
            if getattr(self, "_misplaced", None):
                self._misplaced = []
                self.rebuild_tree()
            return
        w = getattr(self, "mis_worker", None)
        if w and w.isRunning():
            QTimer.singleShot(3000, self.start_misplaced)
            return
        self.mis_worker = Worker(misplaced_job, dest, bool(self.settings.get("tag_suggestions")),
                                 bool(self.settings.get("export_ai_folder")))
        self.mis_worker.ok.connect(self.misplaced_done)
        self.mis_worker.failed.connect(lambda tb: log(tb))
        self.mis_worker.start()

    def misplaced_done(self, res):
        before = len(getattr(self, "_misplaced", None) or ())
        self._misplaced = res or []
        if res:
            log(f"consistency check: {len(res)} photos not where they belong")
        if self.viewer_open():
            return
        if len(self._misplaced) != before:
            self.rebuild_tree()
            if before == 0 and self._misplaced:
                self.statusBar().showMessage(f"{len(self._misplaced):,} photos in your library aren't in the folder "
                                             "they belong in - see Other > Not where they belong.", 12000)
        if self.view[0] == "misplaced":
            self.refresh_grid()

    def fix_misplaced(self):
        dest = self.library_dest()
        ids = [r[0] for r in getattr(self, "_misplaced", None) or () if r[2] is not None]
        if not dest or not ids or self.busy():
            return
        if QMessageBox.question(self, "Put them back", f"Move {len(ids):,} photo{'s' if len(ids) != 1 else ''} into "
                                f"the folders they belong in, inside\n{dest}?") != QMessageBox.Yes:
            return

        def done(st):
            self._misplaced = [r for r in self._misplaced if r[2] is None]
            self.pix_cache.clear()
            msg = f"{st['moved']:,} photo{'s' if st['moved'] != 1 else ''} put back where they belong."
            if st.get("folders_cleaned"):
                msg += f" {st['folders_cleaned']:,} empty folders went to '{jobs.DISCARD_FOLDER}'."
            if st["errors"]:
                msg += f" {len(st['errors'])} couldn't be moved (see the log)."
                for p, e in st["errors"][:50]:
                    log(f"put back: {p}: {e}")
            self.statusBar().showMessage(msg, 12000)
            self.view = ("none", None) if self.view[0] == "misplaced" else self.view
            self.refresh_all()
            QTimer.singleShot(500, self.start_misplaced)
        self.run_job("export", jobs.export_folders, dest, bool(self.settings.get("tag_suggestions")), True,
                     on_done=done, roots=[dest], ai_folder=bool(self.settings.get("export_ai_folder")),
                     convert_webp=bool(self.settings.get("export_convert", True)), only=ids)

    def check_duplicates(self):
        if not store.DUP_UNCHECKED[0] or self.busy():
            return

        self.run_job("dupes", dup_check_job)

    # ---- quick updates after discarding duplicates (no full re-check, no full redraw)
    def dup_cache_valid(self):
        return hasattr(self, "_dups") and getattr(self, "_dup_key", None) == self.dup_key()

    def prune_dups(self, gone, was_valid):
        """Photos just discarded (or ruled out of their set): take them out of the duplicate sets
        we already have, instead of working every set out again (the result is the same - a set
        only loses members). Sets left with one photo go; a set whose best copy went gets a new
        best (the largest picture, then the biggest file, then the earliest taken)."""
        if not was_valid or not gone:
            return
        gone = set(gone)
        out, redo = [], []
        for d in self._dups:
            ids = [i for i in d["ids"] if i not in gone]
            if len(ids) < 2:
                continue
            nd = dict(d, ids=ids)
            if d["best"] in gone:
                redo.append(nd)
            out.append(nd)
        if redo:
            need = [i for d in redo for i in d["ids"]]
            q = ",".join("?" * len(need))
            info = {r[0]: r for r in self.conn.execute(
                f"SELECT id, width, height, size, taken, path FROM photos WHERE id IN ({q})", need)}
            prefer_raw = bool(self.settings.get("dup_prefer_raw", True))      # (the same rule as the check)
            for d in redo:
                d["best"] = max(d["ids"], key=lambda i: (prefer_raw and engine.is_raw(info[i][5]),
                                                         (info[i][1] or 0) * (info[i][2] or 0), info[i][3] or 0,
                                                         -(info[i][4] or 9e12), -len(info[i][5])))
        paths = dict(self.conn.execute(
            f"SELECT id, path FROM photos WHERE id IN ({','.join('?' * len(out))})", [d["best"] for d in out])) if out else {}
        out.sort(key=lambda d: (-len(d["ids"]), paths.get(d["best"], "")))
        self._dups = out
        self._dup_key = self.dup_key()

    def quick_refresh(self):
        """Show a small change at once (this gallery only); the lists catch up when you pause."""
        self.refresh_grid(keep_selection=True)
        self.after_change(regroup=False, defer=True)

    def keep_best_all(self):
        sets = self.dup_sets(wait=True)
        extra = [p for d in sets for p in d["ids"] if p != d["best"]]
        if extra and QMessageBox.question(
                self, "Duplicates", f"Keep the best copy of each of the {len(sets):,} sets and mark the other "
                f"{len(extra):,} photos to discard?\n\nNothing is deleted: they go to “~To Be Discarded” when you sort "
                "into folders, and Restore brings them back.") == QMessageBox.Yes:
            ok = self.dup_cache_valid()
            store.set_photos_discarded(self.conn, extra, True)
            self.prune_dups(extra, ok)
            self.after_change(regroup=False)

    def keep_this_copy(self, pid):
        d = next((d for d in self.dup_sets() if pid in d["ids"]), None)
        if d:
            ok = self.dup_cache_valid()
            gone = [p for p in d["ids"] if p != pid]
            store.set_photos_discarded(self.conn, gone, True)
            self.prune_dups(gone, ok)
            self.quick_refresh()

    def not_duplicate(self, pid):
        d = next((d for d in self.dup_sets() if pid in d["ids"]), None)
        if d:
            ok = self.dup_cache_valid()
            store.not_duplicates(self.conn, pid, d["ids"])
            self.prune_dups([pid], ok)          # ruled out against the whole set: it leaves the set
            self.quick_refresh()

    def act_not_ai(self, photos=None):
        photos = self.sel_photos() if photos is None else list(photos)
        if photos:
            self.conn.executemany("UPDATE photos SET ai=0, ai_info=NULL, ai_score=-1 WHERE id=?", [(p,) for p in photos])
            self.conn.commit()
            self._pid_cache = {}
            self.after_change(regroup=False)

    def build_folder_bar(self):
        while self.folder_bar.count():
            w = self.folder_bar.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        heads = [(i, r) for i, r in enumerate(self.model.rows) if r[0] == 0]
        self._bar_heads = []
        if not heads:
            self.folder_bar_area.hide()
            return
        skipped = store.dup_skipped_folders(self.conn)
        for i, r in heads:
            label = r[1]
            target = r[2] if isinstance(r[2], tuple) else None
            fid = target[1] if target and target[0] in ("pf", "cat") else None
            if target and target[0] == "pf":
                drop = lambda ids, t=target: self.file_into(ids, t)
            elif target and target[0] == "cat":
                drop = lambda ids, k=fid: self.file_into_folder(ids, k)
            else:
                drop = None
            b = FolderButton(label.replace("   ·   ", "  ·  "), drop, folder=fid,
                             on_folder_drop=(lambda src, k=fid: self.tree_drop(("category", src), ("category", k)))
                             if fid is not None else None,
                             on_menu=lambda pos, t=target, i=i: self.folder_bar_menu(t, i, pos))
            is_folder = fid is not None or (self.view[0] == "category" and not target)
            if is_folder:
                b.setIcon(std_icon(QStyle.SP_DirIcon))     # a folder (not "Not in a folder")
            elif target and target[0] == "person":
                b.setIcon(app_icon("person"))
            b.setToolTip("Jump to this part of the gallery"
                         + ("\nDrop photos here to put them in this folder" if drop else "")
                         + ("\nDrag it onto another folder (here or on the left) to move it" if fid is not None else "")
                         + ("\nLeft out of duplicate checks" if fid in skipped else "")
                         + "\nRight-click for more")
            b.clicked.connect(lambda _=False, i=i: (self.grid.scrollTo(self.model.index(i),
                                                                      QAbstractItemView.PositionAtTop),
                                                    self.mark_folder_bar()))
            self.folder_bar.addWidget(b)
            self._bar_heads.append((i, b))
        self.folder_bar.addStretch(1)
        self.folder_bar_area.show()
        QTimer.singleShot(0, self.mark_folder_bar)

    def folder_bar_resized(self, *_):
        w = self.folder_bar_area.width()
        if self.folder_bar_area.isVisible() and w >= FOLDER_BAR_MIN:
            self.settings["folder_bar_w"] = w
            for _, b in getattr(self, "_bar_heads", []):
                b.resizeEvent(QResizeEvent(b.size(), b.size()))     # re-fit the names

    def file_into_folder(self, ids, cid):
        photos = sorted({-k for k in ids if k < 0} | set(self.photos_of_faces([k for k in ids if k > 0])))
        if photos and cid is not None:
            store.set_photo_folder(self.conn, photos, cid)
            self.conn.commit()
            self.after_change(regroup=False)
            self.statusBar().showMessage(f"{len(photos):,} photo{'s' if len(photos) != 1 else ''} → {self.cat_name(cid)}", 6000)

    def section_photos(self, row):
        """The photos under the heading at this row of the gallery (up to the next heading)."""
        out = []
        for r in self.model.rows[row + 1:]:
            if r[0] == 0:
                break
            out.append(r[0])
        return sorted({-k for k in out if k < 0} | set(self.photos_of_faces([k for k in out if k > 0])))

    def folder_bar_menu(self, target, row, pos):
        """Right-click a folder in the folder bar: manage it right there."""
        c = self.conn
        m = QMenu(self)
        fid = target[1] if target and target[0] in ("pf", "cat") else None
        if fid is not None:
            self.folder_actions(m, fid)
        elif target and target[0] == "person":
            pid = target[1]
            m.addAction("Go to this person", lambda: self.select_item(self.find_tree_item(("person", pid))))
            base = target[2] if len(target) > 2 else None
            if base is not None and base != (self.view[1] if self.view[0] == "category" else None):
                self.folder_actions(m.addMenu(f"Folder “{self.cat_name(base).split(store.PATH_SEP)[-1].strip()}”"), base)
        photos = self.section_photos(row)
        if photos:
            if not m.isEmpty():
                m.addSeparator()
            m.addAction(f"Leave these {len(photos):,} photos out of duplicate checks",
                        lambda: self.set_dup_skip_photos(photos, True))
            m.addAction("Check them for duplicates again", lambda: self.set_dup_skip_photos(photos, False))
        if not m.isEmpty():
            m.exec(pos)

    def folder_actions(self, m, fid):
        """Rename / merge, new folder inside, move, duplicate checks, delete - for one folder."""
        c = self.conn
        if True:
            m.addAction("Rename / merge…", lambda: self.rename_category(fid))
            m.addAction("New folder inside…", lambda: self.new_category(fid))
            self.category_menu(m, "Move into", lambda k: self.tree_drop(("category", fid), ("category", k)),
                               exclude={fid} | store.category_descendants(c, fid))
            if self.find_tree_item(("category", fid)) is not None:      # (a person's folders aren't listed)
                m.addAction("Open in the left panel", lambda: self.select_item(self.find_tree_item(("category", fid))))
            m.addSeparator()
            skip = bool((c.execute("SELECT dup_skip FROM categories WHERE id=?", (fid,)).fetchone() or [0])[0])
            a = m.addAction("Leave out of duplicate checks")
            a.setCheckable(True)
            a.setChecked(skip)
            a.toggled.connect(lambda on: self.set_dup_skip_folder(fid, on))
            m.addSeparator()
            m.addAction("Delete folder…", lambda: self.delete_category(fid, stay=True))

    def set_dup_skip_folder(self, cid, on):
        store.set_dup_skip(self.conn, folder_id=cid, on=on)
        self._dup_key = None
        self.after_change(regroup=False)
        self.statusBar().showMessage(f"“{self.cat_name(cid)}” " + ("is left out of duplicate checks (its photos are "
                                     "never counted as duplicates)" if on else "is checked for duplicates again"), 8000)

    def set_dup_skip_photos(self, photos, on):
        if not photos:
            return
        store.set_dup_skip(self.conn, photos, on=on)
        self._dup_key = None
        self.after_change(regroup=False)
        self.statusBar().showMessage(f"{len(photos):,} photo{'s' if len(photos) != 1 else ''} "
                                     + ("left out of duplicate checks" if on else "checked for duplicates again"), 6000)

    def mark_folder_bar(self):
        """Highlight the folder bar button of the section you're looking at."""
        heads = getattr(self, "_bar_heads", [])
        if not heads:
            return
        top = self.grid.indexAt(QPoint(20, 20))
        row = top.row() if top.isValid() else 0
        cur = heads[0][1]
        for i, b in heads:
            if i <= row:
                cur = b
        for _, b in heads:
            b.setChecked(b is cur)

    def file_into(self, ids, target):
        """Photos dropped on a person's folder button (or right-click > Move to folder)."""
        _, folder, person = target
        photos = sorted({-k for k in ids if k < 0} | set(self.photos_of_faces([k for k in ids if k > 0])))
        if not photos:
            return
        if folder:
            store.set_photo_folder(self.conn, photos, folder)
        else:   # "Not in a folder": take them out of this person's folders (and the others' copies)
            store.take_out_of_person_folders(self.conn, photos, person)
        self.conn.commit()
        self.after_change(regroup=False)
        self.statusBar().showMessage(f"{len(photos):,} photo{'s' if len(photos) != 1 else ''} → "
                                     + (self.cat_name(folder) if folder else "not in a folder"), 6000)

    def create_folder_here(self):
        """Gallery right-click > Create Folder: a folder inside the person / folder you're looking
        at; the selected photos go into it."""
        kind, ident = self.view
        if kind == "person":
            parent, owner, where = None, ident, f"for {self.person_name(ident)}"
        elif kind == "category":
            parent, owner, where = ident, None, f"inside “{self.cat_name(ident)}”"
        else:
            return
        name, ok = QInputDialog.getText(self, "Create Folder", f"Folder name ({where}):")
        name = " ".join(name.split()) if ok else ""
        if not name:
            return
        cid = store.create_category(self.conn, name, parent, owner)
        photos = self.sel_photos()
        if photos:
            store.set_photo_folder(self.conn, photos, cid)
            self.conn.commit()
        self.structure_changed(f"Folder created: {self.cat_name(cid)}"
                               + (f" ({len(photos)} photo{'s' if len(photos) != 1 else ''} in it)" if photos else ""))

    def destinations(self, include_suggestions=False):
        """store.photo_destinations, worked out once per change of the library (it reads every
        photo, so the sidebar and the views share one copy)."""
        key = (self.conn.total_changes, self._data_gen, include_suggestions, id(self.conn))
        cache = getattr(self, "_dest_cache", {})
        if key not in cache:
            conf, sugg = store.photo_destinations(self.conn, "both")      # both kinds in one read
            cache = {key[:2] + (False,) + key[3:]: conf, key[:2] + (True,) + key[3:]: sugg}
            self._dest_cache = cache
        return cache[key]

    def photo_dirs(self):
        """{photo id: folder}, re-read only when photos were added, removed or moved (not on every
        naming - this reads every photo)."""
        sig = self.conn.execute("SELECT COUNT(*), MAX(id), TOTAL(LENGTH(path)) FROM photos").fetchone()
        key = (self._data_gen, id(self.conn)) + tuple(sig)
        if getattr(self, "_dirs_key", None) != key:
            self._dirs, self._dirs_key = store.photo_dirs(self.conn), key
        return self._dirs

    def unnamed_counts(self):
        """store.unnamed_per_folder, shared by the sidebar and the group views of one redraw."""
        dirs = self.photo_dirs()
        key = (self._dirs_key, self.conn.total_changes)
        if getattr(self, "_unnamed_key", None) != key:
            self._unnamed, self._unnamed_key = store.unnamed_per_folder(self.conn, dirs), key
        return self._unnamed

    def places(self):
        by_cat, uncategorized = {}, set()
        for pid, dests in self.destinations().items():
            for base, person, inner in dests:
                if base:
                    by_cat.setdefault(base, set()).add(pid)
                elif person:
                    uncategorized.add(pid)
                if inner:
                    by_cat.setdefault(inner, set()).add(pid)
        return by_cat, uncategorized

    def photo_rows(self, photo_ids, limit=10000):
        """Grid rows (whole photos) for a set of photo ids, sorted by path."""
        ids = sorted(photo_ids)
        rows = []
        for i in range(0, len(ids), 900):
            chunk = ids[i:i + 900]
            rows += self.conn.execute(f"""SELECT -ph.id, ph.path, {PEOPLE_LABEL}, 1 FROM photos ph
                                          WHERE ph.id IN ({','.join('?' * len(chunk))})""", chunk).fetchall()
        rows.sort(key=lambda r: r[1])
        return rows[:limit]

    def sectioned_rows(self, top=None):
        """Photos saved under a folder (top; None = ~Uncategorized), with a heading for every
        folder inside it - sub-folders, people and inner folders - in the order the folders
        will have."""
        c = self.conn
        paths = store.category_paths(c)
        names = dict(c.execute("SELECT id, name FROM persons"))
        owner_top = store.folder_owner(c, top) if top is not None else None
        inside_top = owner_top is not None
        scope = ({top} | store.category_descendants(c, top)) if top else set()
        depth = len(paths.get(top, [])) if top else 0
        sections = {}
        targets = {}       # what each heading stands for (folder bar: drop, drag, right-click)
        for pid, dests in self.destinations().items():
            for base, person, inner in dests:
                who = (names.get(person, "?"),) if person else (jobs.NO_PEOPLE_FOLDER,)
                if top is None:
                    if base or not person:
                        continue
                    key = who + tuple(paths.get(inner, []))
                    tgt = ("pf", inner, person) if inner else ("person", person)
                elif inside_top:
                    if inner not in scope or person != owner_top:
                        continue
                    key = tuple(paths.get(inner, [])[depth:]) or (paths.get(top, ["?"])[-1],)
                    tgt = ("pf", inner, owner_top)
                else:
                    if base not in scope:
                        continue
                    key = tuple(paths.get(base, [])[depth:]) + who + tuple(paths.get(inner, []))
                    tgt = ("pf", inner, person) if inner else (("person", person, base) if person else ("cat", base))
                sections.setdefault(key, set()).add(pid)
                targets.setdefault(key, tgt)
        rows, allp = [], set()
        for key in sorted(sections, key=lambda k: [x.casefold() for x in k]):
            prow = self.photo_rows(sections[key])
            rows.append((0, f"{' › '.join(key)}   ·   {len(prow):,}", targets.get(key), 1))
            rows += prow
            allp |= sections[key]
        return rows, allp

    def dropped_rows(self, batch):
        """A drop's photos, with a heading for each place they'll be saved."""
        c = self.conn
        paths = store.category_paths(c)
        names = dict(c.execute("SELECT id, name FROM persons"))
        mine = {r[0] for r in c.execute("SELECT id FROM photos WHERE dropped=? AND error IS NULL "
                                        "AND COALESCE(sort_later, 0) != 2", (batch,))}
        dests = self.destinations(True)
        sections = {}
        for pid in mine:
            got = False
            for base, person, inner in dests.get(pid, ()):
                key = tuple(paths.get(base, [])) + ((names.get(person, "?"),) if person else ()) + tuple(paths.get(inner, []))
                sections.setdefault(key or ("No folder",), set()).add(pid)
                got = True
            if not got:
                sections.setdefault(("Not tagged yet",), set()).add(pid)
        rows = []
        for key in sorted(sections, key=lambda k: (k == ("Not tagged yet",), [x.casefold() for x in k])):
            prow = self.photo_rows(sections[key])
            rows.append((0, f"{' › '.join(key)}   ·   {len(prow):,}", None, 1))
            rows += prow
        return rows, mine

    def fill_extra_files(self, batch):
        """The drop's other files, each with the sub-folder and person / folder it'll go under."""
        rows = store.drop_files(self.conn, batch) if batch is not None else []
        self.extra_list.clear()
        if not rows:
            self.extra_box.hide()
            return
        c = self.conn
        paths = store.category_paths(c)
        names = dict(c.execute("SELECT id, name FROM persons"))
        dir_dests = {}
        dests = self.destinations(True)
        for pid, path in c.execute("SELECT id, path FROM photos WHERE dropped=?", (batch,)):
            for base, person, inner in dests.get(pid, ()):
                lbl = " › ".join(paths.get(base, []) + ([names.get(person, "?")] if person else []) + paths.get(inner, []))
                lst = dir_dests.setdefault(os.path.normcase(os.path.dirname(path)), [])
                if lbl and lbl not in lst:
                    lst.append(lbl)
        icons = {"link": app_icon("export"), "archive": app_icon("dupes"), "file": app_icon("info")}
        for fid, _b, path, kind, root in rows:
            under = jobs.drop_file_folders(path, root, dir_dests)
            it = QTreeWidgetItem([os.path.basename(path), companions.SUB.get(kind, "~files"),
                                  " + ".join(under) if under else "(nothing to go with yet - stays where it is)"])
            it.setIcon(0, icons.get(kind, icons["file"]))
            it.setToolTip(0, path)
            it.setData(0, Qt.UserRole, path)
            it.setData(1, Qt.UserRole, fid)
            self.extra_list.addTopLevelItem(it)
        n = {k: sum(1 for r in rows if r[3] == k) for k in ("link", "archive", "file")}
        self.extra_title.setText(f"Other files   ·   {len(rows):,}   ("
                                 + ", ".join(f"{v} → {companions.SUB[k]}" for k, v in n.items() if v) + ")")
        self.extra_box.show()

    def extra_menu(self, pos):
        it = self.extra_list.itemAt(pos)
        if it is None:
            return
        sel = self.extra_list.selectedItems() or [it]
        m = QMenu(self)
        m.addAction("Open", lambda: open_file(it.data(0, Qt.UserRole)))
        m.addAction("Open window to this file", lambda: show_in_folder(it.data(0, Qt.UserRole)))
        m.addSeparator()
        m.addAction(f"Leave {'this file' if len(sel) == 1 else f'these {len(sel)} files'} where "
                    f"{'it is' if len(sel) == 1 else 'they are'}",
                    lambda: (store.forget_drop_files(self.conn, [i.data(1, Qt.UserRole) for i in sel]),
                             self.fill_extra_files(self.view[1] if self.view[0] == "dropped" else None)))
        m.exec(self.extra_list.viewport().mapToGlobal(pos))

    def done_checking(self, batch=None):
        store.done_checking(self.conn, batch)
        self.statusBar().showMessage("Done - those photos are part of the library now (Sort into folders includes them).", 8000)
        self.view = ("none", None)
        self.after_change(regroup=False)

    def person_sections(self, person_id, rows):
        """A person's thumbnails split by their own folders (Beach 2023, …) - every folder of
        theirs gets a heading, even an empty one, so you can drop photos onto it."""
        c = self.conn
        paths = store.category_paths(c)
        tree = store.folder_tree(c)
        mine = {k: store.PATH_SEP.join(paths.get(k, [])) for k, v in tree.items() if v[2] == person_id}
        sub = {}
        for pid, dests in self.destinations(True).items():
            for base, person, inner in dests:
                if person == person_id and inner:
                    sub[pid] = inner
        if not sub and not mine:
            return rows
        face_photo = {}
        fids = [r[0] for r in rows if r[0] > 0]
        for i in range(0, len(fids), 900):
            chunk = fids[i:i + 900]
            face_photo.update(c.execute(f"SELECT id, photo_id FROM faces WHERE id IN ({','.join('?' * len(chunk))})",
                                        chunk).fetchall())
        groups = {k: [] for k in mine}
        groups[None] = []
        for r in rows:
            pid = face_photo.get(r[0]) if r[0] > 0 else -r[0]
            groups.setdefault(sub.get(pid), []).append(r)
        out = []
        for key in sorted(groups, key=lambda k: (k is not None, (mine.get(k) or "").casefold())):
            label = mine.get(key) or (" › ".join(paths.get(key, [])) if key else "Not in a folder")
            out.append((0, f"{label}   ·   {len(groups[key]):,}", ("pf", key, person_id), 1))
            out += groups[key]
        return out
