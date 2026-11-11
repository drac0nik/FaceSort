"""High-accuracy face engine: SCRFD-10G detector + ArcFace ResNet-50 recognizer
(InsightFace "buffalo_l" models, free for non-commercial use).

Same interface as engine.FaceEngine, so the rest of FaceSort doesn't care which engine
made a face: detections are returned in YuNet's row format
[x, y, w, h, 10 landmark coords, score] and fingerprints are L2-normalised vectors
(512 numbers instead of 128).

Improvements over the standard engine:
* SCRFD finds partial, occluded, turned and small faces far more reliably and makes
  fewer mistakes on things that aren't faces.
* Photos are padded at the edges before detection so faces cut off by the frame keep
  enough context to be found.
* Big photos are also searched in overlapping tiles, so small faces in group shots are
  found at full detail.
* Every face is fingerprinted together with its mirror image (more stable results).
"""
import os
import threading

import cv2
import numpy as np

import engine

DET_FILE = "det_10g.onnx"
REC_FILE = "w600k_r50.onnx"
MODEL_NAME = "arcface"          # stored with each face so fingerprints from two engines never mix

# The recognizers that run on this detector. "adaface" (1.38): AdaFace IR-101 trained on
# WebFace12M (Kim, Jain & Liu; MIT licence; ONNX export by Yakhyokhuja Valikhujaev). It was
# trained to cope with poor pictures - in FaceSort's tests it told people apart better than
# ArcFace on dark, tiny and blurry faces (and the same on clear ones). About 5x the work per
# face of ArcFace: comfortable with a graphics card, slow on the processor alone.
RECOGNIZERS = {
    "arcface": {"file": REC_FILE, "rgb": True, "quality": True},
    "adaface": {"file": "adaface_ir_101.onnx", "rgb": False, "quality": False,      # BGR in; unit-length out
                "url": "https://github.com/yakhyo/adaface-onnx/releases/download/weights/adaface_ir_101.onnx",
                "sha256": "f2eb07d03de0af560a82e1214df799fec5e09375d43521e2868f9dc387e5a43e",
                "size": 260704652},
}
HQ_MODELS = tuple(RECOGNIZERS)

ARCFACE_DST = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                        [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)


def model_dir(model="arcface"):
    """Models live next to the app, or (big download) in the data folder."""
    rec = RECOGNIZERS[model]["file"]
    for d in (engine.resource_path("models"), os.path.join(engine.store_data_dir(), "models")):
        if os.path.exists(os.path.join(d, rec)) and os.path.exists(os.path.join(d, DET_FILE)):
            return d
    return None


def available(model="arcface"):
    return model in RECOGNIZERS and model_dir(model) is not None


class _Sessions:
    """ONNX Runtime sessions shared by all threads (GPU when possible, else CPU)."""
    _lock = threading.Lock()
    _inst = {}

    @classmethod
    def get(cls, use_gpu, model="arcface"):
        key = (bool(use_gpu), model)
        with cls._lock:
            if key not in cls._inst:
                cls._inst[key] = cls(use_gpu, model)
            return cls._inst[key]

    def __init__(self, use_gpu, model="arcface"):
        import onnxruntime as ort
        d = model_dir(model)
        if d is None:
            raise FileNotFoundError("High-accuracy face models are not installed")
        self.model = model
        self.rgb = RECOGNIZERS[model]["rgb"]
        self.has_quality = RECOGNIZERS[model]["quality"]
        so = ort.SessionOptions()
        so.log_severity_level = 3
        provs, self.label = ["CPUExecutionProvider"], "CPU"
        avail = ort.get_available_providers()
        if use_gpu and "DmlExecutionProvider" in avail:
            so.enable_mem_pattern = False
            so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            provs, self.label = [("DmlExecutionProvider", {"performance_preference": "high_performance"}),
                                 "CPUExecutionProvider"], "GPU (DirectML)"
        elif use_gpu and "CUDAExecutionProvider" in avail:
            provs, self.label = ["CUDAExecutionProvider", "CPUExecutionProvider"], "GPU (CUDA)"
        self.det = ort.InferenceSession(os.path.join(d, DET_FILE), so, providers=provs)
        self.rec = ort.InferenceSession(os.path.join(d, RECOGNIZERS[model]["file"]), so, providers=provs)
        if provs[0] != "CPUExecutionProvider" and self.det.get_providers()[0] == "CPUExecutionProvider":
            self.label = "CPU"
        self.det_in = self.det.get_inputs()[0].name
        self.rec_in = self.rec.get_inputs()[0].name
        self.det_lock = threading.Lock() if self.label != "CPU" else _NoLock()
        self.rec_lock = threading.Lock() if self.label != "CPU" else _NoLock()


