"""Poses (1.46): how the people in a photo are standing, sitting, lying...

How it works
- The person finder (YOLOX, the same one the same-shoot suggestions use) marks everyone in the
  photo; if same-shoot already found them, those boxes are used.
- RTMPose (OpenMMLab, Apache-2.0) marks 17 points on each person: nose, eyes, ears, shoulders,
  elbows, wrists, hips, knees, ankles. Both models are downloaded once (about 90 MB together)
  and run on this PC, in the graphics card helper.
- From the points, simple geometry gives the poses: Standing, Sitting, Lying down, Kneeling,
  Arms raised, Facing away, Full body, Close-up. They're offered as tags beside the photo, can
  be searched, and rules and smart albums can use them. Nothing about who someone is.
"""
import json
import os
import time

import cv2
import numpy as np

import store

POSE_URL = "https://github.com/drac0nik/FaceSort/releases/download/models-pose/rtmpose_m_body7_256x192.onnx"
POSE_SHA = "5c0a4bf67953e6d2ac43ce15e77dc9d5d354ae18430a47d2c5963a7bc5683e3c"
POSE_FILE = "rtmpose_m_body7_256x192.onnx"
DOWNLOAD_MB = 54
POSES = ["Standing", "Sitting", "Lying down", "Kneeling", "Arms raised", "Facing away", "Full body", "Close-up"]
KP = {n: i for i, n in enumerate(["nose", "l_eye", "r_eye", "l_ear", "r_ear", "l_shoulder", "r_shoulder", "l_elbow",
                                  "r_elbow", "l_wrist", "r_wrist", "l_hip", "r_hip", "l_knee", "r_knee", "l_ankle",
                                  "r_ankle"])}
SEEN = 0.55                  # a point counts as seen at this confidence (hidden ones are guessed at 0.3-0.5)
IN_W, IN_H = 192, 256
MEAN = np.array([123.675, 116.28, 103.53], np.float32)
STD = np.array([58.395, 57.12, 57.375], np.float32)
WORK_SIDE = 1280


def model_dir():
    return os.path.join(store.data_dir(), "models", "pose")


def model_path():
    return os.path.join(model_dir(), POSE_FILE)


def ready():
    import bodies
    return os.path.exists(model_path()) and os.path.exists(bodies.model_path("detect"))


def download(progress=lambda d, t, m: None, cancelled=lambda: False):
    """The pose model (from FaceSort's GitHub page) and the person finder (OpenCV's model zoo)."""
    import hashlib
    import urllib.request
    import bodies
    os.makedirs(model_dir(), exist_ok=True)
    todo = []
    if not os.path.exists(model_path()):
        todo.append((POSE_URL, model_path(), POSE_SHA, None))
    det = bodies.model_path("detect")
    if not os.path.exists(det):
        os.makedirs(os.path.dirname(det), exist_ok=True)
        todo.append((bodies.MODELS["detect"][2], det, None, bodies.SIZES["detect"]))
    for url, dest, sha, size in todo:
        tmp = dest + ".part"
        h = hashlib.sha256()
        with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or size or 0)
            got = 0
            while True:
                if cancelled():
                    raise RuntimeError("cancelled")
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                got += len(chunk)
                progress(got, total, f"Downloading the pose models… {got // (1 << 20)} MB")
        if sha and h.hexdigest() != sha:
            os.remove(tmp)
            raise RuntimeError("the pose model didn't download correctly (checksum)")
        if size and os.path.getsize(tmp) != size:
            os.remove(tmp)
            raise RuntimeError("the person finder didn't download correctly (size)")
        os.replace(tmp, dest)
    return True


