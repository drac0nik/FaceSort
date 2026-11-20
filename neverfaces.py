"""Never track (1.51): things that look like a face but never are - a site's drawn-face logo, a
watermark, a mascot, a printed face on a T-shirt…

Right-click a face > "Never track this…": its fingerprint goes on a permanent list. From then on
every face that matches it closely - in the library now and in every photo scanned later - is set
aside as "not a face" by itself (you don't see it in groups or suggestions). Faces you've named
yourself are never touched.

The list lives next to your settings (never_track.json), not in the library, so it survives
clearing the library and rescanning. Each entry keeps a small picture (to recognise it in the list)
and where it was first seen, so after switching face engines the same spot is measured again with
the new engine and the entry keeps working. Settings > Faces > Never track list… shows it; removing
an entry can also bring back the faces it hid.
"""
import base64
import json
import os
import time

import numpy as np

import store

FILE = "never_track.json"
SIM = {"arcface": 0.55, "adaface": 0.55, "sface": 0.68}   # this alike an entry = the same thing
SAME_ENTRY = 0.80          # adding something this alike an existing entry adds to that entry


def _path():
    return os.path.join(store.data_dir(), FILE)


def load():
    """[{"id", "label", "added", "embs": {model: [vectors]}, "seen": [(photo path, box)], "thumb": bytes}]"""
    try:
        import cryptbox
        data = json.loads(cryptbox.read_private(_path(), "never track").decode("utf-8"))
    except Exception:
        return []
    out = []
    for e in data.get("entries", []):
        embs = {}
        for model, vecs in (e.get("embs") or {}).items():
            embs[model] = [np.frombuffer(base64.b64decode(v), np.float32) for v in vecs]
        out.append({"id": e.get("id"), "label": e.get("label") or "Never track", "added": e.get("added", 0),
                    "embs": embs, "seen": [tuple(s) for s in e.get("seen", [])],
                    "thumb": base64.b64decode(e["thumb"]) if e.get("thumb") else None})
    return out


def save(entries):
    data = {"version": 1, "entries": [{
        "id": e["id"], "label": e["label"], "added": e["added"],
        "embs": {m: [base64.b64encode(np.asarray(v, np.float32).tobytes()).decode() for v in vecs]
                 for m, vecs in e["embs"].items()},
        "seen": [list(s) for s in e["seen"]][:20],
        "thumb": base64.b64encode(e["thumb"]).decode() if e.get("thumb") else None} for e in entries]}
    import cryptbox
    cryptbox.write_private(_path(), json.dumps(data).encode("utf-8"), "never track")   # (encrypted with the library)


