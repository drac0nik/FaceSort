"""MainWindow, part: Sort Later and the folder structure."""
import known
import bodies
import store
from PySide6.QtWidgets import QInputDialog, QMessageBox


class FoldersMixin:
    """Part of MainWindow (see facesort.py): Sort Later and the folder structure."""

    # ---- Sort Later
    def act_sort_later(self):
        """L: put the selected photos in Sort Later (or, in Sort Later, take them out)."""
        photos = self.sel_photos()
        if not photos:
            return
        out = self.view[0] == "sortlater" or self.view[0].startswith("later_")
        store.set_sort_later(self.conn, photos, not out)
        self.after_change()
        self.statusBar().showMessage(
            f"{len(photos):,} photo{'s' if len(photos) != 1 else ''} "
            + ("taken out of Sort Later" if out else "moved to Sort Later (press L there to bring back)"), 6000)

    def later_out_shown(self):
        photos = sorted({-k for k in self.all_ids() if k < 0} | set(self.photos_of_faces([k for k in self.all_ids() if k > 0])))
        if photos:
            store.set_sort_later(self.conn, photos, False)
            self.after_change()
            self.statusBar().showMessage(f"{len(photos):,} photos taken out of Sort Later", 6000)

    def sort_later_all_out(self):
        ids = [r[0] for r in self.conn.execute("SELECT id FROM photos WHERE sort_later=1")]
        if ids and QMessageBox.question(self, "Sort Later",
                                        f"Take all {len(ids):,} photos out of Sort Later?") == QMessageBox.Yes:
            store.set_sort_later(self.conn, ids, False)
            self.after_change()

    # ---- category structure (nesting, people's places)
    def structure_changed(self, msg=""):
        try:
            known.save_structure(self.conn)
        except Exception:
            pass
        self.after_change(regroup=False)
        if msg:
            self.statusBar().showMessage(msg, 6000)

    def cat_name(self, cid):
        return store.folder_label(self.conn, cid) or "?"

    def person_name(self, pid):
        r = self.conn.execute("SELECT name FROM persons WHERE id=?", (pid,)).fetchone()
        return r[0] if r else "?"

    def new_category(self, parent_id=None, owner=None):
        where = f" inside “{self.cat_name(parent_id)}”" if parent_id else (
            f" for {self.person_name(owner)} (saved inside their folder)" if owner else "")
        name, ok = QInputDialog.getText(self, "New folder", f"Name of the new folder{where}:")
        name = " ".join(name.split()) if ok else ""
        if not name:
            return None
        existing = store.find_child(self.conn, name, parent_id, owner)
        cid = store.create_category(self.conn, name, parent_id, owner)
        self.structure_changed(f"Folder “{self.cat_name(cid)}” " + ("moved" if existing else "created"))
        return cid

    def new_folder_here(self):
        """Ctrl+Shift+N (as in Windows Explorer): a new folder where you are - inside the folder
        you're looking at, in a person's folder when you're in their gallery, a new album among
        the albums, otherwise a new folder at the top of the Folders tab."""
        kind, ident = self.view if self.view else (None, None)
        if kind == "category" and ident:
            cid = self.new_category(ident)
        elif kind == "person" and ident:
            cid = self.new_category(owner=ident)
        elif kind == "album":
            return self.new_album()
        else:
            cid = self.new_category()
        if cid:
            it = self.find_tree_item(("category", cid))
            if it is not None:
                it.treeWidget().scrollToItem(it)
        return cid

    def place_people(self, person_ids, cat_id):
        if not store.place_person(self.conn, person_ids, cat_id):
            self.statusBar().showMessage("People can't be placed in a folder that goes inside people's folders.", 8000)
            return
        names = ", ".join(self.person_name(p) for p in person_ids[:3]) + ("…" if len(person_ids) > 3 else "")
        self.structure_changed(f"{names}: saved under “{self.cat_name(cat_id)}”" if cat_id
                               else f"{names}: taken out of their folder")

    def move_cat(self, cid, parent_id, owner=False):
        if not store.move_category(self.conn, cid, parent_id, owner):
            self.statusBar().showMessage("Can't move it there: a folder can't go inside itself, and two folders "
                                         "side by side can't have the same name.", 8000)
            return
        self.structure_changed(f"Moved: {self.cat_name(cid)}")

    def tree_drop(self, src, target):
        """A row was dragged in the sidebar and dropped on a folder or heading."""
        if src[0] == "person":
            if target[0] == "category":
                self.place_people([src[1]], target[1])
            elif target[0] in ("cat_root", "people_root"):
                self.place_people([src[1]], None)
        elif src[0] == "category":
            if target[0] == "category":
                self.move_cat(src[1], target[1])
            elif target[0] == "cat_root":
                self.move_cat(src[1], None, owner=None)
            elif target[0] == "owner":
                self.move_cat(src[1], None, owner=target[1])

    def category_menu(self, menu, title, pick, exclude=(), top_level=None, new_parent=False):
        """A submenu listing every folder (full path) - pick(cid) is called."""
        sub = menu.addMenu(title)
        paths = store.category_paths(self.conn)
        for cid, path in sorted(paths.items(), key=lambda kv: [x.casefold() for x in kv[1]]):
            if cid in exclude:
                continue
            sub.addAction("    " * (len(path) - 1) + path[-1], lambda cid=cid: pick(cid))
        if paths:
            sub.addSeparator()
        if top_level:
            sub.addAction(top_level, lambda: pick(None))
        if new_parent:
            sub.addAction("New folder…", lambda: (lambda c: c and pick(c))(self.new_category()))
        return sub

    def sel_tattoo_photos(self):
        """Selected photo items that are tattoo-match suggestions."""
        rows = sorted({i.row() for i in self.grid.selectionModel().selectedIndexes()})
        return [-self.model.rows[r][0] for r in rows if self.model.rows[r][0] < 0 and not self.model.rows[r][3]]

    def act_confirm(self):
        sb = self.sel_shoot_bodies()
        if sb:
            bodies.confirm(self.conn, sb)
            self.quick_change()
            return
        tp = self.sel_tattoo_photos()
        if tp and self.view[0] in ("person", "tattoos"):
            store.confirm_tattoo_matches(self.conn, tp, self.view[1] if self.view[0] == "person" else None)
        ids = self.sel_faces()
        multi_people = self.view[0] == "multi" and {k[0] for k in self.view[1]} <= {"person", "later_person"}
        if ids and (self.view[0] in ("person", "later_person") or multi_people):
            store.confirm(self.conn, ids)
            self.patch_rows(ids, confirmed=1)       # shown straight away
        if tp or (ids and (self.view[0] in ("person", "later_person") or multi_people)):
            self.after_change(regroup=bool(ids), defer=not tp)

    def patch_rows(self, ids, confirmed=None):
        """Update thumbnails in place (no reload of the list)."""
        want = set(ids)
        for i, r in enumerate(self.model.rows):
            if r[0] in want and confirmed is not None:
                self.model.rows[i] = (r[0], r[1], r[2], confirmed)
                ix = self.model.index(i)
                self.model.dataChanged.emit(ix, ix)
        self.update_hint()

    def confirm_all_tattoos(self):
        person = self.view[1] if self.view[0] == "person" else None
        photos = [-r[0] for r in self.model.rows if r[0] < 0 and not r[3]]
        if photos and QMessageBox.question(
                self, "Confirm tattoo matches",
                f"Confirm all {len(photos):,} tattoo match{'es' if len(photos) != 1 else ''} shown here?\n"
                "Each photo gets tagged with that person.") == QMessageBox.Yes:
            store.confirm_tattoo_matches(self.conn, photos, person)
            self.after_change(regroup=False)
