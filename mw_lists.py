"""MainWindow, part: the left-hand lists (People / Folders / Albums / Smart Groups)."""
import os
import time
import store
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont, QIcon
from PySide6.QtWidgets import QStyle, QTreeWidgetItem
from icons import app_icon
from ui_common import key_of, log, NOFACES_WHERE, ROLE_DROP, round_pixmap, std_icon, UNASSOC_WHERE


class ListsMixin:
    """Part of MainWindow (see facesort.py): the left-hand lists (People / Folders / Albums / Smart Groups)."""

    # ------------------------------------------------------------------ refresh
    def refresh_all(self, first=False):
        self._pid_cache = {}
        self._data_gen += 1     # the background jobs write through their own connection
        has_folders = bool(self.settings["folders"])
        self.stack.setCurrentIndex(1 if has_folders else 0)
        self.update_lists(now=first)

    # ------------------------------------------------------------------ (1.50) numbers read ahead
    def lists_async(self):
        return os.environ.get("FACESORT_SYNC_LISTS") != "1" and bool(getattr(self, "_tree_questions", None))

    def update_lists(self, grid=True, stats=True, now=False):
        """Redraw the side lists (and the gallery and the counts at the bottom). The numbers they
        need are read on a background thread first (listcache.py), so the window doesn't freeze
        while the library is asked; the redraw itself then only builds the rows."""
        want = getattr(self, "_lists_want", (False, False))
        self._lists_want = (want[0] or grid, want[1] or stats)
        if now or not self.lists_async() or getattr(self, "_closing", False):
            self._lists_want = (False, False)
            self.apply_lists(None, grid or want[0], stats or want[1])
            return
        w = getattr(self, "_ra_worker", None)
        if w is not None and w.isRunning():
            self._ra_again = True              # changed again meanwhile: read once more when that's done
            return
        self._ra_tries = 0
        self._start_read_ahead()

    def _start_read_ahead(self):
        import listcache
        from ui_common import Worker
        try:
            if self.conn.in_transaction:
                self.conn.commit()              # (the background connection only sees saved changes)
        except Exception:
            pass
        self._ra_again = False
        mark = (id(self.conn), store.data_dir(), self.conn.total_changes)
        scope = None
        try:
            if self.scope_on():
                scope = self.in_scope
        except Exception:
            scope = None
        w = Worker(listcache.read_ahead, list(self._tree_questions), scope)
        w.ok.connect(lambda answers, mark=mark: self._read_ahead_done(answers, mark))
        w.failed.connect(lambda tb: self._read_ahead_done(None, mark))
        self._ra_worker = w
        self._ra_started = time.monotonic()
        w.start()

    def _read_ahead_done(self, answers, mark):
        self._ra_worker = None
        if getattr(self, "_closing", False):
            return
        if answers is None or (id(self.conn), store.data_dir()) != mark[:2]:
            self._ra_ready = None              # (failed, or another library now: draw it as usual)
            grid, stats = getattr(self, "_lists_want", (True, True))
            self._lists_want = (False, False)
            self.apply_lists(None, grid, stats)
            return
        self._ra_ready = (answers, mark)
        self._apply_when_quiet()

    def _apply_when_quiet(self):
        """(1.50.3) Swap the new lists in at a short pause in your clicking / typing - so the moment
        it takes never lands in the middle of tagging. Changed again since they were read: the side
        lists and counts are still newer than what's on screen, so they go in (the gallery doesn't -
        you've changed it since) and everything is read once more."""
        from ui_common import APPLY_QUIET
        ready = getattr(self, "_ra_ready", None)
        if ready is None or getattr(self, "_closing", False):
            return
        answers, mark = ready
        quiet = time.monotonic() - getattr(self, "_last_input", 0.0)
        waited = time.monotonic() - getattr(self, "_ra_started", 0.0)
        if quiet < APPLY_QUIET and waited < 8.0 and self.viewer is None:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(int((APPLY_QUIET - quiet) * 1000) + 30, self._apply_when_quiet)
            return
        self._ra_ready = None
        stale = self._ra_again or (id(self.conn), store.data_dir(), self.conn.total_changes) != mark
        grid, stats = getattr(self, "_lists_want", (True, True))
        if stale:
            self.apply_lists(answers, False, stats)
            self._lists_want = (True, False)
            self._start_read_ahead()
            return
        self._lists_want = (False, False)
        self.apply_lists(answers, grid, stats)

    def apply_lists(self, answers, grid=True, stats=True):
        """Redraw with answers read ahead (None: ask as usual). (1.50.3) A part whose answers are
        exactly the ones it was last drawn from (a regroup that changed nothing, a change that only
        touched the gallery…) isn't redrawn at all."""
        import listcache
        view = tuple(self.view) if self.view else None
        last = getattr(self, "_last_parts", None)

        def same(part):
            if answers is None or not last or last.get("view") != view or part not in last:
                return False
            keys, old = last[part]
            return all(k in answers for k in keys) and [answers[k] for k in keys] == old
        do_tree = not same("tree")
        grid = grid and not (same("grid") and not do_tree)
        stats = stats and not same("stats")
        if not (do_tree or grid or stats):
            return
        real = self.conn
        if isinstance(real, listcache.Recording):
            real = real._real
        rec = listcache.Recording(real, answers)
        self.conn = rec
        try:
            if do_tree:
                self._rebuild_tree()
            rec.part = "grid"
            if grid:
                self.refresh_grid()
            rec.part = "stats"
            if stats:
                self.refresh_stats()
        finally:
            self.conn = real
            rec._answers = {}                  # (a button made during the redraw keeps a plain connection)
        if do_tree and grid and stats:
            self._tree_questions = list(rec.asked)
        else:                                  # (a part skipped: its questions are still wanted next time)
            self._tree_questions = list(dict.fromkeys(list(getattr(self, "_tree_questions", ())) + list(rec.asked)))
        self._lists_hits = (rec.hits, rec.misses)
        self._lists_missed = rec.missed[:20]
        parts = dict(last) if last and last.get("view") == tuple(self.view or ()) else {}
        parts["view"] = tuple(self.view) if self.view else None
        if answers is not None:
            known = dict(answers)
            known.update(rec.fetched)
            for part, keys in rec.parts.items():
                ks = list(keys)
                if all(k in known for k in ks):
                    parts[part] = (ks, [known[k] for k in ks])
                else:
                    parts.pop(part, None)
        else:
            parts = {"view": parts["view"]}
        self._last_parts = parts

    def sort_other(self, sec):
        """Order the Smart Groups' "Other" list: as FaceSort lays it out, most / fewest first, or A-Z."""
        mode = self.settings.get("other_sort", "default")
        if mode == "default" or sec is None:
            return
        import re

        def count(it):
            m = re.search(r"(\d[\d,]*)\D*$", it.text(0))
            return int(m.group(1).replace(",", "")) if m else 0

        def name(it):
            return re.sub(r"[\s\d,]+(sets?)?$", "", it.text(0)).lstrip("~").casefold()
        kids = [sec.takeChild(0) for _ in range(sec.childCount())]
        expanded = {id(k): k.isExpanded() for k in kids}
        if mode == "az":
            kids.sort(key=name)
        else:
            kids.sort(key=lambda k: (count(k), name(k)), reverse=(mode == "most"))
            if mode == "fewest":
                kids.sort(key=lambda k: (count(k), name(k)))
        for k in kids:
            sec.addChild(k)
            k.setExpanded(expanded[id(k)])

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

    def view_still_there(self):
        """(1.49.1) Views that aren't a row on the left (search results, a suggested smart album, a
        dropped batch…) or whose row is only hidden stay where they are after the lists are redrawn."""
        kind, ident = (tuple(self.view) + (None, None))[:2] if self.view else (None, None)
        c = self.conn
        try:
            if kind in ("search", "smart_preview"):
                return True
            if kind == "album":
                return bool(c.execute("SELECT 1 FROM albums WHERE id=?", (ident,)).fetchone())
            if kind == "smart":
                return bool(c.execute("SELECT 1 FROM smart_albums WHERE id=?", (ident,)).fetchone())
            if kind == "dropped":
                return bool(c.execute("SELECT 1 FROM drop_batches WHERE batch=?", (ident,)).fetchone())
        except Exception:
            return False
        return False

    def rebuild_tree(self):
        """Redraw the side lists now (the questions it asks are noted, to read ahead next time)."""
        import listcache
        if isinstance(self.conn, listcache.Recording):
            return self._rebuild_tree()
        real = self.conn
        rec = listcache.Recording(real)
        self.conn = rec
        try:
            self._rebuild_tree()
        finally:
            self.conn = real
        self._tree_questions = list(rec.asked)
        self._last_parts = None

    def _rebuild_tree(self):
        c = self.conn
        for t in self.trees:
            t.blockSignals(True)
        scrolls = [t.verticalScrollBar().value() for t in self.trees]
        collapsed = {top.text(0).split(" (")[0] for top in self.top_items() if not top.isExpanded()}
        multi_keys = list(self.view[1]) if self.view[0] == "multi" else []
        for it in self.iter_items():
            if not it.childCount():
                continue
            k = key_of(it)
            if k and k[0] == "person" and it.childCount():
                k = ("category", ("p", k[1]))
            elif k and k[0] == "sortlater" and it.childCount():
                k = ("category", ("later", None))
            if k and k[0] == "category" and it.childCount():
                (self._cat_collapsed.discard if it.isExpanded() else self._cat_collapsed.add)(k[1])
        # (1.49.1) the row of what the gallery shows - not whatever a list last had current (a list
        # you left keeps its old current row, and its neighbours sent you to Unnamed group 1)
        cur = self.find_tree_item(self.view) if self.view and self.view[0] not in ("multi", "none") else None
        if cur is None:
            cur = self.current_item()
            if cur is not None and (not self.view or key_of(cur) != tuple(self.view)):
                cur = None
        old_view = tuple(self.view) if self.view else None
        old_tree = self.trees.index(cur.treeWidget()) if cur is not None and cur.treeWidget() in self.trees else None
        neighbours = []                  # (1.45.5) if the group you're on goes, the one after it
        if cur is not None:
            top = cur
            while top.parent() is not None:
                top = top.parent()
            self._view_section = top.text(0).split(" (")[0]
            par = cur.parent()
            if par is not None:
                sib = [par.child(j) for j in range(par.childCount())]
                i = sib.index(cur) if cur in sib else -1
                # the ones after it first (the very next group), then before it (it was the last),
                # then the section / folder it was in
                # (only the 40 nearest each side: working them all out for 1,300 people was slow)
                neighbours = [key_of(x) for x in sib[i + 1:i + 41]] + [key_of(x) for x in reversed(sib[max(0, i - 40):i])] + \
                    [key_of(par)]
                neighbours = [k for k in neighbours if k]
        self._tree_gen = getattr(self, "_tree_gen", 0) + 1      # (the row index is made again when asked)
        self._tree_idx = None
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

        icon_wait = []

        def rep_icon(it, face_id):
            # a face's round list icon: made once and reused on every refresh. Missing ones are made
            # together at the end (one query for all their pictures, not one each)
            if face_id is None:
                return
            got = self.rep_cache.get(face_id)
            if got is not None:
                it.setIcon(0, got[1])
                if self.rep_cache_checked == self.pix_cache.gen:
                    return
            icon_wait.append((it, face_id))

        # people, with counts (done as a few grouped queries: quick on big libraries)
        # (1.45.1: back to one plain read, counted here. Asking the database for each person's best
        # face in the same query was quick on a test library, but on some libraries SQLite planned it
        # as a scan of every face per person - almost a minute. A plain read is always quick.)
        # (1.50) counted by the database in the same single pass (one row per person, not one per
        # face): the icon is the face with the highest confirmed / front-on / clear score
        nface, nsugg, best = {}, {}, {}
        for person, n, ns, fid, _score in c.execute(
                f"""SELECT person_id, COUNT(*), TOTAL(confirmed=0), id,
                           MAX(confirmed * 10000000 + (NOT (ABS(COALESCE(yaw, 0)) > 30 OR ABS(COALESCE(pitch, 0)) > 25))
                               * 1000000 + COALESCE({store.clear_order("")}, 0))
                    FROM faces INDEXED BY faces_cover WHERE ignored=0 AND person_id IS NOT NULL GROUP BY person_id"""):
            nface[person] = n
            if ns:
                nsugg[person] = int(ns)
            best[person] = (None, fid)
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
            rep_icon(it, rep)
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
        by_cat, uncategorized = self.places(stale_ok=True)
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
            if it.treeWidget() is self.tree:
                continue
            k = key_of(it)
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
        # (1.48) smart albums: saved filters that fill themselves
        import smart
        sm = smart.all_smart(c)
        secs = section(f"Smart albums ({len(sm)})", ("smart_root",), tree=self.treeA)
        for sid, sname, f in sm:
            it = QTreeWidgetItem([sname])
            it.setIcon(0, app_icon("sparkle"))
            it.setData(0, Qt.UserRole, ("smart", sid))
            it.setData(0, Qt.UserRole + 1, sname.lower())
            it.setToolTip(0, "Smart album: " + smart.describe(f, c) + "\n\nIt keeps itself up to date · right-click to "
                             "change it or make it a normal album")
            secs.addChild(it)
        it = QTreeWidgetItem(["Suggested smart albums…"])
        it.setIcon(0, app_icon("wand"))
        it.setData(0, Qt.UserRole, ("smart_sugg", None))
        it.setToolTip(0, "Smart albums FaceSort suggests from your library (people together, events, people in a scene)")
        secs.addChild(it)
        secs.setExpanded(True)

        groups, gdirs = {}, {}
        hints_on = self.settings.get("folder_hints", True)
        # (1.45.1: one plain read again - see the people above)
        dirs = self.photo_dirs() if hints_on else {}
        for cl, fid, q, photo in c.execute(f"""SELECT cluster, id, {store.clear_order("")}, photo_id
                                              FROM faces INDEXED BY faces_cover WHERE cluster IS NOT NULL AND person_id IS NULL AND ignored=0"""):
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
            rep_icon(it, fid)
            it.setData(0, Qt.UserRole, ("group", cl))
            it.setData(0, Qt.UserRole + 2, i + 1)
            sec2.addChild(it)

        sec3 = section("Other", tree=self.tree2)
        unk = c.execute("SELECT COUNT(*) FROM faces WHERE person_id IS NULL AND cluster IS NULL AND ignored=0").fetchone()[0]
        disc = c.execute("""SELECT COUNT(*) FROM photos WHERE sort_later=0 AND (discarded=1
                            OR id IN (SELECT photo_id FROM faces WHERE ignored=2))""").fetchone()[0]
        nof = c.execute(f"""SELECT COUNT(*) FROM photos ph WHERE {self.nofaces_sql()}""").fetchone()[0]
        nlater = c.execute("SELECT COUNT(*) FROM photos WHERE sort_later=1").fetchone()[0]
        nunassoc = c.execute(f"SELECT COUNT(*) FROM photos ph WHERE {UNASSOC_WHERE}").fetchone()[0]
        errs = c.execute("SELECT COUNT(*) FROM photos WHERE error IS NOT NULL").fetchone()[0]
        ntat = c.execute(f"""SELECT COUNT(DISTINCT ts.photo_id) FROM tattoo_suggestions ts JOIN photos ph
                             ON ph.id=ts.photo_id WHERE ph.sort_later=0 AND NOT {self.sorted_photo_sql()}""").fetchone()[0]
        nmarks = c.execute("SELECT COUNT(*) FROM tattoos").fetchone()[0]
        nmaybe = c.execute("SELECT COUNT(*) FROM faces WHERE ignored=4").fetchone()[0]
        st = self.style()
        unk, unk_men, unk_unsure = self.smart_face_counts("unknown")
        part, part_men, part_unsure = self.smart_face_counts("partial")
        details = bool(self.settings.get("face_details"))
        for text, key, icon in ((f"Unknown faces   {unk}", ("unknown", None), QStyle.SP_MessageBoxQuestion),
                                (f"Partially tagged   {part}", ("partial", None), "partial"),
                                (f"Sort Later   {nlater}", ("sortlater", None), QStyle.SP_FileDialogListView),
                                (f"No faces found   {nof}", ("nofaces", None), QStyle.SP_FileIcon),
                                (f"Unassociated files   {nunassoc}", ("unassoc", None), QStyle.SP_FileLinkIcon),
                                (f"~To Be Discarded   {disc}", ("discarded", None), QStyle.SP_TrashIcon)):
            if (key[0] == "unassoc" and not nunassoc) or (key[0] == "partial" and not part):
                continue
            it = QTreeWidgetItem([text])
            if key[0] == "unassoc":
                it.setToolTip(0, "Other files (documents, music, archives…) nobody is tagged on and that aren't in a "
                                 "folder yet. Tag people or set a folder and they move with Sort into folders.")
            elif key[0] == "unknown":
                it.setToolTip(0, "Faces nobody is named for, not like any group, on photos that aren't sorted at all "
                                 "(nobody tagged, no folder)")
            elif key[0] == "partial":
                it.setToolTip(0, "Group photos where someone is already tagged and other faces aren't named yet")
            it.setIcon(0, app_icon(icon) if isinstance(icon, str) else std_icon(icon))
            it.setData(0, Qt.UserRole, key)
            sec3.addChild(it)
            if key[0] == "sortlater" and nlater:
                self.add_later_children(it, rep_icon)
            if key[0] in ("unknown", "partial") and details:
                men, unsure = (unk_men, unk_unsure) if key[0] == "unknown" else (part_men, part_unsure)
                for n, label, sub, ic, tip in (
                        (men, "Men", "men", "men", "Faces that look male (an estimate - unclear ones are under Unsure)"),
                        (unsure, "Unsure", "unsure", "unsure",
                         "Faces where it's hard to tell (too small, turned away, or the estimate wasn't sure)")):
                    if not n:
                        continue
                    ch = QTreeWidgetItem([f"{label}   {n}"])
                    ch.setData(0, Qt.UserRole, (key[0], sub))
                    ch.setIcon(0, app_icon(ic))
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
        nshoot = c.execute(f"""SELECT COUNT(*) FROM body_suggestions s JOIN bodies b ON b.id=s.body_id
                               JOIN photos ph ON ph.id=b.photo_id WHERE ph.sort_later=0
                               AND NOT {self.sorted_photo_sql()}""").fetchone()[0] \
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
        import samepeople                  # (1.49) people who may be one
        nsame = len(samepeople.pairs(c))
        if nsame:
            it = QTreeWidgetItem([f"Possibly the same person   {nsame}"])
            it.setIcon(0, app_icon("compare"))
            it.setData(0, Qt.UserRole, ("same", None))
            it.setToolTip(0, "Pairs of people who may be one person: faces that look alike, almost the same name, or a "
                             "generic name that looks like someone. Merge them, or Not the same (remembered).")
            sec3.insertChild(min(2, sec3.childCount()), it)
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
        if self.settings.get("content_on"):
            import content
            counts = dict(c.execute(f"""SELECT pc.subject, COUNT(*) FROM photos ph JOIN photo_content pc
                                       ON pc.photo_id=ph.id WHERE {self.nopeople_sql()}
                                       GROUP BY pc.subject""").fetchall())
            nnp = sum(counts.values())
            if nnp:
                it = QTreeWidgetItem([f"No people   {nnp}"])
                it.setIcon(0, app_icon("nopeople"))
                it.setData(0, Qt.UserRole, ("nopeople", None))
                it.setToolTip(0, "Photos with nobody in them - food, landscapes, screenshots, animals… "
                                 "(no face, no body, nobody tagged, and not sorted yet)")
                sec3.addChild(it)
                for key, (label, _) in content.SUBJECTS.items():
                    if counts.get(key):
                        ch = QTreeWidgetItem([f"{label}   {counts[key]}"])
                        ch.setIcon(0, app_icon(key))
                        ch.setData(0, Qt.UserRole, ("nopeople", key))
                        it.addChild(ch)
        ndup = len(self.dup_sets())
        it = QTreeWidgetItem([f"Duplicates   {ndup} set{'s' if ndup != 1 else ''}"])   # (always shown: 0 = none found)
        it.setIcon(0, app_icon("dupes"))
        it.setData(0, Qt.UserRole, ("dupes", None))
        it.setToolTip(0, "The same picture more than once (exact copies, or resized / re-saved copies). FaceSort "
                         "checks new photos in the background; copies made on purpose for each person on a group "
                         "photo don't count.")
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
        self.sort_other(sec3)
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
            keep = target is None and self.view_still_there()
            if target is None and not keep and self.view[0] != "none":
                # (1.45.5) the group you were on is gone (all done, say): the very next one - the
                # gallery never jumps somewhere else on its own
                for k in neighbours:
                    target = self.find_tree_item(k, self._view_section)
                    if target is not None:
                        break
            if target is None and not keep and old_tree is not None and self.view[0] != "none":
                # (1.49.2) nothing beside it: the first row of the same list (not Unnamed group 1)
                t = self.trees[old_tree]
                target = next((x for x in self.iter_items() if x.treeWidget() is t and key_of(x)
                               and not x.isHidden()), None)
            if target is None and not keep and (self.view[0] == "none" or not neighbours):
                target = self.first_selectable()
            if target is not None:
                self.select_item(target, switch=False)
                self.view = key_of(target)
                # (1.51.3: only when it lands on another kind of list - one group done, the next group
                # is what's meant to happen and filled the log)
                if old_view and tuple(self.view) != old_view and old_view[0] not in ("none", self.view[0]):
                    log(f"list: {old_view} is gone - now showing {tuple(self.view)}")
        self.make_rep_icons(icon_wait)
        if self.search.text().strip() or self.scope_on():
            self.filter_tree(self.search.text())    # (1.50: new rows are all shown already otherwise)
        for t, sc in zip(self.trees, scrolls):
            t.verticalScrollBar().setValue(sc)   # don't jump the lists after every change
            t.blockSignals(False)
