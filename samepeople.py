"""Possibly the same person (1.49): two people in your library who may be one.

Why a pair is suggested (any of):
- their faces look alike: a typical face of one is clearly matched by a face of the other
  (both ways), on the face engine in use
- their names are almost the same ("Jen Smith" / "Jen  Smith." / "Jenn Smith")
- one has a generic name ("Model 04") and looks like a named person

"Not the same" is remembered (and never suggested again); merging keeps the name and folder you
choose and moves everything (faces, tags, tattoos) to one person - it can be undone in History.
Background job; the results are kept in the same_people table until the next run.
"""
import difflib
import re
import time
import unicodedata

import numpy as np

import store

PER_PERSON = 40          # clearest faces per person compared
FACE_MARGIN = 0.03       # typical face matched at least this much over the matching threshold
NAME_RATIO = 0.88        # names this alike (after tidying) count as "almost the same"
MAX_PAIRS = 300


def tidy(name):
    """Name for comparing: no accents, case, punctuation or extra spaces."""
    n = unicodedata.normalize("NFKD", name or "")
    n = "".join(ch for ch in n if not unicodedata.combining(ch))
    n = re.sub(r"[^\w\s]", " ", n.casefold())
    return " ".join(n.split())


def is_generic(name, generics=()):
    m = re.match(r"^(.*\D)\s*\d+$", name or "")
    if not m:
        return False
    base = m.group(1).strip().casefold()
    return not generics or base in {g.casefold() for g in generics}


def _not_same(conn):
    return {(a, b) for a, b in conn.execute("SELECT a, b FROM not_same_people")}


def mark_not_same(conn, a, b):
    a, b = sorted((int(a), int(b)))
    conn.execute("INSERT OR IGNORE INTO not_same_people(a, b, at) VALUES(?,?,?)", (a, b, time.time()))
    conn.execute("DELETE FROM same_people WHERE a=? AND b=?", (a, b))
    conn.commit()


def _faces(conn, model):
    """{person id: unit vectors of their clearest confirmed faces}."""
    out = {}
    try:
        rows = conn.execute("""SELECT person_id, emb FROM (
                                   SELECT person_id, emb, ROW_NUMBER() OVER (PARTITION BY person_id
                                       ORDER BY COALESCE(clarity, 0) DESC, id) AS rn
                                   FROM faces WHERE confirmed=1 AND ignored=0 AND person_id IS NOT NULL
                                   AND model=? AND COALESCE(manual, 0) < 2)
                               WHERE rn <= ?""", (model, PER_PERSON)).fetchall()
    except Exception:
        rows = []
    for pid, blob in rows:
        v = np.frombuffer(blob, np.float32)
        n = np.linalg.norm(v)
        if n > 0:
            out.setdefault(pid, []).append(v / n)
    return {p: np.stack(v) for p, v in out.items()}


def face_score(A, B):
    """How alike two people's faces are: the median, both ways, of each face's best match."""
    S = A @ B.T
    return float(min(np.median(S.max(axis=1)), np.median(S.max(axis=0))))


