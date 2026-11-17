"""Smart albums (1.48): saved filters that fill themselves.

A smart album is a set of conditions - every one you fill in must match:
- people: these people on it (any / all of them) - a confirmed face or whole-photo tag (and, if
  ticked, a suggestion at least as sure as the rules' setting)
- tags: tagged with these (any / all); not tagged with these
- subject, pose
- taken between two dates
- in a library folder (or one inside it), or with someone placed in it
- with faces / without faces; photos / videos
- Safe / Suggestive / Explicit
Photos in a locked vault are never in one. A smart album changes as the library changes; "Make a
normal album" freezes what's in it now.

Suggestions (Albums > Suggested smart albums): people often photographed together, a burst of photos
over a few days (an event), and a person often in one scene (Anna · Beach). Say no and it's not
suggested again.

Near misses: photos that match everything except one tag, which FaceSort thinks they probably have.
Yes tags the photo (FaceSort learns the tag from it), No teaches it that the photo isn't that.
"""
import json
import time
from datetime import datetime

import store

LEVELS = {"safe": "pc.level=0", "suggestive": "pc.level=1", "explicit": "pc.level=2",
          "not_explicit": "COALESCE(pc.level, 0) IN (0, 1, 3)", "safe_only": "pc.level=0"}
LEVEL_LABELS = [("", "Any"), ("safe", "Safe"), ("suggestive", "Suggestive"), ("explicit", "Explicit"),
                ("not_explicit", "Not explicit")]
PAIR_MIN = 30          # people photographed together at least this often -> suggested
EVENT_MIN = 40         # photos in one burst -> suggested as an event
EVENT_GAP = 2 * 86400  # a gap this long ends a burst
EVENT_SPAN = 10 * 86400
SCENE_MIN = 15         # a person in a scene this often -> suggested
NEAR_P = 0.6           # near misses: at least this sure the photo has the missing tag


def _day(text, end=False):
    try:
        t = datetime.strptime(text, "%Y-%m-%d").timestamp()
        return t + 86400 - 0.001 if end else t
    except (TypeError, ValueError):
        return None


def conditions(f, threshold=None, skip=None):
    """(SQL conditions on photos ph, args). skip: one key left out (near misses)."""
    where = ["ph.error IS NULL", "COALESCE(ph.sort_later, 0) != 2", "ph.discarded=0"]
    args = []
    people = [int(p) for p in f.get("people") or []]
    if people and skip != "people":
        sugg = ""
        if f.get("suggested") and threshold is not None:
            sugg = f" OR (f.confirmed=0 AND COALESCE(f.sugg_conf, 0) >= {float(threshold)})"
        has = ("(EXISTS (SELECT 1 FROM faces f WHERE f.photo_id=ph.id AND f.person_id=? AND f.ignored=0 "
               f"AND (f.confirmed=1{sugg})) OR EXISTS (SELECT 1 FROM photo_people pp WHERE pp.photo_id=ph.id "
               "AND pp.person_id=?))")
        where.append("(" + (" AND " if f.get("people_mode") == "all" else " OR ").join([has] * len(people)) + ")")
        for p in people:
            args += [p, p]
    tagged = ("EXISTS (SELECT 1 FROM album_photos ap JOIN albums al ON al.id=ap.album_id "
              "WHERE ap.photo_id=ph.id AND al.name=?)")
    tags = [t for t in f.get("tags") or [] if t and t != skip]
    if tags and skip != "tags":
        where.append("(" + (" AND " if f.get("tags_mode") == "all" else " OR ").join([tagged] * len(tags)) + ")")
        args += tags
    for t in f.get("not_tags") or []:
        where.append("NOT " + tagged)
        args.append(t)
    if f.get("subject") and skip != "subject":
        where.append("EXISTS (SELECT 1 FROM photo_content pc WHERE pc.photo_id=ph.id AND pc.subject=?)")
        args.append(f["subject"])
    if f.get("pose") and skip != "pose":
        where.append("EXISTS (SELECT 1 FROM photo_pose po WHERE po.photo_id=ph.id AND po.pose=?)")
        args.append(f["pose"])
    a, b = _day(f.get("date_from")), _day(f.get("date_to"), end=True)
    if a is not None:
        where.append("COALESCE(ph.taken, ph.created, ph.mtime) >= ?")
        args.append(a)
    if b is not None:
        where.append("COALESCE(ph.taken, ph.created, ph.mtime) <= ?")
        args.append(b)
    if f.get("faces") == "with":
        where.append("ph.nfaces > 0")
    elif f.get("faces") == "without":
        where.append("ph.nfaces = 0")
    if f.get("media") == "photos":
        where.append("COALESCE(ph.is_video, 0) = 0")
    elif f.get("media") == "videos":
        where.append("ph.is_video = 1")
    if f.get("level") in LEVELS:
        where.append(f"EXISTS (SELECT 1 FROM photo_content pc WHERE pc.photo_id=ph.id AND {LEVELS[f['level']]})")
    return where, args


