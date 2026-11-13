"""Face details from InsightFace's "buffalo_l" models (free for non-commercial use):
 - genderage.onnx (1.3 MB): how male a face looks, 0-1 (an estimate - see MALE / FEMALE below)
 - 1k3d68.onnx (137 MB): 68 points of the face in 3D, giving the head's angle (turned left /
   right = yaw, up / down = pitch, tilted = roll)

Both read the stored face thumbnails - framed just as these models expect (the face box 1.5 x,
centred, 192 px) - so no photo is opened. Only these two files are downloaded from the model
pack on InsightFace's GitHub page (the server sends just the bytes asked for), each checked
against its known checksum.
"""
import hashlib
import os
import struct
import threading
import urllib.request
import zlib

import cv2
import numpy as np

import engine

PACK_URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"
FILES = {
    "genderage.onnx": "4fde69b1c810857b88c64a335084f1c3fe8f01246c9a191b48c7bb756d6652fb",
    "1k3d68.onnx": "df5c06b8a0c12e422b2ed8947b8869faa4105387f199c477af038aa01f9a45cc",
}
DOWNLOAD_MB = 108             # (compressed: 1.2 + 107 MB)
MALE = 0.8                    # "Men": the model is at least this sure
FEMALE = 0.2                  # at or below: looks female; in between: "Unsure"
SIDE_ON = 60                  # degrees turned: too far round to judge (-> Unsure)
SMALL_PX = 36                 # faces smaller than this (px across in the photo) -> Unsure
UA = {"User-Agent": "FaceSort"}
_LOCK = threading.Lock()


def model_dir():
    return os.path.join(engine.store_data_dir(), "models", "face_details")


def ready():
    return all(os.path.exists(os.path.join(model_dir(), f)) for f in FILES)


def _range(start, end, timeout=60):
    req = urllib.request.Request(PACK_URL, headers=dict(UA, Range=f"bytes={start}-{end}"))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if r.status != 206:
            raise RuntimeError("the download server didn't send part of the file")
        return r.read(), r.headers.get("Content-Range", "")


def _directory():
    """{name: (local header offset, compressed size)} from the model pack's index (at its end)."""
    _, cr = _range(0, 0)
    total = int(cr.rsplit("/", 1)[1])
    tail, _ = _range(max(0, total - 65536), total - 1)
    i = tail.rfind(b"PK\x05\x06")
    if i < 0:
        raise RuntimeError("the model pack's index wasn't found")
    _, size, _ = struct.unpack("<HII", tail[i + 10:i + 20])
    cd, p, out = tail[i - size:i], 0, {}
    while p + 46 <= len(cd):
        f = struct.unpack("<IHHHHHHIIIHHHHHII", cd[p:p + 46])
        name = cd[p + 46:p + 46 + f[10]].decode("utf-8", "replace")
        out[os.path.basename(name)] = (f[16], f[8], f[4])        # offset, compressed size, method
        p += 46 + f[10] + f[11] + f[12]
    return out


def download(progress=lambda d, t, m: None, cancelled=lambda: False):
    """Both models, once. True when ready, False if cancelled; raises if a file doesn't check out."""
    os.makedirs(model_dir(), exist_ok=True)
    want = [f for f in FILES if not os.path.exists(os.path.join(model_dir(), f))]
    if not want:
        return True
    index = _directory()
    total = sum(index[f][1] for f in want) or 1
    got = 0
    for name in want:
        off, size, method = index[name]
        head, _ = _range(off, off + 29)
        fnl, exl = struct.unpack("<HH", head[26:30])
        start = off + 30 + fnl + exl
        buf = bytearray()
        pos = start
        while pos < start + size:
            if cancelled():
                return False
            end = min(start + size, pos + (4 << 20)) - 1
            part, _ = _range(pos, end)
            buf += part
            pos = end + 1
            progress((got + len(buf)) >> 20, total >> 20,
                     f"Getting the face detail models… {(got + len(buf)) >> 20} / {total >> 20} MB")
        data = zlib.decompress(bytes(buf), -15) if method == 8 else bytes(buf)
        if hashlib.sha256(data).hexdigest() != FILES[name]:
            raise RuntimeError(f"{name} doesn't match its checksum - not used")
        dest = os.path.join(model_dir(), name)
        with open(dest + ".part", "wb") as f:
            f.write(data)
        os.replace(dest + ".part", dest)
        got += size
    return True


