"""Known people: a separate file of face fingerprints for every person you've named.

It lives next to the library in %LOCALAPPDATA%\\FaceSort (never in the app folder, so
updating FaceSort can't touch it) and is NOT cleared with the library or when a photo
folder is removed. New scans use it to recognize people straight away.

Only small numeric fingerprints are stored (no photos): up to CAP per person, picked to
cover different angles, ages and lighting.
"""
import hashlib
import os
import sqlite3
from contextlib import contextmanager

import numpy as np

import store

CAP = 150

SCHEMA = """
CREATE TABLE IF NOT EXISTS people(name TEXT PRIMARY KEY COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS samples(
    h TEXT PRIMARY KEY,                 -- fingerprint hash (matches faces.fhash in the library)
    name TEXT NOT NULL COLLATE NOCASE,
    emb BLOB NOT NULL,
    model TEXT DEFAULT 'sface'          -- which face engine made it (kept apart; switching back restores them)
);
CREATE INDEX IF NOT EXISTS samples_name ON samples(name);
CREATE TABLE IF NOT EXISTS info(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS photo_tags(          -- people tagged on photos without a face box
    path TEXT NOT NULL, name TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY(path, name)
);
CREATE TABLE IF NOT EXISTS photo_categories(    -- each photo's category
    path TEXT PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE
);
CREATE TABLE IF NOT EXISTS tattoo_marks(        -- tattoos you marked / confirmed (by photo path)
    path TEXT NOT NULL, name TEXT NOT NULL COLLATE NOCASE,
    x REAL, y REAL, w REAL, h REAL, src TEXT,
    PRIMARY KEY(path, name, x, y)
);
CREATE TABLE IF NOT EXISTS tattoo_rejects(
    path TEXT NOT NULL, name TEXT NOT NULL COLLATE NOCASE, PRIMARY KEY(path, name)
);
CREATE TABLE IF NOT EXISTS manual_faces(        -- faces you drew by hand (Tag Person)
    path TEXT NOT NULL, x REAL, y REAL, w REAL, h REAL, t REAL, name TEXT NOT NULL,
    model TEXT, emb BLOB, thumb BLOB, manual INTEGER, score REAL, px INTEGER
);
CREATE TABLE IF NOT EXISTS folders2(            -- your folder layout: 'A › B' paths ('' owner = normal folder)
    path TEXT NOT NULL, owner TEXT NOT NULL DEFAULT '', PRIMARY KEY(path, owner)
);
CREATE TABLE IF NOT EXISTS photo_person_folders( -- person folders each photo is in
    path TEXT NOT NULL, person TEXT NOT NULL, chain TEXT NOT NULL, PRIMARY KEY(path, person, chain)
);
CREATE TABLE IF NOT EXISTS cat_tree(            -- your category structure (nesting)
    name TEXT PRIMARY KEY COLLATE NOCASE, parent TEXT COLLATE NOCASE
);
CREATE TABLE IF NOT EXISTS person_place(        -- which category each person is placed in
    person TEXT PRIMARY KEY COLLATE NOCASE, category TEXT NOT NULL COLLATE NOCASE
);
CREATE TABLE IF NOT EXISTS marks(               -- faces you ignored (1) or discarded (2)
    path TEXT NOT NULL, x REAL, y REAL, w REAL, h REAL, state INTEGER,
    PRIMARY KEY(path, x, y)
);
CREATE TABLE IF NOT EXISTS face_names(          -- faces you confirmed: who is where in each photo
    path TEXT NOT NULL, x REAL, y REAL, w REAL, h REAL, t REAL, name TEXT NOT NULL,
    PRIMARY KEY(path, x, y, t)
);
"""

# tables keyed by photo path (moved / renamed together; copied to the Photos library's reference file)
PATH_TABLES = ("photo_tags", "photo_categories", "tattoo_marks", "tattoo_rejects", "manual_faces",
               "photo_person_folders", "marks", "face_names")
REF_NAME = "FaceSort Library.fslib"     # reference file kept in your Photos library folder


def path():
    return os.path.join(store.data_dir(), "known_people.db")


