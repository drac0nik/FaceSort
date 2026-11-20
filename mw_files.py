"""The window's file tools (1.53): Rename…, Export… and Contact sheet… for a selection, a person or a folder."""
import os



class FilesMixin:
    # ---------------------------------------------------------------- which photos
    def gallery_photo_order(self, keys=None):
        """Photo ids in the order the gallery shows them (selected ones, or all)."""
        keys = self.selected_ids() if keys is None else keys
        faces = [k for k in keys if k > 0]
        fmap = {}
        if faces:
            for i in range(0, len(faces), 900):
                ch = faces[i:i + 900]
                fmap.update(self.conn.execute(f"SELECT id, photo_id FROM faces WHERE id IN ({','.join('?' * len(ch))})",
                                              ch).fetchall())
        out = [(-k if k < 0 else fmap.get(k)) for k in keys]
        return list(dict.fromkeys(p for p in out if p))

    def person_file_ids(self, pid):
        c = self.conn
        ids = [r[0] for r in c.execute("""SELECT DISTINCT f.photo_id FROM faces f JOIN photos ph ON ph.id=f.photo_id
                                          WHERE f.person_id=? AND f.confirmed=1 AND f.ignored=0 AND ph.error IS NULL
                                          AND ph.discarded=0""", (pid,))]
        ids += [r[0] for r in c.execute("""SELECT pp.photo_id FROM photo_people pp JOIN photos ph ON ph.id=pp.photo_id
                                           WHERE pp.person_id=? AND ph.error IS NULL AND ph.discarded=0""", (pid,))]
        return list(dict.fromkeys(ids))

    def folder_photo_ids(self, cid):
        _rows, pids = self.sectioned_rows(cid)
        return list(pids)

    def _target(self, what, ident=None):
        """(photo ids, label, person name or None, gallery order or None)."""
        if what == "person":
            name = self.person_name(ident)
            return self.person_file_ids(ident), name, name, None
        if what == "folder":
            return self.folder_photo_ids(ident), self.cat_name(ident), None, None
        if what == "ids":                         # (the viewer's ⋯ menu: the photo on screen)
            ids = list(ident)
            row = self.conn.execute("SELECT path FROM photos WHERE id=?", (ids[0],)).fetchone() if ids else None
            label = os.path.splitext(os.path.basename(row[0]))[0] if row else "FaceSort photos"
            return ids, label, None, ids
        order = self.gallery_photo_order()
        person = self.person_name(self.view[1]) if self.view[0] == "person" else None
        label = person or (self.cat_name(self.view[1]) if self.view[0] == "category" else None) or "FaceSort photos"
        return order, label, person, order

    # ---------------------------------------------------------------- the tools
    def rename_files(self, what="selection", ident=None):
        import rename_ui
        pids, label, person, order = self._target(what, ident)
        title = None
        if what == "person":
            title = f"Rename {label}'s photos ({len(pids):,})"
        elif what == "folder":
            title = f"Rename the photos in “{label}” ({len(pids):,})"
        rename_ui.run_rename(self, pids, person, title, order)

    def export_files(self, what="selection", ident=None):
        import export_ui
        pids, label, _person, order = self._target(what, ident)
        export_ui.run_export(self, order or pids, label)

    def contact_sheet(self, what="selection", ident=None):
        import contactsheet_ui
        pids, label, person, order = self._target(what, ident)
        contactsheet_ui.run_contact_sheet(self, order or pids, person or label)

    def file_tools_menu(self, m, what="selection", ident=None, n=None):
        """Adds Rename… / Export… / Contact sheet… to a right-click menu."""
        from icons import app_icon
        s = "" if n is None else (f"   ({n:,})" if n > 1 else "")
        m.addAction(app_icon("tag"), "Rename…" + s, lambda: self.rename_files(what, ident))
        m.addAction(app_icon("folder"), "Export…" + s, lambda: self.export_files(what, ident))
        m.addAction("Contact sheet…" + s, lambda: self.contact_sheet(what, ident))