def _run(name, x):
    import photo_tools                       # (its runner uses the graphics card when there is one)
    return photo_tools._run(os.path.join(model_dir(), name), {"data": x})[0]


def analyse(thumbs):
    """For a list of thumbnail JPEG bytes: [(male 0-1, yaw, pitch, roll) or None]."""
    imgs, keep = [], []
    for i, t in enumerate(thumbs):
        im = cv2.imdecode(np.frombuffer(t or b"", np.uint8), cv2.IMREAD_COLOR) if t else None
        if im is not None and im.size:
            imgs.append(im)
            keep.append(i)
    out = [None] * len(thumbs)
    if not imgs:
        return out
    with _LOCK:
        g = _run("genderage.onnx", cv2.dnn.blobFromImages([cv2.resize(i, (96, 96)) for i in imgs], 1.0, (96, 96),
                                                           (0, 0, 0), swapRB=True))
        lm = _run("1k3d68.onnx", cv2.dnn.blobFromImages([cv2.resize(i, (192, 192)) for i in imgs], 1.0, (192, 192),
                                                         (0, 0, 0), swapRB=True))
    for k, i in enumerate(keep):
        e = np.exp(g[k][:2] - g[k][:2].max())
        male = float(e[1] / e.sum())
        out[i] = (male,) + head_angle(lm[k].reshape(-1, 3)[-68:])
    return out


def head_angle(p):
    """(yaw, pitch, roll) in degrees from the 68 3D points: the face's own left-right line (outer
    eye corners) and up-down line (top of the nose to the chin) give its orientation."""
    p = p.astype(np.float64)
    x = p[45] - p[36]
    x /= np.linalg.norm(x) or 1
    y = p[8] - p[27]
    y -= x * np.dot(y, x)
    y /= np.linalg.norm(y) or 1
    z = np.cross(x, y)
    yaw = float(np.degrees(np.arctan2(z[0], z[2])))
    pitch = float(np.degrees(np.arctan2(-z[1], z[2])))
    roll = float(np.degrees(np.arctan2(x[1], x[0])))
    return round(yaw, 1), round(pitch, 1), round(roll, 1)


def side_on(yaw, pitch):
    return yaw is not None and (abs(yaw) > 45 or abs(pitch or 0) > 35)


def read_missing(conn, progress=lambda d, t: None, cancelled=lambda: False, batch=64):
    """(Background) faces without details get them, read from their thumbnails. Carries on where
    it left off. Returns how many faces were read."""
    import time
    if not ready():
        return 0
    total = conn.execute("SELECT COUNT(*) FROM faces WHERE male IS NULL AND thumb IS NOT NULL").fetchone()[0]
    done = 0
    while not cancelled():
        rows = conn.execute("SELECT id, thumb FROM faces WHERE male IS NULL AND thumb IS NOT NULL LIMIT ?",
                            (batch,)).fetchall()
        if not rows:
            break
        res = analyse([t for _, t in rows])
        conn.executemany("UPDATE faces SET male=?, yaw=?, pitch=?, roll=? WHERE id=?",
                         [((r or (-1.0, None, None, None)) + (fid,)) for (fid, _), r in zip(rows, res)])
        conn.commit()
        update_guesses(conn, [fid for fid, _ in rows])
        done += len(rows)
        progress(done, total)
        time.sleep(0.01)                     # (gives the window its turn)
    return done


# ------------------------------------------------------------------ your corrections, and learning from them
CARRY_MARGIN = 0.08           # a look-alike this much closer than the matching threshold takes your correction
MIN_EACH = 15                 # labelled faces of each kind needed before FaceSort trains its own model


