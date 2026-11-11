"""Tattoo matching: find the same tattoo artwork in other photos.

You mark a tattoo (a box on a photo) and say whose it is. FaceSort then looks for the
same artwork elsewhere using local feature matching (SIFT keypoints + a geometric
check with RANSAC). That tolerates the tattoo being rotated, a different size, seen at
an angle, partly covered or on a curved arm, and rejects patterns that merely look
similar. Matching only ever compares against tattoos you marked in your own library.
"""
import threading

import cv2
import numpy as np

import engine

SEARCH_SIDE = 1400     # photos are searched at this size
SAMPLE_SIDE = 2400     # marked tattoos are read at higher detail
MIN_KEYPOINTS = 15     # a mark with fewer features than this is too plain to match


class Sample:
    """Features of one marked tattoo."""
    __slots__ = ("tattoo_id", "person_id", "photo_id", "kp", "desc", "w", "h")

    def __init__(self, tattoo_id, person_id, photo_id, kp, desc, w, h):
        self.tattoo_id, self.person_id, self.photo_id = tattoo_id, person_id, photo_id
        self.kp, self.desc, self.w, self.h = kp, desc, w, h


_local = threading.local()


def _sift():
    s = getattr(_local, "sift", None)
    if s is None:
        s = cv2.SIFT_create(nfeatures=4000, contrastThreshold=0.03)
        _local.sift = s
    return s


def _matcher():
    m = getattr(_local, "matcher", None)
    if m is None:
        m = cv2.FlannBasedMatcher(dict(algorithm=1, trees=4), dict(checks=48))
        _local.matcher = m
    return m


def _root(desc):
    """RootSIFT: more reliable matching of SIFT descriptors (Arandjelovic & Zisserman)."""
    if desc is None:
        return None
    d = desc / (np.abs(desc).sum(axis=1, keepdims=True) + 1e-7)
    return np.sqrt(d).astype(np.float32)


def _gray(bgr):
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    return cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(g)  # even out skin tones / lighting


def make_sample(path, box, tattoo_id=0, person_id=0, photo_id=0):
    """Features of the marked area (box = normalized x, y, w, h as displayed). None if too plain."""
    img = engine.load_image(path, max_side=SAMPLE_SIDE).bgr
    h, w = img.shape[:2]
    x, y, bw, bh = box
    x0, y0 = int(max(0, x * w)), int(max(0, y * h))
    x1, y1 = int(min(w, (x + bw) * w)), int(min(h, (y + bh) * h))
    crop = img[y0:y1, x0:x1]
    if crop.size == 0 or min(crop.shape[:2]) < 24:
        return None
    # comparable detail to the searched photos: long side at most ~700 px
    k = min(1.0, 700 / max(crop.shape[:2]))
    if k < 1:
        crop = cv2.resize(crop, (round(crop.shape[1] * k), round(crop.shape[0] * k)), interpolation=cv2.INTER_AREA)
    kp, desc = _sift().detectAndCompute(_gray(crop), None)
    desc = _root(desc)
    if desc is None or len(kp) < MIN_KEYPOINTS:
        return None
    pts = np.float32([p.pt for p in kp])
    return Sample(tattoo_id, person_id, photo_id, pts, desc, crop.shape[1], crop.shape[0])


def photo_features(path):
    img = engine.load_image(path, max_side=SEARCH_SIDE).bgr
    kp, desc = _sift().detectAndCompute(_gray(img), None)
    desc = _root(desc)
    if desc is None or len(kp) < 10:
        return None
    return np.float32([p.pt for p in kp]), desc, img.shape[1], img.shape[0]


DEFAULT_MIN_INLIERS = 14   # tested: 0 false matches on look-alike tattoos (worst wrong match scored 10)


def match(sample, feats, min_inliers=DEFAULT_MIN_INLIERS):
    """Look for the sample in a photo's features. Returns (inliers, box) or None.
    box = normalized x, y, w, h of the tattoo in that photo."""
    pts, desc, W, H = feats
    if len(desc) < 2:
        return None
    try:
        pairs = _matcher().knnMatch(sample.desc, desc, k=2)
    except cv2.error:
        return None
    good = [m for m in (p for p in pairs if len(p) == 2) if m[0].distance < 0.8 * m[1].distance]
    good = [m[0] for m in good]
    if len(good) < min_inliers:
        return None
    src = sample.kp[[m.queryIdx for m in good]].reshape(-1, 1, 2)
    dst = pts[[m.trainIdx for m in good]].reshape(-1, 1, 2)
    Hm, mask = cv2.findHomography(src, dst, cv2.RANSAC, 8.0, maxIters=2000, confidence=0.995)
    if Hm is None or mask is None:
        return None
    inl = int(mask.sum())
    if inl < min_inliers:
        return None
    # the matched points must be spread out (not one tiny clump) ...
    ip = dst[mask.ravel() == 1].reshape(-1, 2)
    if np.ptp(ip[:, 0]) < 12 or np.ptp(ip[:, 1]) < 12:
        return None
    # ... and the tattoo outline must map to a sensible shape (no folding, no wild stretching)
    corners = np.float32([[0, 0], [sample.w, 0], [sample.w, sample.h], [0, sample.h]]).reshape(-1, 1, 2)
    quad = cv2.perspectiveTransform(corners, Hm).reshape(-1, 2)
    if not cv2.isContourConvex(quad.astype(np.float32)):
        return None
    area = cv2.contourArea(quad)
    scale = np.sqrt(max(area, 1e-6) / (sample.w * sample.h))
    if not (0.08 < scale < 8):
        return None
    sides = [np.linalg.norm(quad[i] - quad[(i + 1) % 4]) for i in range(4)]
    if min(sides) < 1e-3 or max(sides) / min(sides) > 12:
        return None
    x0, y0 = np.clip(quad.min(axis=0), 0, [W, H])
    x1, y1 = np.clip(quad.max(axis=0), 0, [W, H])
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return inl, (float(x0 / W), float(y0 / H), float((x1 - x0) / W), float((y1 - y0) / H))
