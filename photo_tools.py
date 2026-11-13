"""Photo tools for the viewer: red-eye removal, upscaling (Real-ESRGAN, shipped), a light
auto-enhance, restoring old faded photos, and colourizing black & white / sepia photos
(DeOldify, downloaded once). Everything runs on this PC; nothing is uploaded.

All functions take and return RGB uint8 arrays (full size)."""
import os
import re
import threading

import cv2
import numpy as np
from PIL import Image, ImageOps

import engine

UPSCALE_MODEL = engine.resource_path("models", "upscale_x4.onnx")      # realesr-general-x4v3 (BSD-3)
COLOR_URL = "https://github.com/instant-high/deoldify-onnx/releases/download/deoldify-onnx/deoldify.onnx"
COLOR_MB = 243
MAX_OUT_MP = 300              # biggest upscale offered (megapixels)

_SESS = {}
_LOCK = threading.Lock()


# ------------------------------------------------------------------ loading / saving
def load_full(path):
    """(RGB array at full size, upright, EXIF bytes to keep or None)."""
    if engine.is_raw(path):
        li = engine.load_image(path, max_side=100000)
        return np.ascontiguousarray(li.bgr[:, :, ::-1]), None
    with Image.open(path) as im:
        exif = None
        try:
            ex = im.getexif()
            if ex:
                ex[0x0112] = 1                         # it's saved upright
                exif = ex.tobytes()
        except Exception:
            exif = None
        if getattr(im, "n_frames", 1) > 1:
            im.seek(0)
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")
        return np.ascontiguousarray(np.asarray(im)), exif


def out_path(src, label, png=False):
    folder = os.path.dirname(src)
    stem, ext = os.path.splitext(os.path.basename(src))
    ext = ".png" if png or ext.lower() == ".png" else ".jpg"
    out = os.path.join(folder, f"{stem} - {label}{ext}")
    n = 2
    while os.path.exists(out):
        out = os.path.join(folder, f"{stem} - {label} ({n}){ext}")
        n += 1
    return out


def save(rgb, path, exif=None):
    im = Image.fromarray(rgb)
    kw = {"exif": exif} if exif else {}
    if path.lower().endswith(".png"):
        im.save(path, "PNG", compress_level=6, **kw)      # lossless
    else:
        im.save(path, "JPEG", quality=95, subsampling=0, **kw)
    return path


def _run(path, feeds):
    """Run a model - on the graphics card when there is one; if that fails, on the processor."""
    try:
        return _session(path).run(None, feeds)
    except Exception:
        if _SESS.get(("cpu", path)) is None and _SESS.get(path) is not None and \
                _SESS[path].get_providers()[0] == "CPUExecutionProvider":
            raise
        return _session(path, cpu=True).run(None, feeds)


def _session(path, cpu=False):
    import onnxruntime as ort
    if cpu:
        with _LOCK:
            s = _SESS.get(("cpu", path))
            if s is None:
                so = ort.SessionOptions()
                so.log_severity_level = 3
                s = _SESS[("cpu", path)] = ort.InferenceSession(path, so, providers=["CPUExecutionProvider"])
                _SESS[path] = s
            return s
    with _LOCK:
        s = _SESS.get(path)
        if s is None:
            so = ort.SessionOptions()
            so.log_severity_level = 3
            avail = ort.get_available_providers()
            provs = [p for p in ("DmlExecutionProvider", "CUDAExecutionProvider") if p in avail] + ["CPUExecutionProvider"]
            if "DmlExecutionProvider" in provs:
                so.enable_mem_pattern = False
                so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            try:
                s = ort.InferenceSession(path, so, providers=provs)
            except Exception:
                s = ort.InferenceSession(path, so, providers=["CPUExecutionProvider"])
            _SESS[path] = s
        return s


# ------------------------------------------------------------------ red eye
def _eye_spots(rgb, hints=()):
    """[(x, y, r)] eye centres with a search radius, from the face detector's eye points; the
    face boxes already in the library (0-1 coordinates) fill in faces it misses."""
    h, w = rgb.shape[:2]
    spots = []
    try:
        k = min(1.0, 1600 / max(w, h))
        small = cv2.resize(rgb, (max(1, round(w * k)), max(1, round(h * k))), interpolation=cv2.INTER_AREA) if k < 1 else rgb
        det = cv2.FaceDetectorYN.create(engine.DET_MODEL, "", (small.shape[1], small.shape[0]), 0.6, 0.3, 5000)
        _, faces = det.detect(np.ascontiguousarray(small[:, :, ::-1]))
        for f in ([] if faces is None else faces):
            f = np.asarray(f, np.float32)
            (rx, ry), (lx, ly) = f[4:6] / k, f[6:8] / k
            d = float(np.hypot(lx - rx, ly - ry))
            r = max(4.0, d * 0.28)
            spots += [(rx, ry, r), (lx, ly, r)]
    except Exception:
        pass
    for bx, by, bw, bh in hints:
        x, y, ww, hh = bx * w, by * h, bw * w, bh * h
        if any(x <= sx <= x + ww and y <= sy <= y + hh for sx, sy, _r in spots):
            continue
        r = max(4.0, ww * 0.16)
        spots += [(x + ww * 0.32, y + hh * 0.40, r), (x + ww * 0.68, y + hh * 0.40, r)]
    return spots


