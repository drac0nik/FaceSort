"""History (1.47): what you've done, with an undo for each change - not just the last one.

How it's recorded
- The changes you make in the window (naming, confirming, ignoring, discarding, folders,
  albums and tags, Sort Later, renaming / merging / deleting people, rules, files moved by Sort
  into folders) save the values they change *before and after* - just those rows and columns,
  as text. One click or key = one entry.
- Kept: everything from the last 30 days, and at least the latest 5,000 entries.

Undo
- Each entry can be undone on its own. Something changed again since (a face you named and
  later named someone else)? Those items are left as they are now and you're told how many;
  the rest is undone.
- Files moved by Sort into folders go back where they were, if they're still where FaceSort put
  them (and nothing new is in the old place).
"""
import contextlib
import functools
import json
import os
import shutil
import threading
import time

KEEP_DAYS = 30
KEEP_MIN = 5000

FACE_COLS = ("person_id", "confirmed", "cluster", "ignored", "face_ok", "later_prev")
PHOTO_COLS = ("category_id", "sort_later", "discarded", "dropped")
PERSON_COLS = ("name", "category_id", "sex")
_state = threading.local()


def _on():
    return getattr(_state, "on", 0) == 0


@contextlib.contextmanager
def paused():
    """Changes made inside aren't recorded (undoing, background work)."""
    _state.on = getattr(_state, "on", 0) + 1
    try:
        yield
    finally:
        _state.on -= 1


def _pending():
    p = getattr(_state, "pending", None)
    if p is None:
        p = _state.pending = []
    return p


def _ids(seq):
    try:
        return [int(x) for x in seq]
    except TypeError:
        return [int(seq)]


def _rows(conn, table, cols, ids):
    out = {}
    ids = list(dict.fromkeys(ids))
    for i in range(0, len(ids), 900):
        part = ids[i:i + 900]
        for r in conn.execute(f"SELECT id, {', '.join(cols)} FROM {table} WHERE id IN ({','.join('?' * len(part))})",
                              part):
            out[r[0]] = list(r[1:])
    return out


def _links(conn, table, col, ids):
    rows = set()
    ids = list(dict.fromkeys(ids))
    other = {"photo_people": "person_id", "photo_folders": "folder_id", "album_photos": "album_id"}[table]
    for i in range(0, len(ids), 900):
        part = ids[i:i + 900]
        rows |= set(conn.execute(f"SELECT photo_id, {other} FROM {table} WHERE {col} IN ({','.join('?' * len(part))})",
                                 part).fetchall())
    return rows


class Snapshot:
    """The before-values of the rows a change touches."""

    def __init__(self, conn, label, faces=(), photos=(), persons=(), files=None):
        self.conn, self.label = conn, label
        self.faces, self.photos, self.persons = list(faces), list(photos), list(persons)
        self.b_faces = _rows(conn, "faces", FACE_COLS, self.faces) if self.faces else {}
        self.b_photos = _rows(conn, "photos", PHOTO_COLS, self.photos) if self.photos else {}
        self.b_persons = _rows(conn, "persons", PERSON_COLS, self.persons) if self.persons else {}
        self.b_links = {t: _links(conn, t, "photo_id", self.photos) for t in ("photo_people", "photo_folders",
                                                                          "album_photos")} if self.photos else {}
        self.files = files or []

    def finish(self):
        """{"rows": [[table, id, {col: [old, new]}]], "links": [[table, added, removed]], "gone": [...]}"""
        c = self.conn
        rows = []
        for table, cols, before, ids in (("faces", FACE_COLS, self.b_faces, self.faces),
                                         ("photos", PHOTO_COLS, self.b_photos, self.photos),
                                         ("persons", PERSON_COLS, self.b_persons, self.persons)):
            if not ids:
                continue
            after = _rows(c, table, cols, ids)
            for i, old in before.items():
                new = after.get(i)
                if new is None:
                    rows.append([table, i, {"__deleted__": [old, None], "__cols__": list(cols)}])
                    continue
                diff = {col: [o, n] for col, o, n in zip(cols, old, new) if o != n}
                if diff:
                    rows.append([table, i, diff])
        links = []
        for t, before in self.b_links.items():
            after = _links(c, t, "photo_id", self.photos)
            added, removed = sorted(after - before), sorted(before - after)
            if added or removed:
                links.append([t, added, removed])
        if not rows and not links and not self.files:
            return None
        return {"rows": rows, "links": links, "files": self.files}


