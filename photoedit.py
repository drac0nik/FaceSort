"""Edit mode's picture math (1.54): light, color, white balance, color pop, vignette, detail, and
crop / straighten / flip / turn. Everything works on RGB uint8 arrays and is the same on the small
preview and on the full-size picture that's saved (sizes in the settings are relative).

Settings (all 0 = no change):
  exposure, brightness, contrast, highlights, shadows        -100..100
  temp, tint (white balance), saturation, vibrance           -100..100
  bw                                                         True = black & white
  pop (color pop: one color kept, the rest grey)             None or {"hue": 0-360, "sens": 0-100}
  vignette    -100 (completely black) .. 0 .. 100 (completely white)
  sharpen, noise                                             0..100
  turn (quarter turns clockwise), flip_h, flip_v, straighten (degrees, -45..45),
  crop: None or (x, y, w, h) as fractions of the turned / straightened picture"""
import math

import cv2
import numpy as np

DEFAULTS = {"exposure": 0, "brightness": 0, "contrast": 0, "highlights": 0, "shadows": 0, "temp": 0, "tint": 0,
            "saturation": 0, "vibrance": 0, "bw": False, "pop": None, "vignette": 0, "sharpen": 0, "noise": 0,
            "turn": 0, "flip_h": False, "flip_v": False, "straighten": 0.0, "crop": None}
SWATCHES = [("Reds", 0), ("Oranges", 30), ("Yellows", 55), ("Greens", 120), ("Cyans", 185), ("Blues", 220),
            ("Purples / pinks", 290)]
GEOMETRY = ("turn", "flip_h", "flip_v", "straighten", "crop")


def defaults():
    return dict(DEFAULTS)


def changed(p):
    """The settings that differ from 'no change'."""
    return {k: v for k, v in p.items() if k in DEFAULTS and v != DEFAULTS[k]}


def geometry_changed(p):
    return any(p.get(k) != DEFAULTS[k] for k in GEOMETRY)


def kelvin(temp):
    """The white balance slider as a color temperature to show (about 2500 K .. 10000 K)."""
    return int(round(6500 * (2 ** (-temp / 100 * 1.35)) / 50) * 50)