def photo_ids(conn, f, threshold=None, skip=None):
    where, args = conditions(f, threshold, skip)
    try:
        ids = [r[0] for r in conn.execute(f"SELECT ph.id FROM photos ph WHERE {' AND '.join(where)}", args)]
    except Exception:
        return []                      # (a table a condition needs isn't there yet)
    if f.get("folder"):
        fid = int(f["folder"])
        inside = {fid} | store.category_descendants(conn, fid)
        marks = ",".join(map(str, inside))
        ok = {r[0] for r in conn.execute(f"SELECT id FROM photos WHERE category_id IN ({marks})")}
        ok |= {r[0] for r in conn.execute(f"""SELECT f.photo_id FROM faces f JOIN persons p ON p.id=f.person_id
                                              WHERE f.confirmed=1 AND p.category_id IN ({marks})""")}
        ok |= {r[0] for r in conn.execute(f"""SELECT pp.photo_id FROM photo_people pp JOIN persons p ON p.id=pp.person_id
                                              WHERE p.category_id IN ({marks})""")}
        ids = [i for i in ids if i in ok]
    return ids


def describe(f, conn=None):
    """A smart album's filter in words."""
    parts = []
    if f.get("people"):
        names = f.get("people_names") or [str(p) for p in f["people"]]
        parts.append(("with all of " if f.get("people_mode") == "all" and len(names) > 1 else "with ")
                     + ", ".join(names) + (" (or suggested)" if f.get("suggested") else ""))
    if f.get("tags"):
        parts.append(("tagged all of " if f.get("tags_mode") == "all" and len(f["tags"]) > 1 else "tagged ")
                     + ", ".join(f["tags"]))
    if f.get("not_tags"):
        parts.append("not tagged " + ", ".join(f["not_tags"]))
    if f.get("subject"):
        parts.append(f"of {f['subject']}")
    if f.get("pose"):
        parts.append(f"pose {f['pose']}")
    if f.get("date_from") and f.get("date_to"):
        parts.append(f"taken {f['date_from']} – {f['date_to']}")
    elif f.get("date_from"):
        parts.append(f"taken from {f['date_from']}")
    elif f.get("date_to"):
        parts.append(f"taken until {f['date_to']}")
    if f.get("folder"):
        label = store.folder_label(conn, int(f["folder"])) if conn is not None else None
        parts.append(f"in {label or 'a folder'}")
    if f.get("faces"):
        parts.append(f"{f['faces']} faces")
    if f.get("media"):
        parts.append(f"{f['media']} only")
    if f.get("level"):
        parts.append(dict(LEVEL_LABELS).get(f["level"], f["level"]))
    return ", ".join(parts) or "every photo"


# ------------------------------------------------------------------ saved smart albums
def all_smart(conn):
    """[(id, name, filter)] by name."""
    try:
        rows = conn.execute("SELECT id, name, filter FROM smart_albums ORDER BY name COLLATE NOCASE").fetchall()
    except Exception:
        return []
    return [(i, n, json.loads(f or "{}")) for i, n, f in rows]


def get(conn, sid):
    r = conn.execute("SELECT name, filter FROM smart_albums WHERE id=?", (sid,)).fetchone()
    return (r[0], json.loads(r[1] or "{}")) if r else (None, None)


