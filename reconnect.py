"""Reconnect to galleries (1.60): files left in ~Unsorted (often by earlier versions, when a face wasn't
picked up) matched back to the gallery folder they belong to - by shoot evidence only:

- the same original folder as photos already in that gallery (moves FaceSort recorded),
- a folder name that matches the gallery's,
- the filename fits the gallery's numbering (…_0412 between _0411 and _0413),
- taken during the gallery's shoot (and the same picture size),
- the same scene / set (the description index),
- the same outfit and figure - only alongside one of the shoot clues above (same occasion),
- a tattoo or jewelry match for the gallery's person - a tiebreaker only.

Nothing is moved until you confirm. A file with no shoot evidence stays in ~Unsorted."""
import json
import os
import re
import time
from collections import Counter, defaultdict

import numpy as np

import libmove
import store

SURE = 100               # this sure: ticked to move
LIKELY = 60              # this sure: shown, for you to tick
CLOSE_CALL = 15          # two galleries this close: neither is ticked
W_FOLDER, W_NAME, W_SEQ, W_SEQ_GENERIC = 100, 80, 70, 40
W_TIME_NEAR, W_TIME, W_SIZE = 45, 35, 10
W_SCENE_SAME, W_SCENE, W_OUTFIT, W_MARK = 35, 25, 30, 20
SCENE_SAME, SCENE_ALIKE = 0.88, 0.82
OUTFIT_ALIKE = 0.62
PER_GALLERY = 24         # scene / outfit samples kept per gallery
SHOOT_SLACK = 30 * 60    # taken this long before / after a gallery's photos still counts
NEAR = 10 * 60
MAX_SHOOT = 12 * 3600    # a gallery spread over longer than this isn't one shoot (no time clue)
GENERIC_PREFIX = {"", "img", "dsc", "dscn", "dscf", "photo", "image", "pic", "p", "dji", "gopr", "pxl", "mvimg",
                  "vid", "mov", "screenshot", "scan", "untitled", "file", "frame"}
GENERIC_FOLDER = {"downloads", "download", "pictures", "pics", "photos", "images", "desktop", "documents",
                  "newfolder", "dcim", "camera", "temp", "tmp", "misc", "unsorted", "users", "home", "new", "old",
                  "backup", "export", "exports", "saved", "savedpictures", "camerauploads"}
_NUM = re.compile(r"^(.*?)(\d+)(\D*)$")


def _seq(name):
    """("prefix", number) of a file name, or None."""
    stem = os.path.splitext(os.path.basename(name))[0].lower()
    stem = re.sub(r"\s*\(\d+\)$", "", stem)                       # (a "(2)" copy number isn't the sequence)
    m = _NUM.match(stem)
    if not m or len(m.group(2)) > 9:
        return None
    return re.sub(r"[\s_\-.]+$", "", m.group(1)), int(m.group(2))


def _has(conn, table):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _plain(s):
    return re.sub(r"[^0-9a-z]+", "", (s or "").lower())


class Gallery:
    __slots__ = ("folder", "ids", "names", "seq", "taken", "sizes", "person", "person_name")

    def __init__(self, folder):
        self.folder = folder
        self.ids, self.names, self.taken = [], set(), []
        self.seq = defaultdict(list)
        self.sizes = Counter()
        self.person, self.person_name = None, None


def galleries(conn, lib, min_photos=2):
    """{folder: Gallery} - library folders (outside ~Unsorted) with photos in them."""
    uns = libmove.unsorted_dir(lib)
    out = {}
    for pid, path, taken, w, h in conn.execute(
            """SELECT id, path, taken, width, height FROM photos WHERE COALESCE(is_other, 0) = 0
               AND COALESCE(discarded, 0) = 0 AND error IS NULL"""):
        if not libmove.inside(path, lib) or libmove.inside(path, uns):
            continue
        d = os.path.dirname(path)
        g = out.get(d)
        if g is None:
            g = out[d] = Gallery(d)
        g.ids.append(pid)
        base = os.path.basename(path).lower()
        g.names.add(base)
        s = _seq(base)
        if s:
            g.seq[s[0]].append(s[1])
        if taken:
            g.taken.append(taken)
        if w and h:
            g.sizes[(max(w, h), min(w, h))] += 1
    out = {d: g for d, g in out.items() if len(g.ids) >= min_photos}
    # the gallery's person: who's confirmed on most of its photos
    by_photo = {}
    for gd, g in out.items():
        for i in g.ids:
            by_photo[i] = gd
    counts = defaultdict(Counter)
    for photo_id, person in conn.execute(
            """SELECT photo_id, person_id FROM faces WHERE confirmed = 1 AND ignored = 0 AND person_id IS NOT NULL
               UNION SELECT photo_id, person_id FROM photo_people"""):
        gd = by_photo.get(photo_id)
        if gd:
            counts[gd][person] += 1
    names = dict(conn.execute("SELECT id, name FROM persons"))
    for gd, c in counts.items():
        person, n = c.most_common(1)[0]
        if n * 2 >= len(out[gd].ids) or n >= 3 and n >= sum(c.values()) * 0.6:
            out[gd].person, out[gd].person_name = person, names.get(person)
    for g in out.values():
        g.taken.sort()
        for k in g.seq:
            g.seq[k].sort()
    return out


