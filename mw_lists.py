"""MainWindow, part: the left-hand lists (People / Folders / Albums / Smart Groups)."""
import os
import time
import store
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont, QIcon
from PySide6.QtWidgets import QStyle, QTreeWidgetItem
from icons import app_icon
from ui_common import key_of, log, NOFACES_WHERE, ROLE_DROP, round_pixmap, std_icon, UNASSOC_WHERE


def _read_ahead_and_rows(conn, questions, scope, ctx, progress=None, cancelled=lambda: False):
    """(1.62) On the background thread: the lists' numbers read ahead, and the side lists' rows worked out
    (with their digest - unchanged rows aren't patched in at all)."""
    import listcache
    answers = listcache.read_ahead(conn, questions, scope, progress=progress, cancelled=cancelled)
    if ctx is None:
        return answers
    import treespec
    try:
        rows = treespec.build(conn, ctx)
        return answers, (rows, treespec.digest(rows))
    except Exception:
        log("lists: " + __import__("traceback").format_exc())
        return answers


class ListsMixin:
    """Part of MainWindow (see facesort.py): the left-hand lists (People / Folders / Albums / Smart Groups)."""

    # ------------------------------------------------------------------ refresh
    # ---- (1.61) the questions the side lists ask, kept between sessions: the window opens at once and the
    # lists' numbers are read in the background, like after any change
    def _questions_file(self):
        return os.path.join(store.data_dir(), "list_questions.json")

    def save_list_questions(self, answers=None):
        """The questions (small); answers too (a few MB: only when closing, when a moment doesn't matter)."""
        qs = list(getattr(self, "_seen_q", None) or getattr(self, "_tree_questions", None) or ())
        if not qs:
            return
        with_answers = answers if answers is not None else getattr(self, "_closing", False)
        self._save_lists(qs, getattr(self, "_last_answers", None) if with_answers else None,
                         getattr(self, "_dest_cache", None), getattr(self, "_last_spec", None))

    def _save_lists(self, qs, ans, dest, spec=None):
        import json
        try:
            out = [[sql, list(params)] for sql, params in qs if isinstance(params, tuple)]
            tmp = self._questions_file() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "q": out}, f)
            os.replace(tmp, self._questions_file())
        except Exception:
            pass
        if ans:
            import pickle
            try:
                tmp = self._answers_file() + ".tmp"
                with open(tmp, "wb") as f:
                    pickle.dump({"version": 1, "a": ans, "dest": dest[1:] if dest else None, "spec": spec}, f,
                                protocol=pickle.HIGHEST_PROTOCOL)
                os.replace(tmp, self._answers_file())
            except Exception:
                pass

    def _answers_file(self):
        return os.path.join(store.data_dir(), "list_answers.cache")

    def load_list_answers(self):
        """(1.61) The lists' numbers as they were when FaceSort last closed - drawn at once on opening (then
        brought up to date in the background, which changes nothing if nothing changed)."""
        import pickle
        try:
            with open(self._answers_file(), "rb") as f:
                data = pickle.load(f)
            ans = data.get("a") if isinstance(data, dict) and data.get("version") == 1 else None
            dest = data.get("dest") if isinstance(data, dict) else None
            self._boot_spec = data.get("spec") if isinstance(data, dict) else None
            if dest and len(dest) == 2 and getattr(self, "_dest_cache", None) is None:
                self._dest_cache = (None, dest[0], dest[1])       # (out of date: used, and worked out again)
            return ans if isinstance(ans, dict) and ans else None
        except Exception:
            return None

    def load_list_questions(self):
        import json
        try:
            with open(self._questions_file(), encoding="utf-8") as f:
                data = json.load(f)
            qs = [(sql, tuple(params)) for sql, params in data.get("q", [])]
        except Exception:
            return False
        if qs:
            self._tree_questions = qs
            return True
        return False

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
        ctx = None
        if scope is None and os.environ.get("FACESORT_TREE_ON_WINDOW") != "1":
            try:
                ctx = self.tree_ctx()               # (1.62) the lists' rows are worked out there too
            except Exception as e:
                log(f"lists: {e}")
        w = Worker(_read_ahead_and_rows, list(self._tree_questions), scope, ctx)
        w.ok.connect(lambda answers, mark=mark: self._read_ahead_done(answers, mark))
        w.failed.connect(lambda tb: self._read_ahead_done(None, mark))
        self._ra_worker = w
        self._ra_started = time.monotonic()
        w.start()

    def _read_ahead_done(self, answers, mark):
        self._ra_worker = None
        self._ra_spec = None
        if isinstance(answers, tuple):
            answers, self._ra_spec = answers
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
            self.apply_lists(answers, False, stats, spec=getattr(self, "_ra_spec", None))
            self._lists_want = (True, False)
            self._start_read_ahead()
            return
        self._lists_want = (False, False)
        self.apply_lists(answers, grid, stats, spec=getattr(self, "_ra_spec", None))

    def apply_lists(self, answers, grid=True, stats=True, spec=None):
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
        digest = None
        if spec is not None:
            # (1.62) the rows came worked out: patched in unless they're exactly what's shown
            digest = spec[1]
            do_tree = digest != getattr(self, "_spec_digest", None) or view != getattr(self, "_spec_view", None)
            spec = spec[0]
        else:
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
                for tw in self.trees:              # (1.61) one repaint at the end, not one per row
                    tw.setUpdatesEnabled(False)
                try:
                    self._rebuild_tree(spec)
                    self._spec_digest, self._spec_view = digest, view
                finally:
                    for tw in self.trees:
                        tw.setUpdatesEnabled(True)
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
        seen = list(getattr(self, "_seen_q", {})) + list(rec.asked)     # (1.61) all asked this session, for next time
        self._seen_q = dict.fromkeys(seen[-400:])
        self._lists_hits = (rec.hits, rec.misses)
        self._lists_missed = rec.missed[:20]
        parts = dict(last) if last and last.get("view") == tuple(self.view or ()) else {}
        parts["view"] = tuple(self.view) if self.view else None
        if answers is not None:
            known = dict(answers)
            known.update(rec.fetched)
            self._last_answers = known                 # (1.61) kept for the next start
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
        self._spec_digest = None

    def _parent_items(self):
        """(1.61) The rows that have rows under them (people with folders, folders) - without visiting the
        thousands of rows that have none."""
        def walk(it):
            for j in range(it.childCount()):
                ch = it.child(j)
                if ch.childCount():
                    yield ch
                    yield from walk(ch)
        for top in self.top_items():
            yield from walk(top)

    # ---- (1.62) the side lists are worked out as plain rows (treespec.py) - on the background thread that
    # reads their numbers when it can - and only what changed is touched on screen
    def tree_ctx(self):
        """What working the lists out needs from the window (its thread only): settings, folder counts,
        SQL that depends on where the library is, duplicate sets…"""
        import face_attrs
        from ui_common import STD_ICONS
        sp = {k: STD_ICONS.get(getattr(QStyle, "SP_" + k), "info") for k in (
            "DirIcon", "DirOpenIcon", "MessageBoxQuestion", "FileDialogListView", "FileIcon", "FileLinkIcon",
            "TrashIcon", "FileDialogContentsView", "FileDialogInfoView", "DialogCancelButton", "MessageBoxWarning")}
        mode = self.settings.get("bottom_sort", "face")
        buckets = None
        if mode != "face":
            buckets = (self.photo_buckets(mode), {"taken": "To sort by date taken", "created": "To sort by date created",
                                                  "location": "To sort by original location"}[mode],
                       "pin" if mode == "location" else "calendar")
        for it in self._parent_items():          # what you've opened / closed, for rows shown the first time
            k = key_of(it)
            if k and k[0] == "person" and it.childCount():
                k = ("category", ("p", k[1]))
            elif k and k[0] == "sortlater" and it.childCount():
                k = ("category", ("later", None))
            if k and k[0] == "category" and it.childCount():
                (self._cat_collapsed.discard if it.isExpanded() else self._cat_collapsed.add)(k[1])
        return {"settings": dict(self.settings), "places": self.places(stale_ok=True),
                "sorted_sql": self.sorted_photo_sql(), "nofaces_sql": self.nofaces_sql(),
                "nopeople_sql": self.nopeople_sql(), "unassoc_where": UNASSOC_WHERE,
                "unknown_sql": self.smart_faces_sql("unknown"), "partial_sql": self.smart_faces_sql("partial"),
                "men_sql": face_attrs.men_sql(), "unsure_sql": face_attrs.unsure_sql(),
                "later_face": self.LATER_FACE, "ndup": len(self.dup_sets()),
                "nmisplaced": len(getattr(self, "_misplaced", None) or ()), "cat_collapsed": set(self._cat_collapsed),
                "buckets": buckets, "SP": sp, "ROLE_DROP": int(ROLE_DROP)}

    def _rebuild_tree(self, spec=None):
        import treespec
        c = self.conn
        if spec is None:
            spec = treespec.build(c, self.tree_ctx())
        self._last_spec = spec
        for t in self.trees:
            t.blockSignals(True)
        scrolls = [t.verticalScrollBar().value() for t in self.trees]
        multi_keys = list(self.view[1]) if self.view[0] == "multi" else []
        # (1.49.1) the row of what the gallery shows
        cur = None
        if self.view and self.view[0] not in ("multi", "none"):
            cc = self.current_item()
            try:
                cur = cc if cc is not None and key_of(cc) == tuple(self.view) else self.find_tree_item(self.view)
            except RuntimeError:
                cur = self.find_tree_item(self.view)
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
                neighbours = [key_of(x) for x in sib[i + 1:i + 41]] + [key_of(x) for x in reversed(sib[max(0, i - 40):i])] + \
                    [key_of(par)]
                neighbours = [k for k in neighbours if k]
        dark = self.palette().window().color().lightness() < 128
        brushes = {"green": QBrush(QColor(95, 211, 138) if dark else QColor(28, 140, 64)),
                   "yellow": QBrush(QColor(240, 200, 60) if dark else QColor(176, 128, 0))}
        bold = QFont(self.tree.font())
        bold.setBold(True)
        empty = QIcon()

        def icon(desc):
            if desc is None:
                return empty
            if desc[0] == "face":
                got = self.rep_cache.get(desc[1])
                if got is None or self.rep_cache_checked != self.pix_cache.gen:
                    return got[1] if got is not None else None
                return got[1]
            return app_icon(desc[1], desc[2]) if desc[2] else app_icon(desc[1])
        ap = treespec.Applier(icon, brushes.get, bold, QTreeWidgetItem, lambda f: Qt.ItemFlag(f))
        for tw, n in zip(self.trees, (0, 1, 2, 3)):
            ap.sync_tree(tw, spec.get(n, []))
        if ap.touched or ap.removed:
            self._tree_gen = getattr(self, "_tree_gen", 0) + 1      # (the row index is made again when asked)
            self._tree_idx = None
        icon_wait = ap.icon_wait
        self._tree_patch = (ap.touched, ap.removed)
        if self.scope_on():
            self.apply_scope_to_tree(c)
        else:
            self.update_scope_button()
            if getattr(self, "_scope_was_on", False):          # (scope just turned off: everything shown again)
                for it in self.iter_items():
                    it.setHidden(False)
        self._scope_was_on = self.scope_on()
        if cur is not None and multi_keys == []:
            try:
                still = cur.treeWidget() is not None and key_of(cur) == old_view
            except RuntimeError:
                still = False
            if still:                                  # (the usual case: the row you're on is still there)
                self.make_rep_icons(icon_wait)
                if self.search.text().strip() or self.scope_on():
                    self.filter_tree(self.search.text())
                for t, sc in zip(self.trees, scrolls):
                    t.verticalScrollBar().setValue(sc)
                    t.blockSignals(False)
                return

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