def connect(p=None):
    p = p or path()
    fresh = not os.path.exists(p)
    c = sqlite3.connect(p, timeout=30)
    c.executescript(SCHEMA)  # also adds tables introduced in newer versions
    if "model" not in {r[1] for r in c.execute("PRAGMA table_info(samples)")}:
        c.execute("ALTER TABLE samples ADD COLUMN model TEXT DEFAULT 'sface'")
        c.commit()
    if "inside" not in {r[1] for r in c.execute("PRAGMA table_info(cat_tree)")}:
        c.execute("ALTER TABLE cat_tree ADD COLUMN inside INTEGER DEFAULT 0")
        c.commit()
    if fresh:
        c.execute("INSERT OR IGNORE INTO info(k, v) VALUES('format', 'facesort-known-people-1')")
        c.commit()
    return c


@contextmanager
def _db():
    """Open, commit and always close - never leaves the file locked."""
    c = connect()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def ehash(blob):
    return hashlib.sha1(bytes(blob)).hexdigest()


def counts():
    with _db() as k:
        return (k.execute("SELECT COUNT(*) FROM people").fetchone()[0],
                k.execute("SELECT COUNT(*) FROM samples").fetchone()[0])


def references(model="sface"):
    """[(name, embedding, hash)] for recognition with the given engine."""
    with _db() as k:
        return [(n, store.emb_from_blob(b), h) for h, n, b in
                k.execute("SELECT h, name, emb FROM samples WHERE model=?", (model,))]


def _diverse(embs, cap):
    """Farthest-point sampling: keeps a spread of looks instead of near-duplicates."""
    n = len(embs)
    if n <= cap:
        return list(range(n))
    E = np.stack(embs)
    mean = E.mean(axis=0)
    first = int(np.argmax(E @ mean))
    chosen = [first]
    dist = 1 - E @ E[first]
    for _ in range(cap - 1):
        i = int(np.argmax(dist))
        chosen.append(i)
        dist = np.minimum(dist, 1 - E @ E[i])
    return chosen


def _trim(k, names):
    for name in names:
        for (model,) in k.execute("SELECT DISTINCT model FROM samples WHERE name=?", (name,)).fetchall():
            rows = k.execute("SELECT h, emb FROM samples WHERE name=? AND model=? ORDER BY h", (name, model)).fetchall()
            if len(rows) <= CAP:
                continue
            keep = set(_diverse([store.emb_from_blob(b) for _, b in rows], CAP))
            k.executemany("DELETE FROM samples WHERE h=?", [(h,) for i, (h, _) in enumerate(rows) if i not in keep])


