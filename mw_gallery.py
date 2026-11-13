"""MainWindow, part: library folders shown, and filling the gallery."""
import os
import re
import clip_search
import details
import bodies
import store
from PySide6.QtCore import QItemSelectionModel, Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QListWidgetItem, QMessageBox, QStyle, QTreeWidgetItem
from icons import app_icon
from ui_common import (
    button_icon, HAS_PERSON, key_of, LEGEND_GRAY, legend_html, NOFACES_WHERE, PEOPLE_LABEL_ALL, std_icon, UNASSOC_WHERE,
    tool_button)


class GalleryMixin:
    """Part of MainWindow (see facesort.py): library folders shown, and filling the gallery."""

    # ---- library folders shown (the dropdown under "Find a person…")
    OTHER_PLACES = "*other*"

    def scope_folders(self):
        """The library folders being looked at ([] = all of them)."""
        want = [f for f in self.settings.get("scope_folders", []) if f == self.OTHER_PLACES or f in self.settings["folders"]]
        return want if want and len(want) < len(self.settings["folders"]) + 1 else []

    def scope_on(self):
        return bool(self.scope_folders())

    def _scope_prefixes(self):
        key = (tuple(self.settings.get("scope_folders", [])), tuple(self.settings["folders"]))
        if getattr(self, "_scope_key", None) != key:
            norm = lambda f: os.path.normcase(os.path.normpath(f)).rstrip("\\/") + os.sep
            self._scope_all = tuple(norm(f) for f in self.settings["folders"])
            self._scope_in = tuple(norm(f) for f in self.scope_folders() if f != self.OTHER_PLACES)
            self._scope_other = self.OTHER_PLACES in self.scope_folders()
            self._scope_key = key
        return self._scope_in, self._scope_other, self._scope_all

    def in_scope(self, path):
        inside, other, every = self._scope_prefixes()
        p = os.path.normcase(os.path.normpath(path or ""))
        if inside and p.startswith(inside):
            return True
        return other and not p.startswith(every)

    def scoped_photo_ids(self, c=None):
        """Ids of the photos in the folders being looked at (None = everything)."""
        if not self.scope_on():
            return None
        c = c or self.conn
        return {pid for pid, path in c.execute("SELECT id, path FROM photos") if self.in_scope(path)}

    def update_scope_button(self):
        sel = self.scope_folders()
        if not sel:
            self.scope_btn.setText("All library folders")
            self.scope_btn.setStyleSheet("")
            return
        names = [("Other places" if f == self.OTHER_PLACES else (os.path.basename(f.rstrip("\\/")) or f)) for f in sel]
        self.scope_btn.setText("Showing: " + (names[0] if len(names) == 1 else f"{names[0]} + {len(names) - 1} more"))
        self.scope_btn.setStyleSheet("QToolButton { color: #f59e0b; font-weight: bold; }")

    def fill_scope_list(self):
        lst = self.scope_list
        lst.blockSignals(True)
        lst.clear()
        sel = set(self.scope_folders())
        counts = {}
        every = self._scope_prefixes()[2]
        for (path,) in self.conn.execute("SELECT path FROM photos"):
            p = os.path.normcase(os.path.normpath(path))
            hit = next((i for i, f in enumerate(every) if p.startswith(f)), None)
            counts[hit] = counts.get(hit, 0) + 1
        it = QListWidgetItem("All library folders")
        it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        it.setCheckState(Qt.Checked if not sel else Qt.Unchecked)
        it.setData(Qt.UserRole, None)
        f = it.font()
        f.setBold(True)
        it.setFont(f)
        lst.addItem(it)
        rows = [(fo, fo, counts.get(i, 0)) for i, fo in enumerate(self.settings["folders"])]
        if counts.get(None):
            rows.append(("Other places (dropped in from elsewhere)", self.OTHER_PLACES, counts[None]))
        for label, key, n in rows:
            it = QListWidgetItem(f"{label}   ·   {n:,}")
            it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            it.setCheckState(Qt.Checked if (not sel or key in sel) else Qt.Unchecked)
            it.setData(Qt.UserRole, key)
            it.setToolTip(key if key != self.OTHER_PLACES else "Photos that aren't inside any of your library folders")
            lst.addItem(it)
        lst.blockSignals(False)
        lst.setMinimumWidth(min(700, max(320, lst.sizeHintForColumn(0) + 30)))
        lst.setFixedHeight(min(420, lst.sizeHintForRow(0) * lst.count() + 8))

    def scope_item_changed(self, it):
        lst = self.scope_list
        lst.blockSignals(True)
        if it.data(Qt.UserRole) is None:          # "All": tick or untick everything
            st = it.checkState()
            if st == Qt.Unchecked:                # (nothing at all makes no sense: back to all)
                st = Qt.Checked
                it.setCheckState(st)
            for i in range(1, lst.count()):
                lst.item(i).setCheckState(st)
        lst.blockSignals(False)
        keys = [lst.item(i).data(Qt.UserRole) for i in range(1, lst.count())
                if lst.item(i).checkState() == Qt.Checked]
        every = len(keys) == lst.count() - 1
        lst.blockSignals(True)
        lst.item(0).setCheckState(Qt.Checked if every else Qt.Unchecked)
        lst.blockSignals(False)
        if not keys:                              # unticked the last one: show everything again
            keys, every = [], True
        self.settings["scope_folders"] = [] if every else keys
        store.save_settings(self.settings)
        self.update_scope_button()
        self.rebuild_tree()
        self.refresh_grid(keep_selection=True)

    def apply_scope_to_tree(self, c):
        """Only some library folders shown: people, groups, folders and albums count just the photos
        from those folders, and the ones with none there are hidden."""
        self.update_scope_button()
        ids = self.scoped_photo_ids(c)
        if ids is None:
            return
        c.execute("CREATE TEMP TABLE IF NOT EXISTS scope_ids(id INTEGER PRIMARY KEY)")
        c.execute("DELETE FROM temp.scope_ids")
        c.executemany("INSERT INTO temp.scope_ids(id) VALUES(?)", ((i,) for i in ids))
        conf, sugg = {}, {}
        for pid, n in c.execute("""SELECT person_id, COUNT(DISTINCT photo_id) FROM (
                                     SELECT person_id, photo_id FROM faces WHERE confirmed=1 AND ignored=0
                                       AND person_id IS NOT NULL
                                     UNION SELECT person_id, photo_id FROM photo_people)
                                   WHERE photo_id IN (SELECT id FROM temp.scope_ids) GROUP BY person_id"""):
            conf[pid] = n
        for pid, n in c.execute("""SELECT person_id, COUNT(*) FROM faces WHERE confirmed=0 AND ignored=0
                                   AND person_id IS NOT NULL AND photo_id IN (SELECT id FROM temp.scope_ids)
                                   GROUP BY person_id"""):
            sugg[pid] = n
        groups = dict(c.execute("""SELECT cluster, COUNT(*) FROM faces WHERE cluster IS NOT NULL AND person_id IS NULL
                                   AND ignored=0 AND photo_id IN (SELECT id FROM temp.scope_ids) GROUP BY cluster"""))
        cats = dict(c.execute("""SELECT cid, COUNT(DISTINCT pid) FROM (
                                   SELECT category_id AS cid, id AS pid FROM photos WHERE category_id IS NOT NULL
                                   UNION SELECT folder_id, photo_id FROM photo_folders)
                                 WHERE pid IN (SELECT id FROM temp.scope_ids) GROUP BY cid"""))
        albums = dict(c.execute("""SELECT album_id, COUNT(*) FROM album_photos
                                   WHERE photo_id IN (SELECT id FROM temp.scope_ids) GROUP BY album_id"""))
        count_re = re.compile(r"^(.*?)   ([\d,]+)(.*)$")

        def relabel(it, n, extra=None):
            m = count_re.match(it.text(0))
            if m:
                rest = m.group(3)
                if extra is not None:
                    rest = re.sub(r"\s*\(\+\d+\?\)", "", rest) + (f"  (+{extra}?)" if extra else "")
                it.setText(0, f"{m.group(1)}   {n:,}{rest}")

        def walk(it):
            """-> True if this row (or something under it) has photos in the folders shown."""
            k = key_of(it)
            kids = [walk(it.child(i)) for i in range(it.childCount())]
            any_kid = any(kids)
            if not k:
                return any_kid
            kind, ident = k[0], k[1] if len(k) > 1 else None
            if kind == "person":
                n, sg = conf.get(ident, 0), sugg.get(ident, 0)
                relabel(it, n, sg)
                show = bool(n or sg) or any_kid
            elif kind == "group":
                n = groups.get(ident, 0)
                relabel(it, n)
                show = n >= 2
            elif kind == "category":
                n = cats.get(ident, 0)
                relabel(it, n)
                show = bool(n) or any_kid
            elif kind == "album":
                n = albums.get(ident, 0)
                relabel(it, n)
                show = bool(n)
            else:
                return True
            it.setHidden(not show)
            it.setData(0, Qt.UserRole + 11, None if show else True)     # (Find a person keeps it hidden)
            return show
        for top in self.top_items():
            for i in range(top.childCount()):
                walk(top.child(i))

    def set_pending_only(self, on):
        self.settings["people_pending_only"] = bool(on)
        store.save_settings(self.settings)
        self.rebuild_tree()

    def set_hide_people(self, on):
        self.settings["hide_people"] = bool(on)
        store.save_settings(self.settings)
        if on and self.view[0] == "person":
            self.view = ("none", None)
        self.rebuild_tree()
        self.refresh_grid(keep_selection=False)

    def set_bottom_sort(self, i):
        self.settings["bottom_sort"] = self.sort_modes[i][0]
        store.save_settings(self.settings)
        if self.view[0] == "bucket":
            self.view = ("none", None)
        self.rebuild_tree()
        self.refresh_grid(keep_selection=False)

    def set_other_sort(self, i):
        self.settings["other_sort"] = self.other_modes[i][0]
        store.save_settings(self.settings)
        self.rebuild_tree()

    def add_buckets(self, section, mode):
        """Lower list sorted by date taken / date created / original location instead of by face."""
        buckets = store.photo_buckets(self.conn, mode)
        title = {"taken": "To sort by date taken", "created": "To sort by date created",
                 "location": "To sort by original location"}[mode]
        sec = section(f"{title} ({len(buckets)})", tree=self.tree2)
        icon = app_icon("pin" if mode == "location" else "calendar")
        for key, (label, _, pids) in sorted(buckets.items(), key=lambda kv: kv[1][1]):
            it = QTreeWidgetItem([f"{label}   {len(pids)}"])
            it.setIcon(0, icon)
            it.setData(0, Qt.UserRole, ("bucket", f"{mode}|{key}"))
            it.setData(0, Qt.UserRole + 1, label.lower())
            if mode == "location":
                it.setToolTip(0, key)
            sec.addChild(it)
        return sec

    LATER_FACE = "f.ignored=5 AND COALESCE(f.later_prev, 0)=0"

    def add_later_children(self, parent, rep_icon):
        """Sort Later can be opened up and sorted by face, like the main lists: the people in it,
        its look-alike groups, the other faces, and photos with no face."""
        c = self.conn
        people = c.execute(f"""SELECT p.id, p.name, COUNT(*), SUM(f.confirmed=0),
                                      (SELECT id FROM faces g WHERE g.person_id=p.id AND g.ignored=5 LIMIT 1)
                               FROM faces f JOIN persons p ON p.id=f.person_id WHERE {self.LATER_FACE}
                               GROUP BY p.id ORDER BY p.name COLLATE NOCASE""").fetchall()
        for pid, name, n, sugg, rep in people:
            it = QTreeWidgetItem([f"{name}   {n}" + (f"  (+{sugg}?)" if sugg else "")])
            rep_icon(it, rep)
            it.setData(0, Qt.UserRole, ("later_person", pid))
            it.setData(0, Qt.UserRole + 1, name.lower())
            parent.addChild(it)
        groups = c.execute(f"""SELECT f.later_cluster, COUNT(*), MAX(f.id) FROM faces f
                               WHERE {self.LATER_FACE} AND f.person_id IS NULL AND f.later_cluster IS NOT NULL
                               GROUP BY f.later_cluster ORDER BY COUNT(*) DESC""").fetchall()
        for i, (cl, n, fid) in enumerate(groups[:500]):
            it = QTreeWidgetItem([f"Group {i + 1}   {n} faces"])
            rep_icon(it, fid)
            it.setData(0, Qt.UserRole, ("later_group", cl))
            it.setData(0, Qt.UserRole + 2, i + 1)
            parent.addChild(it)
        other = c.execute(f"""SELECT COUNT(*) FROM faces f WHERE {self.LATER_FACE} AND f.person_id IS NULL
                              AND f.later_cluster IS NULL""").fetchone()[0]
        nof = c.execute(f"""SELECT COUNT(*) FROM photos ph WHERE ph.sort_later=1 AND NOT EXISTS
                            (SELECT 1 FROM faces f WHERE f.photo_id=ph.id AND {self.LATER_FACE})""").fetchone()[0]
        st = self.style()
        for n, text, key, icon in ((other, f"Other faces   {other}", ("later_other", None), QStyle.SP_MessageBoxQuestion),
                                   (nof, f"No faces   {nof}", ("later_nofaces", None), QStyle.SP_FileIcon)):
            if n:
                it = QTreeWidgetItem([text])
                it.setIcon(0, std_icon(icon))
                it.setData(0, Qt.UserRole, key)
                parent.addChild(it)
        parent.setExpanded(("later", None) not in self._cat_collapsed)

    def iter_items(self, section=None):
        """Every row below the section headings (any depth)."""
        def walk(it):
            for j in range(it.childCount()):
                ch = it.child(j)
                yield ch
                yield from walk(ch)
        for top in self.top_items():
            if section is None or top.text(0).split(" (")[0] == section:
                yield from walk(top)

    def top_items(self):
        for t in self.trees:
            for i in range(t.topLevelItemCount()):
                yield t.topLevelItem(i)

    def current_item(self):
        return self._active_tree.currentItem()

    def select_item(self, it, switch=True):
        """Select one row in whichever list it's in (and nothing in the other lists). switch: show
        the tab it's in (not when the lists are only being refreshed)."""
        if it is None:
            return
        t = it.treeWidget()
        for other in self.trees:
            if other is not t:
                other.blockSignals(True)
                other.clearSelection()
                other.setCurrentItem(None)
                other.blockSignals(False)
        self._active_tree = t
        if switch and hasattr(self, "side_tabs") and t in self.trees and \
                self.side_tabs.currentIndex() != self.trees.index(t):
            self.side_tabs.blockSignals(True)
            self.side_tabs.setCurrentIndex(self.trees.index(t))
            self.side_stack.setCurrentIndex(self.trees.index(t))
            self.settings["side_tab"] = self.trees.index(t)
            self.side_tabs.blockSignals(False)
        t.setCurrentItem(it)
        if not it.isSelected():
            t.clearSelection()
            it.setSelected(True)

    def find_tree_item(self, key, section=None):
        """The row for key - in the tab that's showing if it's there (a folder is in both People
        and Folders), else wherever it is."""
        key = tuple(key)
        found = [it for it in self.iter_items(section) if key_of(it) == key] if section else []
        if not found:
            found = [it for it in self.iter_items() if key_of(it) == key]
        if not found:
            return None
        shown = self.trees[self.side_tabs.currentIndex()] if hasattr(self, "side_tabs") else None
        return next((it for it in found if it.treeWidget() is shown), found[0])

    def side_tab_changed(self, i):
        """People / Folders / Albums / Smart groups."""
        self.side_stack.setCurrentIndex(i)
        self.settings["side_tab"] = i
        if not hasattr(self, "view") or not self.trees[i].topLevelItemCount():
            return
        # what's open in the gallery stays; it's picked in this tab too when it's there
        t = self.trees[i]
        it = next((x for x in self.iter_items() if x.treeWidget() is t and key_of(x) == tuple(self.view)), None) \
            if self.view and self.view[0] != "multi" else None
        for other in self.trees:
            other.blockSignals(True)
            if other is not t:
                other.clearSelection()
            other.blockSignals(False)
        if it is not None:
            t.blockSignals(True)
            t.setCurrentItem(it)
            it.setSelected(True)
            t.scrollToItem(it)
            t.blockSignals(False)
            self._active_tree = t

    def find_group_for_anchor(self):
        for fid in self.anchor[:20]:
            r = self.conn.execute("SELECT cluster FROM faces WHERE id=? AND person_id IS NULL", (fid,)).fetchone()
            if r and r[0] is not None:
                it = self.find_tree_item(("group", r[0]))
                if it:
                    return it
        return None

    def first_selectable(self):
        prefer = ("group", "person", "unknown", "partial")
        items = list(self.iter_items())
        for kind in prefer:
            for it in items:
                k = key_of(it)
                if k and k[0] == kind:
                    return it
        return None

    def filter_tree(self, text):
        t = text.strip().lower()

        def walk(it):
            shown = False
            for j in range(it.childCount()):
                ch = it.child(j)
                sub = walk(ch)
                name = ch.data(0, Qt.UserRole + 1)
                vis = (not t or sub or (name is not None and t in name)) and not ch.data(0, Qt.UserRole + 11)
                ch.setHidden(not vis)
                shown = shown or vis
            return shown
        for top in self.top_items():
            walk(top)

    def leave_viewer_for_list(self):
        """You picked something else on the left while a photo was open: back to the gallery
        (it then shows what you picked)."""
        if self.viewer is not None:
            self.close_viewer(refresh=False)
            self.refresh_timer.start()        # counts catch up a moment later
        if getattr(self, "review", None) is not None:
            self.close_review()

    def tree_changed(self, cur, _prev, tree=None):
        if cur is None:
            return
        self.leave_viewer_for_list()
        tree = tree or cur.treeWidget()
        self._active_tree = tree
        for other in self.trees:              # one list at a time
            if other is not tree and other.selectedItems():
                other.blockSignals(True)
                other.clearSelection()
                other.setCurrentItem(None)
                other.blockSignals(False)
        self._schedule_view_sync()

    def tree_selection_changed(self, tree):
        if tree.selectedItems():
            self.leave_viewer_for_list()
            self._active_tree = tree
        self._schedule_view_sync()

    def _schedule_view_sync(self):
        if not getattr(self, "_sync_pending", False):
            self._sync_pending = True
            QTimer.singleShot(0, self._sync_view)

    def _sync_view(self):
        """Show what's selected on the left: one row, or several together (Ctrl+click / drag)."""
        self._sync_pending = False
        t = self._active_tree
        keys = t.selected_keys()
        if len(keys) > 1:
            view = ("multi", tuple(keys))
        else:
            k = keys[0] if keys else key_of(t.currentItem())
            if not k:
                return
            view = k
        if view != self.view:
            self.view = view
            self.refresh_grid(keep_selection=False)

    def ignore_all_shown(self):
        """Men / Unsure: ignore every face shown here (asked first). Undo: Smart Groups > Ignored."""
        ids = [r[0] for r in self.model.rows if r[0] > 0]
        if not ids:
            return
        part = {"men": "Men", "unsure": "Unsure"}.get(self.view[1], "")
        if QMessageBox.question(self, "Ignore all", f"Ignore all {len(ids):,} face{'s' if len(ids) != 1 else ''} in "
                                f"{self.title.text()}?\n\nThey're set aside as strangers (not suggested or grouped). "
                                "You can bring any of them back from Ignored.",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        store.set_ignored(self.conn, ids, True)
        self.statusBar().showMessage(f"✓ {len(ids):,} face{'s' if len(ids) != 1 else ''} ignored ({part})", 8000)
        self.quick_change(regroup=False, drop=ids)

    def shoot_rows(self):
        """Same shoot: one section per suggested person. A row is the person's face (or, with no face
        to see, the photo - several people without a face on one photo share its row); self._shoot
        maps each row's id to [(body, reason, similarity, name)]."""
        self._shoot = {}
        out = self.conn.execute(f"""SELECT s.body_id, s.person_id, p.name, s.reason, s.score, b.face_id, b.photo_id,
                                          ph.path FROM body_suggestions s JOIN bodies b ON b.id=s.body_id
                                   JOIN photos ph ON ph.id=b.photo_id JOIN persons p ON p.id=s.person_id
                                   WHERE ph.sort_later=0 AND NOT {self.sorted_photo_sql()}
                                   ORDER BY p.name COLLATE NOCASE, s.score DESC""").fetchall()
        sections = {}                        # person -> (name, [(row id, path)])
        for bid, person, name, reason, score, fid, photo, path in out:
            rid = fid if fid else -photo
            if rid not in self._shoot:
                sections.setdefault(person, (name, []))[1].append((rid, path))
            self._shoot.setdefault(rid, []).append((bid, reason, score, name))
        rows = []
        for person, (name, items) in sections.items():
            rows.append((0, f"{name}   ·   {len(items):,}", None, 1))
            rows += [(rid, path, " / ".join(v[3] for v in self._shoot[rid]), 0) for rid, path in items]
        return rows

    def sel_shoot_bodies(self):
        if self.view[0] != "shoot":
            return []
        info = getattr(self, "_shoot", {})
        return [v[0] for k in self.selected_ids() if k in info for v in info[k]]

    def confirm_all_shoot(self):
        info = getattr(self, "_shoot", {})
        n = sum(len(v) for v in info.values())
        if info and QMessageBox.question(
                self, "Same shoot", f"Confirm all {n:,} same-shoot suggestion{'s' if n != 1 else ''} shown here?\n"
                "Faces get the name; people with no face showing get the photo tagged.") == QMessageBox.Yes:
            bodies.confirm(self.conn, [v[0] for vs in info.values() for v in vs])
            self.after_change()

    def refresh_grid(self, keep_selection=True):
        """keep_selection=True: same view refreshed after an action - keep the scroll position
        and selection. False: a different view was opened - start at the top."""
        kind, ident = self.view
        c = self.conn
        sel = set(self.selected_ids()) if keep_selection else set()
        sel_rows = sorted(i.row() for i in self.grid.selectionModel().selectedIndexes()) if keep_selection else []
        scroll = self.grid.verticalScrollBar().value() if keep_selection else 0
        base = """SELECT f.id, ph.path, p.name, f.confirmed FROM faces f JOIN photos ph ON ph.id=f.photo_id
                  LEFT JOIN persons p ON p.id=f.person_id """
        if kind == "person":
            rows = c.execute(base + "WHERE f.person_id=? AND f.ignored=0 ORDER BY f.confirmed, " + store.clear_order() + " DESC",
                             (ident,)).fetchall()
            name = c.execute("SELECT name FROM persons WHERE id=?", (ident,)).fetchone()
            name = name[0] if name else "?"
            sugg = sum(1 for r in rows if not r[3])
            nface = len(rows)
            tagged = c.execute("""SELECT -ph.id, ph.path, 'tagged on photo', 1 FROM photo_people pp
                                  JOIN photos ph ON ph.id=pp.photo_id WHERE pp.person_id=? AND ph.sort_later=0
                                  AND NOT EXISTS (SELECT 1 FROM faces f WHERE f.photo_id=pp.photo_id
                                                  AND f.person_id=pp.person_id AND f.ignored=0)
                                  ORDER BY ph.path""", (ident,)).fetchall()
            tsug = c.execute("""SELECT -ph.id, ph.path, 'tattoo match', 0 FROM tattoo_suggestions ts
                                JOIN photos ph ON ph.id=ts.photo_id WHERE ts.person_id=? AND ph.sort_later=0
                                ORDER BY ts.inliers DESC""", (ident,)).fetchall()
            pcat = c.execute("SELECT category_id FROM persons WHERE id=?", (ident,)).fetchone()
            pcat = pcat[0] if pcat else None
            nmarks = c.execute("SELECT COUNT(*) FROM tattoos WHERE person_id=? AND src='mark'", (ident,)).fetchone()[0]
            # everything still to confirm goes to the top (whatever folder it's in); once confirmed
            # it drops back into its own folder's section
            every = rows + tsug + tagged
            pending = [r for r in every if r[0] and not r[3]]
            done = [r for r in every if not (r[0] and not r[3])]
            rows = self.person_sections(ident, done)
            if pending:
                if rows and rows[0][0] != 0:
                    rows = [(0, f"Confirmed   ·   {len(rows):,}", None, 1)] + rows
                rows = [(0, f"To confirm   ·   {len(pending):,}", ("pending",), 1)] + pending + rows
            self.title.setText(name)
            self.subtitle.setText(f"{nface - sugg} confirmed face{'s' if nface - sugg != 1 else ''}"
                                  + (f" · {sugg} suggestion{'s' if sugg != 1 else ''} to check" if sugg else "")
                                  + (f" · {len(tagged)} photo{'s' if len(tagged) != 1 else ''} tagged without a face"
                                     if tagged else "")
                                  + (f" · {nmarks} tattoo{'s' if nmarks != 1 else ''} marked" if nmarks else "")
                                  + (f" · {len(tsug)} tattoo match{'es' if len(tsug) != 1 else ''} to check" if tsug else "")
                                  + (" · saved under " + " › ".join(store.category_paths(c).get(pcat, []))
                                     if pcat else ""))
            self.set_buttons([("Rename…", self.rename_person),
                              (("Confirm suggestion" if sugg == 1 else f"Confirm all {sugg} suggestions"), self.confirm_all) if sugg else None,
                              (f"Confirm {len(tsug)} tattoo match{'es' if len(tsug) != 1 else ''}",
                               self.confirm_all_tattoos) if tsug else None,
                              ("Find tattoo matches", lambda: self.start_tattoo_search([ident])) if nmarks else None,
                              ("Set folder…", self.act_category),
                              ("Delete person", self.delete_person)])
        elif kind == "group":
            rows = c.execute(base + "WHERE f.cluster=? AND f.person_id IS NULL AND f.ignored=0 ORDER BY " + store.clear_order() + " DESC",
                             (ident,)).fetchall()
            it = self.find_tree_item(("group", ident))
            num = it.data(0, Qt.UserRole + 2) if it else ""
            self.title.setText(f"Group {num}")
            folder = store.group_folder(c, [r[0] for r in rows], self.unnamed_counts()) if self.settings.get("folder_hints", True) else None
            self.subtitle.setText(f"{len(rows)} faces that look like the same person"
                                  + (f" · matches your folder “{folder}” (suggested as the name)" if folder else ""))
            self.set_buttons([("Name this group…", self.name_group, True),
                              ("Ignore group", self.ignore_group)])
        elif kind in ("unknown", "partial"):
            if ident not in (None, "men", "unsure"):
                ident = None                          # (older versions' "sorted" / "unsorted" parts)
                self.view = (kind, None)
            rows = c.execute(base + "WHERE " + self.smart_faces_sql(kind, ident) +
                             " ORDER BY " + store.clear_order() + " DESC LIMIT 5000").fetchall()
            top = "Unknown faces" if kind == "unknown" else "Partially tagged"
            self.title.setText(top + {None: "", "men": " · Men", "unsure": " · Unsure"}[ident])
            if kind == "unknown":
                what = "Faces that didn't match anyone, on photos that aren't sorted yet (nobody tagged, no folder)"
            else:
                what = "Faces nobody is named for yet, on group photos where someone else is already tagged"
            self.subtitle.setText(what + {None: "", "men": " - the ones that look male (an estimate)",
                                          "unsure": " - the ones where it's hard to tell"}[ident])
            n = sum(1 for r in rows if r[0] > 0)
            self.set_buttons([(f"Ignore all {n:,}…", self.ignore_all_shown)] if ident and n else [])
        elif kind in ("notfaces", "maybe_notfaces"):
            rows = c.execute(base + "WHERE f.ignored=? ORDER BY f.id DESC LIMIT 5000",
                             (3 if kind == "notfaces" else 4,)).fetchall()
            if kind == "notfaces":
                self.title.setText("Not faces")
                self.subtitle.setText("Things you marked with N (knees, elbows, patterns…). Lookalikes are set aside "
                                      "automatically under “Probably not faces”.")
                self.set_buttons([("It's a face - restore", self.act_isface)])
            else:
                self.title.setText("Probably not faces")
                self.subtitle.setText("These look like things you marked “not a face”, so they were set aside. "
                                      "N = not a face · Restore = it is a face (never set aside again).")
                self.set_buttons([("Not faces - confirm all", self.act_notface_all, True),
                                  ("It's a face - restore", self.act_isface)])
        elif kind == "ignored":
            rows = c.execute(base + f"WHERE f.ignored=1 AND NOT {HAS_PERSON('f.photo_id')} "
                                    "ORDER BY f.id DESC LIMIT 5000").fetchall()
            self.title.setText("Ignored")
            self.subtitle.setText("Strangers and people you don't want to sort. Photos drop off this list once "
                                  "someone in them is named or tagged. (Use X for things that aren't faces.)")
            self.set_buttons([("Restore selected", self.act_unignore)])
        elif kind == "discarded":
            rows = c.execute(base + "WHERE f.ignored=2 AND ph.sort_later=0 ORDER BY ph.path").fetchall()
            rows += c.execute("SELECT -id, path, NULL, 1 FROM photos WHERE discarded=1 AND sort_later=0 "
                              "ORDER BY path").fetchall()
            self.title.setText("~To Be Discarded")
            n = len({r[1] for r in rows})
            self.subtitle.setText(f"{n} photo{'s' if n != 1 else ''} you marked with D. "
                                  "They are never matched again, and 'Sort into folders' puts them in "
                                  "a '~To Be Discarded' folder.")
            self.set_buttons([("Restore selected", self.act_undiscard)])
        elif kind == "shoot":
            rows = self.shoot_rows()
            n = sum(1 for r in rows if r[0])
            self.title.setText("Same shoot")
            self.subtitle.setText(f"{n:,} photo{'' if n == 1 else 's'} with someone whose face couldn't be recognised, "
                                  "wearing the same clothes as someone you confirmed at the same occasion. "
                                  "C = yes, it's them · R = not them · point at one to see why.")
            self.set_buttons([("Confirm all…", self.confirm_all_shoot) if n else None])
        elif kind == "tattoos":
            rows = c.execute(f"""SELECT -ph.id, ph.path, GROUP_CONCAT(p.name || '?', ', '), 0 FROM tattoo_suggestions ts
                                JOIN photos ph ON ph.id=ts.photo_id JOIN persons p ON p.id=ts.person_id
                                WHERE ph.sort_later=0 AND NOT {self.sorted_photo_sql()}
                                GROUP BY ph.id ORDER BY MAX(ts.inliers) DESC LIMIT 10000""").fetchall()
            nmarks = c.execute("SELECT COUNT(*) FROM tattoos WHERE src='mark'").fetchone()[0]
            self.title.setText("Tattoo matches")
            self.subtitle.setText(f"{len(rows):,} photo{'s' if len(rows) != 1 else ''} where a marked tattoo seems to "
                                  f"appear ({nmarks} tattoo{'s' if nmarks != 1 else ''} marked). "
                                  "C = yes, it's them · R = not them · double-click to see where.")
            self.set_buttons([("Find tattoo matches", self.start_tattoo_search, True),
                              ("Confirm all…", self.confirm_all_tattoos) if rows else None])
        elif kind == "category":
            # everything saved under this category (and the categories inside it)
            inside = {ident} | store.category_descendants(c, ident)
            rows, pids = self.sectioned_rows(ident)
            self.title.setText(store.folder_label(c, ident) or "?")
            nobody = len({r[0] for r in rows if r[0] and not r[2]})
            nphotos = len(pids)
            nplaced = c.execute(f"SELECT COUNT(*) FROM persons WHERE category_id IN ({','.join(map(str, inside))})"
                                ).fetchone()[0]
            self.subtitle.setText(f"Folder · {nphotos:,} photo{'s' if nphotos != 1 else ''}"
                                  + (f" ({nobody:,} with no one tagged)" if nobody else "")
                                  + (f" · {nplaced} {'person' if nplaced == 1 else 'people'} placed here"
                                     if nplaced else ""))
            self.set_buttons([("Rename / merge…", self.rename_category),
                              ("New subfolder…", lambda: self.new_category(ident)),
                              ("Delete folder", self.delete_category)])
        elif kind == "sortlater":
            rows = c.execute(f"""SELECT -ph.id, ph.path, {PEOPLE_LABEL_ALL}, 1 FROM photos ph
                                 WHERE ph.sort_later=1 ORDER BY ph.path LIMIT 20000""").fetchall()
            self.title.setText("Sort Later")
            self.subtitle.setText(f"{len(rows):,} photo{'s' if len(rows) != 1 else ''} set aside with L. They're out of "
                                  "every other list until you take them out; 'Sort into folders' puts them in "
                                  "a '~Sort Later' folder.")
            self.set_buttons([("Take out of Sort Later", self.act_sort_later, True),
                              ("Take all out…", self.sort_later_all_out) if rows else None])
        elif kind == "multi":
            rows = []
            labels = {}
            for it in self.iter_items():
                k = key_of(it)
                if k:
                    labels.setdefault(k, it.text(0).split("   ")[0])
            saved = self.view
            self._dry = True
            try:
                for k in ident:
                    self.view = tuple(k)
                    self._dry_rows = []
                    self.refresh_grid(keep_selection=False)
                    part = [r for r in self._dry_rows if r[0]]
                    rows.append((0, f"{labels.get(tuple(k), k[0])}   ·   {len(part):,}", None, 1))
                    rows += part
            finally:
                self._dry = False
                self.view = saved
            kinds = {k[0] for k in ident}
            self.title.setText(f"{len(ident)} selected")
            self.subtitle.setText("Ctrl+click or drag on the left to pick more; the folder bar beside the gallery jumps between them.")
            if kinds <= {"group", "later_group"}:
                self.set_buttons([("Name as one person…", self.name_group, True), ("Ignore all", self.ignore_group)])
            elif kinds == {"person"}:
                self.set_buttons([("Confirm all suggestions", lambda: [self.confirm_person(k[1]) for k in ident])])
            else:
                self.set_buttons([])
        elif kind == "search":
            text, hits = getattr(self, "_search", ("", []))
            order = {pid: i for i, (pid, _sc) in enumerate(hits)}
            got = self.photo_rows(set(order), limit=len(order) + 1)
            rows = sorted(got, key=lambda r: order.get(-r[0], 1 << 30))
            have, total = clip_search.index_counts(c)
            self.title.setText(f"Search · “{text}”")
            self.subtitle.setText(f"{len(rows):,} photo{'s' if len(rows) != 1 else ''}, best match first"
                                  + (f" · {have:,} of {total:,} photos searchable so far (the rest are being prepared)"
                                     if have < total else "")
                                  + ". Found by what's in the picture - no tags needed.")
            self.set_buttons([])
        elif kind == "album":
            row = c.execute("SELECT name FROM albums WHERE id=?", (ident,)).fetchone()
            ids = {r[0] for r in c.execute("""SELECT ap.photo_id FROM album_photos ap JOIN photos ph ON ph.id=ap.photo_id
                                              WHERE ap.album_id=? AND COALESCE(ph.sort_later, 0) != 2
                                              AND ph.error IS NULL""", (ident,))}
            rows = self.photo_rows(ids, limit=50000)
            self.title.setText(f"Album · {row[0] if row else '?'}")
            self.subtitle.setText(f"{len(rows):,} photo{'s' if len(rows) != 1 else ''}. Albums never move a file - a photo can "
                                  "be in several. Drag photos onto an album to add them; right-click > Remove from album.")
            self.set_buttons([])
        elif kind == "vault":
            rows = []
            name = c.execute("SELECT name FROM categories WHERE id=?", (ident,)).fetchone()
            self.title.setText(f"{name[0] if name else 'Vault'}   (locked)")
            self.subtitle.setText("Its photos, people and folders are hidden everywhere in FaceSort until you unlock it. "
                                  "The files themselves are still in their folders on disk.")
            self.set_buttons([("Unlock…", lambda: self.unlock_vault(ident), True)])
        elif kind == "dropped":
            rows, pids = self.dropped_rows(ident)
            name = c.execute("SELECT name FROM drop_batches WHERE batch=?", (ident,)).fetchone()
            self.title.setText(f"Recently dropped · {name[0] if name else ''}")
            self.subtitle.setText(f"{len(pids):,} photo{'s' if len(pids) != 1 else ''}, shown the way they'll be saved "
                                  "(folder › person › folder); other files that came with them are listed at the "
                                  "bottom. Fix anything that's wrong, then Done checking - the next Sort into "
                                  "folders files them all.")
            self.set_buttons([("Done checking", lambda: self.done_checking(ident), True)])
        elif kind == "ai":
            cond = "ph.ai>0" if ident is None else f"ph.ai={int(ident)}"
            rows = c.execute(f"""SELECT -ph.id, ph.path, ph.ai_info, 1 FROM photos ph WHERE {cond} AND ph.error IS NULL
                                 ORDER BY ph.ai, ph.path LIMIT 20000""").fetchall()
            self.title.setText({None: "AI images", 1: "Marked as AI", 2: "Possibly AI"}[ident])
            self.subtitle.setText(f"{len(rows):,} photo{'s' if len(rows) != 1 else ''}. "
                                  + ("“Marked as AI”: the file itself says so (reliable). " if ident != 2 else "")
                                  + ("“Possibly AI”: the detector's guess - often wrong, check them yourself." if ident != 1 else ""))
            self.set_buttons([("Not AI (take off the list)", self.act_not_ai)])
        elif kind == "misplaced":
            mis = getattr(self, "_misplaced", None) or []
            dest = self.library_dest() or ""
            live = dict(c.execute("SELECT id, path FROM photos"))
            groups, untagged = {}, []
            for pid, path, goes in mis:
                if pid not in live:
                    continue
                if goes is None:
                    untagged.append((pid, live[pid]))
                else:
                    groups.setdefault(os.path.dirname(live[pid]), []).append((pid, live[pid], goes))

            def shown(folder):
                return os.path.relpath(folder, dest) if dest and folder.lower().startswith(dest.lower()) else folder
            rows = []
            for i, (folder, items) in enumerate(sorted(groups.items(), key=lambda kv: kv[0].casefold())):
                rows.append((0, f"In  {shown(folder)}", ("misplaced", i), 1))
                for pid, path, goes in sorted(items, key=lambda r: r[1].casefold()):
                    rows.append((-pid, path, "goes to " + goes.replace("/", "\\"), 1))
            if untagged:
                rows.append((0, f"Not tagged any more  ·  {len(untagged)} - tag them (or D to discard), then Put them back",
                             ("misplaced", -1), 1))
                for pid, path in sorted(untagged, key=lambda r: r[1].casefold()):
                    rows.append((-pid, path, "in " + shown(os.path.dirname(path)), 1))
            nmove = sum(len(v) for v in groups.values())
            self.title.setText("Not where they belong")
            self.subtitle.setText(f"In your sorted library ({dest or 'no destination set'}): {nmove:,} photo"
                                  f"{'s' if nmove != 1 else ''} in a different folder than their tags say"
                                  + (f", and {len(untagged):,} that aren't tagged any more" if untagged else "")
                                  + ". Put them back moves just these (nothing else in the library is touched); "
                                  "empty folders left behind go to '~To Be Discarded'.")
            self.set_buttons([("Put them back", self.fix_misplaced, True)] if nmove else [])
        elif kind == "dupes":
            sets = self.dup_sets()
            rows = []
            want = [p for d in sets for p in d["ids"]]
            meta = {}
            for i0 in range(0, len(want), 900):          # just the photos in the sets
                part = want[i0:i0 + 900]
                meta.update({r[0]: r for r in c.execute(
                    f"SELECT id, path, width, height, size FROM photos WHERE id IN ({','.join('?' * len(part))})", part)})
            sets = [d for d in sets if all(p in meta for p in d["ids"])]
            for i, d in enumerate(sets, 1):
                rows.append((0, f"Set {i}  ·  {len(d['ids'])} photos  ·  {'exact copies' if d['exact'] else 'same picture'}",
                             ("dup", i - 1), 1))
                for pid in sorted(d["ids"], key=lambda x: x != d["best"]):
                    _, path, w_, h_, sz = meta[pid]
                    lab = ("Keep · " if pid == d["best"] else "") + f"{w_ or '?'}×{h_ or '?'} · {(sz or 0) / 1e6:.1f} MB"
                    rows.append((-pid, path, lab, 1))
            nphotos = sum(len(d["ids"]) for d in sets)
            self.title.setText("Duplicates")
            self.subtitle.setText(f"{len(sets):,} set{'s' if len(sets) != 1 else ''} · {nphotos:,} photos. “Keep” marks the "
                                  "best copy (largest picture). D discards a copy; right-click for more.")
            self.set_buttons([("Keep the best of every set…", self.keep_best_all, True)] if sets else [])
        elif kind == "bucket":
            mode, _, key = ident.partition("|")
            b = store.photo_buckets(c, mode).get(key, ["", 0, []])
            pids = b[2]
            rows = []
            for i in range(0, len(pids), 900):
                chunk = pids[i:i + 900]
                q = ",".join("?" * len(chunk))
                rows += c.execute(base + f"WHERE f.photo_id IN ({q}) AND f.ignored=0 AND f.person_id IS NULL", chunk).fetchall()
                rows += c.execute(f"""SELECT -ph.id, ph.path, NULL, 1 FROM photos ph WHERE ph.id IN ({q})
                                      AND ph.nfaces=0""", chunk).fetchall()
            rows.sort(key=lambda r: r[1])
            self.title.setText(b[0] or "?")
            self.subtitle.setText(f"{len(pids):,} photo{'s' if len(pids) != 1 else ''} still to sort"
                                  + (f" · {key}" if mode == "location" else ""))
            self.set_buttons([])
        elif kind in ("later_person", "later_group", "later_other", "later_nofaces"):
            lf = self.LATER_FACE
            if kind == "later_person":
                rows = c.execute(base + f"WHERE {lf} AND f.person_id=? ORDER BY f.confirmed, " + store.clear_order() + " DESC",
                                 (ident,)).fetchall()
                who = c.execute("SELECT name FROM persons WHERE id=?", (ident,)).fetchone()
                title, sub = f"Sort Later › {who[0] if who else '?'}", "their faces in photos set aside in Sort Later"
                buttons = [("Confirm all", self.confirm_all) if any(not r[3] for r in rows) else None]
            elif kind == "later_group":
                rows = c.execute(base + f"WHERE {lf} AND f.person_id IS NULL AND f.later_cluster=? "
                                        "ORDER BY " + store.clear_order() + " DESC", (ident,)).fetchall()
                it = self.find_tree_item(("later_group", ident))
                num = it.data(0, Qt.UserRole + 2) if it else ""
                title, sub = f"Sort Later › Group {num}", "faces that look like the same person"
                buttons = [("Name this group…", self.name_group, True)]
            elif kind == "later_other":
                rows = c.execute(base + f"WHERE {lf} AND f.person_id IS NULL AND f.later_cluster IS NULL "
                                        "ORDER BY " + store.clear_order() + " DESC LIMIT 5000").fetchall()
                title, sub = "Sort Later › Other faces", "faces that didn't match anyone else in Sort Later"
                buttons = []
            else:
                rows = c.execute(f"""SELECT -ph.id, ph.path, {PEOPLE_LABEL_ALL}, 1 FROM photos ph WHERE ph.sort_later=1
                                     AND NOT EXISTS (SELECT 1 FROM faces f WHERE f.photo_id=ph.id AND {lf})
                                     ORDER BY ph.path""").fetchall()
                title, sub = "Sort Later › No faces", "photos in Sort Later with no face found"
                buttons = []
            self.title.setText(title)
            self.subtitle.setText(f"{len(rows):,} · {sub}. Naming and sorting here keeps the photos in Sort Later "
                                  "until you take them out.")
            self.set_buttons(buttons + [("Take out of Sort Later", self.act_sort_later),
                                        ("Take all shown out", lambda: self.later_out_shown())])
        elif kind == "nocategory":
            rows, unc = self.sectioned_rows(None)
            self.title.setText("No folder")
            self.subtitle.setText(f"{len(unc):,} photo{'s' if len(unc) != 1 else ''} with people but no folder "
                                  "(sorted into “~Uncategorized”). Select photos and press A to set one.")
            self.set_buttons([("Set folder…", self.act_category, True)])
        elif kind == "unassoc":
            rows = c.execute(f"""SELECT -ph.id, ph.path, NULL, 1 FROM photos ph WHERE {UNASSOC_WHERE}
                                 ORDER BY ph.path""").fetchall()
            self.title.setText("Unassociated files")
            self.subtitle.setText(f"{len(rows):,} other file{'s' if len(rows) != 1 else ''} (documents, music, "
                                  "archives…) not tagged with anyone and not in a folder. Select files and press Enter "
                                  "to tag people, A to set a folder (or drag them onto one) - Sort into folders then "
                                  "moves them with the photos. Double-click opens one.")
            self.set_buttons([("Tag people…", self.act_tag, True), ("Set folder…", self.act_category, True)])
        elif kind == "nofaces":
            rows = c.execute(f"""SELECT -ph.id, ph.path, NULL, 1 FROM photos ph WHERE {self.nofaces_sql()}
                                 ORDER BY ph.path LIMIT 5000""").fetchall()
            self.title.setText("No faces found")
            self.subtitle.setText(f"{len(rows):,} photo{'s' if len(rows) != 1 else ''} where no face was detected "
                                  "and no one is tagged yet. Select photos and press Enter to tag who's in them (T to draw a face box) - "
                                  "they then move to that person.")
            self.set_buttons([("Tag people…", self.act_tag, True),
                              ("Look again for faces", self.rescan_nofaces)])
        elif kind == "errors":
            rows = []
            errs = c.execute("SELECT path, error FROM photos WHERE error IS NOT NULL ORDER BY path").fetchall()
            self.title.setText("Unreadable files")
            self.subtitle.setText(f"{len(errs):,} file{'s' if len(errs) != 1 else ''} couldn't be opened "
                                  "(damaged, or not really an image). Double-click one to show it in its folder.")
            self.err_list.clear()
            for path, err in errs:
                err = store.friendly_error(err, path)  # also tidies messages saved by older versions
                it = QTreeWidgetItem([os.path.basename(path), err])
                it.setToolTip(0, path)
                it.setToolTip(1, str(err))
                it.setData(0, Qt.UserRole, path)
                self.err_list.addTopLevelItem(it)
            self.set_buttons([("Try again", self.retry_unreadable)])
        else:
            rows = []
        self.center.setCurrentWidget(self.err_list if kind == "errors" else self.grid)
        if kind == "dropped":
            self.fill_extra_files(ident)
        else:
            self.extra_box.hide()
        self.zoom_row.setVisible(kind != "errors")
        if kind in self.smart_kinds() and kind not in ("dupes", "errors", "shoot"):
            rows = self.split_by_folder(rows)  # Smart Groups: one section per folder path
        if kind != "search":                # (search results stay best match first)
            rows = self.sort_rows(rows)
        if getattr(self, "_dry", False):
            self._dry_rows = rows
            return
        rows = self.media_filter_rows(rows)
        self._gallery_pids = None
        rows = self.gallery_dupes_rows(rows, kind)
        if kind != "dupes" and not (getattr(self, "dupes_btn", None) and self.dupes_btn.isChecked()
                                    and self.dupes_btn.isEnabled()):
            rows = self.hide_copies_rows(rows)       # exact copies (e.g. one per person's folder): shown once
        else:
            self._copies = {}
        if self.viewer_mode and kind not in ("category", "nocategory"):
            # Viewer: one tile per photo (the first face of each photo stands for it)
            seen = set()
            rows = [r for r in rows if not r[0] or (r[1] not in seen and not seen.add(r[1]))]
        self.model.set_rows(rows)
        self.apply_grid_layout()
        self.build_folder_bar()
        self.anchor = [r[0] for r in rows[:20]] if kind == "group" else []
        self.grid.doItemsLayout()                       # lay out now so the old position exists again
        self.grid.verticalScrollBar().setValue(scroll)
        sm = self.grid.selectionModel()
        kept = False
        if sel:
            for i, r in enumerate(rows):
                if r[0] in sel:
                    sm.select(self.model.index(i), QItemSelectionModel.SelectionFlag.Select)
                    kept = True
            if not kept and rows and sel_rows:
                # what you selected left this list (e.g. tagged): select the next one in line,
                # so you can keep going with the keyboard
                at = min(sel_rows[0], len(rows) - 1)
                real = [i for i, r in enumerate(rows) if r[0]]
                if real:
                    at = next((i for i in real if i >= at), real[-1])
                nxt = self.model.index(at)
                sm.setCurrentIndex(nxt, QItemSelectionModel.SelectionFlag.ClearAndSelect)
                self.grid.verticalScrollBar().setValue(scroll)  # don't let the new selection jump the view
        self.update_hint()

    def set_buttons(self, specs):
        while self.btn_row.count():
            w = self.btn_row.takeAt(0).widget()
            if w:
                w.hide()            # gone right away, not just when Qt gets round to deleting it
                w.setParent(None)
                w.deleteLater()
        for spec in specs:
            if not spec:
                continue
            b = tool_button(spec[0], button_icon(spec[0]), spec[0])
            b.clicked.connect(spec[1])
            if len(spec) > 2 and spec[2]:
                f = b.font()
                f.setBold(True)
                b.setFont(f)
            self.btn_row.addWidget(b)
        QTimer.singleShot(0, self.fit_tool_row)

    def fit_tool_row(self):
        """Labels on the gallery buttons while they fit; icons only (in steps) when they don't."""
        row = getattr(self, "zoom_row", None)
        if row is None:
            return
        groups = [[self.btn_row.itemAt(i).widget() for i in range(self.btn_row.count())
                   if self.btn_row.itemAt(i).widget()],
                  list(self.mode_group.buttons()), list(self.media_group.buttons()) + [self.dupes_btn],
                  [self.details_btn]]
        for g in groups:
            for b in g:
                b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        room = row.width()
        lay = row.layout()

        def needed():
            total, n = 0, 0

            def walk(layout):
                nonlocal total, n
                for i in range(layout.count()):
                    it = layout.itemAt(i)
                    if it.widget() is not None and it.widget().isVisibleTo(row):
                        wd = it.widget()
                        total += wd.minimumWidth() if wd is self.size_slider else wd.sizeHint().width()
                        n += 1
                    elif it.layout() is not None:
                        walk(it.layout())
            walk(lay)
            m = lay.contentsMargins()
            return total + max(0, n - 1) * lay.spacing() + m.left() + m.right() + 24
        for g in groups:
            if needed() <= room:
                break
            for b in g:
                b.setToolButtonStyle(Qt.ToolButtonIconOnly)

    def show_details(self, on):
        self.details.setVisible(on)
        self.settings["details_panel"] = bool(on)
        store.save_settings(self.settings)
        if on:
            self.update_details()

    def update_details(self):
        """Fill the details panel for the selected picture (or the first one shown)."""
        if not self.details.isVisible():
            return
        sel = [i for i in self.grid.selectionModel().selectedIndexes() if self.model.rows[i.row()][0]]
        row = self.model.rows[sel[0].row()] if sel else None
        self.details_top.clear()
        self.details_meta.clear()
        if row is None:
            QTreeWidgetItem(self.details_top, ["", "Select a picture to see its details"])
            return
        path = row[1]
        c = self.conn
        ph = c.execute("""SELECT id, size, width, height, taken, created, mtime, ai_info, is_video, nfaces, category_id
                          FROM photos WHERE path=?""", (path,)).fetchone()
        rows = [("Name", os.path.basename(path)), ("Folder", os.path.dirname(path))]
        if ph:
            pid, size, w, h, taken, created, mtime, ai_info, video, nfaces, cat = ph
            rows += [("Size", details._size(size)),
                     ("Dimensions", f"{w:,} × {h:,}  ({w * h / 1e6:.1f} MP)" if w and h else None),
                     ("Type", ("Video · " if video else "") + os.path.splitext(path)[1].lstrip(".").upper()),
                     ("Date taken", details._when(taken)), ("Date created", details._when(created)),
                     ("Date modified", details._when(mtime))]
            people = store.people_on_photo(c, pid)
            names = dict(c.execute("SELECT id, name FROM persons"))
            rows.append(("People", ", ".join(names.get(p, "?") for p in people)))
            rows.append(("Faces found", str(nfaces)))
            folders = ([store.folder_label(c, cat)] if cat else []) + [
                store.folder_label(c, f) for (f,) in c.execute("SELECT folder_id FROM photo_folders WHERE photo_id=?", (pid,))]
            rows.append(("In folder", ", ".join(folders)))
            rows.append(("AI", ai_info))
            d = next((d for d in self.dup_sets() if pid in d["ids"]), None)
            if d:
                rows.append(("Duplicates", f"{len(d['ids'])} copies" + (" (this is the best)" if pid == d["best"] else "")))
        for k, v in rows:
            if v:
                it = QTreeWidgetItem(self.details_top, [k, str(v)])
                it.setToolTip(1, str(v))
                it.setForeground(0, QBrush(QColor(LEGEND_GRAY)))
        self.details_top.setFixedHeight(min(320, 6 + sum(self.details_top.sizeHintForRow(i)
                                                          for i in range(self.details_top.topLevelItemCount()))))
        for k, v in details.metadata(path):
            it = QTreeWidgetItem(self.details_meta, [k, str(v)])
            it.setToolTip(1, str(v))
            it.setForeground(0, QBrush(QColor(LEGEND_GRAY)))
        if not self.details_meta.topLevelItemCount():
            QTreeWidgetItem(self.details_meta, ["", "No EXIF / metadata in this file"])

    def update_hint(self, *_):
        if self.details.isVisible():
            self.details_timer.start()
        sel = self.grid.selectionModel().selectedIndexes()
        n = len(sel)
        if n == 1 and 0 <= sel[0].row() < len(self.model.rows):
            self.path_label.setText(os.path.normpath(self.model.rows[sel[0].row()][1]))
            self.path_label.show()
        else:
            self.path_label.hide()
        kind = self.view[0]
        face_keys = ["[C]onfirm", "[R]emove Face", "[T]ag Person", "[D]iscard", "Sort [L]ater", "[N]ot a Face", "[I]gnore"]
        if kind == "person":
            items = ["[Enter] Rename"] + face_keys + ["Set C[a]tegory"]
        elif kind == "group":
            items = ["[Enter] Name", "[R]emove Face", "[T]ag Person", "[D]iscard", "Sort [L]ater", "[N]ot a Face", "[I]gnore"]
        elif kind in ("unknown", "partial"):
            items = ["[Enter] Name", "[T]ag Person", "[D]iscard", "Sort [L]ater", "[N]ot a Face", "[I]gnore"]
        elif kind in ("notfaces", "maybe_notfaces"):
            items = ["[N]ot a Face", "Restore button = it is a face"]
        elif kind in ("ignored", "discarded"):
            items = ["Select and click Restore to bring them back"]
        elif kind == "tattoos":
            items = ["[C]onfirm", "[R]emove Match", "[M]ark Tattoo in the preview"]
        elif kind == "shoot":
            items = ["[C]onfirm", "[R]emove Suggestion", "[Double-click] Open the photo"]
        elif kind in ("category", "nocategory"):
            items = ["Set C[a]tegory", "[T]ag Person", "[D]iscard", "Sort [L]ater"]
        elif kind == "sortlater":
            items = ["Sort [L]ater again = take out", "[D]iscard"]
        elif kind == "misplaced":
            items = ["[T]ag Person", "[D]iscard", "Sort [L]ater"]
        elif kind == "dupes":
            items = ["[D]iscard a copy", "[Right-click] Keep this copy / Not a duplicate", "[Double-click] Compare"]
        elif kind == "ai":
            items = ["[T]ag Person", "[D]iscard", "Sort [L]ater", "[Right-click] Not AI"]
        elif kind in ("bucket", "multi"):
            items = ["[Enter] Name", "[C]onfirm", "[R]emove Face", "[T]ag Person", "[D]iscard", "Sort [L]ater",
                     "[N]ot a Face", "[I]gnore"]
        elif kind in ("later_person", "later_group", "later_other"):
            items = ["[Enter] Name", "[C]onfirm", "[R]emove Face", "[T]ag Person", "[D]iscard",
                     "Sort [L]ater again = take out", "[N]ot a Face", "[I]gnore"]
        elif kind == "later_nofaces":
            items = ["[T]ag Person", "[Enter] Tag Whole Photo", "[D]iscard", "Sort [L]ater again = take out"]
        elif kind == "nofaces":
            items = ["[T]ag Person", "[Enter] Tag Whole Photo", "[R]emove Tags", "[D]iscard", "Sort [L]ater"]
        elif kind == "unassoc":
            items = ["[Enter] Tag People", "[A] Set Folder", "[D]iscard", "Sort [L]ater", "[Double-click] Open"]
        else:
            items = []
        if items and kind not in ("ignored", "discarded", "dupes", "unassoc"):
            items.append("[Double-click] Preview")
        h = legend_html(items)
        self.hint.setText((f"<b>{n} selected</b>&nbsp;&nbsp;&nbsp;" if n else "") + h)
