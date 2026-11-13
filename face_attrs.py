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
        done += len(rows)
        progress(done, total)
        time.sleep(0.01)                     # (gives the window its turn)
    return done


# the SQL conditions for the Men / Unsure sub-groups (male = -1: couldn't be read -> Unsure)
def men_sql(a="f."):
    return (f"{a}male >= {MALE} AND COALESCE({a}px, 99) >= {SMALL_PX} "
            f"AND ABS(COALESCE({a}yaw, 0)) <= {SIDE_ON}")


def unsure_sql(a="f."):
    return (f"{a}male IS NOT NULL AND ({a}male < 0 OR ({a}male > {FEMALE} AND {a}male < {MALE}) "
            f"OR COALESCE({a}px, 99) < {SMALL_PX} OR ABS(COALESCE({a}yaw, 0)) > {SIDE_ON})")