def sync_from_library(conn):
    """Bring the known-people file up to date with what you've confirmed in the library."""
    # faces hidden by a locked vault count as what they really are (nothing is forgotten while locked)
    conn.execute("""CREATE TEMP VIEW IF NOT EXISTS fx AS SELECT id, photo_id, person_id, confirmed,
                    CASE WHEN ignored=6 THEN COALESCE(vault_prev, 0) ELSE ignored END AS ignored,
                    later_prev, emb, fhash, model, manual, x, y, w, h, frame_t, score, px, thumb, quality
                    FROM faces""")
    decided = {}     # face hash -> name you gave it ("" = ignored), only for faces you decided on
    new = {}         # confirmed faces to add: hash -> (name, blob)
    for h, name, conf, ign, blob, model in conn.execute(
            """SELECT f.fhash, p.name, f.confirmed, f.ignored,
                      CASE WHEN f.confirmed=1 AND f.ignored=0 AND f.person_id IS NOT NULL THEN f.emb END, f.model
               FROM fx f LEFT JOIN persons p ON p.id=f.person_id
               WHERE (f.ignored IN (1, 2, 3) OR (f.confirmed=1 AND f.person_id IS NOT NULL AND f.ignored=0))
               AND COALESCE(f.manual, 0)<2"""):
        if ign:
            decided[h] = ""
        else:
            decided[h] = name
            new[h] = (name, blob, model or "sface")
    rejected = {}    # face hash -> names you said it is NOT
    for h, name in conn.execute("""SELECT f.fhash, p.name FROM rejects r
                                   JOIN faces f ON f.id=r.face_id JOIN persons p ON p.id=r.person_id"""):
        rejected.setdefault(h, set()).add(name.casefold())
    with _db() as k:
        known = {h: n for h, n in k.execute("SELECT h, name FROM samples")}
        # a remembered face stops counting if you ignored it, named it someone else, or said "not them"
        stale = [h for h, n in known.items()
                 if (h in decided and decided[h].casefold() != n.casefold())
                 or n.casefold() in rejected.get(h, ())]
        k.executemany("DELETE FROM samples WHERE h=?", [(h,) for h in stale])
        stale = set(stale)
        changed = set()
        for h, (name, blob, model) in new.items():
            if h not in stale and known.get(h, "").casefold() == name.casefold():
                continue
            k.execute("INSERT OR IGNORE INTO people(name) VALUES(?)", (name,))
            k.execute("INSERT OR REPLACE INTO samples(h, name, emb, model) VALUES(?,?,?,?)", (h, name, blob, model))
            changed.add(name)
        _trim(k, changed)
        k.execute("DELETE FROM people WHERE name NOT IN (SELECT DISTINCT name FROM samples)")
        # ignored / discarded faces, so they stay that way after clearing the library
        marks = conn.execute("""SELECT ph.path, f.x, f.y, f.w, f.h,
                                       CASE WHEN f.ignored=5 THEN f.later_prev ELSE f.ignored END FROM fx f
                                JOIN photos ph ON ph.id=f.photo_id
                                WHERE f.ignored IN (1, 2, 3) OR (f.ignored=5 AND f.later_prev IN (1, 2, 3))""").fetchall()
        lib_paths = [r[0] for r in conn.execute("SELECT path FROM photos")]
        k.execute("CREATE TEMP TABLE lib(path TEXT PRIMARY KEY)")
        k.executemany("INSERT OR IGNORE INTO lib VALUES(?)", [(p,) for p in lib_paths])
        k.execute("DELETE FROM marks WHERE path IN (SELECT path FROM lib)")  # library is the truth for its photos
        k.executemany("INSERT OR REPLACE INTO marks VALUES(?,?,?,?,?,?)",
                      [(p, round(x, 4), round(y, 4), w, h, st) for p, x, y, w, h, st in marks])
        k.executemany("INSERT OR REPLACE INTO marks VALUES(?,-1,-1,0,0,2)",   # whole photo discarded
                      conn.execute("SELECT path FROM photos WHERE discarded=1").fetchall())
        k.executemany("INSERT OR REPLACE INTO marks VALUES(?,-2,-2,0,0,5)",   # in Sort Later
                      conn.execute("SELECT path FROM photos WHERE sort_later=1 OR (sort_later=2 AND vault_prev=1)").fetchall())
        _save_structure(conn, k)
        k.execute("DELETE FROM face_names WHERE path IN (SELECT path FROM lib)")
        k.executemany("INSERT OR REPLACE INTO face_names VALUES(?,?,?,?,?,?,?)", conn.execute(
            """SELECT ph.path, f.x, f.y, f.w, f.h, COALESCE(f.frame_t, 0), p.name FROM fx f
               JOIN photos ph ON ph.id=f.photo_id JOIN persons p ON p.id=f.person_id
               WHERE f.confirmed=1 AND COALESCE(f.manual, 0)=0
               AND (f.ignored=0 OR (f.ignored=5 AND COALESCE(f.later_prev, 0)=0))""").fetchall())
        k.execute("DELETE FROM manual_faces WHERE path IN (SELECT path FROM lib)")
        k.executemany("INSERT INTO manual_faces VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", conn.execute(
            """SELECT ph.path, f.x, f.y, f.w, f.h, f.frame_t, p.name, f.model, f.emb, f.thumb, f.manual, f.score, f.px
               FROM fx f JOIN photos ph ON ph.id=f.photo_id JOIN persons p ON p.id=f.person_id
               WHERE f.manual>0 AND f.confirmed=1""").fetchall())
        k.execute("DELETE FROM tattoo_marks WHERE path IN (SELECT path FROM lib)")
        k.executemany("INSERT OR REPLACE INTO tattoo_marks VALUES(?,?,?,?,?,?,?)", [
            (p, n, round(x, 4), round(y, 4), w, h, src) for p, n, x, y, w, h, src in conn.execute(
                """SELECT ph.path, pe.name, t.x, t.y, t.w, t.h, t.src FROM tattoos t
                   JOIN photos ph ON ph.id=t.photo_id JOIN persons pe ON pe.id=t.person_id""")])
        k.execute("DELETE FROM tattoo_rejects WHERE path IN (SELECT path FROM lib)")
        k.executemany("INSERT OR IGNORE INTO tattoo_rejects VALUES(?,?)", conn.execute(
            """SELECT ph.path, pe.name FROM tattoo_rejects r JOIN photos ph ON ph.id=r.photo_id
               JOIN persons pe ON pe.id=r.person_id""").fetchall())
        k.execute("DELETE FROM photo_categories WHERE path IN (SELECT path FROM lib)")
        import store
        tree = store.folder_tree(conn)
        paths = store.category_paths(conn)
        k.executemany("INSERT OR REPLACE INTO photo_categories(path, name) VALUES(?,?)", [
            (p, store.PATH_SEP.join(paths[c])) for p, c in conn.execute(
                "SELECT path, category_id FROM photos WHERE category_id IS NOT NULL") if c in tree and tree[c][2] is None])
        k.execute("DELETE FROM photo_person_folders WHERE path IN (SELECT path FROM lib)")
        names = dict(conn.execute("SELECT id, name FROM persons"))
        k.executemany("INSERT OR IGNORE INTO photo_person_folders(path, person, chain) VALUES(?,?,?)", [
            (p, names[tree[f][2]], store.PATH_SEP.join(paths[f])) for p, f in conn.execute(
                """SELECT ph.path, pf.folder_id FROM photo_folders pf JOIN photos ph ON ph.id=pf.photo_id""")
            if f in tree and tree[f][2] in names])
        k.execute("DELETE FROM photo_tags WHERE path IN (SELECT path FROM lib)")
        k.executemany("INSERT OR IGNORE INTO photo_tags(path, name) VALUES(?,?)", conn.execute(
            """SELECT ph.path, p.name FROM photo_people pp JOIN photos ph ON ph.id=pp.photo_id
               JOIN persons p ON p.id=pp.person_id""").fetchall())
        k.execute("DROP TABLE lib")
    try:
        root = store.load_settings().get("library_root")
        if root:
            export_root(root)         # keep the Photos library's reference file up to date
    except Exception:
        pass