def red_eye(rgb, hints=()):
    """(fixed RGB, eyes fixed). Red pupils near each eye are found by colour (much redder than
    green / blue, a compact blob near the eye centre) and turned dark neutral, with soft edges."""
    out = rgb.copy()
    fixed = 0
    H, W = rgb.shape[:2]
    for cx, cy, r in _eye_spots(rgb, hints):
        R = int(r * 1.6) + 2
        x0, y0, x1, y1 = max(0, int(cx - R)), max(0, int(cy - R)), min(W, int(cx + R)), min(H, int(cy + R))
        if x1 - x0 < 4 or y1 - y0 < 4:
            continue
        crop = out[y0:y1, x0:x1].astype(np.float32)
        rr, gg, bb = crop[:, :, 0], crop[:, :, 1], crop[:, :, 2]
        gb = (gg + bb) / 2
        redness = rr - np.maximum(gg, bb)
        # red-eye red is far redder than skin or lips (skin: red ≈ 1.5 × green/blue; red eye: 3 × or more)
        mask = ((redness > 50) & (rr > 70) & (rr > gb * 2.2)).astype(np.uint8)
        if mask.sum() < 4:
            continue
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        n, lab, stats, cents = cv2.connectedComponentsWithStats(mask, 8)
        best, bd = 0, None
        for i in range(1, n):
            dx, dy = cents[i][0] + x0 - cx, cents[i][1] + y0 - cy
            dist = float(np.hypot(dx, dy))
            area = stats[i, cv2.CC_STAT_AREA]
            if dist > r * 0.6 or area < 4 or area > np.pi * (r * 0.8) ** 2:
                continue                                  # a pupil sits in the middle of the eye, and is small
            bw, bh = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
            if max(bw, bh) > 2.2 * max(1, min(bw, bh)) or area < 0.4 * bw * bh:
                continue                                  # a streak or a ragged patch (skin, lids), not a pupil
            if bd is None or dist < bd:
                best, bd = i, dist
        if not best:
            continue
        m = (lab == best).astype(np.uint8) * 255
        # fill holes (the catch-light) and grow a hair so the red rim goes too
        cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(m, cnts, -1, 255, -1)
        m = cv2.dilate(m, np.ones((3, 3), np.uint8))
        soft = cv2.GaussianBlur(m.astype(np.float32) / 255, (0, 0), max(0.8, r * 0.08))[:, :, None]
        dark = np.minimum(gb, rr)
        fix = np.dstack([dark * 0.9, gg * 0.9, bb * 0.9])
        # keep the catch-light bright: only pull down what was red
        red_w = np.clip((redness - 10) / 40, 0, 1)[:, :, None]
        wgt = soft * np.maximum(red_w, (m[:, :, None] > 0) * 0.6)
        crop = crop * (1 - wgt) + fix * wgt
        out[y0:y1, x0:x1] = np.clip(crop + 0.5, 0, 255).astype(np.uint8)
        fixed += 1
    return out, fixed


# ------------------------------------------------------------------ upscaling
def upscale_choices(w, h):
    """[(factor, new w, new h, allowed)] for x2 / x4 / x8."""
    return [(f, w * f, h * f, w * f * h * f <= MAX_OUT_MP * 1e6) for f in (2, 4, 8)]


def _x4(rgb, progress=lambda f: None, cancelled=lambda: False, tile=192, pad=12):
    """Real-ESRGAN x4 in tiles (so big photos fit in memory)."""
    h, w = rgb.shape[:2]
    out = np.zeros((h * 4, w * 4, 3), np.uint8)
    x = rgb.astype(np.float32) / 255.0
    tiles = [(ty, tx) for ty in range(0, h, tile) for tx in range(0, w, tile)]
    for n, (ty, tx) in enumerate(tiles):
        if cancelled():
            raise RuntimeError("cancelled")
        y0, x0 = max(0, ty - pad), max(0, tx - pad)
        y1, x1 = min(h, ty + tile + pad), min(w, tx + tile + pad)
        blob = np.ascontiguousarray(x[y0:y1, x0:x1].transpose(2, 0, 1)[None])
        y = _run(UPSCALE_MODEL, {"input": blob})[0][0].transpose(1, 2, 0)
        oy, ox = (ty - y0) * 4, (tx - x0) * 4
        th, tw = min(tile, h - ty) * 4, min(tile, w - tx) * 4
        out[ty * 4:ty * 4 + th, tx * 4:tx * 4 + tw] = np.clip(y[oy:oy + th, ox:ox + tw] * 255 + 0.5, 0, 255).astype(np.uint8)
        progress((n + 1) / len(tiles))
    return out


