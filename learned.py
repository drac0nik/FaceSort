"""Sharing what FaceSort has learned (1.48) - without anything personal.

What's in an export (a small .fslearn text file)
- Tag models: for each tag you choose - the built-in content, scene and pose tags, and any of
  your own tags you tick - 512 numbers and an offset that turn a photo's description (CLIP) into
  "how much is this a Beach / Studio / Sitting photo", learned from the photos you tagged.
- The Safe / Suggestive / Explicit model learned from your corrections.
Never in it: photos, faces, fingerprints, people or names, album / folder / file names, dates,
anything about where photos are - and nothing learned from photos in a vault.

On another FaceSort (a fresh install, say) an imported file is a head start: until that
FaceSort has its own examples of a tag (5 photos), the imported model is used; after that the
two are blended, its own counting for more as it learns more. Imported models never overwrite
anything learned locally.
"""
import json
import time

import numpy as np

import content
import store

FORMAT = "facesort-learned-1"
SPACE = "clip-vit-base-patch32"           # the description model the numbers belong to
IMPORT_WEIGHT_MAX = 200                   # an imported model counts as at most this many examples


def vault_photo_ids(conn):
    """Photos in any vault (never learned from for an export)."""
    out = set()
    for vid, *_ in store.vaults(conn):
        try:
            out |= set(store.vault_members(conn, vid)[2])
        except Exception:
            pass
    out |= {r[0] for r in conn.execute("SELECT id FROM photos WHERE COALESCE(sort_later, 0)=2")}
    return out


def train_tag(conn, name, skip=frozenset()):
    """(w, b, positives) for a tag/album from its photos (vault photos left out), or None."""
    r = conn.execute("SELECT id FROM albums WHERE name=?", (name,)).fetchone()
    if not r or not content.clip_ok():
        return None
    aid = r[0]
    pos = [p for (p,) in conn.execute("SELECT photo_id FROM album_photos WHERE album_id=?", (aid,)) if p not in skip]
    if len(pos) < 5:
        return None
    rej = [p for (p,) in conn.execute("SELECT photo_id FROM album_rejects WHERE album_id=?", (aid,)) if p not in skip]
    other = [p for (p,) in conn.execute(
        "SELECT photo_id FROM clip_emb WHERE photo_id NOT IN (SELECT photo_id FROM album_photos WHERE album_id=?) "
        "ORDER BY (photo_id * 2654435761) % 1000003 LIMIT ?", (aid, max(300, 4 * len(pos)))) if p not in skip]
    vecs = content.clip_vecs(conn, pos + rej + other)
    P = [vecs[p] for p in pos if p in vecs]
    N = [vecs[p] for p in rej + other if p in vecs]
    if len(P) < 5 or len(N) < 20:
        return None
    X = np.stack(P + N)
    X /= np.linalg.norm(X, axis=1, keepdims=True) + 1e-9
    y = np.array([1.0] * len(P) + [0.0] * len(N), np.float32)
    w, b = content._train(X, y, steps=300)
    return w, b, len(P)


def train_levels(conn, skip=frozenset()):
    """{"nsfw": (w, b, n), "explicit": (w, b, n)} from your Safe / Suggestive / Explicit corrections."""
    said = {p: s for p, s in conn.execute("SELECT photo_id, level_set FROM photo_content WHERE level_set IS NOT NULL")
            if p not in skip}
    if not said or not content.clip_ok():
        return {}
    vecs = content.clip_vecs(conn, said)
    lab = [(vecs[p], s) for p, s in said.items() if p in vecs]
    out = {}
    if len(lab) < 20:
        return out
    X = np.stack([v for v, _ in lab])
    X /= np.linalg.norm(X, axis=1, keepdims=True) + 1e-9
    for key, yes in (("nsfw", (1, 2)), ("explicit", (2,))):
        y = np.array([1.0 if s in yes else 0.0 for _, s in lab], np.float32)
        if 5 <= y.sum() <= len(lab) - 5:
            w, b = content._train(X, y)
            out[key] = (w, b, int(y.sum()))
    return out


def shareable_tags(conn):
    """[(tag, photos, built-in?)]: tags that have enough photos to learn from."""
    import pose
    builtin = set(content.CONTENT_TAGS + content.SCENE_TAGS + pose.POSES)
    rows = conn.execute("""SELECT a.name, COUNT(ap.photo_id) FROM albums a JOIN album_photos ap ON ap.album_id=a.id
                           GROUP BY a.id HAVING COUNT(ap.photo_id) >= 5 ORDER BY a.tag DESC, a.name""").fetchall()
    return [(n, c, n in builtin) for n, c in rows]