def _save_structure(conn, k):
    # folder layout and people's places (the library is the truth while it has any)
    import store
    ftree = store.folder_tree(conn)
    paths = store.category_paths(conn)
    names = dict(conn.execute("SELECT id, name FROM persons"))
    rows = [(store.PATH_SEP.join(paths[c]), names.get(v[2], "") if v[2] else "") for c, v in ftree.items()]
    places = [(n, store.PATH_SEP.join(paths[c])) for n, c in conn.execute(
        "SELECT name, category_id FROM persons WHERE category_id IS NOT NULL") if c in paths]
    if rows or places:
        k.execute("DELETE FROM folders2")
        k.executemany("INSERT OR REPLACE INTO folders2(path, owner) VALUES(?,?)", rows)
        k.execute("DELETE FROM person_place")
        k.executemany("INSERT OR REPLACE INTO person_place(person, category) VALUES(?,?)", places)


def save_structure(conn):
    """Remember the category tree and people's places right away (survives Clear library)."""
    with _db() as k:
        _save_structure(conn, k)


def person_folders_for(path_):
    """[(person name, 'Beach 2023 › Day 2')] remembered for this photo."""
    with _db() as k:
        return k.execute("SELECT person, chain FROM photo_person_folders WHERE path=?", (path_,)).fetchall()


def manual_for(path_):
    """Faces you drew on this photo: [dict] ready for store.store_photo(manual_faces=...)."""
    import hashlib
    with _db() as k:
        rows = k.execute("SELECT x, y, w, h, t, name, model, emb, thumb, manual, score, px FROM manual_faces "
                         "WHERE path=?", (path_,)).fetchall()
    return [{"x": x, "y": y, "w": w, "h": h, "frame_t": t, "name": n, "model": m, "emb": e, "thumb": th,
             "manual": mn, "score": sc or 0.0, "px": px or 0, "fhash": hashlib.sha1((e or b"") + str(x).encode()).hexdigest()}
            for x, y, w, h, t, n, m, e, th, mn, sc, px in rows]