class Model:
    def __init__(self):
        import photo_tools
        self.sess = photo_tools._session(model_path())
        self.inp = self.sess.get_inputs()[0].name

    def crop(self, bgr, box):
        """The person, cut out at the model's size (with a margin), and how to map back."""
        x, y, w, h = box[:4]
        cx, cy = x + w / 2, y + h / 2
        aspect = IN_W / IN_H
        if w > h * aspect:
            h = w / aspect
        else:
            w = h * aspect
        w, h = w * 1.25, h * 1.25
        src = np.array([[cx - w / 2, cy - h / 2], [cx + w / 2, cy - h / 2], [cx - w / 2, cy + h / 2]], np.float32)
        dst = np.array([[0, 0], [IN_W, 0], [0, IN_H]], np.float32)
        M = cv2.getAffineTransform(src, dst)
        img = cv2.warpAffine(bgr, M, (IN_W, IN_H), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))
        return img, (cx - w / 2, cy - h / 2, w / IN_W, h / IN_H)

    def keypoints(self, bgr, boxes):
        """[(17, 3) array of x, y (pixels of bgr), confidence] per box."""
        if not boxes:
            return []
        crops, maps = [], []
        for b in boxes:
            img, m = self.crop(bgr, b)
            crops.append(((img[:, :, ::-1].astype(np.float32) - MEAN) / STD).transpose(2, 0, 1))
            maps.append(m)
        out = []
        for i in range(0, len(crops), 16):
            sx, sy = self.sess.run(None, {self.inp: np.stack(crops[i:i + 16]).astype(np.float32)})
            for k in range(sx.shape[0]):
                px = sx[k].argmax(axis=1) / 2.0             # (simcc split ratio 2)
                py = sy[k].argmax(axis=1) / 2.0
                conf = np.minimum(sx[k].max(axis=1), sy[k].max(axis=1))
                x0, y0, kx, ky = maps[i + k]
                out.append(np.stack([x0 + px * kx, y0 + py * ky, conf], 1).astype(np.float32))
        return out


_MODEL = [None]


def model():
    if _MODEL[0] is None:
        _MODEL[0] = Model()
    return _MODEL[0]


LOWER = {"l_hip", "r_hip", "l_knee", "r_knee", "l_ankle", "r_ankle"}
SEEN_UPPER = 0.45            # head, shoulders, arms: rarely guessed


def _pt(k, name):
    p = k[KP[name]]
    return (float(p[0]), float(p[1])) if p[2] >= (SEEN if name in LOWER else SEEN_UPPER) else None


def _mid(a, b):
    if a and b:
        return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    return a or b


def poses_of(k, box, photo_h):
    """The poses one person's points show (a set of names from POSES)."""
    out = set()
    g = lambda n: _pt(k, n)
    nose, leye, reye = g("nose"), g("l_eye"), g("r_eye")
    lsh, rsh, lhip, rhip = g("l_shoulder"), g("r_shoulder"), g("l_hip"), g("r_hip")
    lkn, rkn, lan, ran = g("l_knee"), g("r_knee"), g("l_ankle"), g("r_ankle")
    lwr, rwr = g("l_wrist"), g("r_wrist")
    sh, hip, kn, an = _mid(lsh, rsh), _mid(lhip, rhip), _mid(lkn, rkn), _mid(lan, ran)
    head = nose or _mid(leye, reye) or _mid(g("l_ear"), g("r_ear"))
    torso = None
    if sh and hip:
        dx, dy = hip[0] - sh[0], hip[1] - sh[1]
        torso = (dx, dy, max(1e-6, (dx * dx + dy * dy) ** 0.5))
    # how much of the body is in the picture
    if sh and hip and an:
        out.add("Full body")
    elif sh and not hip and not kn and head and abs(sh[1] - head[1]) > 0.10 * photo_h:
        out.add("Close-up")                                 # head and shoulders, big in the picture
    # facing away: the shoulders are clear but no face points are
    if lsh and rsh and not nose and not leye and not reye:
        out.add("Facing away")
    # arms raised: a wrist above the head (or above the shoulders by a good margin)
    top = head[1] if head else (sh[1] - (torso[2] * 0.35 if torso else 0) if sh else None)
    if top is not None and any(w and w[1] < top for w in (lwr, rwr)):
        out.add("Arms raised")
    if torso:
        dx, dy, tl = torso
        upright = abs(dy) > abs(dx) * 1.5 and dy > 0
        if abs(dx) > abs(dy) * 1.2:
            out.add("Lying down")
        elif upright and (lkn or rkn):
            # each leg on its own (a lunge or a kick has one leg each way - not kneeling or sitting)
            legs = []
            for h_, k_, a_ in ((lhip, lkn, lan), (rhip, rkn, ran)):
                h_ = h_ or hip
                if not (h_ and k_):
                    continue
                t = (k_[0] - h_[0], k_[1] - h_[1])
                tdown = t[1] / max(1e-6, (t[0] ** 2 + t[1] ** 2) ** 0.5)     # 1 = straight down
                sdown = None
                if a_:
                    sh_ = (a_[0] - k_[0], a_[1] - k_[1])
                    sdown = sh_[1] / max(1e-6, (sh_[0] ** 2 + sh_[1] ** 2) ** 0.5)
                legs.append((tdown, sdown))
            if legs and all(t < 0.55 for t, _ in legs):
                out.add("Sitting")                          # thighs out in front
            elif legs and all(sd is not None and sd < 0.45 and t > 0.75 for t, sd in legs):
                out.add("Kneeling")                         # thighs down, shins along the ground
            elif legs and all(t > 0.8 and (sd is None or sd > 0.7) for t, sd in legs):
                out.add("Standing")
    return out