def save(conn, name, f, sid=None):
    name = " ".join((name or "").split()) or describe(f, conn)[:60]
    if sid:
        conn.execute("UPDATE smart_albums SET name=?, filter=? WHERE id=?", (name, json.dumps(f), sid))
    else:
        sid = conn.execute("INSERT INTO smart_albums(name, filter, created) VALUES(?,?,?)",
                           (name, json.dumps(f), time.time())).lastrowid
    conn.commit()
    return sid


def delete(conn, sid):
    conn.execute("DELETE FROM smart_albums WHERE id=?", (sid,))
    conn.commit()


def make_normal(conn, sid, threshold=None):
    """Freeze a smart album into a normal album (same name; " (album)" if that's taken). Returns its id."""
    name, f = get(conn, sid)
    if name is None:
        return None
    nm = name
    if conn.execute("SELECT 1 FROM albums WHERE name=?", (nm,)).fetchone():
        nm = f"{name} (album)"
    aid = store.create_album(conn, nm)
    store.add_to_album(conn, aid, photo_ids(conn, f, threshold))
    return aid


# ------------------------------------------------------------------ suggestions
def dismiss(conn, key):
    conn.execute("INSERT OR REPLACE INTO smart_dismissed(key, at) VALUES(?,?)", (key, time.time()))
    conn.commit()


def _dismissed(conn):
    try:
        return {r[0] for r in conn.execute("SELECT key FROM smart_dismissed")}
    except Exception:
        return set()


def _people_photos(conn):
    """{person id: set of photo ids} (confirmed faces and whole-photo tags; no vault photos)."""
    out = {}
    hidden = {r[0] for r in conn.execute("SELECT id FROM photos WHERE COALESCE(sort_later, 0)=2 OR error IS NOT NULL")}
    for pid, ph in conn.execute("SELECT person_id, photo_id FROM faces WHERE confirmed=1 AND ignored=0 "
                                "AND person_id IS NOT NULL"):
        if ph not in hidden:
            out.setdefault(pid, set()).add(ph)
    for ph, pid in conn.execute("SELECT photo_id, person_id FROM photo_people"):
        if ph not in hidden:
            out.setdefault(pid, set()).add(ph)
    return out