def marks_for(path_):
    """[(x, y, w, h, state)] remembered for one photo."""
    with _db() as k:
        return k.execute("SELECT x, y, w, h, state FROM marks WHERE path=?", (path_,)).fetchall()


def rename(old, new):
    with _db() as k:
        k.execute("INSERT OR IGNORE INTO people(name) VALUES(?)", (new,))
        k.execute("UPDATE samples SET name=? WHERE name=?", (new, old))
        k.execute("UPDATE OR REPLACE photo_tags SET name=? WHERE name=?", (new, old))
        k.execute("UPDATE OR REPLACE tattoo_marks SET name=? WHERE name=?", (new, old))
        k.execute("UPDATE OR REPLACE tattoo_rejects SET name=? WHERE name=?", (new, old))
        k.execute("UPDATE OR REPLACE person_place SET person=? WHERE person=?", (new, old))
        if old.casefold() != new.casefold():
            k.execute("DELETE FROM people WHERE name=?", (old,))
        else:
            k.execute("UPDATE people SET name=? WHERE name=?", (new, old))
        _trim(k, [new])


def forget(name):
    with _db() as k:
        k.execute("DELETE FROM samples WHERE name=?", (name,))
        k.execute("DELETE FROM photo_tags WHERE name=?", (name,))
        k.execute("DELETE FROM tattoo_marks WHERE name=?", (name,))
        k.execute("DELETE FROM tattoo_rejects WHERE name=?", (name,))
        k.execute("DELETE FROM person_place WHERE person=?", (name,))
        k.execute("DELETE FROM people WHERE name=?", (name,))


def export_to(dest):
    if os.path.exists(dest):
        os.remove(dest)
    with _db() as k:
        k.execute("VACUUM INTO ?", (dest,))


def import_from(src):
    """Merge another known-people file in. Returns the number of people in it."""
    s = sqlite3.connect(src)
    try:
        fmt = s.execute("SELECT v FROM info WHERE k='format'").fetchone()
        if not fmt or not str(fmt[0]).startswith("facesort-known-people"):
            raise ValueError("This isn't a FaceSort people file.")
        cols = {r[1] for r in s.execute("PRAGMA table_info(samples)")}
        rows = s.execute("SELECT h, name, emb, " + ("model" if "model" in cols else "'sface'") + " FROM samples").fetchall()
    finally:
        s.close()
    names = set()
    with _db() as k:
        for h, name, blob, model in rows:
            k.execute("INSERT OR IGNORE INTO people(name) VALUES(?)", (name,))
            k.execute("INSERT OR IGNORE INTO samples(h, name, emb, model) VALUES(?,?,?,?)", (h, name, blob, model))
            names.add(name)
        _trim(k, names)
    return len(names)


def move_marks(old_path, new_path):
    """Keep everything remembered about a photo attached when it is moved."""
    with _db() as k:
        for t in PATH_TABLES:
            k.execute(f"UPDATE OR REPLACE {t} SET path=? WHERE path=?", (new_path, old_path))


def copy_marks(src_path, new_path):
    """A photo was copied (Sort into folders makes a copy for each person on it): the copy starts
    with everything remembered about the original - who is where, tags, ignored faces..."""
    with _db() as k:
        for t in PATH_TABLES:
            cols = [r[1] for r in k.execute(f"PRAGMA table_info({t})")]
            rest = ", ".join(c for c in cols if c != "path")
            k.execute(f"INSERT OR REPLACE INTO {t}(path{', ' + rest if rest else ''}) "
                      f"SELECT ?{', ' + rest if rest else ''} FROM {t} WHERE path=?", (new_path, src_path))


def move_prefix(old_dir, new_dir):
    """A folder was renamed / moved: every remembered photo inside it follows."""
    old_dir, new_dir = old_dir.rstrip("\\/"), new_dir.rstrip("\\/")
    pre = old_dir + os.sep
    with _db() as k:
        for t in PATH_TABLES:
            k.execute(f"UPDATE OR REPLACE {t} SET path=? || substr(path, ?) WHERE substr(path, 1, ?)=?",
                      (new_dir, len(old_dir) + 1, len(pre), pre))


def face_names_for(path_):
    """[(name, x, y, w, h)] of the faces you confirmed in this photo."""
    with _db() as k:
        return k.execute("SELECT name, x, y, w, h FROM face_names WHERE path=?", (path_,)).fetchall()


