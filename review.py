"""Quick review: ask the yes/no questions that sort the most faces per keypress.

Questions (A = something unnamed, B = a person or another unnamed group):
  group  ↔ person   "Is this group Barack?"           yes names the whole group
  group  ↔ group    "Same person?"                    yes merges the two groups
  single ↔ person   "Is this Barack?"                 yes names the face
  single ↔ group    "Same person as this group?"      yes adds the face to the group

Each candidate gets   priority = chance of "yes"  ×  faces it sorts,  so likely,
high-impact questions come first. Photos taken close together in time nudge the chance
up a little (same event, same people); two faces in the same photo are never asked
about (they can't be the same person). "No" answers are remembered.
"""
import math

import numpy as np

import store

EVENT_WINDOW = 30 * 60      # photos taken within 30 minutes count as the same event
EVENT_BOOST = 0.04          # at most this much extra similarity for a shared event
BAND = 0.12                 # ask about pairs down to (threshold - BAND)
MAX_FACES = 40              # faces per group used for comparing


class Unit:
    __slots__ = ("kind", "key", "faces", "embs", "photos", "times", "centroid", "show", "name", "cluster", "quality")

    def __init__(self, kind, key):
        self.kind, self.key = kind, key
        self.faces, self.embs, self.photos, self.times, self.show = [], [], set(), [], []
        self.centroid, self.name, self.cluster, self.quality = None, None, None, []

    @property
    def size(self):
        return len(self.faces)

    def finish(self):
        E = np.stack(self.embs).astype(np.float32)
        c = E.mean(axis=0)
        self.centroid = c / (np.linalg.norm(c) or 1)
        order = np.argsort(-np.array(self.quality))
        self.show = [self.faces[i] for i in order[:8]]
        if len(E) > MAX_FACES:
            keep = order[:MAX_FACES]
            self.embs = E[keep]
        else:
            self.embs = E
        self.times = sorted(t for t in self.times if t)


class Question:
    __slots__ = ("a", "b", "sim", "boost", "event_gap", "priority", "kind")

    def __init__(self, a, b, sim, boost, gap):
        self.a, self.b, self.sim, self.boost, self.event_gap = a, b, sim, boost, gap
        self.kind = f"{a.kind}-{b.kind}"
        self.priority = 0.0

    @property
    def key(self):
        return (self.a.key, self.b.key)


def load_units(conn):
    """Unnamed groups, unnamed single faces, and named people, from the library."""
    groups, singles, people = {}, {}, {}
    rows = conn.execute("""SELECT f.id, f.photo_id, f.emb, f.cluster, f.person_id, f.confirmed, f.px*f.score, ph.taken
                           FROM faces f JOIN photos ph ON ph.id=f.photo_id
                           WHERE f.ignored=0 AND (f.confirmed=1 OR f.person_id IS NULL) AND f.model=?
                           AND COALESCE(f.manual, 0)<2""",
                        (store.get_model(conn),))
    names = dict(conn.execute("SELECT id, name FROM persons"))
    for fid, photo, blob, cluster, person, conf, q, taken in rows:
        if conf and person:
            u = people.get(person)
            if u is None:
                u = people[person] = Unit("person", ("p", person))
                u.name = names.get(person, "?")
        elif cluster is not None:
            u = groups.get(cluster)
            if u is None:
                u = groups[cluster] = Unit("group", ("g", cluster))
                u.cluster = cluster
        else:
            u = singles[fid] = Unit("single", ("f", fid))
        u.faces.append(fid)
        u.embs.append(store.emb_from_blob(blob))
        u.photos.add(photo)
        u.times.append(taken)
        u.quality.append(q or 0)
    out = {}
    for d in (groups, singles, people):
        for u in d.values():
            u.finish()
            out[u.key] = u
    return out