def read_photo(bgr, boxes=None):
    """{"people": [{"box": [x, y, w, h] (0-1), "points": [[x, y, conf] (0-1)], "poses": [...]}]}"""
    import bodies
    h, w = bgr.shape[:2]
    if boxes is None:
        boxes = bodies.models().people(bgr) if bodies.available() else _people_only(bgr)
    pts = model().keypoints(bgr, boxes)
    people = []
    for b, k in zip(boxes, pts):
        people.append({"box": [round(b[0] / w, 4), round(b[1] / h, 4), round(b[2] / w, 4), round(b[3] / h, 4)],
                       "points": [[round(float(p[0]) / w, 4), round(float(p[1]) / h, 4), round(float(p[2]), 3)]
                                  for p in k],
                       "poses": sorted(poses_of(k, b, h))})
    return {"people": people}


_DET = [None]


def _people_only(bgr):
    """The person finder on its own (same-shoot's appearance model isn't needed for poses)."""
    import bodies
    if _DET[0] is None:
        class _M(bodies.Models):
            def __init__(self):            # (only the person finder)
                import photo_tools
                self.det = photo_tools._session(bodies.model_path("detect"))
                self.det_in = self.det.get_inputs()[0].name
                s = 640
                grids, strides = [], []
                for st in (8, 16, 32):
                    n = s // st
                    yv, xv = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
                    grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
                    strides.append(np.full((n * n, 1), st))
                self.grids = np.concatenate(grids).astype(np.float32)
                self.strides = np.concatenate(strides).astype(np.float32)
        _DET[0] = _M()
    return _DET[0].people(bgr)


def read_missing(conn, progress=lambda d, t, m: None, cancelled=lambda: False):
    """Poses for every photo not read yet (carries on where it left off). Returns photos read."""
    import engine
    import helpers
    todo = conn.execute("""SELECT id, path FROM photos WHERE COALESCE(pose_done, 0)=0 AND is_video=0
                           AND error IS NULL AND COALESCE(sort_later, 0)<>2
                           AND (nfaces > 0 OR id IN (SELECT photo_id FROM bodies)) ORDER BY id DESC""").fetchall()
    if not todo:
        return 0
    done = 0
    pictures = helpers.decoded(todo, lambda r: engine.load_image(r[1], max_side=WORK_SIDE).bgr)
    for (pid, path), bgr in pictures:
        if cancelled():
            break
        info, ok = None, False
        if not isinstance(bgr, Exception):
            try:
                h, w = bgr.shape[:2]
                known = conn.execute("SELECT x, y, w, h, score FROM bodies WHERE photo_id=?", (pid,)).fetchall()
                boxes = [(x * w, y * h, bw * w, bh * h, s) for x, y, bw, bh, s in known] if known else None
                info = read_photo(bgr, boxes)
                ok = True
            except Exception:
                ok = False
        with conn.write():
            conn.execute("DELETE FROM photo_pose WHERE photo_id=?", (pid,))
            if ok:
                rows = {(pid, p) for person in info["people"] for p in person["poses"]}
                conn.executemany("INSERT OR IGNORE INTO photo_pose(photo_id, pose) VALUES(?,?)", list(rows))
                conn.execute("INSERT OR REPLACE INTO photo_points(photo_id, info) VALUES(?,?)",
                             (pid, json.dumps(info, separators=(",", ":"))))
            conn.execute("UPDATE photos SET pose_done=? WHERE id=?", (1 if ok else 2, pid))
        done += 1
        if done % 5 == 0 or done == len(todo):
            progress(done, len(todo), f"Reading poses… {done:,} / {len(todo):,}")
        time.sleep(0)
    return done


def photo_poses(conn, photo_id):
    try:
        return [r[0] for r in conn.execute("SELECT pose FROM photo_pose WHERE photo_id=? ORDER BY pose", (photo_id,))]
    except Exception:
        return []