def _rel(root, p):
    return os.path.relpath(p, root).replace(os.sep, "/")


def export_root(root, conn=None):
    """Write the reference file in the Photos library folder: who is in each photo there,
    tags, folders, discards, Sort Later, drawn faces, tattoos - by path inside the library -
    plus the people's fingerprints and your folder layout. Choosing that folder again later
    (or on another PC) brings all of it back without confirming anything again."""
    root = store.norm_path(root)
    if not os.path.isdir(root):
        return False
    ref = os.path.join(root, REF_NAME)
    pre = root.rstrip("\\/") + os.sep
    try:
        r = sqlite3.connect(ref, timeout=30)
        r.executescript(SCHEMA)
        if "inside" not in {x[1] for x in r.execute("PRAGMA table_info(cat_tree)")}:
            r.execute("ALTER TABLE cat_tree ADD COLUMN inside INTEGER DEFAULT 0")
        with _db() as k:
            names = set()
            for t in PATH_TABLES:
                cols = [x[1] for x in k.execute(f"PRAGMA table_info({t})")]
                rows = k.execute(f"SELECT {','.join(cols)} FROM {t} WHERE substr(path, 1, ?)=?",
                                 (len(pre), pre)).fetchall()
                pi = cols.index("path")
                r.execute(f"DELETE FROM {t}")
                r.executemany(f"INSERT OR REPLACE INTO {t}({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                              [tuple(_rel(root, v) if i == pi else v for i, v in enumerate(row)) for row in rows])
                if "name" in cols:
                    names |= {row[cols.index("name")] for row in rows}
                if "person" in cols:
                    names |= {row[cols.index("person")] for row in rows}
            for t in ("folders2", "person_place"):
                cols = [x[1] for x in k.execute(f"PRAGMA table_info({t})")]
                r.execute(f"DELETE FROM {t}")
                r.executemany(f"INSERT OR REPLACE INTO {t}({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                              k.execute(f"SELECT {','.join(cols)} FROM {t}").fetchall())
            r.execute("DELETE FROM samples")
            r.execute("DELETE FROM people")
            for n in names:
                r.execute("INSERT OR IGNORE INTO people(name) VALUES(?)", (n,))
                r.executemany("INSERT OR REPLACE INTO samples(h, name, emb, model) VALUES(?,?,?,?)",
                              k.execute("SELECT h, name, emb, model FROM samples WHERE name=?", (n,)).fetchall())
        r.execute("INSERT OR REPLACE INTO info(k, v) VALUES('format', 'facesort-library-1')")
        r.commit()
        r.close()
        return True
    except (sqlite3.Error, OSError):
        return False


def import_root(root):
    """Bring back what the Photos library's reference file remembers (paths become paths
    inside root, wherever it is now). Returns how many photos it knows about."""
    root = store.norm_path(root)
    ref = os.path.join(root, REF_NAME)
    if not os.path.exists(ref):
        return 0
    try:
        r = sqlite3.connect(ref, timeout=30)
        r.executescript(SCHEMA)
    except sqlite3.Error:
        return 0
    paths = set()
    with _db() as k:
        for t in PATH_TABLES:
            cols = [x[1] for x in r.execute(f"PRAGMA table_info({t})")]
            kcols = {x[1] for x in k.execute(f"PRAGMA table_info({t})")}
            cols = [c_ for c_ in cols if c_ in kcols]
            pi = cols.index("path")
            rows = r.execute(f"SELECT {','.join(cols)} FROM {t}").fetchall()
            out = []
            for row in rows:
                p = store.norm_path(os.path.join(root, *row[pi].split("/")))
                paths.add(p)
                out.append(tuple(p if i == pi else v for i, v in enumerate(row)))
            k.executemany(f"INSERT OR REPLACE INTO {t}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", out)
        k.executemany("INSERT OR IGNORE INTO people(name) VALUES(?)", r.execute("SELECT name FROM people").fetchall())
        k.executemany("INSERT OR IGNORE INTO samples(h, name, emb, model) VALUES(?,?,?,?)",
                      r.execute("SELECT h, name, emb, model FROM samples").fetchall())
        for t, cols in (("folders2", "path, owner"), ("person_place", "person, category")):
            k.executemany(f"INSERT OR IGNORE INTO {t}({cols}) VALUES(?,?)",
                          r.execute(f"SELECT {cols} FROM {t}").fetchall())
    r.close()
    return len(paths)


def photo_tags_for(path_):
    with _db() as k:
        return [r[0] for r in k.execute("SELECT name FROM photo_tags WHERE path=?", (path_,))]


def names():
    """Everyone remembered, even people with no photos in the current library."""
    with _db() as k:
        return [r[0] for r in k.execute("SELECT name FROM people ORDER BY name COLLATE NOCASE")]


def category_for(path_):
    with _db() as k:
        r = k.execute("SELECT name FROM photo_categories WHERE path=?", (path_,)).fetchone()
        return r[0] if r else None


def category_names():
    """Every category remembered (also ones with no photos in the current folders)."""
    with _db() as k:
        return [r[0] for r in k.execute("SELECT DISTINCT name FROM photo_categories ORDER BY name COLLATE NOCASE")]


def rename_category(old, new):
    with _db() as k:
        k.execute("UPDATE photo_categories SET name=? WHERE name=?", (new, old))
        k.execute("DELETE FROM cat_tree WHERE name=? AND EXISTS (SELECT 1 FROM cat_tree WHERE name=?)", (old, new))
        k.execute("UPDATE cat_tree SET name=? WHERE name=?", (new, old))
        k.execute("UPDATE cat_tree SET parent=? WHERE parent=?", (new, old))
        k.execute("UPDATE person_place SET category=? WHERE category=?", (new, old))


def forget_category(name):
    with _db() as k:
        k.execute("DELETE FROM photo_categories WHERE name=?", (name,))
        r = k.execute("SELECT parent FROM cat_tree WHERE name=?", (name,)).fetchone()
        parent = r[0] if r else None
        k.execute("UPDATE cat_tree SET parent=? WHERE parent=?", (parent, name))
        if parent:
            k.execute("UPDATE person_place SET category=? WHERE category=?", (parent, name))
        else:
            k.execute("DELETE FROM person_place WHERE category=?", (name,))
        k.execute("DELETE FROM cat_tree WHERE name=?", (name,))


def tattoo_marks_for(path_):
    with _db() as k:
        return k.execute("SELECT name, x, y, w, h, src FROM tattoo_marks WHERE path=?", (path_,)).fetchall()


def tattoo_rejects_for(path_):
    with _db() as k:
        return [r[0] for r in k.execute("SELECT name FROM tattoo_rejects WHERE path=?", (path_,))]


def apply_structure(conn):
    """Recreate your folder layout and people's places in a fresh library."""
    import store
    with _db() as k:
        rows = k.execute("SELECT path, owner FROM folders2").fetchall()
        places = k.execute("SELECT person, category FROM person_place").fetchall()
        tree = k.execute("SELECT name, parent, inside FROM cat_tree").fetchall() if not rows else []
    if rows:
        for path_, owner in sorted(rows, key=lambda r: r[0].count(store.PATH_SEP)):
            parts = path_.split(store.PATH_SEP)
            store.folder_chain(conn, parts, owner=store.get_or_create_person(conn, owner) if owner else None)
        for person, cat in places:
            cid = store.folder_chain(conn, cat.split(store.PATH_SEP))
            conn.execute("UPDATE persons SET category_id=? WHERE id=?", (cid, store.get_or_create_person(conn, person)))
        conn.commit()
        return
    ids = {name.casefold(): store.get_or_create_category(conn, name) for name, _, _ in tree}
    for name, parent, inside in tree:
        if parent:
            pid = ids.get(parent.casefold()) or store.get_or_create_category(conn, parent)
            store.move_category(conn, ids[name.casefold()], pid)
        elif inside:     # old shared "inside each person" folder: turned into person folders on load
            conn.execute("UPDATE categories SET inside_person=1 WHERE id=?", (ids[name.casefold()],))
    for person, cat in places:
        cid = ids.get(cat.casefold()) or store.get_or_create_category(conn, cat)
        conn.execute("UPDATE persons SET category_id=? WHERE id=?", (cid, store.get_or_create_person(conn, person)))
    conn.commit()
