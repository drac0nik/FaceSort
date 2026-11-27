"""The side lists, worked out as plain rows and then patched into the lists on screen (1.62).

Before, every redraw cleared the lists and made every row again (thousands of rows: 0.15-0.3 s with the
window frozen, after each change). Now:

- build(conn, ctx) works out what the lists should show, as plain Python rows (Row) - no Qt, so it runs
  on the background thread that reads the lists' numbers, with its own connection;
- apply(tree, rows) compares them with the rows on screen and touches only what changed: a count,
  a new or removed row, a row that moved. Rows that didn't change aren't touched at all, and rows
  stay where they are - the selection, what's open or closed and the scroll position simply stay.
"""
import os
import time

ROLE_USER = 0x0100                 # Qt.UserRole
ROLE_SIG = ROLE_USER + 20          # a row's look, as one number: unchanged rows are skipped quickly
ROLE_NUM = ROLE_USER + 2           # a group's number in the list

# Qt enum values used as plain numbers here (this module doesn't import Qt)
F_NONE, F_SELECTABLE, F_EDITABLE, F_DRAG, F_DROP, F_CHECK, F_ENABLED = 0, 1, 2, 4, 8, 16, 32
F_DEFAULT = F_SELECTABLE | F_DRAG | F_DROP | F_CHECK | F_ENABLED      # a new QTreeWidgetItem's flags


class Row:
    """Stands in for a QTreeWidgetItem while the lists are worked out (the same calls)."""
    __slots__ = ("txt", "tip", "icon", "fg", "bold", "flg", "dat", "kids", "exp", "par")

    def __init__(self, texts=("",)):
        self.txt = texts[0] if texts else ""
        self.tip = ""
        self.icon = None                # ("app", name, color) / ("face", face id)
        self.fg = None                  # "green" / "yellow"
        self.bold = False
        self.flg = F_DEFAULT
        self.dat = {}
        self.kids = []
        self.exp = None                 # expanded when first shown (None: as Qt does)
        self.par = None

    # -- the QTreeWidgetItem calls the list code makes
    def text(self, _col=0):
        return self.txt

    def setText(self, _col, t):
        self.txt = t

    def setToolTip(self, _col, t):
        self.tip = t

    def toolTip(self, _col=0):
        return self.tip

    def setIcon(self, _col, icon):
        self.icon = icon

    def setForeground(self, _col, fg):
        self.fg = fg

    def setFont(self, _col, _font):
        self.bold = True

    def setFlags(self, f):
        self.flg = int(f)

    def flags(self):
        return self.flg

    def setData(self, _col, role, value):
        self.dat[int(role)] = value

    def data(self, _col, role):
        return self.dat.get(int(role))

    def addChild(self, r):
        r.par = self
        self.kids.append(r)

    def insertChild(self, i, r):
        r.par = self
        self.kids.insert(i, r)

    def takeChild(self, i):
        r = self.kids.pop(i)
        r.par = None
        return r

    def childCount(self):
        return len(self.kids)

    def child(self, i):
        return self.kids[i] if 0 <= i < len(self.kids) else None

    def parent(self):
        return self.par

    def setExpanded(self, on):
        self.exp = bool(on)

    def isExpanded(self):
        return bool(self.exp)

    # -- for comparing
    def ident(self):
        k = self.dat.get(ROLE_USER)
        if k is not None:
            return ("k",) + tuple(k)
        return ("t", self.txt.split("   ")[0].split(" (")[0])

    def sig(self):
        """Everything but the text and the group number (those change for every group below one that's
        named - just the text and number are set then)."""
        return hash((self.tip, self.icon, self.fg, self.bold, self.flg,
                     tuple(sorted((r, _hashable(v)) for r, v in self.dat.items() if r != ROLE_NUM))))


def digest(tops):
    """One number for everything the lists show (to skip patching when nothing changed)."""
    def walk(r):
        return (r.txt, r.sig(), r.exp, tuple(walk(k) for k in r.kids))
    return hash(tuple((n, tuple(walk(r) for r in tops.get(n, ()))) for n in sorted(tops)))


def _hashable(v):
    if isinstance(v, list):
        return tuple(_hashable(x) for x in v)
    return v