def _event(a, b):
    """(boost, smallest time gap in seconds or None) for two units' photo dates."""
    if not a.times or not b.times:
        return 0.0, None
    tb = np.array(b.times)
    gaps = [np.min(np.abs(tb - t)) for t in a.times[:60]]
    best = float(min(gaps))
    frac = float(np.mean([g <= EVENT_WINDOW for g in gaps]))
    return EVENT_BOOST * frac, best


def _sim_to_person(a, p):
    sims = p.embs @ a.centroid
    k = min(3, len(sims))
    return float(np.sort(sims)[-k:].mean())


def build_queue(conn, threshold, skipped=frozenset(), limit=200):
    units = load_units(conn)
    groups = [u for u in units.values() if u.kind == "group"]
    singles = [u for u in units.values() if u.kind == "single"]
    people = [u for u in units.values() if u.kind == "person"]
    floor = threshold - BAND

    # remembered "no" answers
    rejected = {}
    for fid, pid in conn.execute("SELECT face_id, person_id FROM rejects"):
        rejected.setdefault(pid, set()).add(fid)
    nolinks = set()
    for a, b in conn.execute("SELECT a, b FROM face_nolinks"):
        nolinks.add((a, b))
        nolinks.add((b, a))

    def blocked(a, b):
        if b.kind == "person":
            rej = rejected.get(b.key[1], ())
            if sum(1 for f in a.faces if f in rej) >= max(1, a.size // 2):
                return True
        elif nolinks:
            fa = set(a.faces)
            if any((x, y) in nolinks for x in fa for y in b.faces[:200]):
                return True
        shared = a.photos & b.photos
        return len(shared) > max(0, int(0.2 * a.size)) if b.kind == "person" else bool(shared)

    cands = []

    def consider(a, b, sim):
        boost, gap = _event(a, b)
        if sim + boost < floor or (a.key, b.key) in skipped or blocked(a, b):
            return
        q = Question(a, b, sim, boost, gap)
        p_yes = 1 / (1 + math.exp(-((sim + boost) - threshold) / 0.035))
        impact = {"group-person": a.size, "group-group": min(a.size, b.size) + 0.5,
                  "single-person": 1.0, "single-group": 0.8}[q.kind]
        q.priority = p_yes * impact
        cands.append(q)

    # vectorised first pass, then exact scores for the promising pairs
    if people:
        P = np.stack([p.centroid for p in people])
        for a in groups + singles:
            rough = P @ a.centroid
            for j in np.where(rough >= floor - 0.1)[0]:
                consider(a, people[j], _sim_to_person(a, people[j]))
    if len(groups) > 1:
        G = np.stack([g.centroid for g in groups])
        S = G @ G.T
        np.fill_diagonal(S, -1)
        ii, jj = np.where(np.triu(S) >= floor - 0.05)
        for i, j in zip(ii, jj):
            a, b = (groups[i], groups[j]) if groups[i].size <= groups[j].size else (groups[j], groups[i])
            consider(a, b, float(S[i, j]))
    if singles and groups:
        G = np.stack([g.centroid for g in groups])
        F = np.stack([s.centroid for s in singles])
        S = F @ G.T
        for i in range(len(singles)):
            j = int(np.argmax(S[i]))
            if S[i, j] >= floor:
                consider(singles[i], groups[j], float(S[i, j]))

    # one question per unnamed thing: its best one
    best = {}
    for q in cands:
        if q.a.key not in best or q.priority > best[q.a.key].priority:
            best[q.a.key] = q
    queue = sorted(best.values(), key=lambda q: -q.priority)
    return queue[:limit], len(queue)


# ---------------------------------------------------------------------- answering
class Session:
    """Applies answers and can undo them."""

    def __init__(self, conn):
        self.conn = conn
        self.history = []     # [(description, undo steps)]
        self.skipped = set()
        self.asked = set()    # questions answered this session are never asked again
        self.sorted_faces = 0
        self.answered = 0

    def _snapshot(self, face_ids):
        q = ",".join("?" * len(face_ids))
        return self.conn.execute(f"SELECT id, person_id, confirmed, cluster FROM faces WHERE id IN ({q})",
                                 face_ids).fetchall()

    def exclude(self):
        return frozenset(self.skipped | self.asked)

    def yes(self, q, name=None):
        self.asked.add(q.key)
        a, b = q.a, q.b
        undo = []
        moved = list(a.faces)
        if b.kind == "person" or name:
            faces = list(a.faces) + (list(b.faces) if b.kind != "person" else [])
            if b.kind == "person":
                # leave out faces that share a photo with this person (can't be them)
                bad = a.photos & b.photos
                if bad:
                    q_ = ",".join("?" * len(faces))
                    faces = [f for f, ph in self.conn.execute(
                        f"SELECT id, photo_id FROM faces WHERE id IN ({q_})", faces) if ph not in bad]
                pid = b.key[1]
            else:
                pid = store.get_or_create_person(self.conn, name)
            undo.append(("faces", self._snapshot(faces)))
            store.assign(self.conn, faces, pid)
            moved = faces
        else:
            # merge into the group: remember the link so regrouping keeps them together
            rep_a, rep_b = a.show[0], b.show[0]
            undo.append(("faces", self._snapshot(list(a.faces))))
            cur = self.conn.execute("INSERT OR IGNORE INTO face_links(a, b) VALUES(?,?)", (rep_a, rep_b))
            undo.append(("link", (rep_a, rep_b) if cur.rowcount else None))
            q_ = ",".join("?" * len(a.faces))
            self.conn.execute(f"UPDATE faces SET cluster=? WHERE id IN ({q_})", [b.cluster, *a.faces])
            self.conn.commit()
        self.history.append((undo, len(moved), q.key))
        self.sorted_faces += len(moved)
        self.answered += 1
        return len(moved)

    def no(self, q):
        self.asked.add(q.key)
        a, b = q.a, q.b
        undo = []
        if b.kind == "person":
            pid = b.key[1]
            new = [f for f in a.faces if not self.conn.execute(
                "SELECT 1 FROM rejects WHERE face_id=? AND person_id=?", (f, pid)).fetchone()]
            self.conn.executemany("INSERT OR IGNORE INTO rejects(face_id, person_id) VALUES(?,?)",
                                  [(f, pid) for f in new])
            undo.append(("rejects", [(f, pid) for f in new]))
        else:
            pair = (a.show[0], b.show[0])
            cur = self.conn.execute("INSERT OR IGNORE INTO face_nolinks(a, b) VALUES(?,?)", pair)
            undo.append(("nolink", pair if cur.rowcount else None))
        self.conn.commit()
        self.history.append((undo, 0, q.key))
        self.answered += 1

    def skip(self, q):
        self.skipped.add(q.key)
        self.history.append(([("skip", q.key)], 0, None))

    def undo(self):
        if not self.history:
            return False
        steps, n, key = self.history.pop()
        if key is not None:
            self.asked.discard(key)   # an undone question can be asked again
        for kind, data in steps:
            if kind == "faces":
                self.conn.executemany("UPDATE faces SET person_id=?, confirmed=?, cluster=? WHERE id=?",
                                      [(p, c, cl, f) for f, p, c, cl in data])
            elif kind == "link" and data:
                self.conn.execute("DELETE FROM face_links WHERE a=? AND b=?", data)
            elif kind == "rejects":
                self.conn.executemany("DELETE FROM rejects WHERE face_id=? AND person_id=?", data)
            elif kind == "nolink" and data:
                self.conn.execute("DELETE FROM face_nolinks WHERE a=? AND b=?", data)
            elif kind == "skip":
                self.skipped.discard(data)
        self.conn.commit()
        self.sorted_faces -= n
        if not (steps and steps[0][0] == "skip"):
            self.answered -= 1
        return True