def decide(male, learned, carried, said, px, yaw, person_sex=None):
    """1 looks male, 0 looks female, 2 unsure, None not read yet. What you said wins, then the
    person's Man / Woman, then a look-alike you corrected; otherwise InsightFace's estimate,
    checked against FaceSort's own model once it has learned from you (if they disagree: unsure)."""
    for v in (said, person_sex, carried):
        if v is not None:
            return int(v)
    if male is None:
        return None
    if male < 0 or (px or 99) < SMALL_PX or abs(yaw or 0) > SIDE_ON:
        return 2
    if learned is None:
        return 1 if male >= MALE else 0 if male <= FEMALE else 2
    if male >= MALE and learned >= 0.5 or learned >= 0.95 and male >= 0.5:
        return 1
    if male <= FEMALE and learned < 0.5 or learned <= 0.05 and male <= 0.5:
        return 0
    return 2


def update_guesses(conn, ids=None):
    """Work the decision out again (for these faces, or all) and save what changed."""
    q = """SELECT f.id, f.male, f.male_learned, f.male_carried, f.male_set, f.px, f.yaw, p.sex, f.sex_guess
           FROM faces f LEFT JOIN persons p ON p.id=f.person_id"""
    rows = []
    if ids is None:
        rows = conn.execute(q).fetchall()
    else:
        ids = list(ids)
        for i in range(0, len(ids), 900):
            part = ids[i:i + 900]
            rows += conn.execute(q + f" WHERE f.id IN ({','.join('?' * len(part))})", part).fetchall()
    changed = []
    for fid, male, learned, carried, said, px, yaw, psex, old in rows:
        new = decide(male, learned, carried, said, px, yaw, psex)
        if new != old:
            changed.append((new, fid))
    if changed:
        conn.executemany("UPDATE faces SET sex_guess=? WHERE id=?", changed)
        conn.commit()
    return len(changed)


def set_said(conn, face_ids, value):
    """Your correction on faces: 1 looks male, 0 looks female, None = let FaceSort judge again."""
    face_ids = list(face_ids)
    for i in range(0, len(face_ids), 900):
        part = face_ids[i:i + 900]
        conn.execute(f"UPDATE faces SET male_set=? WHERE id IN ({','.join('?' * len(part))})", [value] + part)
    conn.commit()
    update_guesses(conn, face_ids)


def set_person_sex(conn, person_ids, value):
    person_ids = list(person_ids)
    conn.executemany("UPDATE persons SET sex=? WHERE id=?", [(value, p) for p in person_ids])
    conn.commit()
    ids = [r[0] for p in person_ids for r in conn.execute("SELECT id FROM faces WHERE person_id=?", (p,))]
    update_guesses(conn, ids)


def _train(X, y, steps=400, l2=1e-3, lr=0.05):
    """Logistic regression (numpy, Adam), each kind weighted equally. Returns (w, b)."""
    n, d = X.shape
    pos = y.mean()
    wt = np.where(y > 0.5, 0.5 / max(pos, 1e-6), 0.5 / max(1 - pos, 1e-6)).astype(np.float32)
    w = np.zeros(d, np.float32)
    b = np.float32(0.0)
    m_w, v_w, m_b, v_b = np.zeros_like(w), np.zeros_like(w), 0.0, 0.0
    for t in range(1, steps + 1):
        z = X @ w + b
        p = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
        g = (p - y) * wt / n
        gw = X.T @ g + l2 * w
        gb = float(g.sum())
        m_w = 0.9 * m_w + 0.1 * gw
        v_w = 0.999 * v_w + 0.001 * gw * gw
        m_b = 0.9 * m_b + 0.1 * gb
        v_b = 0.999 * v_b + 0.001 * gb * gb
        w -= lr * (m_w / (1 - 0.9 ** t)) / (np.sqrt(v_w / (1 - 0.999 ** t)) + 1e-8)
        b -= lr * (m_b / (1 - 0.9 ** t)) / (np.sqrt(v_b / (1 - 0.999 ** t)) + 1e-8)
    return w, b