def candidates(conn, lib):
    """[(id, path, taken, w, h)] waiting in ~Unsorted that nobody's confirmed on (those move by themselves)."""
    uns = store.norm_path(libmove.unsorted_dir(lib)).rstrip("\\/") + os.sep
    return conn.execute(
        """SELECT p.id, p.path, p.taken, p.width, p.height FROM photos p
           WHERE substr(p.path, 1, ?) = ? AND COALESCE(p.is_other, 0) = 0 AND COALESCE(p.discarded, 0) = 0
             AND COALESCE(p.sort_later, 0) = 0
             AND NOT EXISTS (SELECT 1 FROM faces f WHERE f.photo_id = p.id AND f.confirmed = 1 AND f.ignored = 0
                             AND f.person_id IS NOT NULL)
             AND NOT EXISTS (SELECT 1 FROM photo_people pp WHERE pp.photo_id = p.id)
           ORDER BY p.path""", (len(uns), uns)).fetchall()


def _moved_dirs(conn, lib):
    """{source folder: {gallery folder: files}} from moves FaceSort recorded (History, ~Unsorted moves)."""
    out = defaultdict(Counter)
    try:
        for (data,) in conn.execute("SELECT data FROM history WHERE COALESCE(undone, 0) = 0"):
            try:
                files = json.loads(data).get("files") or []
            except (ValueError, AttributeError):
                continue
            for m in files:
                if len(m) >= 2 and m[0] and m[1]:
                    out[os.path.normcase(os.path.dirname(m[0]))][store.norm_path(os.path.dirname(m[1]))] += 1
    except Exception:
        pass
    try:
        for src, dst in conn.execute("SELECT src_dir, dest_dir FROM unsorted_moves"):
            out[os.path.normcase(src)][store.norm_path(dst)] += 1
    except Exception:
        pass
    return out