# ---------------------------------------------------------------------------------- patching
class Applier:
    """Patches rows into QTreeWidgets. icon(desc) -> QIcon (or None for a face whose round icon isn't
    made yet - it's then listed in .icon_wait); brush(name) -> QBrush; bold -> QFont."""

    def __init__(self, icon, brush, bold, Item, flags_of):
        self.icon, self.brush, self.bold, self.Item, self.flags_of = icon, brush, bold, Item, flags_of
        self.icon_wait = []
        self.touched = 0             # rows set (new or changed)
        self.removed = 0

    def _set(self, it, r, new):
        if new or it.text(0) != r.txt:
            it.setText(0, r.txt)
        it.setToolTip(0, r.tip)
        ic = r.icon
        if ic is not None:
            q = self.icon(ic)
            if q is not None:
                it.setIcon(0, q)
            if ic[0] == "face":
                self.icon_wait.append((it, ic[1]))
        elif not new:
            it.setIcon(0, self.icon(None))
        if r.fg:
            it.setForeground(0, self.brush(r.fg))
        elif not new:
            it.setData(0, 9, None)                       # (Qt.ForegroundRole: back to the normal colour)
        if r.bold:
            it.setFont(0, self.bold)
        elif not new:
            it.setData(0, 6, None)                       # (Qt.FontRole)
        it.setFlags(self.flags_of(r.flg))
        for role, v in r.dat.items():
            it.setData(0, role, v)
        it.setData(0, ROLE_SIG, r.sig())
        self.touched += 1

    def make(self, r):
        it = self.Item([r.txt])
        self._set(it, r, True)
        for k in r.kids:
            it.addChild(self.make(k))
        return it

    def _expand_new(self, it, r):
        if r.exp is not None:
            it.setExpanded(r.exp)
        for j, k in enumerate(r.kids):
            if k.kids:
                self._expand_new(it.child(j), k)

    def sync(self, parent, rows, count, child, take, insert, add):
        """Make parent's children (count(), child(j), take(j), insert(j, it), add(it)) match rows."""
        live = [child(j) for j in range(count())]
        by_id = {}
        for it in live:
            by_id.setdefault(_live_ident(it), []).append(it)
        want = []                                            # (row, its live item or None)
        for r in rows:
            got = by_id.get(r.ident())
            want.append((r, got.pop(0) if got else None))
        keep = {id(it) for _r, it in want if it is not None}
        # rows that went
        for j in range(len(live) - 1, -1, -1):
            if id(live[j]) not in keep:
                take(j)
                self.removed += 1
        # in order: the rows that stay are moved only if they're out of place; new rows are put in
        for pos, (r, it) in enumerate(want):
            if it is None:
                new = self.make(r)
                insert(pos, new)
                self._expand_new(new, r)
                continue
            cur = child(pos) if pos < count() else None
            if cur is not it:
                j = next(i for i in range(count()) if child(i) is it)
                exp = it.isExpanded()
                take(j)
                insert(pos, it)
                it.setExpanded(exp)
            if it.data(0, ROLE_SIG) != r.sig():
                self._set(it, r, False)
            elif it.text(0) != r.txt:                      # (just the count / number)
                it.setText(0, r.txt)
                if ROLE_NUM in r.dat:
                    it.setData(0, ROLE_NUM, r.dat[ROLE_NUM])
                self.touched += 1
            elif r.icon is not None and r.icon[0] == "face" and self.icon(r.icon) is None:
                self.icon_wait.append((it, r.icon[1]))
            if r.kids or it.childCount():
                self.sync(it, r.kids, it.childCount, it.child, it.takeChild, it.insertChild, it.addChild)

    def sync_tree(self, tw, rows):
        self.sync(None, rows, tw.topLevelItemCount, tw.topLevelItem, tw.takeTopLevelItem, tw.insertTopLevelItem,
                  tw.addTopLevelItem)


def _live_ident(it):
    k = it.data(0, ROLE_USER)
    if k is not None:
        return ("k",) + tuple(k)
    return ("t", it.text(0).split("   ")[0].split(" (")[0])


# ---------------------------------------------------------------------------------- working it out
def _unnamed(c):
    """{photo id: folder} for photos with unnamed faces, and unnamed faces per folder."""
    dirs, counts = {}, {}
    sep = os.sep
    for pid, path, n in c.execute(
            """SELECT f.photo_id, ph.path, COUNT(*) FROM faces f INDEXED BY faces_cover
               JOIN photos ph ON ph.id = f.photo_id
               WHERE f.person_id IS NULL AND f.ignored = 0 GROUP BY f.photo_id"""):
        d = path.rpartition(sep)[0]
        dirs[pid] = d
        counts[d] = counts.get(d, 0) + n
    return dirs, counts