def _unit(v):
    v = np.asarray(v, np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n else v


def add(conn, face_ids, label="Logo"):
    """Put these faces on the list (one entry, or added to an entry they're very alike).
    Returns (entry id, faces set aside in the library because of it)."""
    model = store.get_model(conn)
    q = ",".join("?" * len(face_ids))
    rows = conn.execute(f"""SELECT f.emb, f.thumb, ph.path, f.x, f.y, f.w, f.h, f.model FROM faces f
                            JOIN photos ph ON ph.id=f.photo_id WHERE f.id IN ({q})""", list(face_ids)).fetchall()
    rows = [r for r in rows if r[7] == model]
    if not rows:
        return None, 0
    entries = load()
    vecs = [_unit(store.emb_from_blob(r[0])) for r in rows]
    target = None
    for e in entries:
        ev = e["embs"].get(model) or []
        if ev and max(float(np.dot(a, b)) for a in vecs for b in ev) >= SAME_ENTRY:
            target = e
            break
    if target is None:
        target = {"id": int(time.time() * 1000), "label": " ".join((label or "").split()) or "Logo",
                  "added": time.time(), "embs": {}, "seen": [], "thumb": rows[0][1]}
        entries.append(target)
    target["embs"].setdefault(model, []).extend(vecs)
    target["embs"][model] = target["embs"][model][-30:]
    for r in rows:
        target["seen"].append((r[2], [r[3], r[4], r[5], r[6]]))
    save(entries)
    n = apply(conn, [target])
    store.set_notface(conn, list(face_ids), 3)
    _note_hits(conn, target["id"], face_ids)
    conn.commit()
    return target["id"], n


def remove(conn, entry_id, restore=False):
    """Take an entry off the list; restore: bring back the faces it set aside."""
    entries = [e for e in load() if e["id"] != entry_id]
    save(entries)
    n = 0
    try:
        ids = [r[0] for r in conn.execute("SELECT face_id FROM never_hits WHERE entry_id=?", (entry_id,))]
        if restore and ids:
            q = ",".join("?" * len(ids))
            n = conn.execute(f"UPDATE faces SET ignored=0 WHERE id IN ({q}) AND ignored=3", ids).rowcount
        conn.execute("DELETE FROM never_hits WHERE entry_id=?", (entry_id,))
        conn.commit()
    except Exception:
        pass
    return n


def rename(entry_id, label):
    entries = load()
    for e in entries:
        if e["id"] == entry_id:
            e["label"] = " ".join(label.split()) or e["label"]
    save(entries)


def _note_hits(conn, entry_id, face_ids):
    try:
        conn.executemany("INSERT OR IGNORE INTO never_hits(face_id, entry_id) VALUES(?,?)",
                         [(int(f), entry_id) for f in face_ids])
    except Exception:
        pass


def carry_over(conn, entries=None):
    """Entries made with another face engine: the same spot in the same photo, measured by the engine
    in use, is added to them (so they keep working after switching engines). Returns entries."""
    entries = load() if entries is None else entries
    model = store.get_model(conn)
    changed = False
    for e in entries:
        if e["embs"].get(model) or not e["seen"]:
            continue
        for path, box in e["seen"]:
            for emb, x, y, w, h in conn.execute(
                    """SELECT f.emb, f.x, f.y, f.w, f.h FROM faces f JOIN photos ph ON ph.id=f.photo_id
                       WHERE ph.path=? AND f.model=?""", (path, model)):
                if _iou((x, y, w, h), box) >= 0.5:
                    e["embs"].setdefault(model, []).append(_unit(store.emb_from_blob(emb)))
                    changed = True
    if changed:
        save(entries)
    return entries


def _iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    u = aw * ah + bw * bh - inter
    return inter / u if u > 0 else 0.0


def matches(conn, entries=None, face_ids=None):
    """{face id: entry id} for faces (not named by you, not already set aside) that match an entry."""
    entries = carry_over(conn, entries)
    model = store.get_model(conn)
    refs, owner = [], []
    for e in entries:
        for v in e["embs"].get(model) or []:
            refs.append(v)
            owner.append(e["id"])
    if not refs:
        return {}
    R = np.stack(refs).astype(np.float32)
    sql = ("SELECT id, emb FROM faces WHERE confirmed=0 AND ignored IN (0, 4) AND COALESCE(face_ok, 0)=0 "
           "AND model=?")
    args = [model]
    if face_ids is not None:
        ids = list(face_ids)
        if not ids:
            return {}
        sql += f" AND id IN ({','.join('?' * len(ids))})"
        args += ids
    rows = conn.execute(sql, args).fetchall()
    out = {}
    thr = SIM.get(model, 0.6)
    for s0 in range(0, len(rows), 4096):
        part = rows[s0:s0 + 4096]
        E = np.stack([_unit(store.emb_from_blob(b)) for _i, b in part]).astype(np.float32)
        S = E @ R.T
        best = S.argmax(axis=1)
        for k, (fid, _b) in enumerate(part):
            if S[k, best[k]] >= thr:
                out[fid] = owner[best[k]]
    return out


def apply(conn, entries=None, face_ids=None):
    """Set aside (as not a face) everything that matches the list. Returns how many."""
    return mark(conn, matches(conn, entries, face_ids))


def mark(conn, hits):
    """Write {face id: entry id} found by matches()."""
    if not hits:
        return 0
    ids = list(hits)
    for s0 in range(0, len(ids), 900):
        part = ids[s0:s0 + 900]
        q = ",".join("?" * len(part))
        conn.execute(f"UPDATE faces SET ignored=3, person_id=NULL, cluster=NULL WHERE id IN ({q}) AND confirmed=0",
                     part)
    try:
        conn.executemany("INSERT OR IGNORE INTO never_hits(face_id, entry_id) VALUES(?,?)", list(hits.items()))
    except Exception:
        pass
    return len(hits)