# ------------------------------------------------------------------ geometry
def geometry(rgb, p, crop=True):
    out = rgb
    t = int(p.get("turn", 0)) % 4
    if t:
        out = np.ascontiguousarray(np.rot90(out, -t))
    if p.get("flip_h"):
        out = out[:, ::-1]
    if p.get("flip_v"):
        out = out[::-1]
    a = float(p.get("straighten") or 0)
    if abs(a) > 0.01:
        h, w = out.shape[:2]
        # turned, then enlarged just enough that no empty corners show
        r = math.radians(abs(a))
        scale = (math.cos(r) + math.sin(r) * max(w / h, h / w))
        m = cv2.getRotationMatrix2D((w / 2, h / 2), -a, scale)
        out = cv2.warpAffine(np.ascontiguousarray(out), m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
    c = p.get("crop")
    if crop and c:
        h, w = out.shape[:2]
        x, y, cw, ch = c
        x0, y0 = int(round(max(0, x) * w)), int(round(max(0, y) * h))
        x1, y1 = int(round(min(1, x + cw) * w)), int(round(min(1, y + ch) * h))
        if x1 - x0 >= 2 and y1 - y0 >= 2:
            out = out[y0:y1, x0:x1]
    return np.ascontiguousarray(out)


def aspect_crop(w, h, ratio):
    """The biggest centered crop with this width / height ratio, as fractions (None = no crop)."""
    if not ratio:
        return None
    if w / h > ratio:
        cw = h * ratio / w
        return ((1 - cw) / 2, 0.0, cw, 1.0)
    ch = w / ratio / h
    return (0.0, (1 - ch) / 2, 1.0, ch)


def fit_aspect(box, w, h, ratio):
    """A drawn crop box (fractions) made to the ratio, keeping its middle."""
    if not ratio:
        return box
    x, y, bw, bh = box
    pw, ph = bw * w, bh * h
    if pw / ph > ratio:
        pw = ph * ratio
    else:
        ph = pw / ratio
    cx, cy = (x + bw / 2) * w, (y + bh / 2) * h
    nx, ny = max(0, min(w - pw, cx - pw / 2)), max(0, min(h - ph, cy - ph / 2))
    return (nx / w, ny / h, pw / w, ph / h)


# ------------------------------------------------------------------ tone and color
def _lum(f):
    return f[..., 0] * 0.2126 + f[..., 1] * 0.7152 + f[..., 2] * 0.0722


def wb_from_sample(rgb):
    """temp, tint that turn this sampled color (RGB 0-255) neutral grey."""
    r, g, b = [max(1.0, float(v)) for v in rgb]
    # gains: r * (1 + k*t), b * (1 - k*t), g * (1 - k2*tint) relative
    k, k2 = 0.006, 0.006
    ratio = b / r                                  # want r*(1+kt) == b*(1-kt)
    t = (ratio - 1) / (k * (ratio + 1))
    rb = r * (1 + k * t)
    tint = (1 - rb / g) / k2
    return max(-100, min(100, round(t))), max(-100, min(100, round(tint)))


def _gains(temp, tint):
    k, k2 = 0.006, 0.006
    gr, gb, gg = 1 + k * temp, 1 - k * temp, 1 - k2 * tint
    m = (gr + gg + gb) / 3
    return np.array([gr / m, gg / m, gb / m], np.float32)


def _hue_mask(f, hue, sens):
    """How much each pixel is the kept color (0..1)."""
    hsv = cv2.cvtColor(np.clip(f, 0, 1).astype(np.float32), cv2.COLOR_RGB2HSV)       # H 0..360, S 0..1
    d = np.abs(hsv[..., 0] - hue)
    d = np.minimum(d, 360 - d)
    tol = 8 + sens / 100 * 72                      # 8° .. 80° either side
    soft = 6 + sens / 100 * 24
    m = np.clip((tol + soft - d) / soft, 0, 1)
    sat_floor = 0.18 - sens / 100 * 0.13           # nearly grey pixels count as grey
    m *= np.clip((hsv[..., 1] - sat_floor) / 0.08, 0, 1)
    return m


def tone_color(rgb, p):
    """Light, white balance, color, color pop and vignette."""
    keys = ("exposure", "brightness", "contrast", "highlights", "shadows", "temp", "tint", "saturation", "vibrance",
            "bw", "pop", "vignette")
    if not any(p.get(k) for k in keys):
        return rgb
    f = rgb.astype(np.float32) / 255.0
    if p.get("temp") or p.get("tint"):
        f *= _gains(p.get("temp", 0), p.get("tint", 0))
    e = p.get("exposure", 0)
    if e:
        f *= 2 ** (e / 50.0)                        # ±2 stops
    b = p.get("brightness", 0)
    if b:
        g = 2 ** (-b / 100.0)                       # midtones lifted / lowered, black and white stay
        f = np.power(np.clip(f, 0, None), g)
    hl, sh = p.get("highlights", 0), p.get("shadows", 0)
    if hl or sh:
        L = np.clip(_lum(f), 0, 1)[..., None]
        if hl:
            w = np.clip((L - 0.45) / 0.55, 0, 1) ** 1.5
            f = f + (hl / 100.0) * 0.45 * w * (1 - f if hl > 0 else f)
        if sh:
            w = np.clip((0.55 - L) / 0.55, 0, 1) ** 1.5
            f = f + (sh / 100.0) * 0.45 * w * ((1 - f) if sh > 0 else f)
    c = p.get("contrast", 0)
    if c:
        k = 1 + c / 100.0
        f = (f - 0.5) * k + 0.5
    f = np.clip(f, 0, 1)
    s, v = p.get("saturation", 0), p.get("vibrance", 0)
    if p.get("bw"):
        L = _lum(f)[..., None]
        f = np.repeat(L, 3, axis=2)
    elif s or v:
        L = _lum(f)[..., None]
        k = 1 + s / 100.0
        if v:
            mx, mn = f.max(axis=2, keepdims=True), f.min(axis=2, keepdims=True)
            sat = (mx - mn) / np.maximum(mx, 1e-4)
            k = k * (1 + (v / 100.0) * (1 - sat) * (1.0 if v > 0 else 0.6))
        f = np.clip(L + (f - L) * k, 0, 1)
    pop = p.get("pop")
    if pop and not p.get("bw"):
        m = _hue_mask(f, float(pop.get("hue", 0)), float(pop.get("sens", 45)))[..., None]
        L = _lum(f)[..., None]
        f = L + (f - L) * m
    vg = p.get("vignette", 0)
    if vg:
        f = vignette(f, vg)
    return (np.clip(f, 0, 1) * 255 + 0.5).astype(np.uint8)


def vignette(f, amount):
    """amount -100 (all black) .. 100 (all white); the corners go first."""
    h, w = f.shape[:2]
    a = min(1.0, abs(amount) / 100.0)
    yy, xx = np.ogrid[:h, :w]
    nx, ny = (xx - w / 2) / (w / 2), (yy - h / 2) / (h / 2)
    r = np.sqrt(nx * nx + ny * ny) / math.sqrt(2)          # 0 middle .. 1 corners
    inner = 0.55 * (1 - a) ** 1.2 - 0.02 * a
    feather = 0.75 * (1 - a) + 1e-3
    m = np.clip((r - inner) / feather, 0, 1)
    m = (m * m * (3 - 2 * m)) * min(1.0, a * 1.6)          # smooth; at ±100 the whole picture
    m = m.astype(np.float32)[..., None]
    target = 0.0 if amount < 0 else 1.0
    return f * (1 - m) + target * m


def detail(rgb, p, scale=1.0):
    """Noise smoothing, then sharpening (radius follows the picture's size)."""
    out = rgb
    n = p.get("noise", 0)
    if n:
        d = max(3, int(round(5 * scale)) | 1)
        sm = cv2.bilateralFilter(out, d, 10 + n * 0.5, 3 + n * 0.08 * scale)
        a = n / 100.0
        out = cv2.addWeighted(out, 1 - a, sm, a, 0)
    s = p.get("sharpen", 0)
    if s:
        blur = cv2.GaussianBlur(out, (0, 0), max(0.6, 1.2 * scale))
        out = cv2.addWeighted(out, 1 + s / 50.0, blur, -s / 50.0, 0)
    return out


def render(rgb, p, crop=True):
    """The whole edit (detail radii follow the picture's size, so the preview matches the saved copy)."""
    out = geometry(rgb, p, crop=crop)
    out = tone_color(out, p)
    if p.get("noise") or p.get("sharpen"):
        out = detail(out, p, scale=max(0.3, max(out.shape[:2]) / 1600))
    return out
