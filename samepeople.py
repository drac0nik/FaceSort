"""Possibly the same person (1.49): two people in your library who may be one.

Why a pair is suggested (any of):
- their faces look alike: a typical face of one is clearly matched by a face of the other
  (both ways), on the face engine in use
- (1.62.1) some of their faces match closely - enough of them, not one fluke (one person named twice
  for different looks: hair, years apart, side-on)
- (1.62.1) a tattoo or piece of jewelry of one found on photos of the other
- supporting only: photos of both in the same folders
- never: two people confirmed on the same photo
- their names are almost the same ("Jen Smith" / "Jen  Smith." / "Jenn Smith")
- one has a generic name ("Model 04") and looks like a named person

"Not the same" is remembered (and never suggested again); merging keeps the name and folder you
choose and moves everything (faces, tags, tattoos) to one person - it can be undone in History.
Background job; the results are kept in the same_people table until the next run.
"""
import difflib
import os
import re
import time
import unicodedata

import numpy as np

import store

PER_PERSON = 40          # clearest faces per person compared
FACE_MARGIN = 0.03       # typical face matched at least this much over the matching threshold
NAME_RATIO = 0.88        # names this alike (after tidying) count as "almost the same"
MAX_PAIRS = 300
NEAREST = 8              # (1.62.1) people compared face by face with each person (nearest by average face)
CENTROID_SLACK = 0.12    # how far under the matching line their average faces may be and still be checked
MAX_FACE_CHECKS = 20000
PARTIAL_MIN = 3          # some faces matching: at least this many…
PARTIAL_FRAC = 0.2       # …and this share of the smaller side's
_ON_PHOTO = """SELECT photo_id, person_id FROM faces WHERE confirmed=1 AND ignored=0 AND person_id IS NOT NULL
               UNION SELECT photo_id, person_id FROM photo_people"""


def _strength(got):
    """Ranking: faces count most, then tattoos / jewelry, then names; more reasons, higher."""
    score, why = got
    w = 0.0
    for r in why:
        if "faces look alike" in r or "looks like them" in r:
            w += 2.0
        elif "faces match closely" in r:
            w += 1.5
        elif "tattoo" in r:
            w += 1.2
        elif "name" in r:
            w += 1.0
        else:
            w += 0.2
    return w + score


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
    # (1.62.1) never the same: two people confirmed on the same photo
    together = set()
    for a, b in conn.execute(f"""SELECT DISTINCT x.person_id, y.person_id FROM ({_ON_PHOTO}) x
                                 JOIN ({_ON_PHOTO}) y ON y.photo_id = x.photo_id AND y.person_id > x.person_id"""):
        together.add((a, b))
    skip |= together
    faces = {p: v for p, v in _faces(conn, model).items() if p in names}
    ids = list(faces)
    if len(ids) >= 2:
        C = np.stack([faces[p].mean(axis=0) for p in ids])
        C /= np.linalg.norm(C, axis=1, keepdims=True) + 1e-9
        sim = C @ C.T
        np.fill_diagonal(sim, -1)
        # each person's nearest few by their average face - not only pairs over the line: two halves of one
        # person (different hair, years apart, side-on) average out differently, but some faces still match
        k = min(NEAREST, len(ids) - 1)
        near = np.argpartition(-sim, k, axis=1)[:, :k] if k > 0 else np.zeros((len(ids), 0), int)
        cand = set()
        for i in range(len(ids)):
            for j in near[i]:
                if sim[i, j] >= thr - CENTROID_SLACK:
                    cand.add((min(i, j), max(i, j)))
        for n, (i, j) in enumerate(sorted(cand, key=lambda ij: -sim[ij])[:MAX_FACE_CHECKS]):
            if cancelled():
                return 0
            a, b = ids[i], ids[j]
            if tuple(sorted((a, b))) in skip:
                continue
            A, B = faces[a], faces[b]
            S = A @ B.T
            fs = float(min(np.median(S.max(axis=1)), np.median(S.max(axis=0))))
            gen = is_generic(names[a], generics) != is_generic(names[b], generics)
            if fs >= thr + FACE_MARGIN:
                add(a, b, fs, "a generic name that looks like them" if gen else "faces look alike")
                continue
            # some of their faces match closely (enough of them, not one fluke)
            ma, mb = S.max(axis=1), S.max(axis=0)
            hits = max(int((ma >= thr + FACE_MARGIN).sum()), int((mb >= thr + FACE_MARGIN).sum()))
            frac = max(float((ma >= thr + FACE_MARGIN).mean()), float((mb >= thr + FACE_MARGIN).mean()))
            if hits >= PARTIAL_MIN and frac >= PARTIAL_FRAC:
                add(a, b, 0.6 + 0.4 * frac, f"{hits} of their faces match closely")
    # tattoos / jewelry of one found on photos of the other
    try:
        for a, b, n in conn.execute(f"""SELECT ts.person_id, x.person_id, COUNT(DISTINCT ts.photo_id)
                                        FROM tattoo_suggestions ts JOIN ({_ON_PHOTO}) x ON x.photo_id = ts.photo_id
                                        WHERE x.person_id <> ts.person_id GROUP BY ts.person_id, x.person_id
                                        HAVING COUNT(DISTINCT ts.photo_id) >= 2"""):
            if a in names and b in names:
                add(a, b, min(1.0, 0.5 + 0.1 * n), f"a tattoo / jewelry match on {n} of the other's photos")
    except Exception:
        pass
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
    found = {k: v for k, v in found.items() if k not in together}
    # supporting (never on its own): photos of both in the same folders
    if found:
        dirs = {}
        involved = {p for k in found for p in k}
        for pid, path in conn.execute(f"SELECT DISTINCT x.person_id, ph.path FROM ({_ON_PHOTO}) x "
                                      "JOIN photos ph ON ph.id = x.photo_id"):
            if pid in involved:
                dirs.setdefault(pid, set()).add(os.path.dirname(path))
        for (a, b), got in found.items():
            shared = len(dirs.get(a, set()) & dirs.get(b, set()))
            if shared:
                got[1].append(f"photos in {shared} of the same folder{'s' if shared != 1 else ''}")
                got[0] += 0.05
    rows = sorted(found.items(), key=lambda kv: -_strength(kv[1]))[:MAX_PAIRS]
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