def learn(conn, model, threshold, progress=lambda d, t: None, cancelled=lambda: False):
    """(Background) Use what you told FaceSort:
    1. look-alikes of faces you corrected take the same answer (when they're surely the same person)
    2. with enough faces of each kind (your corrections, and the faces of people you set as Man or
       Woman), FaceSort trains its own small model on the face fingerprints - tuned to your
       photos - and every face gets its opinion too
    3. the Men / Unsure decision is worked out again.
    Returns {"carried": n, "trained": bool, "labelled": n}."""
    import store
    emb = store.emb_from_blob
    # 1. look-alikes of the faces you corrected
    said = conn.execute("SELECT emb, male_set FROM faces WHERE male_set IS NOT NULL AND model=?", (model,)).fetchall()
    open_ = conn.execute("""SELECT id, emb FROM faces WHERE male_set IS NULL AND person_id IS NULL AND model=?""",
                         (model,)).fetchall()
    carried = {}
    if said and open_:
        S = np.stack([emb(b) for b, _ in said]).astype(np.float32)
        S /= np.linalg.norm(S, axis=1, keepdims=True) + 1e-9
        lab = np.array([v for _, v in said])
        need = threshold + CARRY_MARGIN
        for i in range(0, len(open_), 4096):
            if cancelled():
                return None
            part = open_[i:i + 4096]
            E = np.stack([emb(b) for _, b in part]).astype(np.float32)
            E /= np.linalg.norm(E, axis=1, keepdims=True) + 1e-9
            sims = E @ S.T
            for k, (fid, _) in enumerate(part):
                best = {v: float(sims[k][lab == v].max()) if (lab == v).any() else -1 for v in (0, 1)}
                v = max(best, key=best.get)
                if best[v] >= need and best[v] - best[1 - v] >= 0.03:
                    carried[fid] = v
            progress(min(i + 4096, len(open_)), len(open_) * 2)
    conn.execute("UPDATE faces SET male_carried=NULL WHERE male_carried IS NOT NULL")
    if carried:
        conn.executemany("UPDATE faces SET male_carried=? WHERE id=?", [(v, f) for f, v in carried.items()])
    conn.commit()
    # 2. FaceSort's own model
    rows = conn.execute("""SELECT f.emb, COALESCE(f.male_set, p.sex) FROM faces f LEFT JOIN persons p ON p.id=f.person_id
                           WHERE f.model=? AND (f.male_set IS NOT NULL
                                 OR (p.sex IS NOT NULL AND f.confirmed=1 AND f.ignored=0))""", (model,)).fetchall()
    y = np.array([v for _, v in rows], np.float32)
    trained = len(rows) and (y > 0.5).sum() >= MIN_EACH and (y < 0.5).sum() >= MIN_EACH
    if trained:
        X = np.stack([emb(b) for b, _ in rows]).astype(np.float32)
        X /= np.linalg.norm(X, axis=1, keepdims=True) + 1e-9
        w, b = _train(X, y)
        out = []
        cur = conn.execute("SELECT id, emb FROM faces WHERE model=?", (model,))
        while True:
            if cancelled():
                return None
            part = cur.fetchmany(4096)
            if not part:
                break
            E = np.stack([emb(bb) for _, bb in part]).astype(np.float32)
            E /= np.linalg.norm(E, axis=1, keepdims=True) + 1e-9
            p = 1.0 / (1.0 + np.exp(-np.clip(E @ w + b, -30, 30)))
            out += [(round(float(v), 4), fid) for (fid, _), v in zip(part, p)]
        conn.executemany("UPDATE faces SET male_learned=? WHERE id=?", out)
    else:
        conn.execute("UPDATE faces SET male_learned=NULL WHERE male_learned IS NOT NULL")
    conn.commit()
    # 3. the decision
    update_guesses(conn)
    return {"carried": len(carried), "trained": bool(trained), "labelled": len(rows)}


# the SQL conditions for the Men / Unsure sub-groups
def men_sql(a="f."):
    return f"{a}sex_guess = 1"


def unsure_sql(a="f."):
    return f"{a}sex_guess = 2"