def export(conn, path, tags, levels=True, include_vault=False, progress=lambda d, t, m: None, tag_list=None):
    """Write the .fslearn file. Returns {"tags": n, "levels": n}. tag_list: the viewer's tag list
    entries ({"name", "menu"}) for the chosen tags, so the other FaceSort shows them too."""
    skip = frozenset() if include_vault else frozenset(vault_photo_ids(conn))
    chosen = set(tags)
    out = {"format": FORMAT, "space": SPACE, "made": time.strftime("%Y-%m-%d"), "app": "FaceSort",
           "tags": {}, "levels": {},
           "tag_list": [{"name": t["name"], "menu": bool(t.get("menu", True))} for t in (tag_list or [])
                        if t.get("name") in chosen]}
    for n, t in enumerate(tags, 1):
        progress(n, len(tags), f"Learning {t}…")
        m = train_tag(conn, t, skip)
        if m:
            w, b, k = m
            out["tags"][t] = {"w": [round(float(x), 5) for x in w], "b": round(float(b), 5), "n": int(k)}
    if levels:
        for key, (w, b, k) in train_levels(conn, skip).items():
            out["levels"][key] = {"w": [round(float(x), 5) for x in w], "b": round(float(b), 5), "n": int(k)}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    return {"tags": len(out["tags"]), "levels": len(out["levels"])}


def import_file(conn, path, settings=None):
    """Read a .fslearn file into this library's imported models (and add its tags to the tag list
    in settings). Returns {"tags": n, "levels": n, "new_tags": [names]}."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("format") != FORMAT or data.get("space") != SPACE:
        raise ValueError("this isn't a FaceSort learning file (or it's from a different description model)")
    now = time.time()
    rows = []
    for kind, items in (("tag", data.get("tags", {})), ("level", data.get("levels", {}))):
        for name, m in items.items():
            w = np.asarray(m["w"], np.float32)
            if w.shape != (512,) or not np.all(np.isfinite(w)):
                continue
            rows.append((kind, str(name)[:100], w.tobytes(), float(m["b"]), int(m.get("n", 0)), now))
    with conn.write():
        conn.executemany("INSERT OR REPLACE INTO learned(kind, name, w, b, n, at) VALUES(?,?,?,?,?,?)", rows)
    new_tags = []
    if settings is not None:
        from tagcolumn import tag_list as current
        tl = current(settings)
        have = {t["name"].casefold() for t in tl}
        extra = [{"name": str(t["name"])[:100], "menu": bool(t.get("menu", True))} for t in data.get("tag_list", [])
                 if isinstance(t, dict) and t.get("name")] + \
                [{"name": str(n)[:100], "menu": True} for n in data.get("tags", {})]
        for t in extra:
            if t["name"].casefold() not in have:
                have.add(t["name"].casefold())
                tl.append(t)
                new_tags.append(t["name"])
        settings["tag_list"] = tl
    return {"tags": sum(1 for r in rows if r[0] == "tag"), "levels": sum(1 for r in rows if r[0] == "level"),
            "new_tags": new_tags}


def forget_imported(conn):
    with conn.write():
        conn.execute("DELETE FROM learned")


def imported_summary(conn):
    try:
        return conn.execute("SELECT kind, COUNT(*) FROM learned GROUP BY kind").fetchall()
    except Exception:
        return []


def imported(conn, kind, name):
    """(w, b, n) imported for this tag / level model, or None."""
    try:
        r = conn.execute("SELECT w, b, n FROM learned WHERE kind=? AND name=?", (kind, name)).fetchone()
    except Exception:
        return None
    if not r:
        return None
    return np.frombuffer(r[0], np.float32), float(r[1]), int(r[2])


def blend(local, local_n, imp):
    """Local model (or None) and an imported one -> the model to use."""
    if imp is None:
        return local
    w_i, b_i, n_i = imp
    k = min(max(n_i, 1), IMPORT_WEIGHT_MAX)
    if local is None:
        return w_i, b_i
    w_l, b_l = local
    tot = local_n + k
    return (w_l * local_n + w_i * k) / tot, (b_l * local_n + b_i * k) / tot