def upscale(rgb, factor, progress=lambda f: None, cancelled=lambda: False):
    """x2 (made at x4, then halved - the cleanest x2), x4, or x8 (x4, then a fine x2 resize)."""
    h, w = rgb.shape[:2]
    if w * factor * h * factor > MAX_OUT_MP * 1e6:
        raise ValueError(f"the result would be over {MAX_OUT_MP} megapixels")
    big = _x4(rgb, progress, cancelled)
    if factor == 2:
        return cv2.resize(big, (w * 2, h * 2), interpolation=cv2.INTER_AREA)
    if factor == 8:
        big = cv2.resize(big, (w * 8, h * 8), interpolation=cv2.INTER_LANCZOS4)
        blur = cv2.GaussianBlur(big, (0, 0), 1.2)
        big = cv2.addWeighted(big, 1.35, blur, -0.35, 0)
    return big


# ------------------------------------------------------------------ enhancing
def _denoise(rgb, strength=1.0):
    Y = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    sigma = engine.noise_sigma(Y)
    if sigma < 1.5:
        return rgb, sigma
    hl = float(min(12, sigma * 0.75 * strength))
    out = cv2.fastNlMeansDenoisingColored(rgb, None, hl * 0.8, hl * 1.1, 5, 17)
    return out, sigma


def _levels(rgb, lo=0.4, hi=99.6, per_channel=False, amount=1.0):
    f = rgb.astype(np.float32)
    if per_channel:
        out = np.empty_like(f)
        for c in range(3):
            a, b = np.percentile(f[:, :, c], (lo, hi))
            out[:, :, c] = (f[:, :, c] - a) * (255.0 / max(8.0, b - a))
    else:
        Y = f @ np.array([0.299, 0.587, 0.114], np.float32)
        a, b = np.percentile(Y, (lo, hi))
        out = (f - a) * (255.0 / max(8.0, b - a))
    out = f * (1 - amount) + out * amount
    return np.clip(out, 0, 255).astype(np.uint8)


def _white_balance(rgb, amount=0.5):
    f = rgb.astype(np.float32)
    Y = f.mean(axis=2)
    mid = (Y > 30) & (Y < 225)
    if mid.sum() < 100:
        return rgb
    means = f[mid].mean(axis=0)
    gain = means.mean() / np.maximum(means, 1)
    gain = 1 + (np.clip(gain, 0.8, 1.25) - 1) * amount
    return np.clip(f * gain, 0, 255).astype(np.uint8)


def _local_contrast(rgb, clip=1.6, amount=0.6):
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    L = lab[:, :, 0]
    tiles = max(4, min(16, max(rgb.shape[:2]) // 300))
    eq = cv2.createCLAHE(clipLimit=clip, tileGridSize=(tiles, tiles)).apply(L)
    lab[:, :, 0] = np.clip(L * (1 - amount) + eq * amount, 0, 255).astype(np.uint8)
    return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)


def _vibrance(rgb, amount=0.18):
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV).astype(np.float32)
    s = hsv[:, :, 1] / 255.0
    hsv[:, :, 1] = np.clip((s + amount * (1 - s) * s * 2.0) * 255, 0, 255)       # dull colours lifted most
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB)


def _saturate(rgb, k):
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    lab[:, :, 1:] = np.clip((lab[:, :, 1:] - 128) * k + 128, 0, 255)
    return cv2.cvtColor(lab.astype(np.uint8), cv2.COLOR_LAB2RGB)


def _sharpen(rgb, amount=0.45, radius=1.2):
    blur = cv2.GaussianBlur(rgb, (0, 0), radius)
    return cv2.addWeighted(rgb, 1 + amount, blur, -amount, 0)


def auto_enhance(rgb, progress=lambda f: None, cancelled=lambda: False):
    """A light, all-round touch-up: noise smoothed (only as much as the photo has), a gentle
    white balance, levels, local contrast, livelier colour, and a little sharpening."""
    out, sigma = _denoise(rgb, 0.8)
    progress(0.4)
    out = _white_balance(out, 0.45)
    out = _levels(out, 0.3, 99.7, amount=0.7)
    progress(0.6)
    out = _local_contrast(out, 1.5, 0.5)
    out = _vibrance(out, 0.15)
    progress(0.85)
    out = _sharpen(out, 0.35 if sigma < 4 else 0.2)
    progress(1.0)
    return out