def compute(conn, settings, progress=lambda d, t, m: None, cancelled=lambda: False, generics=(), hidden=()):
    """Work out the pairs again and keep them. Returns how many."""
    model = store.get_model(conn)
    thr = store.threshold(settings, model)
    names = {p: n for p, n in conn.execute("SELECT id, name FROM persons") if n and p not in set(hidden)}
    skip = _not_same(conn)
    found = {}

    def add(a, b, score, why):
        a, b = sorted((a, b))
        if (a, b) in skip or a == b:
            return
        got = found.setdefault((a, b), [0.0, []])
        got[0] = max(got[0], score)
        if why not in got[1]:
            got[1].append(why)

    progress(0, 3, "Possibly the same person: faces…")
    faces = {p: v for p, v in _faces(conn, model).items() if p in names}
    ids = list(faces)
    if len(ids) >= 2:
        C = np.stack([faces[p].mean(axis=0) for p in ids])
        C /= np.linalg.norm(C, axis=1, keepdims=True) + 1e-9
        sim = C @ C.T
        np.fill_diagonal(sim, -1)
        ii, jj = np.where(np.triu(sim) >= thr)
        order = np.argsort(-sim[ii, jj])[:5000]
        for n, k in enumerate(order):
            if cancelled():
                return 0
            a, b = ids[ii[k]], ids[jj[k]]
            fs = face_score(faces[a], faces[b])
            if fs >= thr + FACE_MARGIN:
                gen = is_generic(names[a], generics) or is_generic(names[b], generics)
                add(a, b, fs, "a generic name that looks like them" if gen and not
                    (is_generic(names[a], generics) and is_generic(names[b], generics)) else "faces look alike")
    progress(1, 3, "Possibly the same person: names…")
    tidied = {p: tidy(n) for p, n in names.items() if not is_generic(n, generics)}
    buckets = {}
    for p, t in tidied.items():
        if len(t) >= 4:
            buckets.setdefault(t[0], []).append(p)
            if " " in t:
                buckets.setdefault("~" + t.split()[-1], []).append(p)     # same surname, any first letter
    seen = set()
    for group in buckets.values():
        if len(group) > 3000:
            group = group[:3000]
        for i, a in enumerate(group):
            if cancelled():
                return 0
            ta = tidied[a]
            for b in group[i + 1:]:
                key = (min(a, b), max(a, b))
                if key in seen:
                    continue
                seen.add(key)
                tb = tidied[b]
                if ta == tb:
                    add(a, b, 1.0, "the same name written differently")
                    continue
                if re.sub(r"\d+", "#", ta) == re.sub(r"\d+", "#", tb):
                    continue                    # ("Jane 2" / "Jane 3": numbered on purpose)
                sm = difflib.SequenceMatcher(None, ta, tb)
                if sm.real_quick_ratio() >= NAME_RATIO and sm.quick_ratio() >= NAME_RATIO and sm.ratio() >= NAME_RATIO:
                    add(a, b, sm.ratio(), "names almost the same")
    progress(2, 3, "Possibly the same person: saving…")
    rows = sorted(found.items(), key=lambda kv: (-len(kv[1][1]), -kv[1][0]))[:MAX_PAIRS]
    with conn.write():
        conn.execute("DELETE FROM same_people")
        conn.executemany("INSERT INTO same_people(a, b, score, why) VALUES(?,?,?,?)",
                         [(a, b, float(s), "; ".join(w)) for (a, b), (s, w) in rows])
    return len(rows)


def pairs(conn):
    """[(a, b, score, why)] still worth showing (both people still there), best first."""
    try:
        rows = conn.execute("""SELECT s.a, s.b, s.score, s.why FROM same_people s
                               JOIN persons pa ON pa.id=s.a JOIN persons pb ON pb.id=s.b
                               WHERE NOT EXISTS (SELECT 1 FROM not_same_people n WHERE n.a=s.a AND n.b=s.b)
                               ORDER BY s.rowid""").fetchall()
    except Exception:
        return []
    return rows


def merge(conn, keep, other, name=None, folder="keep"):
    """Make two people one. keep: the person whose id stays; name: the name to use (default keep's);
    folder: "keep", "other" or a folder id / None. Returns the id that's left."""
    import known
    other_folder = conn.execute("SELECT category_id FROM persons WHERE id=?", (other,)).fetchone()
    keep_name = conn.execute("SELECT name FROM persons WHERE id=?", (keep,)).fetchone()[0]
    left = store.rename_person(conn, other, keep_name)       # (moves faces, tags, tattoos to keep)
    if name and name != keep_name:
        left = store.rename_person(conn, left, name)
    if folder == "other":
        cat = other_folder[0] if other_folder else None
        conn.execute("UPDATE persons SET category_id=? WHERE id=?", (cat, left))
    elif folder != "keep":
        conn.execute("UPDATE persons SET category_id=? WHERE id=?", (folder, left))
    conn.execute("DELETE FROM same_people WHERE a IN (?,?) OR b IN (?,?)", (keep, other, keep, other))
    conn.commit()
    try:
        known.invalidate() if hasattr(known, "invalidate") else None
    except Exception:
        pass
    return left