def record_snapshot(snap):
    data = snap.finish()
    if data:
        _pending().append((snap.label, data))


def record_files(label, moves):
    """Files moved by FaceSort: [(old path, new path)]."""
    if moves:
        _pending().append((label, {"rows": [], "links": [], "files": [list(m) for m in moves]}))


def flush(conn, label=None):
    """Everything recorded since the last flush becomes one entry (one click = one entry)."""
    p = _pending()
    if not p:
        return None
    items, _state.pending = list(p), []
    text = label or "; ".join(dict.fromkeys(lbl for lbl, _ in items))
    data = {"rows": [], "links": [], "files": []}
    for _, d in items:
        for k in data:
            data[k] += d.get(k, [])
    size = len(data["rows"]) + sum(len(a) + len(r) for _, a, r in data["links"]) + len(data["files"])
    return record(conn, "change", text, data, size)


def record(conn, kind, text, data, size=None):
    with conn.write():
        rid = conn.execute("INSERT INTO history(at, kind, text, data, size) VALUES(?,?,?,?,?)",
                           (time.time(), kind, text[:300], json.dumps(data, separators=(",", ":"), default=str),
                            size if size is not None else len(json.dumps(data)))).lastrowid
    prune(conn)
    return rid


def prune(conn):
    """Keep the last 30 days, and at least the latest 5,000 entries."""
    cut = time.time() - KEEP_DAYS * 86400
    row = conn.execute("SELECT id FROM history ORDER BY id DESC LIMIT 1 OFFSET ?", (KEEP_MIN - 1,)).fetchone()
    if row:
        with conn.write():
            conn.execute("DELETE FROM history WHERE at < ? AND id < ?", (cut, row[0]))


def entries(conn, limit=500):
    return [{"id": i, "at": at, "kind": k, "text": t, "size": s, "undone": bool(u)} for i, at, k, t, s, u in
            conn.execute("SELECT id, at, kind, text, size, undone FROM history ORDER BY id DESC LIMIT ?", (limit,))]


