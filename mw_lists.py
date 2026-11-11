"""MainWindow, part: the left-hand lists (People / Folders / Albums / Smart Groups)."""
import time
import store
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont, QIcon
from PySide6.QtWidgets import QStyle, QTreeWidgetItem
from icons import app_icon
from ui_common import key_of, NOFACES_WHERE, ROLE_DROP, round_pixmap, std_icon, UNASSOC_WHERE


class ListsMixin:
    """Part of MainWindow (see facesort.py): the left-hand lists (People / Folders / Albums / Smart Groups)."""

    # ------------------------------------------------------------------ refresh
    def refresh_all(self, first=False):
        self._pid_cache = {}
        self._data_gen += 1     # the background jobs write through their own connection
        has_folders = bool(self.settings["folders"])
        self.stack.setCurrentIndex(1 if has_folders else 0)
        self.rebuild_tree()
        self.refresh_grid()
        self.refresh_stats()

    def refresh_stats(self):
        c = self.conn
        photos = c.execute("SELECT COUNT(*) FROM photos WHERE error IS NULL AND COALESCE(is_other, 0)=0").fetchone()[0]
        others = c.execute("SELECT COUNT(*) FROM photos WHERE error IS NULL AND is_other=1").fetchone()[0]
        faces = c.execute("SELECT COUNT(*) FROM faces WHERE ignored=0").fetchone()[0]
        named = c.execute("SELECT COUNT(*) FROM faces WHERE person_id IS NOT NULL AND confirmed=1").fetchone()[0]
        people = c.execute("SELECT COUNT(DISTINCT person_id) FROM faces WHERE person_id IS NOT NULL AND ignored=0").fetchone()[0]
        vids = c.execute("SELECT COUNT(*) FROM photos WHERE error IS NULL AND is_video=1").fetchone()[0]
        wait_n, wait_v = getattr(self, "waiting", (0, 0))
        self.stats_label.setText(f"{photos - vids:,} photos · " + (f"{vids:,} videos · " if vids else "")
                                 + (f"{others:,} other files · " if others else "")
                                 + f"{faces:,} faces · {named:,} named · "
                                 f"{people:,} {'person' if people == 1 else 'people'}"
                                 + (f"   ·   {wait_n:,} waiting to import" if wait_n else "")
                                 + (f"   ·   {wait_v:,} videos waiting (Scan videos)"
                                    if wait_v and wait_v > 0 and self.settings.get("video_mode") == "separate"
                                    and wait_v > wait_n else ""))

    def rebuild_tree(self):
        c = self.conn
        for t in self.trees:
            t.blockSignals(True)
        scrolls = [t.verticalScrollBar().value() for t in self.trees]
        collapsed = {top.text(0).split(" (")[0] for top in self.top_items() if not top.isExpanded()}
        multi_keys = list(self.view[1]) if self.view[0] == "multi" else []
        for it in self.iter_items():
            k = key_of(it)
            if k and k[0] == "person" and it.childCount():
                k = ("category", ("p", k[1]))
            elif k and k[0] == "sortlater" and it.childCount():
                k = ("category", ("later", None))
            if k and k[0] == "category" and it.childCount():
                (self._cat_collapsed.discard if it.isExpanded() else self._cat_collapsed.add)(k[1])
        cur = self.current_item()
        if cur is not None:
            top = cur
            while top.parent() is not None:
                top = top.parent()
            self._view_section = top.text(0).split(" (")[0]
        for t in self.trees:
            t.clear()
        bold = QFont(self.tree.font())
        bold.setBold(True)

        def section(text, drop=None, tree=None):
            it = QTreeWidgetItem([text])
            it.setFlags(Qt.ItemIsEnabled | (Qt.ItemIsDropEnabled if drop else Qt.NoItemFlags))
            if drop:
                it.setData(0, ROLE_DROP, drop)
            it.setFont(0, bold)
            (tree or self.tree).addTopLevelItem(it)
            return it

        def rep_icon(face_id):
            # a face's round list icon: made once and kept with the face pictures (so it's dropped
            # whenever they are - after turning a photo, say), reused on every refresh
            if face_id is None:
                return QIcon()
            ic = self.pix_cache.get(("rep", face_id))
            if ic is None:
                ic = self.pix_cache[("rep", face_id)] = QIcon(round_pixmap(self.face_pixmap(face_id, face_only=True), 72))
            return ic

        # people, with counts (done as a few grouped queries: quick on big libraries)
        nface, nsugg, best = {}, {}, {}
        for person, fid, conf, q in c.execute(f"""SELECT person_id, id, confirmed, {store.clear_order("")} FROM faces
                                                  WHERE ignored=0 AND person_id IS NOT NULL"""):
            nface[person] = nface.get(person, 0) + 1
            if not conf:
                nsugg[person] = nsugg.get(person, 0) + 1
            key = (conf, q or 0)
            if person not in best or key > best[person][0]:
                best[person] = (key, fid)
        ntag = dict(c.execute("""SELECT pp.person_id, COUNT(*) FROM photo_people pp JOIN photos x ON x.id=pp.photo_id
                                 WHERE x.sort_later=0 AND NOT EXISTS (SELECT 1 FROM faces f WHERE f.photo_id=pp.photo_id
                                 AND f.person_id=pp.person_id AND f.ignored=0) GROUP BY pp.person_id"""))
        ntat = dict(c.execute("""SELECT ts.person_id, COUNT(*) FROM tattoo_suggestions ts JOIN photos x
                                 ON x.id=ts.photo_id WHERE x.sort_later=0 GROUP BY ts.person_id"""))
        people = [(pid, name, nface.get(pid, 0) + ntag.get(pid, 0) + ntat.get(pid, 0),
                   nsugg.get(pid, 0) + ntat.get(pid, 0), best[pid][1] if pid in best else None, cat)
                  for pid, name, cat in c.execute("SELECT id, name, category_id FROM persons ORDER BY name COLLATE NOCASE")]
        # locked vaults: their people and folders aren't shown at all
        lockedF, lockedP, _ = store.locked_vault_contents(c)
        vault_info = {v[0]: v for v in store.vaults(c)}
        people = [r for r in people if r[0] not in lockedP]
        everyone = people
        # People with no faces in the current folders stay hidden until a face or tag matches
        # them again (they're still remembered and offered when you type a name).
        people = [r for r in people if r[2] > 0]
        cat_ids = {r[0] for r in c.execute("SELECT id FROM categories")}
        # placed in a category: listed there; also here only while suggestions wait for you
        listed = [r for r in people if r[5] not in cat_ids or r[3] > 0]
        # green = all confirmed (A-Z), yellow = suggestions waiting (A-Z, at the bottom)
        people.sort(key=lambda r: (r[3] > 0, r[1].casefold()))
        dark = self.palette().window().color().lightness() < 128
        green = QColor(95, 211, 138) if dark else QColor(28, 140, 64)
        yellow = QColor(240, 200, 60) if dark else QColor(176, 128, 0)
        waiting = sum(1 for r in listed if r[3])
        batches = store.drop_batches(c)
        secd = section(f"Recently dropped ({sum(b[2] for b in batches):,})")    # always shown, so it's easy to find
        if not batches:
            it = QTreeWidgetItem(["Drag photos or folders onto the window - they land here first"])
            it.setFlags(Qt.NoItemFlags)
            it.setIcon(0, app_icon("drop", "grey"))
            secd.addChild(it)
        if batches:
            secd.setToolTip(0, "What you dragged in, laid out the way it'll be saved. Check it's tagged right, then "
                               "right-click > Done checking. Sort into folders leaves these out until then.")
            for b, name, n in batches:
                it = QTreeWidgetItem([f"{name}   {n:,}"])
                it.setIcon(0, app_icon("drop"))
                it.setData(0, Qt.UserRole, ("dropped", b))
                it.setToolTip(0, "Dropped " + time.strftime("%d %b %Y, %H:%M", time.localtime(b))
                              + "\nRight-click > Done checking when it's tagged right")
                secd.addChild(it)
        sec = section(f"People ({len(listed)})" + (f"  ·  {waiting} to check" if waiting else ""), ("people_root",))
        cat_paths = store.category_paths(c)

        def person_item(pid, name, n, sugg, rep, cat):
            text = f"{name}   {n - sugg}" + (f"  (+{sugg}?)" if sugg else "")
            it = QTreeWidgetItem([text])
            it.setIcon(0, rep_icon(rep))
            it.setForeground(0, QBrush(yellow if sugg else green))
            tip = (f"{sugg} suggestion{'s' if sugg != 1 else ''} waiting for you to confirm"
                   if sugg else "All faces confirmed")
            if cat in cat_paths:
                tip += "\nSaved under: " + " › ".join(cat_paths[cat])
            it.setToolTip(0, tip + "\nDrag onto a folder to place them there · right-click for more")
            it.setData(0, Qt.UserRole, ("person", pid))
            it.setData(0, Qt.UserRole + 1, name.lower())
            return it
        pending_only = bool(self.settings.get("people_pending_only"))
        for row in listed:
            if pending_only and not row[3]:
                continue
            sec.addChild(person_item(*row))
        if pending_only and not waiting:
            it = QTreeWidgetItem(["Nothing waiting to confirm"])
            it.setFlags(Qt.NoItemFlags)
            sec.addChild(it)
        elif not listed:
            it = QTreeWidgetItem(["Everyone is in a folder below" if people else "Name a group to add people"])
            it.setFlags(Qt.NoItemFlags)
            sec.addChild(it)

        # categories as a tree (nested categories, and the people placed in each)
        cats = [r for r in c.execute("SELECT id, name, parent_id FROM categories ORDER BY name COLLATE NOCASE")
                if r[0] not in lockedF or r[0] in vault_info]
        cats.sort(key=lambda r: (r[0] not in vault_info, r[1].casefold()))     # vaults first
        ftree = store.folder_tree(c)
        by_cat, uncategorized = self.places()
        nocat = len(uncategorized)
        kids = {}
        ids = {cid for cid, _, _ in cats}
        for cid, name, parent in cats:
            kids.setdefault(parent if parent in ids else None, []).append((cid, name))
        placed = {}
        for row in everyone:
            if row[5] in ids:
                placed.setdefault(row[5], []).append(row)

        def total(cid, seen=()):
            out = set(by_cat.get(cid, ()))
            for k, _ in kids.get(cid, ()):
                if k not in seen:
                    out |= total(k, seen + (cid,))
            return out
        secc = section(f"Folders ({len(cats)})", ("cat_root",))
        secf = section(f"Folders ({len(cats)})", ("cat_root",), tree=self.treeF)     # the Folders tab
        folder_icon = std_icon(QStyle.SP_DirIcon)

        def add_cats(parent_item, parent_id, seen=(), with_people=True):
            for cid, name in kids.get(parent_id, ()):
                if cid in seen:
                    continue
                if cid in vault_info and vault_info[cid][2]:
                    # a locked vault: just its name and a lock (nothing inside is shown)
                    it = QTreeWidgetItem([f"{name}   (locked)"])
                    it.setIcon(0, app_icon("lock", "grey"))
                    it.setData(0, Qt.UserRole, ("vault", cid))
                    it.setToolTip(0, "A locked vault: its photos, people and folders are hidden everywhere.\n"
                                     "Click to unlock · right-click for more")
                    parent_item.addChild(it)
                    continue
                n = len(total(cid))
                it = QTreeWidgetItem([f"{name}   {n}"])
                it.setIcon(0, folder_icon)
                it.setData(0, Qt.UserRole, ("category", cid))
                it.setData(0, Qt.UserRole + 1, name.lower())
                lbl = store.folder_label(c, cid, ftree)
                it.setToolTip(0, "Saved as: " + (("…\\" + lbl.replace(store.PATH_SEP, "\\")) if ftree[cid][2]
                                                 else lbl.replace(store.PATH_SEP, "\\"))
                              + "\nDrag people or folders here · right-click for more")
                if cid in vault_info:            # an unlocked vault
                    it.setIcon(0, app_icon("unlock"))
                    it.setToolTip(0, "A vault (unlocked) - right-click > Lock to hide everything in it again")
                parent_item.addChild(it)
                add_cats(it, cid, seen + (cid,), with_people)
                for row in (sorted(placed.get(cid, ()), key=lambda r: r[1].casefold()) if with_people else []):
                    it.addChild(person_item(*row))
                it.setExpanded(cid not in self._cat_collapsed)
        owned = {}
        for cid, n in kids.get(None, []):
            if ftree[cid][2] is not None:
                owned.setdefault(ftree[cid][2], []).append((cid, n))
        kids[None] = [(cid, n) for cid, n in kids.get(None, []) if ftree[cid][2] is None]
        add_cats(secc, None)
        add_cats(secf, None, with_people=False)
        # each person's own folders, under that person - except in the People panel, which lists
        # just the people (their folders are the sections of their gallery and the folder bar)
        for it in list(self.iter_items()):
            k = key_of(it)
            if it.treeWidget() is self.tree:
                continue
            if k and k[0] == "person" and owned.get(k[1]):
                kids[("p", k[1])] = owned[k[1]]
                add_cats(it, ("p", k[1]))
                it.setExpanded(("p", k[1]) not in self._cat_collapsed)
        for sc in (secc, secf):
            if nocat:
                it = QTreeWidgetItem([f"No folder   {nocat}"])
                it.setIcon(0, std_icon(QStyle.SP_DirOpenIcon))
                it.setData(0, Qt.UserRole, ("nocategory", None))
                sc.addChild(it)
            if not cats and not nocat:
                it = QTreeWidgetItem(["Right-click here, or press A on photos, to add a folder"])
                it.setFlags(Qt.NoItemFlags)
                sc.addChild(it)
        secf.setExpanded(True)
        # albums: groups a photo can be in any number of, without moving a file
        alb = store.albums(c)
        seca = section(f"Albums ({len(alb)})", ("album_root",), tree=self.treeA)
        album_people = {}
        for aid, name in c.execute("""SELECT ap.album_id, p.name FROM album_photos ap
                                      JOIN faces f ON f.photo_id=ap.photo_id AND f.confirmed=1 AND f.ignored=0
                                      JOIN persons p ON p.id=f.person_id
                                      UNION SELECT ap.album_id, p.name FROM album_photos ap
                                      JOIN photo_people pp ON pp.photo_id=ap.photo_id JOIN persons p ON p.id=pp.person_id"""):
            if name:
                album_people.setdefault(aid, set()).add(name)
        for aid, aname, n in alb:
            it = QTreeWidgetItem([f"{aname}   {n}"])
            it.setIcon(0, app_icon("album", "purple"))
            it.setData(0, Qt.UserRole, ("album", aid))
            it.setData(0, Qt.UserRole + 1, aname.lower())
            who = sorted(album_people.get(aid, ()), key=str.casefold)
            if who:
                shown = ", ".join(who[:25]) + (f" and {len(who) - 25} more" if len(who) > 25 else "")
                tip = f"People in this album ({len(who)}): {shown}"
            else:
                tip = "Nobody tagged in this album yet"
            it.setToolTip(0, tip + "\n\nPhotos stay where they are on disk - a photo can be in several albums.\n"
                                   "Drag photos here from the gallery to add them · right-click for more")
            seca.addChild(it)
        if not alb:
            it = QTreeWidgetItem(["Right-click here, or drag photos here, to make an album"])
            it.setFlags(Qt.NoItemFlags)
            seca.addChild(it)
        seca.setExpanded(True)

        groups, gdirs = {}, {}
        hints_on = self.settings.get("folder_hints", True)
        dirs = self.photo_dirs() if hints_on else {}
        for cl, fid, q, photo in c.execute(f"""SELECT cluster, id, {store.clear_order("")}, photo_id FROM faces
                                              WHERE cluster IS NOT NULL AND person_id IS NULL AND ignored=0"""):
            g = groups.setdefault(cl, [0, fid, -1])
            g[0] += 1
            if (q or 0) > g[2]:
                g[1], g[2] = fid, q or 0
            if hints_on:
                d = gdirs.setdefault(cl, {})
                pd = dirs.get(photo, "")
                d[pd] = d.get(pd, 0) + 1

        unnamed = self.unnamed_counts() if hints_on else {}

        def folder_hint(cl, n):
            if not self.settings.get("folder_hints", True) or not gdirs.get(cl):
                return ""
            name = store.pick_folder(gdirs[cl], n, unnamed)
            return f"  · {name}?" if name else ""
        glist = sorted(((n, cl, fid) for cl, (n, fid, _) in groups.items() if n >= 2), reverse=True)
        mode = self.settings.get("bottom_sort", "face")
        if mode != "face":
            self.add_buckets(section, mode)
            glist_shown = []
        else:
            glist_shown = glist
        sec2 = section(f"Unnamed groups ({len(glist)})", tree=self.tree2) if mode == "face" else None
        for i, (n, cl, fid) in enumerate(glist_shown[:1000]):
            hint = folder_hint(cl, n)
            it = QTreeWidgetItem([f"Group {i + 1}   {n} faces{hint}"])
            if hint:
                it.setToolTip(0, f"Most of these faces are in the folder “{hint[4:-1]}”")
            it.setIcon(0, rep_icon(fid))
            it.setData(0, Qt.UserRole, ("group", cl))
            it.setData(0, Qt.UserRole + 2, i + 1)
            sec2.addChild(it)

        sec3 = section("Other", tree=self.tree2)
        unk = c.execute("SELECT COUNT(*) FROM faces WHERE person_id IS NULL AND cluster IS NULL AND ignored=0").fetchone()[0]
        disc = c.execute("""SELECT COUNT(*) FROM photos WHERE sort_later=0 AND (discarded=1
                            OR id IN (SELECT photo_id FROM faces WHERE ignored=2))""").fetchone()[0]
        nof = c.execute(f"""SELECT COUNT(*) FROM photos ph WHERE {NOFACES_WHERE}""").fetchone()[0]
        nlater = c.execute("SELECT COUNT(*) FROM photos WHERE sort_later=1").fetchone()[0]
        nunassoc = c.execute(f"SELECT COUNT(*) FROM photos ph WHERE {UNASSOC_WHERE}").fetchone()[0]
        errs = c.execute("SELECT COUNT(*) FROM photos WHERE error IS NOT NULL").fetchone()[0]
        ntat = c.execute("""SELECT COUNT(DISTINCT ts.photo_id) FROM tattoo_suggestions ts JOIN photos ph
                            ON ph.id=ts.photo_id WHERE ph.sort_later=0""").fetchone()[0]
        nmarks = c.execute("SELECT COUNT(*) FROM tattoos").fetchone()[0]
        nmaybe = c.execute("SELECT COUNT(*) FROM faces WHERE ignored=4").fetchone()[0]
        st = self.style()
        nunk_sorted = c.execute(f"""SELECT COUNT(*) FROM faces f JOIN photos ph ON ph.id=f.photo_id
                                    WHERE f.person_id IS NULL AND f.cluster IS NULL AND f.ignored=0
                                    AND {self.sorted_photo_sql()}""").fetchone()[0] if unk else 0
        for text, key, icon in ((f"Unknown faces   {unk}", ("unknown", None), QStyle.SP_MessageBoxQuestion),
                                (f"Sort Later   {nlater}", ("sortlater", None), QStyle.SP_FileDialogListView),
                                (f"No faces found   {nof}", ("nofaces", None), QStyle.SP_FileIcon),
                                (f"Unassociated files   {nunassoc}", ("unassoc", None), QStyle.SP_FileLinkIcon),
                                (f"~To Be Discarded   {disc}", ("discarded", None), QStyle.SP_TrashIcon)):
            if key[0] == "unassoc" and not nunassoc:
                continue
            it = QTreeWidgetItem([text])
            if key[0] == "unassoc":
                it.setToolTip(0, "Other files (documents, music, archives…) nobody is tagged on and that aren't in a "
                                 "folder yet. Tag people or set a folder and they move with Sort into folders.")
            it.setIcon(0, std_icon(icon))
            it.setData(0, Qt.UserRole, key)
            sec3.addChild(it)
            if key[0] == "sortlater" and nlater:
                self.add_later_children(it, rep_icon)
            if key[0] == "unknown" and unk:
                for n, label, sub, tip in (
                        (nunk_sorted, "Tagged / sorted", "sorted",
                         "The photo is already sorted: someone on it is tagged, it has a folder, or the file is "
                         "already in your sorted library"),
                        (unk - nunk_sorted, "Untagged / unsorted", "unsorted",
                         "Nobody on the photo is tagged and it has no folder - it hasn't been sorted yet")):
                    ch = QTreeWidgetItem([f"{label}   {n}"])
                    ch.setData(0, Qt.UserRole, ("unknown", sub))
                    ch.setToolTip(0, tip)
                    it.addChild(ch)
                it.setExpanded(True)
        if ntat or nmarks:
            it = QTreeWidgetItem([f"Tattoo matches   {ntat}"])
            it.setIcon(0, std_icon(QStyle.SP_FileDialogContentsView))
            it.setData(0, Qt.UserRole, ("tattoos", None))
            it.setToolTip(0, f"{nmarks} tattoo mark{'s' if nmarks != 1 else ''} · "
                             f"{ntat} photo{'s' if ntat != 1 else ''} with a possible match to check")
            sec3.insertChild(2, it)
        nshoot = c.execute("""SELECT COUNT(*) FROM body_suggestions s JOIN bodies b ON b.id=s.body_id
                              JOIN photos ph ON ph.id=b.photo_id WHERE ph.sort_later=0""").fetchone()[0] \
            if self.settings.get("same_shoot") else 0
        if nshoot:
            it = QTreeWidgetItem([f"Same shoot   {nshoot}"])
            it.setIcon(0, std_icon(QStyle.SP_FileDialogInfoView))
            it.setData(0, Qt.UserRole, ("shoot", None))
            it.setToolTip(0, "People whose face couldn't be recognised, matched by their clothes to someone you "
                             "confirmed at the same occasion (same time and place)")
            sec3.insertChild(1, it)
        for n, text, key, tip in ((nmaybe, f"Probably not faces   {nmaybe}", ("maybe_notfaces", None),
                                   "Look like things you marked “not a face”. N = not a face · Restore = it is a face"),):
            if n:
                it = QTreeWidgetItem([text])
                it.setIcon(0, std_icon(QStyle.SP_DialogCancelButton))
                it.setData(0, Qt.UserRole, key)
                it.setToolTip(0, tip)
                sec3.addChild(it)
        nai1 = c.execute("SELECT COUNT(*) FROM photos WHERE ai=1 AND error IS NULL").fetchone()[0]
        nai2 = c.execute("SELECT COUNT(*) FROM photos WHERE ai=2 AND error IS NULL").fetchone()[0]
        if (nai1 or nai2) and self.settings.get("show_ai_group", False):     # (off: the badge is enough)
            it = QTreeWidgetItem([f"AI images   {nai1 + nai2}"])
            it.setIcon(0, app_icon("ai"))
            it.setData(0, Qt.UserRole, ("ai", None))
            sec3.addChild(it)
            for n, text, key, tip in ((nai1, f"Marked as AI   {nai1}", ("ai", 1), "The file itself says it was made or edited with AI"),
                                      (nai2, f"Possibly AI   {nai2}", ("ai", 2), "The AI detector thinks so - it is often wrong")):
                if n:
                    ch = QTreeWidgetItem([text])
                    ch.setData(0, Qt.UserRole, key)
                    ch.setToolTip(0, tip)
                    it.addChild(ch)
            it.setExpanded(True)
        ndup = len(self.dup_sets())
        if ndup:
            it = QTreeWidgetItem([f"Duplicates   {ndup} set{'s' if ndup != 1 else ''}"])
            it.setIcon(0, app_icon("dupes"))
            it.setData(0, Qt.UserRole, ("dupes", None))
            it.setToolTip(0, "The same picture more than once (exact copies, or resized / re-saved copies)")
            sec3.addChild(it)
        nmis = len(getattr(self, "_misplaced", None) or ())
        if nmis:
            it = QTreeWidgetItem([f"Not where they belong   {nmis}"])
            it.setIcon(0, std_icon(QStyle.SP_MessageBoxWarning))
            it.setData(0, Qt.UserRole, ("misplaced", None))
            it.setToolTip(0, "Photos in your sorted library that are in the wrong folder (left over from an older "
                             "FaceSort, moved by hand, or tagged differently since)")
            sec3.addChild(it)
        if errs:
            it = QTreeWidgetItem([f"Unreadable files   {errs}"])
            it.setIcon(0, std_icon(QStyle.SP_MessageBoxWarning))
            it.setData(0, Qt.UserRole, ("errors", None))
            sec3.addChild(it)
        for top in self.top_items():
            top.setExpanded(top.text(0).split(" (")[0] not in collapsed)
        self.apply_scope_to_tree(c)

        # restore selection (in the same section it was in: a person can be listed twice)
        if multi_keys:
            items = [i for i in (self.find_tree_item(k) for k in multi_keys) if i is not None]
            if len(items) > 1:
                t = items[0].treeWidget()
                t.setCurrentItem(items[0])
                for i in items:
                    if i.treeWidget() is t:
                        i.setSelected(True)
                self._active_tree = t
                self.view = ("multi", tuple(key_of(i) for i in items if i.treeWidget() is t))
                target = None
            else:
                multi_keys = []
                self.view = key_of(items[0]) if items else ("none", None)
        if not multi_keys:
            target = self.find_tree_item(self.view, self._view_section)
            if target is None and self.view[0] == "group":
                target = self.find_group_for_anchor()
            if target is None:
                target = self.first_selectable()
            if target is not None:
                self.select_item(target, switch=False)
                self.view = key_of(target)
        self.filter_tree(self.search.text())
        for t, sc in zip(self.trees, scrolls):
            t.verticalScrollBar().setValue(sc)   # don't jump the lists after every change
            t.blockSignals(False)