def _vectors(conn, table_sql, ids_by_gallery, cap):
    """(matrix of unit vectors, gallery index per row) - a few per gallery."""
    want = {}
    for gi, ids in enumerate(ids_by_gallery):
        step = max(1, len(ids) // cap)
        for i in ids[::step][:cap]:
            want[i] = gi
    rows, owner = [], []
    if not want:
        return None, None
    for pid, blob in conn.execute(table_sql):
        gi = want.get(pid)
        if gi is not None and blob:
            rows.append(np.frombuffer(blob, np.float16).astype(np.float32))
            owner.append(gi)
    if not rows:
        return None, None
    m = np.stack(rows)
    m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-7
    return m, np.asarray(owner)


def _best_per_gallery(sims, owner, n):
    best = np.full(n, -1.0, np.float32)
    np.maximum.at(best, owner, sims)
    return best


def find(conn, lib, progress=lambda d, t, m: None, cancelled=lambda: False):
    """{gallery folder: [Match]} for files in ~Unsorted, plus the galleries. Match = dict(id, path, score,
    reasons, sure, also)."""
    gals = galleries(conn, lib)
    cands = candidates(conn, lib)
    if not gals or not cands:
        return {}, gals
    order = list(gals)
    gindex = {d: i for i, d in enumerate(order)}
    uns = libmove.unsorted_dir(lib)
    moved = _moved_dirs(conn, lib)
    names = {}
    for d in order:
        key = _plain(os.path.basename(d))
        if len(key) >= 4 and key not in GENERIC_FOLDER:
            names.setdefault(key, []).append(d)
    cand_ids = [c[0] for c in cands]
    # scene (description index) and outfit (person finder) - a few samples per gallery
    ids_by_gal = [gals[d].ids for d in order]
    scene_m, scene_owner = _vectors(conn, "SELECT photo_id, vec FROM clip_emb WHERE vec IS NOT NULL", ids_by_gal,
                                    PER_GALLERY) if _has(conn, "clip_emb") else (None, None)
    cand_scene = {}
    if scene_m is not None:
        want = set(cand_ids)
        for pid, blob in conn.execute("SELECT photo_id, vec FROM clip_emb WHERE vec IS NOT NULL"):
            if pid in want and blob:
                v = np.frombuffer(blob, np.float16).astype(np.float32)
                cand_scene[pid] = v / (np.linalg.norm(v) + 1e-7)
    body_m, body_owner, cand_bodies = None, None, defaultdict(list)
    if _has(conn, "bodies"):
        body_rows = defaultdict(list)
        want = set(cand_ids)
        g_of = {}
        for gi, ids in enumerate(ids_by_gal):
            for i in ids:
                g_of[i] = gi
        for photo_id, blob in conn.execute("SELECT photo_id, desc FROM bodies WHERE desc IS NOT NULL"):
            if photo_id in want:
                v = np.frombuffer(blob, np.float16).astype(np.float32)
                cand_bodies[photo_id].append(v / (np.linalg.norm(v) + 1e-7))
            elif photo_id in g_of and len(body_rows[g_of[photo_id]]) < PER_GALLERY:
                body_rows[g_of[photo_id]].append(np.frombuffer(blob, np.float16).astype(np.float32))
        if body_rows:
            rows, owner = [], []
            for gi, vs in body_rows.items():
                rows += vs
                owner += [gi] * len(vs)
            body_m = np.stack(rows)
            body_m /= np.linalg.norm(body_m, axis=1, keepdims=True) + 1e-7
            body_owner = np.asarray(owner)
    marks = defaultdict(list)          # photo -> [(person, kind)]
    if _has(conn, "tattoo_suggestions"):
        kcol = "COALESCE(kind, 'tattoo')" if "kind" in {r[1] for r in conn.execute(
            "PRAGMA table_info(tattoo_suggestions)")} else "'tattoo'"
        for photo_id, person, kind in conn.execute(f"SELECT photo_id, person_id, {kcol} FROM tattoo_suggestions"):
            marks[photo_id].append((person, kind))
    seq_owners = defaultdict(list)     # prefix -> galleries numbering files that way
    for d in order:
        for p in gals[d].seq:
            seq_owners[p].append(d)

    shoot_gal = [d for d in order if len(gals[d].taken) >= 2 and gals[d].taken[-1] - gals[d].taken[0] <= MAX_SHOOT]
    shoot_lo = np.array([gals[d].taken[0] - SHOOT_SLACK for d in shoot_gal])
    shoot_hi = np.array([gals[d].taken[-1] + SHOOT_SLACK for d in shoot_gal])
    out = defaultdict(list)
    last = 0.0
    for k, (pid, path, taken, w, h) in enumerate(cands):
        if cancelled():
            break
        if time.monotonic() - last > 0.3:
            progress(k, len(cands), f"Looking for where files belong… {k:,} / {len(cands):,}")
            last = time.monotonic()
        score = defaultdict(int)
        why = defaultdict(list)
        shoot = defaultdict(bool)        # galleries with a shoot clue (folder, name, numbering, time)
        here = os.path.dirname(path)
        orig = libmove.original(here, uns)
        base = os.path.basename(path).lower()
        # 1. the same original folder as photos already in the gallery
        for src in {os.path.normcase(here), os.path.normcase(orig)}:
            for gd, n in moved.get(src, {}).items():
                if gd in gindex:
                    score[gd] += W_FOLDER
                    why[gd].append(f"came from the same folder as {n:,} file{'s' if n != 1 else ''} already in it")
                    shoot[gd] = True
        # 2. a folder name that matches
        parts = [x for x in orig.replace("\\", "/").split("/") if x][-3:]
        for part in reversed(parts):
            key = _plain(part)
            for gd in names.get(key, ()):
                if not shoot[gd]:
                    score[gd] += W_NAME
                    why[gd].append(f"came from a folder named “{part}”")
                    shoot[gd] = True
        # 3. numbering
        s = _seq(base)
        if s:
            pre, n = s
            fits = []
            for gd in seq_owners.get(pre, ()):
                nums = gals[gd].seq[pre]
                if len(nums) < 2 or base in gals[gd].names:
                    continue
                steps = np.diff(nums)
                step = float(np.median(steps)) if len(steps) else 1.0
                slack = max(3.0, step * 3)
                if nums[0] - slack <= n <= nums[-1] + slack:
                    fits.append((gd, nums))
            if fits and len(fits) <= 2:
                for gd, nums in fits:
                    lo = max((x for x in nums if x < n), default=None)
                    hi = min((x for x in nums if x > n), default=None)
                    where = (f"between {lo} and {hi}" if lo is not None and hi is not None else
                             f"next to {lo if lo is not None else hi}")
                    score[gd] += W_SEQ_GENERIC if pre in GENERIC_PREFIX else W_SEQ
                    why[gd].append(f"its number ({n}) fits the gallery's numbering, {where}")
                    shoot[gd] = True
        # 4. taken during the shoot
        if taken and len(shoot_gal):
            for gi in np.nonzero((shoot_lo <= taken) & (taken <= shoot_hi))[0]:
                gd = shoot_gal[gi]
                t = gals[gd].taken
                i = int(np.searchsorted(t, taken))
                gap = min(abs(taken - t[j]) for j in (i - 1, i) if 0 <= j < len(t))
                score[gd] += W_TIME_NEAR if gap <= NEAR else W_TIME
                mins = gap / 60
                why[gd].append("taken during the shoot (" + ("within a minute of" if mins < 1 else
                               f"{mins:.0f} min from") + " a photo in it)")
                shoot[gd] = True
                if w and h and gals[gd].sizes:
                    common, cn = gals[gd].sizes.most_common(1)[0]
                    if common == (max(w, h), min(w, h)) and cn * 2 >= sum(gals[gd].sizes.values()):
                        score[gd] += W_SIZE
                        why[gd].append("the same picture size")
        # 5. the same scene / set
        scene_best = None
        v = cand_scene.get(pid)
        if v is not None and scene_m is not None:
            scene_best = _best_per_gallery(scene_m @ v, scene_owner, len(order))
            for gi in np.nonzero(scene_best >= SCENE_ALIKE)[0]:
                gd = order[gi]
                same = scene_best[gi] >= SCENE_SAME
                score[gd] += W_SCENE_SAME if same else W_SCENE
                why[gd].append("the same scene / set" if same else "a very similar scene")
        # 6. the same outfit and figure - only at the same occasion (a shoot clue or the same scene)
        if body_m is not None and cand_bodies.get(pid):
            best = np.full(len(order), -1.0, np.float32)
            for bv in cand_bodies[pid]:
                best = np.maximum(best, _best_per_gallery(body_m @ bv, body_owner, len(order)))
            for gi in np.nonzero(best >= OUTFIT_ALIKE)[0]:
                gd = order[gi]
                if shoot[gd] or (scene_best is not None and scene_best[gi] >= SCENE_ALIKE):
                    score[gd] += W_OUTFIT
                    why[gd].append("the same outfit and figure")
        # 7. tattoo / jewelry match for the gallery's person - a tiebreaker
        for person, kind in marks.get(pid, ()):
            for gd in list(score):
                if gals[gd].person == person and score[gd] >= 30:
                    score[gd] += W_MARK
                    why[gd].append(f"{'jewelry' if kind == 'jewelry' else 'tattoo'} match for {gals[gd].person_name}")
        ranked = sorted(((sc, gd) for gd, sc in score.items() if sc >= LIKELY), reverse=True)
        if not ranked:
            continue
        sc, gd = ranked[0]
        also = [g for s2, g in ranked[1:] if sc - s2 < CLOSE_CALL]
        out[gd].append({"id": pid, "path": path, "score": sc, "reasons": why[gd], "also": also,
                        "sure": sc >= SURE and not also})
    progress(len(cands), len(cands), "")
    for gd in out:
        out[gd].sort(key=lambda m: (-m["score"], m["path"]))
    return dict(out), gals


def reconnect_job(conn, lib, picks, tag=True, progress=lambda d, t, m: None, cancelled=lambda: False):
    """Move the picked files into their galleries. picks: [(photo id, gallery folder, person or None)].
    tag: also tag the gallery's person on them (when nobody's named on them yet). Their extra files follow."""
    moves, person_of = [], {}
    for pid, gd, person in picks:
        row = conn.execute("SELECT path FROM photos WHERE id=?", (pid,)).fetchone()
        if row:
            moves.append((row[0], os.path.join(gd, os.path.basename(row[0]))))
            if person:
                person_of[store.norm_path(row[0])] = (pid, person)
    done, errors = libmove.run(conn, moves, lambda d, t: progress(d, t, f"Moving into galleries… {d:,} / {t:,}"),
                               cancelled)
    pairs = [(a, b) for a, b in done.items()]
    libmove.remember_moves(conn, lib, pairs)
    extra = libmove.follow_extras(conn, lib)
    tagged = 0
    if tag:
        by_person = defaultdict(list)
        for old in done:
            hit = person_of.get(old)
            if hit:
                by_person[hit[1]].append(hit[0])
        for person, ids in by_person.items():
            store.tag_photos(conn, ids, person)
            tagged += len(ids)
        conn.commit()
    libmove.remove_empty(libmove.unsorted_dir(lib))
    n = len(done)
    return {"moved": n, "errors": errors, "tagged": tagged, "extra_files": len(extra),
            "moves": pairs + list(extra),
            "history_label": f"Reconnected {n:,} file{'s' if n != 1 else ''} to their galleries"
            + (f" (+{len(extra):,} other file{'s' if len(extra) != 1 else ''})" if extra else "")}