def is_monochrome(rgb):
    """True for black & white and sepia / toned photos (one hue at most)."""
    small = cv2.resize(rgb, (256, max(1, 256 * rgb.shape[0] // max(1, rgb.shape[1]))), interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(small, cv2.COLOR_RGB2LAB).astype(np.float32).reshape(-1, 3)
    L, a, b = lab[:, 0], lab[:, 1] - 128, lab[:, 2] - 128
    if float(np.std(a)) < 4.5 and float(np.std(b)) < 6.5:
        return True
    # a toned print (sepia, cyanotype…): its colour follows the brightness - what's left over is tiny
    X = np.column_stack([L, L * L / 255.0, np.ones_like(L)])
    left = 0.0
    for ch in (a, b):
        coef, *_ = np.linalg.lstsq(X, ch, rcond=None)
        left = max(left, float(np.std(ch - X @ coef)))
    return left < 3.5


def _dust(rgb):
    """Specks of dust and fine scratches (small spots much lighter or darker than around them)
    filled in from their surroundings."""
    g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    s = max(3, (min(g.shape) // 400) * 2 + 3)
    med = cv2.medianBlur(g, s if s % 2 else s + 1)
    diff = cv2.absdiff(g, med)
    thr = max(28.0, float(np.percentile(diff, 99.7)))
    mask = (diff > thr).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    big = max(6, (min(g.shape) // 200) ** 2 * 4)
    keep = np.zeros(n, bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] <= big
    m = keep[lab].astype(np.uint8) * 255
    if not m.any():
        return rgb
    m = cv2.dilate(m, np.ones((3, 3), np.uint8))
    return cv2.inpaint(rgb, m, 3, cv2.INPAINT_TELEA)


def restore_faded(rgb, progress=lambda f: None, cancelled=lambda: False):
    """Old, faded photos: dust and specks taken out, the fade and colour cast removed (each colour
    stretched back to full range), film grain smoothed, contrast and colour brought back."""
    mono = is_monochrome(rgb)
    out = _dust(rgb)
    progress(0.25)
    out = _levels(out, 0.5, 99.5, per_channel=not mono, amount=1.0)
    progress(0.4)
    out, _sigma = _denoise(out, 1.0)
    progress(0.7)
    out = _local_contrast(out, 2.0, 0.6)
    if not mono:
        out = _saturate(out, 1.25)
    out = _sharpen(out, 0.3)
    progress(1.0)
    return out


# ------------------------------------------------------------------ colourizing
def color_model_path():
    return os.path.join(engine.store_data_dir(), "models", "colorize", "deoldify.onnx")


def color_ready():
    return os.path.exists(color_model_path())


def download_color(progress=lambda d, t, m: None, cancelled=lambda: False):
    """The colourizing model (DeOldify, about 243 MB, once). False if cancelled."""
    import urllib.request
    dest = color_model_path()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest):
        return True
    tmp = dest + ".part"
    req = urllib.request.Request(COLOR_URL, headers={"User-Agent": "FaceSort"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        got = 0
        while True:
            if cancelled():
                f.close()
                os.remove(tmp)
                return False
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            progress(got >> 20, COLOR_MB, f"Getting the colourizing model… {got >> 20} / {COLOR_MB} MB")
    if os.path.getsize(tmp) < 50 << 20:
        os.remove(tmp)
        raise RuntimeError("the download was incomplete")
    os.replace(tmp, dest)
    return True


def colorize(rgb, progress=lambda f: None, cancelled=lambda: False, vivid=1.25):
    """Black & white or sepia -> colour. The model guesses colours at 256 x 256; only its colour
    is used - every bit of detail (the brightness) comes from the original at full size."""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    a, b = np.percentile(gray, (0.3, 99.7))           # faded prints: full range back first
    if b - a > 8 and (a > 6 or b < 249):
        gray = np.clip((gray.astype(np.float32) - a) * (255.0 / (b - a)) * 0.85 + gray * 0.15, 0, 255).astype(np.uint8)
    g3 = np.dstack([gray] * 3)
    progress(0.2)
    x = cv2.resize(g3, (256, 256), interpolation=cv2.INTER_AREA).astype(np.float32).transpose(2, 0, 1)[None]
    name = _session(color_model_path()).get_inputs()[0].name
    y = _run(color_model_path(), {name: np.ascontiguousarray(x)})[0][0].transpose(1, 2, 0)
    if y.max() <= 2.0:
        y = y * 255.0
    y = np.clip(y, 0, 255).astype(np.uint8)
    progress(0.7)
    h, w = gray.shape
    col = cv2.resize(y, (w, h), interpolation=cv2.INTER_CUBIC)
    col = cv2.GaussianBlur(col, (0, 0), max(1.0, max(w, h) / 512))       # colour has no fine detail
    lab = cv2.cvtColor(col, cv2.COLOR_RGB2LAB).astype(np.float32)
    lab[:, :, 1:] = np.clip((lab[:, :, 1:] - 128) * vivid + 128, 0, 255)
    lab[:, :, 0] = cv2.cvtColor(g3, cv2.COLOR_RGB2LAB)[:, :, 0]
    out = cv2.cvtColor(lab.astype(np.uint8), cv2.COLOR_LAB2RGB)
    progress(1.0)
    return out


# ------------------------------------------------------------------ dark blotches
def _guided(I, p, r, eps):
    """Guided filter: smooths p but keeps the edges that are in I."""
    k = (2 * r + 1, 2 * r + 1)
    mI, mp = cv2.boxFilter(I, -1, k), cv2.boxFilter(p, -1, k)
    a = (cv2.boxFilter(I * p, -1, k) - mI * mp) / (cv2.boxFilter(I * I, -1, k) - mI * mI + eps)
    b = mp - a * mI
    return cv2.boxFilter(a, -1, k) * I + cv2.boxFilter(b, -1, k)


def smooth_shadows(rgb, lift=False, progress=lambda f: None, cancelled=lambda: False):
    """Dark areas that look blotchy: a dark JPEG keeps only a handful of brightness steps in its
    shadows, and squeezes them into 8 x 8 blocks - so they turn into patches and rings. The
    shadows are rebuilt as smooth gradients (averaged at a quarter size, which brings back the
    in-between shades, then smoothed without crossing edges), only where it's dark and away from
    any edge - lit skin, texture and outlines are left exactly as they were. A fine grain is
    added so saving doesn't bring the steps back. lift: also brighten the shadows a little to
    show what's in them (black stays black)."""
    f = rgb.astype(np.float32) / 255
    h, w = f.shape[:2]
    lum = np.array([0.299, 0.587, 0.114], np.float32)
    Y = f @ lum
    # work at 1/4 size
    k = 4
    sw, sh = max(1, w // k), max(1, h // k)
    small = cv2.resize(f, (sw, sh), interpolation=cv2.INTER_AREA)
    Ys = cv2.resize(Y, (sw, sh), interpolation=cv2.INTER_AREA)
    progress(0.2)
    # averaged only over the dark pixels (normalised blur): a lit outline next to the shadow
    # can't bleed into it, so there are no halos - and the blotch steps melt into a gradient
    ws = np.clip((0.25 - Ys) / 0.08, 0, 1) + 1e-4
    den = cv2.GaussianBlur(ws, (0, 0), 2.5)
    sm = np.dstack([cv2.GaussianBlur(small[:, :, c] * ws, (0, 0), 2.5) / den for c in range(3)])
    sm = cv2.resize(sm, (w, h), interpolation=cv2.INTER_LINEAR)
    sm = cv2.GaussianBlur(sm, (0, 0), k * 0.5)
    progress(0.55)
    # where: the shadows, fading out by about 22 % brightness
    m = np.clip((0.22 - cv2.GaussianBlur(Y, (0, 0), 3)) / 0.12, 0, 1)
    # never where the smoothing would change a pixel by more than a few shades: that's an
    # edge (a lit outline) being smeared, not a blotch being evened out - so no halos
    diff = np.abs((sm - f) @ lum)
    m = m * np.clip((5.0 / 255 - cv2.GaussianBlur(diff, (0, 0), 2.0)) / (2.5 / 255), 0, 1)
    # and only where it's flat - the steps and blocks. Fine grain or skin texture in the
    # shadows (it varies from pixel to pixel) is real detail: left alone.
    hi = Y - cv2.GaussianBlur(Y, (0, 0), 2.0)
    grain = np.sqrt(cv2.GaussianBlur(hi * hi, (0, 0), 3.0))
    m = m * np.clip((4.0 / 255 - grain) / (2.5 / 255), 0, 1)
    m = cv2.GaussianBlur(m, (0, 0), 3.0)[:, :, None]          # no hard seams between the two
    out = f * (1 - m) + sm * m
    progress(0.8)
    if lift:
        # brighten the shadows a little - measured from the photo's own black (its darkest
        # shade), so a black background stays exactly as black as it was
        floor = float(np.percentile(Y, 0.5))
        yo = out @ lum
        x = np.clip((yo - floor) / max(1e-3, 1 - floor), 0, 1)
        ly = yo + 0.10 * (1 - x) ** 6 * np.clip((x - 0.004) / 0.03, 0, 1) ** 1.5
        out = out * (ly / np.maximum(yo, 1e-4))[:, :, None]
    rng = np.random.default_rng(1)
    out = out * 255 + (rng.random(out.shape, dtype=np.float32) - rng.random(out.shape, dtype=np.float32))
    progress(1.0)
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


# ---- touch-up brush: fill a painted area from what's around it
HEAL_MAX_AREA = 50000      # painted pixels filled at full detail; bigger areas are filled smaller, then scaled up


def _patch_fill(bgr, hole, half, cancelled=lambda: False):
    """Exemplar fill (Criminisi et al.): the hole is filled from its edge inwards, each time copying
    the best-matching piece of the picture around it, edges and lines first - so texture and lines
    continue instead of being smeared."""
    img = bgr.astype(np.float32)
    known = (hole == 0)
    orig = known.astype(np.uint8)          # pieces are copied only from the photo as it was, never from
                                           # what was just filled (that's how mistakes would spread)
    conf = known.astype(np.float32)
    h, w = hole.shape
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gx, gy = cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1)
    grad = cv2.magnitude(gx, gy)
    grad /= grad.max() or 1
    kern = np.ones((2 * half + 1, 2 * half + 1), np.uint8)
    reach = max(40, 6 * half)
    left = int((~known).sum())
    guard = left + 50
    while left > 0 and guard > 0:
        guard -= 1
        if cancelled():
            raise RuntimeError("cancelled")
        k8 = known.astype(np.uint8)
        front = (cv2.dilate(k8, np.ones((3, 3), np.uint8)) > 0) & ~known
        fy, fx = np.nonzero(front)
        if not len(fy):
            break
        # priority: how much is already known around the point, times how strong an edge leads into it
        cavg = cv2.blur(conf, (2 * half + 1, 2 * half + 1))
        g = cv2.dilate(grad * known, np.ones((3, 3), np.uint8))
        pri = cavg[fy, fx] * (g[fy, fx] + 0.05)
        j = int(np.argmax(pri))
        cy, cx = int(fy[j]), int(fx[j])
        ty0, ty1 = max(0, cy - half), min(h, cy + half + 1)
        tx0, tx1 = max(0, cx - half), min(w, cx + half + 1)
        th, tw = ty1 - ty0, tx1 - tx0
        tk = known[ty0:ty1, tx0:tx1]
        # where to look: a window around the point, only at pieces that are wholly known
        sy0, sy1 = max(0, cy - reach), min(h, cy + reach + 1)
        sx0, sx1 = max(0, cx - reach), min(w, cx + reach + 1)
        if sy1 - sy0 < th or sx1 - sx0 < tw:
            break
        tmpl = img[ty0:ty1, tx0:tx1]
        m3 = np.repeat(tk[..., None], 3, 2).astype(np.float32)
        if tk.any():
            res = cv2.matchTemplate(img[sy0:sy1, sx0:sx1], tmpl, cv2.TM_SQDIFF, mask=m3)
        else:
            res = np.zeros((sy1 - sy0 - th + 1, sx1 - sx0 - tw + 1), np.float32)
        whole = cv2.erode(orig[sy0:sy1, sx0:sx1], np.ones((th, tw), np.uint8),
                          anchor=(0, 0), borderType=cv2.BORDER_CONSTANT, borderValue=0)
        ok = whole[:res.shape[0], :res.shape[1]] > 0
        if not ok.any():
            reach *= 2                                   # nothing whole nearby: look further
            if reach > max(h, w):
                break
            continue
        res = np.where(ok, np.nan_to_num(res, nan=1e30, posinf=1e30), np.float32(1e30))
        by, bx = np.unravel_index(int(np.argmin(res)), res.shape)
        src = img[sy0 + by:sy0 + by + th, sx0 + bx:sx0 + bx + tw]
        fillm = ~tk
        img[ty0:ty1, tx0:tx1][fillm] = src[fillm]
        conf[ty0:ty1, tx0:tx1][fillm] = cavg[cy, cx]
        gsrc = grad[sy0 + by:sy0 + by + th, sx0 + bx:sx0 + bx + tw]
        grad[ty0:ty1, tx0:tx1][fillm] = gsrc[fillm]
        known[ty0:ty1, tx0:tx1] = True
        left -= int(fillm.sum())
    if left > 0:                                         # (anything left: smooth fill)
        img = cv2.inpaint(np.clip(img, 0, 255).astype(np.uint8), (~known).astype(np.uint8), 3,
                          cv2.INPAINT_TELEA).astype(np.float32)
    return np.clip(img, 0, 255).astype(np.uint8)


def heal(rgb, mask, cancelled=lambda: False):
    """rgb with the painted area (mask > 0) filled in from its surroundings: thin marks (dust, a
    scratch, a date stamp) are blended away; wider areas get texture copied from nearby, so grass
    stays grass and a wall stays a wall."""
    m = (mask > 0).astype(np.uint8)
    if not m.any():
        return rgb
    h, w = m.shape
    ys, xs = np.nonzero(m)
    span = max(np.ptp(ys), np.ptp(xs))
    pad = 32 + int(0.6 * span)
    y0, y1 = max(0, ys.min() - pad), min(h, ys.max() + pad + 1)
    x0, x1 = max(0, xs.min() - pad), min(w, xs.max() + pad + 1)
    crop = np.ascontiguousarray(rgb[y0:y1, x0:x1])
    cm = cv2.dilate(m[y0:y1, x0:x1], np.ones((3, 3), np.uint8))      # cover the soft edge of what's removed
    bgr = cv2.cvtColor(crop, cv2.COLOR_RGB2BGR)
    thick = float(cv2.distanceTransform(cm, cv2.DIST_L2, 3).max())    # half the width of the widest stroke
    if thick <= 3:
        out = cv2.inpaint(bgr, cm, 3, cv2.INPAINT_TELEA)
    else:
        area = int(cm.sum())
        k = min(1.0, (HEAL_MAX_AREA / area) ** 0.5)
        if k < 1:
            sw, sh = max(16, round(bgr.shape[1] * k)), max(16, round(bgr.shape[0] * k))
            small = cv2.resize(bgr, (sw, sh), interpolation=cv2.INTER_AREA)
            sm = (cv2.resize(cm.astype(np.float32), (sw, sh), interpolation=cv2.INTER_AREA) > 0.01).astype(np.uint8)
            fill = _patch_fill(small, sm, 4, cancelled)
            # soften the seams between the copied pieces (their grain is put back below)
            fill = cv2.bilateralFilter(fill, 5, 18, 3)
            fill = cv2.resize(fill, (bgr.shape[1], bgr.shape[0]), interpolation=cv2.INTER_CUBIC)
            fill = cv2.GaussianBlur(fill, (0, 0), 0.5 / k)
            base = bgr.copy()
            base[cm > 0] = fill[cm > 0]
            # grain at full size, like the area around it (the scaled-up fill is a little soft)
            ring = (cv2.dilate(cm, np.ones((15, 15), np.uint8)) > 0) & (cm == 0)
            detail = bgr.astype(np.float32) - cv2.GaussianBlur(bgr, (0, 0), 1.5).astype(np.float32)
            have = base.astype(np.float32) - cv2.GaussianBlur(base, (0, 0), 1.5).astype(np.float32)
            want = np.median(np.abs(detail[ring]), axis=0) if ring.any() else np.zeros(3)
            got = np.median(np.abs(have[cm > 0]), axis=0)
            extra = np.sqrt(np.maximum(want ** 2 - got ** 2, 0)) * 1.25
            noise = np.random.default_rng(area).normal(0, 1, bgr.shape[:2]).astype(np.float32)
            add = noise[..., None] * extra[None, None, :] * (cm > 0)[..., None]
            out = np.clip(base.astype(np.float32) + add, 0, 255).astype(np.uint8)
        else:
            out = _patch_fill(bgr, cm, max(3, min(5, round(thick / 3))), cancelled)
            # soften the seams between the copied pieces, keeping most of their texture
            smooth = cv2.bilateralFilter(out, 5, 24, 2)
            out = np.where((cm > 0)[..., None], cv2.addWeighted(out, 0.4, smooth, 0.6, 0), out)
    # blend the edge over a couple of pixels so no seam shows
    soft = cv2.GaussianBlur(cv2.dilate(cm, np.ones((3, 3), np.uint8)).astype(np.float32), (0, 0), 1.0)[..., None]
    res = out.astype(np.float32) * soft + bgr.astype(np.float32) * (1 - soft)
    done = rgb.copy()
    done[y0:y1, x0:x1] = cv2.cvtColor(np.clip(res, 0, 255).astype(np.uint8), cv2.COLOR_BGR2RGB)
    return done


# ---- face clarity brush: paint over a soft or blurry face and it's made clearer
CLARITY_URL = "https://github.com/drac0nik/FaceSort/releases/download/models-gfpgan/gfpgan_1.4.onnx"
CLARITY_SHA_URL = CLARITY_URL + ".sha256"     # published with it (.github/workflows/models.yml)
CLARITY_MB = 340
# where the eyes, nose and mouth corners sit in the model's 512 x 512 face (FFHQ alignment)
FFHQ_512 = np.array([[192.98138, 239.94708], [318.90277, 240.1936], [256.63416, 314.01935],
                     [201.26117, 371.41043], [313.08905, 371.15118]], np.float32)


def clarity_model_path():
    return os.path.join(engine.store_data_dir(), "models", "face_clarity", "gfpgan_1.4.onnx")


def clarity_ready():
    return os.path.exists(clarity_model_path())


def download_clarity(progress=lambda d, t, m: None, cancelled=lambda: False):
    """The face clarity model (GFPGAN 1.4, about 340 MB, once), checked against its checksum."""
    import hashlib
    import urllib.request
    dest = clarity_model_path()
    if os.path.exists(dest):
        return True
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    h = hashlib.sha256()
    with urllib.request.urlopen(urllib.request.Request(CLARITY_SHA_URL, headers={"User-Agent": "FaceSort"}),
                                timeout=30) as r:
        want = re.search(r"\b([0-9a-f]{64})\b", r.read().decode("utf-8", "replace"), re.I)
    if not want:
        raise RuntimeError("the model's checksum couldn't be read")
    want = want.group(1).lower()
    req = urllib.request.Request(CLARITY_URL, headers={"User-Agent": "FaceSort"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or CLARITY_MB << 20)
        got = 0
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
            got += len(chunk)
            progress(got >> 20, total >> 20, f"Getting the face clarity model… {got >> 20} / {total >> 20} MB")
    if h.hexdigest() != want:
        os.remove(tmp)
        raise RuntimeError("the downloaded model doesn't match its checksum - not used")
    os.replace(tmp, dest)
    return True


def _faces_in(bgr):
    """YuNet rows (box, 5 landmarks, score) for the faces in a picture."""
    det = cv2.FaceDetectorYN.create(engine.DET_MODEL, "", (320, 320), 0.6, 0.3, 5000)
    h, w = bgr.shape[:2]
    k = min(1.0, 1600 / max(h, w))
    img = cv2.resize(bgr, (round(w * k), round(h * k)), interpolation=cv2.INTER_AREA) if k < 1 else bgr
    found = []
    for side in (None, 640, 320):
        im = img
        s = 1.0
        if side:
            if max(img.shape[:2]) <= side * 1.4:
                continue
            s = side / max(img.shape[:2])
            im = cv2.resize(img, (round(img.shape[1] * s), round(img.shape[0] * s)), interpolation=cv2.INTER_AREA)
        det.setInputSize((im.shape[1], im.shape[0]))
        _, faces = det.detect(im)
        for f in ([] if faces is None else faces):
            f = f.copy()
            f[:14] /= s * k
            box = f[:4]
            if not any(_overlap(box, g[:4]) > 0.4 for g in found):
                found.append(f)
    return found


def _overlap(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    return inter / max(1e-6, min(a[2] * a[3], b[2] * b[3]))


def _clarify(bgr):
    """Without the model (or where no face is found): a careful clean-up - noise out, detail and
    local contrast up."""
    den = cv2.fastNlMeansDenoisingColored(bgr, None, 4, 4, 5, 15)
    blur = cv2.GaussianBlur(den, (0, 0), 1.6)
    sharp = cv2.addWeighted(den, 1.8, blur, -0.8, 0)
    lab = cv2.cvtColor(sharp, cv2.COLOR_BGR2LAB)
    lab[:, :, 0] = cv2.createCLAHE(clipLimit=1.6, tileGridSize=(4, 4)).apply(lab[:, :, 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


def face_clarity(rgb, mask, strength=0.8, cancelled=lambda: False):
    """(rgb with the painted faces made clearer, faces done). Each face under the brush is lined up,
    redrawn sharper by the face model (GFPGAN), put back, and blended in only where you painted.
    The model draws fine detail it judges likely (lashes, skin texture): good for soft, blurry or
    low-resolution faces that are still visible; it can't show what isn't there."""
    m = (mask > 0).astype(np.uint8)
    if not m.any():
        return rgb, 0
    H, W = m.shape
    ys, xs = np.nonzero(m)
    span = max(np.ptp(ys), np.ptp(xs)) + 1
    pad = int(span * 0.8) + 32
    y0, y1 = max(0, ys.min() - pad), min(H, ys.max() + pad + 1)
    x0, x1 = max(0, xs.min() - pad), min(W, xs.max() + pad + 1)
    bgr = cv2.cvtColor(np.ascontiguousarray(rgb[y0:y1, x0:x1]), cv2.COLOR_RGB2BGR)
    cm = m[y0:y1, x0:x1]
    out = bgr.astype(np.float32)
    weight = np.zeros(cm.shape, np.float32)
    done = 0
    if clarity_ready():
        for f in _faces_in(bgr):
            if cancelled():
                raise RuntimeError("cancelled")
            fx, fy, fw, fh = f[:4]
            if cm[max(0, int(fy)):int(fy + fh), max(0, int(fx)):int(fx + fw)].sum() < 0.08 * fw * fh:
                continue                                  # not this face: hardly painted over
            lm = f[4:14].reshape(5, 2).astype(np.float32)
            M, _ = cv2.estimateAffinePartial2D(lm, FFHQ_512, method=cv2.LMEDS)
            if M is None:
                continue
            face = cv2.warpAffine(bgr, M, (512, 512), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101)
            x = cv2.cvtColor(face, cv2.COLOR_BGR2RGB).astype(np.float32) / 127.5 - 1
            y = _run(clarity_model_path(), {"input": x.transpose(2, 0, 1)[None]})[0][0]
            new = cv2.cvtColor(((np.clip(y.transpose(1, 2, 0), -1, 1) + 1) * 127.5).astype(np.uint8), cv2.COLOR_RGB2BGR)
            # back into the photo, with a soft oval edge around the face
            oval = np.zeros((512, 512), np.float32)
            cv2.ellipse(oval, (256, 280), (205, 245), 0, 0, 360, 1.0, -1)
            oval = cv2.GaussianBlur(oval, (0, 0), 18)
            Mi = cv2.invertAffineTransform(M)
            back = cv2.warpAffine(new, Mi, (bgr.shape[1], bgr.shape[0]), flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_REFLECT101)
            wmask = cv2.warpAffine(oval, Mi, (bgr.shape[1], bgr.shape[0]), flags=cv2.INTER_LINEAR)
            out = out * (1 - wmask[..., None]) + back.astype(np.float32) * wmask[..., None]
            weight = np.maximum(weight, wmask)
            done += 1
    # where the brush went but no face was redrawn: careful sharpening instead
    rest = (cm > 0) & (weight < 0.5)
    if rest.any():
        sharp = _clarify(bgr).astype(np.float32)
        rw = cv2.GaussianBlur(rest.astype(np.float32), (0, 0), 2)[..., None]
        out = out * (1 - rw) + sharp * rw
    # only where painted (soft edge), and as strongly as asked
    soft = cv2.GaussianBlur(cv2.dilate(cm, np.ones((5, 5), np.uint8)).astype(np.float32), (0, 0),
                            max(2.0, span / 60))[..., None] * float(strength)
    res = out * soft + bgr.astype(np.float32) * (1 - soft)
    result = rgb.copy()
    result[y0:y1, x0:x1] = cv2.cvtColor(np.clip(res, 0, 255).astype(np.uint8), cv2.COLOR_BGR2RGB)
    return result, done