def undo(conn, entry_id):
    """Undo one entry. Returns (undone items, items left as they are now because they changed since)."""
    row = conn.execute("SELECT data, undone FROM history WHERE id=?", (entry_id,)).fetchone()
    if not row or row[1]:
        return 0, 0
    data = json.loads(row[0])
    done = skipped = 0
    with paused():
        with conn.write():
            rows = sorted(data.get("rows", []), key=lambda r: (r[0] != "persons", "__deleted__" not in r[2]))
            for table, rid, diff in rows:          # (people brought back first: faces point at them)
                if "__deleted__" in diff:
                    old, cols = diff["__deleted__"][0], diff["__cols__"]
                    if conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (rid,)).fetchone():
                        skipped += 1
                        continue
                    if table == "persons":
                        conn.execute(f"INSERT INTO persons(id, {', '.join(cols)}) VALUES(?{', ?' * len(cols)})",
                                     [rid] + list(old))
                        done += 1
                    else:
                        skipped += 1           # (a face or photo that's gone can't be brought back from here)
                    continue
                cols = [c for c in diff if not c.startswith("__")]
                cur = conn.execute(f"SELECT {', '.join(cols)} FROM {table} WHERE id=?", (rid,)).fetchone()
                if cur is None or any(cur[k] != diff[c][1] for k, c in enumerate(cols)):
                    skipped += 1               # changed again since (or gone): left as it is now
                    continue
                conn.execute(f"UPDATE {table} SET {', '.join(c + '=?' for c in cols)} WHERE id=?",
                             [diff[c][0] for c in cols] + [rid])
                done += 1
            other = {"photo_people": "person_id", "photo_folders": "folder_id", "album_photos": "album_id"}
            for table, added, removed in data.get("links", []):
                o = other[table]
                for pid, x in added:
                    conn.execute(f"DELETE FROM {table} WHERE photo_id=? AND {o}=?", (pid, x))
                    done += 1
                for pid, x in removed:
                    if table == "album_photos":
                        conn.execute("INSERT OR IGNORE INTO album_photos(photo_id, album_id, added) VALUES(?,?,?)",
                                     (pid, x, time.time()))
                    else:
                        conn.execute(f"INSERT OR IGNORE INTO {table}(photo_id, {o}) VALUES(?,?)", (pid, x))
                    done += 1
        for old, new in reversed(data.get("files", [])):
            if os.path.exists(new) and not os.path.exists(old):
                try:
                    os.makedirs(os.path.dirname(old), exist_ok=True)
                    shutil.move(new, old)
                    with conn.write():
                        conn.execute("UPDATE photos SET path=? WHERE path=?", (old, new))
                    done += 1
                except OSError:
                    skipped += 1
            else:
                skipped += 1
        with conn.write():
            conn.execute("UPDATE history SET undone=1 WHERE id=?", (entry_id,))
    return done, skipped


# ---------------------------------------------------------------- recording store's changes
def _ui(conn):
    return bool(getattr(conn, "ui", False)) and _on()


def _wrap(store, name, label, faces=None, photos=None, persons=None):
    """Record store.<name>'s changes (made from the window) in the history."""
    fn = getattr(store, name)
    if getattr(fn, "_history", False):
        return

    @functools.wraps(fn)
    def wrapper(conn, *a, **k):
        if not _ui(conn):
            return fn(conn, *a, **k)
        try:
            snap = Snapshot(conn, label(conn, *a, **k) if callable(label) else label,
                            faces=faces(conn, *a, **k) if faces else (),
                            photos=photos(conn, *a, **k) if photos else (),
                            persons=persons(conn, *a, **k) if persons else ())
        except Exception:
            return fn(conn, *a, **k)
        with paused():                     # (store functions calling each other: recorded once)
            res = fn(conn, *a, **k)
        try:
            record_snapshot(snap)
        except Exception:
            pass
        return res
    wrapper._history = True
    setattr(store, name, wrapper)


def _person_name(conn, pid):
    r = conn.execute("SELECT name FROM persons WHERE id=?", (pid,)).fetchone()
    return r[0] if r else "?"


def _n(seq, word):
    n = len(_ids(seq))
    return f"{n:,} {word}{'s' if n != 1 else ''}"