def build(c, ctx):
    """What the side lists show: {tree number: [top rows]} (0 People panel, 1 Folders, 2 Albums,
    3 Smart groups). Plain Python and the database only - safe on a background thread.
    ctx: what's worked out on the window's thread first (settings, folder counts, the SQL that depends
    on the library's place, duplicate sets…)."""
    import store
    S = ctx["settings"]
    tops = {0: [], 1: [], 2: [], 3: []}

    def app_icon(name, color=None):
        return ("app", name, color)

    def std_icon(n):
        return ("app", n, None)             # (ctx["SP"] gives the app's icon names for Qt's standard ones)

    def section(text, drop=None, tree=0):
        it = Row([text])
        it.setFlags(F_ENABLED | (F_DROP if drop else F_NONE))
        if drop:
            it.setData(0, ctx["ROLE_DROP"], drop)
        it.bold = True
        tops[tree].append(it)
        return it

    def face(it, fid):
        if fid is not None:
            it.icon = ("face", fid)

    SP = ctx["SP"]
    nface, nsugg, best = {}, {}, {}
    for person, n, ns, fid, _score in c.execute(
            f"""SELECT person_id, COUNT(*), TOTAL(confirmed=0), id,
                       MAX(confirmed * 10000000 + (NOT (ABS(COALESCE(yaw, 0)) > 30 OR ABS(COALESCE(pitch, 0)) > 25))
                           * 1000000 + COALESCE({store.clear_order("")}, 0))
                FROM faces INDEXED BY faces_cover WHERE ignored=0 AND person_id IS NOT NULL GROUP BY person_id"""):
        nface[person] = n
        if ns:
            nsugg[person] = int(ns)
        best[person] = fid
    ntag = dict(c.execute("""SELECT pp.person_id, COUNT(*) FROM photo_people pp JOIN photos x ON x.id=pp.photo_id
                             WHERE x.sort_later=0 AND NOT EXISTS (SELECT 1 FROM faces f WHERE f.photo_id=pp.photo_id
                             AND f.person_id=pp.person_id AND f.ignored=0) GROUP BY pp.person_id"""))
    ntat = dict(c.execute("""SELECT ts.person_id, COUNT(*) FROM tattoo_suggestions ts JOIN photos x
                             ON x.id=ts.photo_id WHERE x.sort_later=0 GROUP BY ts.person_id"""))
    people = [(pid, name, nface.get(pid, 0) + ntag.get(pid, 0) + ntat.get(pid, 0),
               nsugg.get(pid, 0) + ntat.get(pid, 0), best.get(pid), cat)
              for pid, name, cat in c.execute("SELECT id, name, category_id FROM persons ORDER BY name COLLATE NOCASE")]
    lockedF, lockedP, _ = store.locked_vault_contents(c)
    vault_info = {v[0]: v for v in store.vaults(c)}
    people = [r for r in people if r[0] not in lockedP]
    everyone = people
    people = [r for r in people if r[2] > 0]
    cat_ids = {r[0] for r in c.execute("SELECT id FROM categories")}
    listed = [r for r in people if r[5] not in cat_ids or r[3] > 0]
    people.sort(key=lambda r: (r[3] > 0, r[1].casefold()))
    waiting = sum(1 for r in listed if r[3])
    batches = store.drop_batches(c)
    secd = section(f"Recently dropped ({sum(b[2] for b in batches):,})")
    if not batches:
        it = Row(["Drag photos or folders onto the window - they land here first"])
        it.setFlags(F_NONE)
        it.setIcon(0, app_icon("drop", "grey"))
        secd.addChild(it)
    else:
        secd.setToolTip(0, "What you dragged in, laid out the way it'll be saved. Check it's tagged right, then "
                           "right-click > Done checking. Sort into folders leaves these out until then.")
        sq = ctx["sorted_sql"]
        done_of = dict(c.execute(f"""SELECT ph.dropped, TOTAL(CASE WHEN {sq} THEN 1 ELSE 0 END) FROM photos ph
                                     WHERE ph.dropped IS NOT NULL AND ph.error IS NULL GROUP BY ph.dropped"""))
        for b, name, n in batches:
            pct = int(100 * done_of.get(b, 0) // n) if n else 0
            it = Row([f"{name}   {n:,}" + (f"  · {pct}% sorted" if pct else "")])
            it.setIcon(0, app_icon("drop"))
            it.setData(0, ROLE_USER, ("dropped", b))
            it.setToolTip(0, "Dropped " + time.strftime("%d %b %Y, %H:%M", time.localtime(b))
                          + "\nRight-click > Done checking when it's tagged right")
            secd.addChild(it)
    sec = section(f"People ({len(listed)})" + (f"  ·  {waiting} to check" if waiting else ""), ("people_root",))
    cat_paths = store.category_paths(c)

    def person_item(pid, name, n, sugg, rep, cat):
        it = Row([f"{name}   {n - sugg}" + (f"  (+{sugg}?)" if sugg else "")])
        face(it, rep)
        it.fg = "yellow" if sugg else "green"
        tip = (f"{sugg} suggestion{'s' if sugg != 1 else ''} waiting for you to confirm"
               if sugg else "All faces confirmed")
        if cat in cat_paths:
            tip += "\nSaved under: " + " › ".join(cat_paths[cat])
        it.tip = tip + "\nDrag onto a folder to place them there · right-click for more"
        it.dat[ROLE_USER] = ("person", pid)
        it.dat[ROLE_USER + 1] = name.lower()
        return it
    pending_only = bool(S.get("people_pending_only"))
    # (1.63) people with suggestions waiting come first; once confirmed they go back in A-Z order
    for row in sorted(listed, key=lambda r: (not r[3], r[1].casefold())):
        if pending_only and not row[3]:
            continue
        sec.addChild(person_item(*row))
    if pending_only and not waiting:
        it = Row(["Nothing waiting to confirm"])
        it.setFlags(F_NONE)
        sec.addChild(it)
    elif not listed:
        it = Row(["Everyone is in a folder below" if people else "Name a group to add people"])
        it.setFlags(F_NONE)
        sec.addChild(it)

    cats = [r for r in c.execute("SELECT id, name, parent_id FROM categories ORDER BY name COLLATE NOCASE")
            if r[0] not in lockedF or r[0] in vault_info]
    cats.sort(key=lambda r: (r[0] not in vault_info, r[1].casefold()))
    ftree = store.folder_tree(c)
    by_cat, uncategorized = ctx["places"]
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
    secf = section(f"Folders ({len(cats)})", ("cat_root",), tree=1)
    collapsed = ctx["cat_collapsed"]

    def add_cats(parent_item, parent_id, seen=(), with_people=True):
        for cid, name in kids.get(parent_id, ()):
            if cid in seen:
                continue
            if cid in vault_info and vault_info[cid][2]:
                it = Row([f"{name}   (locked)"])
                it.setIcon(0, app_icon("lock", "grey"))
                it.setData(0, ROLE_USER, ("vault", cid))
                it.setToolTip(0, "A locked vault: its photos, people and folders are hidden everywhere.\n"
                                 "Click to unlock · right-click for more")
                parent_item.addChild(it)
                continue
            n = len(total(cid))
            it = Row([f"{name}   {n}"])
            it.setIcon(0, std_icon(SP["DirIcon"]))
            it.setData(0, ROLE_USER, ("category", cid))
            it.setData(0, ROLE_USER + 1, name.lower())
            lbl = store.folder_label(c, cid, ftree)
            it.setToolTip(0, "Saved as: " + (("…\\" + lbl.replace(store.PATH_SEP, "\\")) if ftree[cid][2]
                                             else lbl.replace(store.PATH_SEP, "\\"))
                          + "\nDrag people or folders here · right-click for more")
            if cid in vault_info:
                it.setIcon(0, app_icon("unlock"))
                it.setToolTip(0, "A vault (unlocked) - right-click > Lock to hide everything in it again")
            parent_item.addChild(it)
            add_cats(it, cid, seen + (cid,), with_people)
            for row in (sorted(placed.get(cid, ()), key=lambda r: r[1].casefold()) if with_people else []):
                it.addChild(person_item(*row))
            it.setExpanded(cid not in collapsed)
    owned = {}
    for cid, n in kids.get(None, []):
        if ftree[cid][2] is not None:
            owned.setdefault(ftree[cid][2], []).append((cid, n))
    kids[None] = [(cid, n) for cid, n in kids.get(None, []) if ftree[cid][2] is None]
    add_cats(secc, None)
    add_cats(secf, None, with_people=False)

    def walk(r):
        for k in r.kids:
            yield k
            yield from walk(k)
    # (each person's own folders under that person - outside the People panel, as before)
    for top in [r for tree in (1, 2, 3) for r in tops[tree]]:
        for it in list(walk(top)):
            k = it.dat.get(ROLE_USER)
            if k and k[0] == "person" and owned.get(k[1]):
                kids[("p", k[1])] = owned[k[1]]
                add_cats(it, ("p", k[1]))
                it.setExpanded(("p", k[1]) not in collapsed)
    for sc in (secc, secf):
        if nocat:
            it = Row([f"No folder   {nocat}"])
            it.setIcon(0, std_icon(SP["DirOpenIcon"]))
            it.setData(0, ROLE_USER, ("nocategory", None))
            sc.addChild(it)
        if not cats and not nocat:
            it = Row(["Right-click here, or press A on photos, to add a folder"])
            it.setFlags(F_NONE)
            sc.addChild(it)
    secf.setExpanded(True)
    alb = store.albums(c)
    seca = section(f"Albums ({len(alb)})", ("album_root",), tree=2)
    album_people = {}
    for aid, name in c.execute("""SELECT ap.album_id, p.name FROM album_photos ap
                                  JOIN faces f ON f.photo_id=ap.photo_id AND f.confirmed=1 AND f.ignored=0
                                  JOIN persons p ON p.id=f.person_id
                                  UNION SELECT ap.album_id, p.name FROM album_photos ap
                                  JOIN photo_people pp ON pp.photo_id=ap.photo_id JOIN persons p ON p.id=pp.person_id"""):
        if name:
            album_people.setdefault(aid, set()).add(name)
    for aid, aname, n in alb:
        it = Row([f"{aname}   {n}"])
        it.setIcon(0, app_icon("album", "purple"))
        it.setData(0, ROLE_USER, ("album", aid))
        it.setData(0, ROLE_USER + 1, aname.lower())
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
        it = Row(["Right-click here, or drag photos here, to make an album"])
        it.setFlags(F_NONE)
        seca.addChild(it)
    seca.setExpanded(True)
    import smart
    sm = smart.all_smart(c)
    secs = section(f"Smart albums ({len(sm)})", ("smart_root",), tree=2)
    for sid, sname, f in sm:
        it = Row([sname])
        it.setIcon(0, app_icon("sparkle"))
        it.setData(0, ROLE_USER, ("smart", sid))
        it.setData(0, ROLE_USER + 1, sname.lower())
        it.setToolTip(0, "Smart album: " + smart.describe(f, c) + "\n\nIt keeps itself up to date · right-click to "
                         "change it or make it a normal album")
        secs.addChild(it)
    it = Row(["Suggested smart albums…"])
    it.setIcon(0, app_icon("wand"))
    it.setData(0, ROLE_USER, ("smart_sugg", None))
    it.setToolTip(0, "Smart albums FaceSort suggests from your library (people together, events, people in a scene)")
    secs.addChild(it)
    secs.setExpanded(True)

    groups, gdirs = {}, {}
    hints_on = S.get("folder_hints", True)
    dirs, unnamed = _unnamed(c) if hints_on else ({}, {})
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

    def folder_hint(cl, n):
        if not hints_on or not gdirs.get(cl):
            return ""
        name = store.pick_folder(gdirs[cl], n, unnamed)
        return f"  · {name}?" if name else ""
    try:
        import triage
        impact = triage.group_impact(c, ctx["sorted_sql"])
    except Exception:
        impact = {}
    glist = sorted(((n, cl, fid) for cl, (n, fid, _) in groups.items() if n >= 2),
                   key=lambda g: (impact.get(g[1], 0), g[0]), reverse=True)
    mode = S.get("bottom_sort", "face")
    if mode != "face":
        buckets, title, icon = ctx["buckets"]
        sb = section(f"{title} ({len(buckets)})", tree=3)
        for key, (label, _, pids) in sorted(buckets.items(), key=lambda kv: kv[1][1]):
            it = Row([f"{label}   {len(pids)}"])
            it.setIcon(0, app_icon(icon))
            it.setData(0, ROLE_USER, ("bucket", f"{mode}|{key}"))
            it.setData(0, ROLE_USER + 1, label.lower())
            if mode == "location":
                it.setToolTip(0, key)
            sb.addChild(it)
    else:
        sec2 = section(f"Unnamed groups ({len(glist)})", tree=3)
        for i, (n, cl, fid) in enumerate(glist[:1000]):
            hint = folder_hint(cl, n)
            it = Row([f"Group {i + 1}   {n} faces{hint}"])
            todo = impact.get(cl, 0)
            it.tip = ((f"Most of these faces are in the folder “{hint[4:-1]}”\n" if hint else "") +
                      f"Naming it sorts about {todo:,} photo{'s' if todo != 1 else ''} not sorted yet")
            face(it, fid)
            it.dat[ROLE_USER] = ("group", cl)
            it.dat[ROLE_USER + 2] = i + 1
            sec2.addChild(it)

    sec3 = Row(["Other"])                       # (1.63: shared out into sections below)
    disc = c.execute("""SELECT COUNT(*) FROM photos WHERE sort_later=0 AND (discarded=1
                        OR id IN (SELECT photo_id FROM faces WHERE ignored=2))""").fetchone()[0]
    nof = c.execute(f"""SELECT COUNT(*) FROM photos ph WHERE {ctx['nofaces_sql']}""").fetchone()[0]
    nlater = c.execute("SELECT COUNT(*) FROM photos WHERE sort_later=1").fetchone()[0]
    nunassoc = c.execute(f"SELECT COUNT(*) FROM photos ph WHERE {ctx['unassoc_where']}").fetchone()[0]
    errs = c.execute("SELECT COUNT(*) FROM photos WHERE error IS NOT NULL").fetchone()[0]
    ntat = c.execute(f"""SELECT COUNT(DISTINCT ts.photo_id) FROM tattoo_suggestions ts JOIN photos ph
                         ON ph.id=ts.photo_id WHERE ph.sort_later=0 AND NOT {ctx['sorted_sql']}""").fetchone()[0]
    nmarks = c.execute("SELECT COUNT(*) FROM tattoos").fetchone()[0]
    nmaybe = c.execute("SELECT COUNT(*) FROM faces WHERE ignored=4").fetchone()[0]

    def smart_counts(where):
        row = c.execute(f"""SELECT COUNT(*), TOTAL({ctx['men_sql']}), TOTAL({ctx['unsure_sql']})
                            FROM faces f JOIN photos ph ON ph.id=f.photo_id WHERE {where}""").fetchone()
        return int(row[0]), int(row[1] or 0), int(row[2] or 0)
    unk, unk_men, unk_unsure = smart_counts(ctx["unknown_sql"])
    part, part_men, part_unsure = smart_counts(ctx["partial_sql"])
    details = bool(S.get("face_details"))
    for text, key, icon in ((f"Unknown faces   {unk}", ("unknown", None), SP["MessageBoxQuestion"]),
                            (f"Partially tagged   {part}", ("partial", None), "partial"),
                            (f"Sort Later   {nlater}", ("sortlater", None), SP["FileDialogListView"]),
                            (f"No faces found   {nof}", ("nofaces", None), SP["FileIcon"]),
                            (f"Unassociated files   {nunassoc}", ("unassoc", None), SP["FileLinkIcon"]),
                            (f"~To Be Discarded   {disc}", ("discarded", None), SP["TrashIcon"])):
        if (key[0] == "unassoc" and not nunassoc) or (key[0] == "partial" and not part):
            continue
        it = Row([text])
        if key[0] == "unassoc":
            it.setToolTip(0, "Other files (documents, music, archives…) nobody is tagged on and that aren't in a "
                             "folder yet. Tag people or set a folder and they move with Sort into folders.")
        elif key[0] == "unknown":
            it.setToolTip(0, "Faces nobody is named for, not like any group, on photos that aren't sorted at all "
                             "(nobody tagged, no folder)")
        elif key[0] == "partial":
            it.setToolTip(0, "Group photos where someone is already tagged and other faces aren't named yet")
        it.setIcon(0, app_icon(icon) if isinstance(icon, str) else std_icon(icon))
        it.setData(0, ROLE_USER, key)
        sec3.addChild(it)
        if key[0] == "sortlater" and nlater:
            _later_children(c, it, SP, ctx["later_face"])
        if key[0] in ("unknown", "partial") and details:
            men, unsure = (unk_men, unk_unsure) if key[0] == "unknown" else (part_men, part_unsure)
            for n, label, sub, ic, tip in (
                    (men, "Men", "men", "men", "Faces that look male (an estimate - unclear ones are under Unsure)"),
                    (unsure, "Unsure", "unsure", "unsure",
                     "Faces where it's hard to tell (too small, turned away, or the estimate wasn't sure)")):
                if not n:
                    continue
                ch = Row([f"{label}   {n}"])
                ch.setData(0, ROLE_USER, (key[0], sub))
                ch.setIcon(0, app_icon(ic))
                ch.setToolTip(0, tip)
                it.addChild(ch)
            it.setExpanded(True)
    if ntat or nmarks:
        it = Row([f"Tattoo matches   {ntat}"])
        it.setIcon(0, std_icon(SP["FileDialogContentsView"]))
        it.setData(0, ROLE_USER, ("tattoos", None))
        it.setToolTip(0, f"{nmarks} tattoo mark{'s' if nmarks != 1 else ''} · "
                         f"{ntat} photo{'s' if ntat != 1 else ''} with a possible match to check")
        sec3.insertChild(2, it)
    nshoot = c.execute(f"""SELECT COUNT(*) FROM body_suggestions s JOIN bodies b ON b.id=s.body_id
                           JOIN photos ph ON ph.id=b.photo_id WHERE ph.sort_later=0
                           AND NOT {ctx['sorted_sql']}""").fetchone()[0] if S.get("same_shoot") else 0
    if nshoot:
        it = Row([f"Same shoot   {nshoot}"])
        it.setIcon(0, std_icon(SP["FileDialogInfoView"]))
        it.setData(0, ROLE_USER, ("shoot", None))
        it.setToolTip(0, "People whose face couldn't be recognised, matched by their clothes to someone you "
                         "confirmed at the same occasion (same time and place)")
        sec3.insertChild(1, it)
    if nmaybe:
        it = Row([f"Probably not faces   {nmaybe}"])
        it.setIcon(0, std_icon(SP["DialogCancelButton"]))
        it.setData(0, ROLE_USER, ("maybe_notfaces", None))
        it.setToolTip(0, "Look like things you marked “not a face”. N = not a face · Restore = it is a face")
        sec3.addChild(it)
    import samepeople
    nsame = len(samepeople.pairs(c))
    if nsame:
        it = Row([f"Possibly the same person   {nsame}"])
        it.setIcon(0, app_icon("compare"))
        it.setData(0, ROLE_USER, ("same", None))
        it.setToolTip(0, "Pairs of people who may be one person: faces that look alike, almost the same name, or a "
                         "generic name that looks like someone. Merge them, or Not the same (remembered).")
        sec3.insertChild(min(2, sec3.childCount()), it)
    nai1 = c.execute("SELECT COUNT(*) FROM photos WHERE ai=1 AND error IS NULL").fetchone()[0]
    nai2 = c.execute("SELECT COUNT(*) FROM photos WHERE ai=2 AND error IS NULL").fetchone()[0]
    if (nai1 or nai2) and S.get("show_ai_group", False):
        it = Row([f"AI images   {nai1 + nai2}"])
        it.setIcon(0, app_icon("ai"))
        it.setData(0, ROLE_USER, ("ai", None))
        sec3.addChild(it)
        for n, text, key, tip in ((nai1, f"Marked as AI   {nai1}", ("ai", 1), "The file itself says it was made or edited with AI"),
                                  (nai2, f"Possibly AI   {nai2}", ("ai", 2), "The AI detector thinks so - it is often wrong")):
            if n:
                ch = Row([text])
                ch.setData(0, ROLE_USER, key)
                ch.setToolTip(0, tip)
                it.addChild(ch)
        it.setExpanded(True)
    if S.get("content_on"):
        import content
        counts = dict(c.execute(f"""SELECT pc.subject, COUNT(*) FROM photos ph JOIN photo_content pc
                                   ON pc.photo_id=ph.id WHERE {ctx['nopeople_sql']}
                                   GROUP BY pc.subject""").fetchall())
        nnp = sum(counts.values())
        if nnp:
            it = Row([f"No people   {nnp}"])
            it.setIcon(0, app_icon("nopeople"))
            it.setData(0, ROLE_USER, ("nopeople", None))
            it.setToolTip(0, "Photos with nobody in them - food, landscapes, screenshots, animals… "
                             "(no face, no body, nobody tagged, and not sorted yet)")
            sec3.addChild(it)
            for key, (label, _) in content.SUBJECTS.items():
                if counts.get(key):
                    ch = Row([f"{label}   {counts[key]}"])
                    ch.setIcon(0, app_icon(key))
                    ch.setData(0, ROLE_USER, ("nopeople", key))
                    it.addChild(ch)
    ndup = ctx["ndup"]
    it = Row([f"Duplicates   {ndup} set{'s' if ndup != 1 else ''}"])
    it.setIcon(0, app_icon("dupes"))
    it.setData(0, ROLE_USER, ("dupes", None))
    it.setToolTip(0, "The same picture more than once (exact copies, or resized / re-saved copies). FaceSort "
                     "checks new photos in the background; copies made on purpose for each person on a group "
                     "photo don't count.")
    sec3.addChild(it)
    nmis = ctx["nmisplaced"]
    if nmis:
        it = Row([f"Not where they belong   {nmis}"])
        it.setIcon(0, std_icon(SP["MessageBoxWarning"]))
        it.setData(0, ROLE_USER, ("misplaced", None))
        it.setToolTip(0, "Photos in your sorted library that are in the wrong folder (left over from an older "
                         "FaceSort, moved by hand, or tagged differently since)")
        sec3.addChild(it)
    if errs:
        it = Row([f"Unreadable files   {errs}"])
        it.setIcon(0, std_icon(SP["MessageBoxWarning"]))
        it.setData(0, ROLE_USER, ("errors", None))
        sec3.addChild(it)
    # (1.63) the smart groups in sections: what needs you first, then faces, photos, the library's health
    groups_sec = list(tops[3])
    by_kind = {k.dat.get(ROLE_USER, ("",))[0]: k for k in sec3.kids}
    order = {"needs": ("same", "shoot", "tattoos", "maybe_notfaces"),
             "faces": ("unknown", "partial"),
             "photos": ("nofaces", "nopeople", "sortlater", "ai"),
             "health": ("dupes", "misplaced", "unassoc", "discarded", "errors")}
    used = {k for ks in order.values() for k in ks}
    order["photos"] += tuple(k for k in by_kind if k not in used)          # (anything new: under Photos)
    titles = {"needs": "Needs you", "faces": "Faces", "photos": "Photos", "health": "Library health"}
    made = {}
    for key, kinds in order.items():
        kids = [by_kind[k] for k in kinds if k in by_kind]
        if not kids:
            continue
        if key == "needs":
            n = sum(_count(k.txt) for k in kids)
            sec = Row([f"{titles[key]} ({n:,})"])
            sec.setToolTip(0, "Things only you can decide: pairs that may be one person, faces matched by their "
                              "clothes or a tattoo, things that may not be faces")
        else:
            sec = Row([titles[key]])
        sec.setFlags(F_ENABLED)
        sec.bold = True
        for k in kids:
            sec.addChild(k)
        sort_other(sec, S.get("other_sort", "default"))
        sec.setExpanded(True)
        made[key] = sec
    home = Row(["Overview"])
    home.setIcon(0, app_icon("sparkle"))
    home.setData(0, ROLE_USER, ("smart_home", None))
    home.setToolTip(0, "All the smart groups at a glance - open one to work through it")
    tops[3] = [home] + [made[k] for k in ("needs",) if k in made] + groups_sec + \
        [made[k] for k in ("faces", "photos", "health") if k in made]
    return tops


def _count(text):
    import re
    m = re.search(r"([\d,]+)\D*$", text)
    return int(m.group(1).replace(",", "")) if m else 0


def sort_other(sec, mode):
    """Order the Smart Groups' "Other" list: as FaceSort lays it out, most / fewest first, or A-Z."""
    if mode == "default" or sec is None:
        return
    import re

    def count(it):
        m = re.search(r"(\d[\d,]*)\D*$", it.text(0))
        return int(m.group(1).replace(",", "")) if m else 0

    def name(it):
        return re.sub(r"[\s\d,]+(sets?)?$", "", it.text(0)).lstrip("~").casefold()
    if mode == "az":
        sec.kids.sort(key=name)
    elif mode == "most":
        sec.kids.sort(key=lambda k: (count(k), name(k)), reverse=True)
    else:
        sec.kids.sort(key=lambda k: (count(k), name(k)))


def _later_children(c, parent, SP, later_face):
    """Sort Later opened up by face: its people, look-alike groups, other faces, photos with no face."""
    people = c.execute(f"""SELECT p.id, p.name, COUNT(*), SUM(f.confirmed=0),
                                  (SELECT id FROM faces g WHERE g.person_id=p.id AND g.ignored=5 LIMIT 1)
                           FROM faces f JOIN persons p ON p.id=f.person_id WHERE {later_face}
                           GROUP BY p.id ORDER BY p.name COLLATE NOCASE""").fetchall()
    for pid, name, n, sugg, rep in people:
        it = Row([f"{name}   {n}" + (f"  (+{sugg}?)" if sugg else "")])
        if rep is not None:
            it.icon = ("face", rep)
        it.setData(0, ROLE_USER, ("later_person", pid))
        it.setData(0, ROLE_USER + 1, name.lower())
        parent.addChild(it)
    groups = c.execute(f"""SELECT f.later_cluster, COUNT(*), MAX(f.id) FROM faces f
                           WHERE {later_face} AND f.person_id IS NULL AND f.later_cluster IS NOT NULL
                           GROUP BY f.later_cluster ORDER BY COUNT(*) DESC""").fetchall()
    for i, (cl, n, fid) in enumerate(groups[:500]):
        it = Row([f"Group {i + 1}   {n} faces"])
        it.icon = ("face", fid)
        it.setData(0, ROLE_USER, ("later_group", cl))
        it.setData(0, ROLE_USER + 2, i + 1)
        parent.addChild(it)
    other = c.execute(f"""SELECT COUNT(*) FROM faces f WHERE {later_face} AND f.person_id IS NULL
                          AND f.later_cluster IS NULL""").fetchone()[0]
    nof = c.execute(f"""SELECT COUNT(*) FROM photos ph WHERE ph.sort_later=1 AND NOT EXISTS
                        (SELECT 1 FROM faces f WHERE f.photo_id=ph.id AND {later_face})""").fetchone()[0]
    for n, text, key, icon in ((other, f"Other faces   {other}", ("later_other", None), SP["MessageBoxQuestion"]),
                               (nof, f"No faces   {nof}", ("later_nofaces", None), SP["FileIcon"])):
        if n:
            it = Row([text])
            it.setIcon(0, ("app", icon, None))
            it.setData(0, ROLE_USER, key)
            parent.addChild(it)