def suggestions(conn, limit=12, hidden_people=()):
    """[{"key", "name", "filter", "count", "why"}], best first, without dismissed or already-made ones."""
    gone = _dismissed(conn)
    made = {json.dumps(f, sort_keys=True) for _i, _n, f in all_smart(conn)}
    names = dict(conn.execute("SELECT id, name FROM persons"))
    hidden = set(hidden_people)
    pp = {p: s for p, s in _people_photos(conn).items() if p in names and names[p] and p not in hidden}
    out = []

    def add(key, name, f, count, why):
        f = {k: v for k, v in f.items() if v}
        if key in gone or json.dumps(f, sort_keys=True) in made:
            return
        out.append({"key": key, "name": name, "filter": f, "count": count, "why": why})

    # people often together
    big = sorted((p for p, s in pp.items() if len(s) >= PAIR_MIN), key=lambda p: -len(pp[p]))[:200]
    pairs = []
    for i, a in enumerate(big):
        for b in big[i + 1:]:
            n = len(pp[a] & pp[b])
            if n >= PAIR_MIN:
                pairs.append((n, a, b))
    for n, a, b in sorted(pairs, reverse=True)[:6]:
        lo, hi = sorted((a, b))
        add(f"pair:{lo}:{hi}", f"{names[a]} & {names[b]}",
            {"people": [a, b], "people_names": [names[a], names[b]], "people_mode": "all"}, n,
            f"{n:,} photos with both of them")
    # events: bursts of photos over a few days
    times = [r[0] for r in conn.execute("""SELECT taken FROM photos WHERE taken IS NOT NULL AND error IS NULL
                                           AND COALESCE(sort_later, 0) != 2 ORDER BY taken""")]
    bursts, start = [], 0
    for i in range(1, len(times) + 1):
        if i == len(times) or times[i] - times[i - 1] > EVENT_GAP:
            n = i - start
            if n >= EVENT_MIN and times[i - 1] - times[start] <= EVENT_SPAN:
                bursts.append((n, times[start], times[i - 1]))
            start = i
    for n, a, b in sorted(bursts, reverse=True)[:6]:
        da, db = datetime.fromtimestamp(a), datetime.fromtimestamp(b)
        fa, fb = da.strftime("%Y-%m-%d"), db.strftime("%Y-%m-%d")
        label = da.strftime("%d %b %Y").lstrip("0") if fa == fb else \
            f"{da.strftime('%d').lstrip('0')}–{db.strftime('%d %b %Y').lstrip('0')}" if (da.year, da.month) == (db.year, db.month) \
            else f"{da.strftime('%d %b').lstrip('0')} – {db.strftime('%d %b %Y').lstrip('0')}"
        add(f"event:{fa}:{fb}", f"Event · {label}", {"date_from": fa, "date_to": fb}, n,
            f"{n:,} photos taken over {(b - a) / 86400:.0f} day(s)" if b - a >= 86400 else f"{n:,} photos in one day")
    # a person often in one scene / tag
    tags = {}
    for aid, name in conn.execute("SELECT id, name FROM albums WHERE tag=1"):
        tags[name] = {r[0] for r in conn.execute("SELECT photo_id FROM album_photos WHERE album_id=?", (aid,))}
    combos = []
    for p, s in pp.items():
        if len(s) < SCENE_MIN:
            continue
        for t, ts in tags.items():
            n = len(s & ts)
            if n >= SCENE_MIN and n < len(s):        # (not when it's all of their photos anyway)
                combos.append((n, p, t))
    for n, p, t in sorted(combos, reverse=True)[:8]:
        add(f"scene:{p}:{t}", f"{names[p]} · {t}",
            {"people": [p], "people_names": [names[p]], "tags": [t]}, n, f"{n:,} photos of {names[p]} tagged {t}")
    out.sort(key=lambda s: -s["count"])
    return out[:limit]


# ------------------------------------------------------------------ near misses (training)
def near_misses(conn, f, scorer, threshold=None, limit=60):
    """[(photo id, tag, p)]: photos matching everything except one of the album's tags, which the
    tag model thinks they have (p >= NEAR_P), best first. Needs search by description."""
    tags = list(f.get("tags") or [])
    if not tags or scorer is None:
        return []
    have = set(photo_ids(conn, f, threshold))
    out = {}
    for t in tags:
        if f.get("tags_mode") == "all" and len(tags) > 1:
            rest = dict(f, tags=[x for x in tags if x != t])
        else:
            rest = dict(f, tags=[])
        cands = set(photo_ids(conn, rest, threshold)) - have
        if not cands:
            continue
        aid = conn.execute("SELECT id FROM albums WHERE name=?", (t,)).fetchone()
        if aid:
            cands -= {r[0] for r in conn.execute("SELECT photo_id FROM album_rejects WHERE album_id=?", (aid[0],))}
        for pid, p in scorer.probabilities(t, cands):
            if p >= NEAR_P and p > out.get(pid, (None, 0))[1]:
                out[pid] = (t, p)
    rows = sorted(((pid, t, p) for pid, (t, p) in out.items()), key=lambda r: -r[2])
    return rows[:limit]


def accept_near(conn, pairs):
    """[(photo id, tag)]: yes, it has that tag."""
    for pid, t in pairs:
        store.tag_photo(conn, pid, t, True)
        aid = conn.execute("SELECT id FROM albums WHERE name=?", (t,)).fetchone()
        if aid:
            conn.execute("DELETE FROM album_rejects WHERE album_id=? AND photo_id=?", (aid[0], pid))
    conn.commit()


def reject_near(conn, pairs):
    """[(photo id, tag)]: no, it doesn't have that tag (learned from, never suggested again)."""
    for pid, t in pairs:
        aid = store.create_album(conn, t)
        conn.execute("UPDATE albums SET tag=1 WHERE id=?", (aid,))
        conn.execute("INSERT OR IGNORE INTO album_rejects(album_id, photo_id) VALUES(?,?)", (aid, pid))
    conn.commit()