def install(store):
    """Hook the store functions the window changes things with (once)."""
    _state.pending = []                    # (a new window: nothing carried over)
    faces_of_photos = lambda conn, ids: [r[0] for r in conn.execute(
        f"SELECT id FROM faces WHERE photo_id IN ({','.join('?' * len(ids))})", ids)] if ids else []
    photos_of_faces = lambda conn, ids: [r[0] for r in conn.execute(
        f"SELECT DISTINCT photo_id FROM faces WHERE id IN ({','.join('?' * len(ids))})", ids)] if ids else []
    _wrap(store, "assign", lambda c, f, p: f"Named {_n(f, 'face')} {_person_name(c, p)}",
          faces=lambda c, f, p: _ids(f), photos=lambda c, f, p: photos_of_faces(c, _ids(f)))
    _wrap(store, "confirm", lambda c, f: f"Confirmed {_n(f, 'face')}", faces=lambda c, f: _ids(f),
          photos=lambda c, f: photos_of_faces(c, _ids(f)))
    _wrap(store, "reject", lambda c, f: f"Not them: {_n(f, 'face')}", faces=lambda c, f: _ids(f))
    _wrap(store, "ungroup", lambda c, f: f"Took {_n(f, 'face')} out of a group", faces=lambda c, f: _ids(f))
    _wrap(store, "set_ignored", lambda c, f, v: (f"Ignored {_n(f, 'face')}" if v else f"Un-ignored {_n(f, 'face')}"),
          faces=lambda c, f, v: _ids(f))
    _wrap(store, "set_notface", lambda c, f, state=3: f"Not a face: {_n(f, 'box')}", faces=lambda c, f, state=3: _ids(f))
    _wrap(store, "set_discarded", lambda c, f, value=True: f"{'Discarded' if value else 'Restored'} {_n(f, 'face')}",
          faces=lambda c, f, value=True: _ids(f), photos=lambda c, f, value=True: photos_of_faces(c, _ids(f)))
    _wrap(store, "set_photos_discarded",
          lambda c, p, value=True: f"{'Discarded' if value else 'Restored'} {_n(p, 'photo')}",
          photos=lambda c, p, value=True: _ids(p), faces=lambda c, p, value=True: faces_of_photos(c, _ids(p)))
    _wrap(store, "set_sort_later", lambda c, p, on=True: f"{'Sort Later' if on else 'Out of Sort Later'}: {_n(p, 'photo')}",
          photos=lambda c, p, on=True: _ids(p), faces=lambda c, p, on=True: faces_of_photos(c, _ids(p)))
    _wrap(store, "set_photo_folder", lambda c, p, cid: f"Folder for {_n(p, 'photo')}", photos=lambda c, p, cid: _ids(p))
    _wrap(store, "set_category", lambda c, p, name, people=(): f"Folder “{name}” for {_n(p, 'photo')}",
          photos=lambda c, p, name, people=(): _ids(p))
    _wrap(store, "take_out_of_person_folders", lambda c, p, owner: f"Out of {_person_name(c, owner)}'s folders",
          photos=lambda c, p, owner: _ids(p))
    _wrap(store, "tag_photos", lambda c, p, who: f"Tagged {_n(p, 'photo')} {_person_name(c, who)}",
          photos=lambda c, p, who: _ids(p))
    _wrap(store, "untag_photos", lambda c, p, who: f"Untagged {_person_name(c, who)} on {_n(p, 'photo')}",
          photos=lambda c, p, who: _ids(p))
    _wrap(store, "add_to_album", lambda c, aid, p: f"Added {_n(p, 'photo')} to an album", photos=lambda c, aid, p: _ids(p))
    _wrap(store, "remove_from_album", lambda c, aid, p: f"Took {_n(p, 'photo')} out of an album",
          photos=lambda c, aid, p: _ids(p))
    _wrap(store, "tag_photo", lambda c, p, name, on=True: f"{'Tagged' if on else 'Untagged'} “{name}”",
          photos=lambda c, p, name, on=True: _ids(p))

    def person_faces(c, pid, *a, **k):
        return [r[0] for r in c.execute("SELECT id FROM faces WHERE person_id=?", (pid,))]

    def person_photos(c, pid, *a, **k):
        return [r[0] for r in c.execute("SELECT photo_id FROM photo_people WHERE person_id=? UNION "
                                        "SELECT photo_id FROM faces WHERE person_id=?", (pid, pid))]
    _wrap(store, "rename_person", lambda c, pid, new: f"Renamed {_person_name(c, pid)} to {new}",
          faces=person_faces, photos=person_photos, persons=lambda c, pid, new: [pid])
    _wrap(store, "delete_person", lambda c, pid: f"Deleted {_person_name(c, pid)}",
          faces=person_faces, photos=person_photos, persons=lambda c, pid: [pid])