class _NoLock:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _scrfd(sess, bgr, thr):
    """Run SCRFD on an image (any size, padded to /32). Returns YuNet-format rows."""
    h, w = bgr.shape[:2]
    ph, pw = -(-h // 32) * 32, -(-w // 32) * 32
    pad = np.zeros((ph, pw, 3), np.uint8)
    pad[:h, :w] = bgr
    blob = cv2.dnn.blobFromImage(pad, 1.0 / 128, (pw, ph), (127.5, 127.5, 127.5), swapRB=True)
    with sess.det_lock:
        outs = sess.det.run(None, {sess.det_in: blob})
    rows = []
    for i, st in enumerate((8, 16, 32)):
        scores = outs[i].reshape(-1)
        keep = np.where(scores >= thr)[0]
        if not len(keep):
            continue
        gh, gw = ph // st, pw // st
        cy, cx = np.mgrid[:gh, :gw]
        centers = np.stack([cx, cy], axis=-1).reshape(-1, 2).astype(np.float32) * st
        centers = np.repeat(centers, 2, axis=0)        # 2 anchors per position
        c = centers[keep]
        d = outs[i + 3].reshape(-1, 4)[keep] * st
        k = outs[i + 6].reshape(-1, 10)[keep] * st
        x1, y1, x2, y2 = c[:, 0] - d[:, 0], c[:, 1] - d[:, 1], c[:, 0] + d[:, 2], c[:, 1] + d[:, 3]
        kps = np.empty_like(k)
        kps[:, 0::2] = c[:, :1] + k[:, 0::2]
        kps[:, 1::2] = c[:, 1:] + k[:, 1::2]
        rows.append(np.column_stack([x1, y1, x2 - x1, y2 - y1, kps, scores[keep]]))
    if not rows:
        return np.zeros((0, 15), np.float32)
    return np.concatenate(rows).astype(np.float32)


def _nms(F, thr_iou=0.4):
    if not len(F):
        return F
    idx = cv2.dnn.NMSBoxes(F[:, :4].tolist(), F[:, 14].tolist(), 0.0, thr_iou, top_k=5000)
    return F[np.array(idx).flatten()] if len(idx) else F[:0]


def _contained_dupes(F):
    """Drop a box that mostly sits inside a more confident one (same face found twice)."""
    out = []
    for f in sorted(F, key=lambda r: -float(r[14])):
        a = float(f[2] * f[3])
        dup = False
        for g in out:
            ix = max(0.0, min(f[0] + f[2], g[0] + g[2]) - max(f[0], g[0]))
            iy = max(0.0, min(f[1] + f[3], g[1] + g[3]) - max(f[1], g[1]))
            if ix * iy > 0.6 * min(a, float(g[2] * g[3])):
                dup = True
                break
        if not dup:
            out.append(f)
    return np.array(out, np.float32) if out else np.zeros((0, 15), np.float32)


def plausible(f, at_edge=False):
    """Is the landmark layout face-like? Real faces: eyes side by side above the mouth,
    nose between them, mouth corners below the eyes. Skin creases, knees and elbows
    rarely produce this arrangement.
    Confident detections and faces cut off by the photo's edge (whose hidden landmarks
    are only estimated) get a lenient check; doubtful ones inside the frame a strict one."""
    score = float(f[14])
    le, re_, no, lm, rm = f[4:6], f[6:8], f[8:10], f[10:12], f[12:14]
    eye_d = np.linalg.norm(re_ - le)
    if eye_d < 2:
        return False
    ang = np.arctan2(re_[1] - le[1], re_[0] - le[0])     # put the face upright, then check the layout
    R = np.array([[np.cos(-ang), -np.sin(-ang)], [np.sin(-ang), np.cos(-ang)]])
    P = (np.stack([le, re_, no, lm, rm]) - le) @ R.T / eye_d
    eyes_y = (P[0, 1] + P[1, 1]) / 2
    mouth_y = (P[3, 1] + P[4, 1]) / 2
    mouth_w = P[4, 0] - P[3, 0]
    nose_x, nose_y = P[2, 0], P[2, 1]
    ar = f[3] / max(f[2], 1e-3)
    if score >= 0.7 or at_edge:
        # lenient: just "mouth below the eyes" and not upside down
        return bool(0.3 < mouth_y - eyes_y < 3.0 and abs(ang) < np.radians(75))
    return bool(abs(ang) < np.radians(50) and 0.55 < mouth_y - eyes_y < 1.8 and eyes_y + 0.1 < nose_y < mouth_y + 0.15
                and -0.25 < nose_x < 1.25 and mouth_w > 0.3 and 0.7 < ar < 2.3)


class HQFaceEngine:
    """Drop-in replacement for engine.FaceEngine."""
    model = MODEL_NAME
    dim = 512
    SIDE = 640          # detector input for the whole photo
    TILE_MIN = 1000     # photos at least this big are also searched in tiles

    def __init__(self, min_face_px=20, use_gpu=True, threshold=0.5, rotations=True, model="arcface"):
        self.model = model
        self.s = _Sessions.get(use_gpu, model)
        self.gpu = self.s if self.s.label != "CPU" else None
        self.min_face_px = min_face_px
        self.threshold = float(threshold)
        self.rotations = rotations

    # ---- detection
    def _detect_scaled(self, bgr, side):
        h, w = bgr.shape[:2]
        k = min(1.0, side / max(h, w))
        img = cv2.resize(bgr, (round(w * k), round(h * k)), interpolation=cv2.INTER_AREA) if k < 1 else bgr
        # pad the edges so faces cut off by the frame keep some context
        p = int(round(max(img.shape[:2]) * 0.08))
        img = cv2.copyMakeBorder(img, p, p, p, p, cv2.BORDER_CONSTANT, value=(0, 0, 0))
        F = _scrfd(self.s, img, self.threshold)
        if len(F):
            F[:, [0, 1]] -= p
            F[:, 4:14:2] -= p
            F[:, 5:14:2] -= p
            F[:, :14] /= k
        return F

    def detect(self, bgr):
        h, w = bgr.shape[:2]
        # whole photo at two sizes: 640 (normal) and 320 (faces that fill the frame)
        parts = [self._detect_scaled(bgr, self.SIDE), self._detect_scaled(bgr, 320)]
        first = _nms(np.concatenate(parts))
        # group photos and big photos: also 2 x 2 overlapping tiles for small faces at full detail
        many = len(first) >= 2 or any(min(f[2], f[3]) < 0.06 * max(h, w) for f in first)
        if max(h, w) >= self.TILE_MIN and (many or self.gpu is not None):
            th, tw = int(h * 0.6), int(w * 0.6)
            for y0 in (0, h - th):
                for x0 in (0, w - tw):
                    F = self._detect_scaled(bgr[y0:y0 + th, x0:x0 + tw], self.SIDE)
                    if len(F):
                        F[:, [0, 4, 6, 8, 10, 12]] += x0
                        F[:, [1, 5, 7, 9, 11, 13]] += y0
                        parts.append(F)
        F = _contained_dupes(_nms(np.concatenate(parts)))
        out = []
        for f in F:
            if min(f[2], f[3]) < self.min_face_px:
                continue
            # clip the box to the photo (a cut-off face keeps the visible part)
            x0, y0 = max(0.0, f[0]), max(0.0, f[1])
            x1, y1 = min(float(w), f[0] + f[2]), min(float(h), f[1] + f[3])
            if x1 - x0 < 4 or y1 - y0 < 4:
                continue
            m = 0.03 * max(h, w)   # near the photo's border: likely cut off by the frame
            at_edge = f[0] < m or f[1] < m or f[0] + f[2] > w - m or f[1] + f[3] > h - m
            if not plausible(f, at_edge):
                continue
            g = f.copy()
            g[:4] = (x0, y0, x1 - x0, y1 - y0)
            out.append(g)
        return out

    # ---- recognition
    @staticmethod
    def align(bgr, f):
        src = f[4:14].reshape(5, 2).astype(np.float32)
        M, _ = cv2.estimateAffinePartial2D(src, ARCFACE_DST, method=cv2.LMEDS)
        if M is None:
            M = cv2.getAffineTransform(src[:3], ARCFACE_DST[:3])
        return cv2.warpAffine(bgr, M, (112, 112), borderValue=0)

    def embed_many(self, bgr, faces):
        return [e for e, _ in self.embed_with_quality(bgr, faces)]

    def embed_with_quality(self, bgr, faces):
        """[(fingerprint, reading strength)]. Real faces read strongly (tested: never below 15);
        knees, elbows and patterns mostly read weakly."""
        if not faces:
            return []
        crops = [self.align(bgr, f) for f in faces]
        batch = np.concatenate([crops, [c[:, ::-1] for c in crops]])   # with mirror images
        blob = cv2.dnn.blobFromImages(list(batch), 1.0 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=self.s.rgb)
        with self.s.rec_lock:
            feats = self.s.rec.run(None, {self.s.rec_in: blob})[0].astype(np.float32)
        n = len(crops)
        feats = feats[:n] + feats[n:]
        norms = np.linalg.norm(feats, axis=1, keepdims=True)
        # ArcFace: how strongly it reads (real faces strongly, knees and patterns weakly). AdaFace's
        # export gives unit-length fingerprints, so it has no such reading.
        quality = (norms[:, 0] / 2).tolist() if self.s.has_quality else [None] * n
        norms[norms == 0] = 1
        return list(zip(list(feats / norms), quality))

    def embed(self, bgr, face):
        return self.embed_many(bgr, [face])[0]

    thumbnail = staticmethod(engine.FaceEngine.thumbnail)

    def process(self, path):
        if engine.is_video(path):
            return engine.process_video(self, path)
        return self.process_image(engine.load_image(path))

    def process_image(self, img):
        h, w = img.bgr.shape[:2]
        cands = [(f, img.bgr, None) for f in self.detect(img.bgr)]
        if self.rotations and not cands:   # SCRFD copes with tilted faces; only retry when nothing was found
            for code in (cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE):
                turned = cv2.rotate(img.bgr, code)
                cands += [(f, turned, code) for f in self.detect(turned)]
        cands.sort(key=lambda c: -float(c[0][14]))
        found, boxes = [], []
        for f, work, rot in cands:
            box = engine._orig_box(f, rot, w, h)
            if any(engine._iou_px(box, b) > 0.3 for b in boxes):
                continue
            found.append((f, work, rot))
            boxes.append(box)
        img.bright = engine.brightness(img.bgr)[0]
        extra, bright = ([], None) if img.dark_seen == -1 else \
            engine.second_look(self, img.bgr, found, min(0.99, self.threshold + engine.DARK_EXTRA_SCORE))
        img.dark_seen = 1 if engine.DARK_SECOND_LOOK and img.dark_seen != -1 else 0
        found += extra
        out = []
        for f, work, rot in found:
            emb, quality = self.embed_with_quality(work, [f])[0]
            clarity = engine.clarity_of(work, f)
            if bright is not None and work is img.bgr:
                # a face in a dark photo: read it from the brightened copy too, keep the clearer
                c2 = engine.clarity_of(bright, f)
                if c2 is not None and (clarity is None or c2 > clarity + 0.05):
                    (emb, quality), clarity, work = self.embed_with_quality(bright, [f])[0], c2, bright
            x, y, fw, fh = engine._orig_box(f, rot, w, h)
            x, y = max(0.0, x), max(0.0, y)
            fw, fh = min(fw, w - x), min(fh, h - y)
            if fw <= 1 or fh <= 1:
                continue
            out.append({"x": x / w, "y": y / h, "w": fw / w, "h": fh / h, "score": float(f[14]),
                        "px": int(min(fw, fh) * img.width / w), "emb": emb, "thumb": self.thumbnail(work, f),
                        "quality": quality, "clarity": clarity})
        return img, out


MODELS_URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"
DOWNLOAD_MB = 290


def download_models(progress=lambda done, total, msg: None, cancelled=lambda: False):
    """Download the model pack once and keep just the two files FaceSort uses (~190 MB)."""
    import urllib.request
    import zipfile
    dest = os.path.join(engine.store_data_dir(), "models")
    os.makedirs(dest, exist_ok=True)
    tmp = os.path.join(dest, "buffalo_l.zip.part")
    req = urllib.request.Request(MODELS_URL, headers={"User-Agent": "FaceSort"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or DOWNLOAD_MB * 1024 * 1024)
        done = 0
        while True:
            if cancelled():
                f.close()
                os.remove(tmp)
                return False
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            progress(done // (1 << 20), total // (1 << 20), f"Downloading high-accuracy models… {done >> 20} / {total >> 20} MB")
    progress(0, 0, "Unpacking…")
    with zipfile.ZipFile(tmp) as z:
        for name in z.namelist():
            base = os.path.basename(name)
            if base in (DET_FILE, REC_FILE):
                with z.open(name) as src, open(os.path.join(dest, base + ".part"), "wb") as out:
                    while True:
                        b = src.read(1 << 20)
                        if not b:
                            break
                        out.write(b)
                os.replace(os.path.join(dest, base + ".part"), os.path.join(dest, base))
    os.remove(tmp)
    with _Sessions._lock:
        _Sessions._inst.clear()
    return available()


def download_adaface(progress=lambda done, total, msg: None, cancelled=lambda: False):
    """The AdaFace recognizer (about 260 MB, once), checked against its known checksum. Gets the
    detector too if it isn't there yet. False if cancelled."""
    import hashlib
    import urllib.request
    info = RECOGNIZERS["adaface"]
    dest = os.path.join(engine.store_data_dir(), "models")
    os.makedirs(dest, exist_ok=True)
    if not available("arcface") and not os.path.exists(os.path.join(dest, DET_FILE)):
        if not download_models(progress, cancelled):
            return False
    out = os.path.join(dest, info["file"])
    if not os.path.exists(out):
        tmp = out + ".part"
        h = hashlib.sha256()
        req = urllib.request.Request(info["url"], headers={"User-Agent": "FaceSort"})
        done = 0
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or info["size"])
            while True:
                if cancelled():
                    f.close()
                    os.remove(tmp)
                    return False
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                progress(done >> 20, total >> 20, f"Downloading the AdaFace model… {done >> 20} / {total >> 20} MB")
        if h.hexdigest() != info["sha256"]:
            os.remove(tmp)
            raise RuntimeError("the AdaFace model download doesn't match its known checksum - not used")
        os.replace(tmp, out)
    with _Sessions._lock:
        _Sessions._inst.clear()
    return available("adaface")
