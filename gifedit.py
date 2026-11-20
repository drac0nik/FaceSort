"""Animated GIF editing (1.57.2): every edit-mode tool applied to every frame, plus the GIF's own
choices - which frames to keep, speed, reverse, bounce - and saving each frame as a picture.

Frames are read one at a time (fully drawn, as they look when playing) and written back as they're
done, so a long GIF doesn't need every frame in memory at full color."""
import os

import numpy as np
from PIL import Image, ImageSequence

import photoedit

SPEEDS = [(0.25, "¼× (slower)"), (0.5, "½×"), (0.75, "¾×"), (1.0, "1× (as it is)"), (1.5, "1½×"), (2.0, "2×"),
          (3.0, "3× (faster)")]
MIN_DELAY = 20                       # ms - browsers slow anything quicker than this right down


def gif_defaults(n):
    return {"start": 0, "end": max(0, n - 1), "speed": 1.0, "reverse": False, "bounce": False}


def gif_changed(g, n):
    return bool(g) and g != gif_defaults(n)


def info(path):
    """(frame count, loop count (0 = for ever), total milliseconds)."""
    with Image.open(path) as im:
        n = getattr(im, "n_frames", 1)
        loop = im.info.get("loop", 0)
        total = 0
        for fr in ImageSequence.Iterator(im):
            total += int(fr.info.get("duration", 100) or 100)
    return n, loop, total


def frames(path):
    """Each frame as (RGBA array, milliseconds), drawn as it shows when the GIF plays."""
    with Image.open(path) as im:
        for i in range(getattr(im, "n_frames", 1)):
            im.seek(i)
            yield np.asarray(im.convert("RGBA")), int(im.info.get("duration", 100) or 100)


def order(n, g):
    """Which source frames, in what order."""
    g = g or gif_defaults(n)
    a, b = max(0, min(n - 1, int(g["start"]))), max(0, min(n - 1, int(g["end"])))
    if b < a:
        a, b = b, a
    seq = list(range(a, b + 1))
    if g.get("reverse"):
        seq.reverse()
    if g.get("bounce") and len(seq) > 2:
        seq = seq + seq[-2:0:-1]
    return seq


def edit_frame(rgba, p):
    """One frame through edit mode's settings; the see-through parts stay see-through."""
    rgb, alpha = np.ascontiguousarray(rgba[..., :3]), np.ascontiguousarray(rgba[..., 3])
    out = photoedit.render(rgb, p) if photoedit.changed(p) else rgb
    if photoedit.geometry_changed(p):
        alpha = photoedit.geometry(alpha, p)
        if alpha.shape[:2] != out.shape[:2]:
            import cv2
            alpha = cv2.resize(alpha, (out.shape[1], out.shape[0]), interpolation=cv2.INTER_NEAREST)
    return np.dstack([out, alpha])


def _to_p(rgba):
    """A frame in GIF colors (256), with see-through kept as see-through."""
    im = Image.fromarray(rgba, "RGBA")
    clear = rgba[..., 3] < 128
    rgb = im.convert("RGB")
    few = rgb.getcolors(255 if clear.any() else 256) is not None      # already GIF colors: kept exactly
    dither = Image.Dither.NONE if few else Image.Dither.FLOYDSTEINBERG
    if not clear.any():
        return rgb.quantize(256, method=Image.Quantize.MEDIANCUT, dither=dither)
    q = rgb.quantize(255, method=Image.Quantize.MEDIANCUT, dither=dither)
    pal = q.getpalette()[:255 * 3]
    q.putpalette(pal + [0, 0, 0])
    arr = np.asarray(q).copy()
    arr[clear] = 255
    out = Image.fromarray(arr, "P")
    out.putpalette(q.getpalette())
    out.info["transparency"] = 255
    return out


def save_gif(src, dest, p, g, step=None, cancelled=None):
    """The edited GIF. step(fraction) reports progress; cancelled() -> True stops (returns None)."""
    n, loop, _t = info(src)
    seq = order(n, g)
    want = {}
    for i in seq:
        want[i] = want.get(i, 0) + 1
    speed = float((g or {}).get("speed") or 1.0)
    done, cache, durations = 0, {}, {}
    gen = frames(src)
    try:
        for i, (rgba, ms) in enumerate(gen):
            if cancelled and cancelled():
                return None
            if i in want:
                cache[i] = _to_p(edit_frame(rgba, p))
                durations[i] = max(MIN_DELAY, int(round(ms / speed / 10.0)) * 10)
                done += 1
                if step:
                    step(0.9 * done / max(1, len(want)))
            if i >= max(want):
                break
    finally:
        gen.close()                                 # (lets go of the file - it may be replaced next)
    out = [cache[i] for i in seq]
    durs = [durations[i] for i in seq]
    kw = {"save_all": True, "append_images": out[1:], "duration": durs, "loop": loop, "disposal": 2,
          "optimize": False}
    if any("transparency" in f.info for f in out):
        kw["transparency"] = 255
    tmp = dest + ".part"
    out[0].save(tmp, "GIF", **kw)
    os.replace(tmp, dest)
    if step:
        step(1.0)
    return dest


def frame_folder(src):
    stem = os.path.splitext(os.path.basename(src))[0]
    base = os.path.join(os.path.dirname(src), f"{stem} frames")
    out, k = base, 2
    while os.path.exists(out):
        out = f"{base} ({k})"
        k += 1
    return out


def save_frames(src, folder, p=None, g=None, step=None, cancelled=None):
    """Every kept frame as its own PNG (see-through kept), with edits applied. Returns the paths."""
    n, _loop, _t = info(src)
    keep = sorted(set(order(n, g)))
    stem = os.path.splitext(os.path.basename(src))[0]
    width = max(3, len(str(n)))
    os.makedirs(folder, exist_ok=True)
    paths = []
    gen = frames(src)
    try:
        for i, (rgba, _ms) in enumerate(gen):
            if cancelled and cancelled():
                break
            if i not in keep:
                if keep and i > keep[-1]:
                    break
                continue
            fr = edit_frame(rgba, p) if p else rgba
            path = os.path.join(folder, f"{stem} frame {i + 1:0{width}d}.png")
            im = Image.fromarray(fr, "RGBA")
            if fr[..., 3].min() == 255:
                im = im.convert("RGB")
            im.save(path, "PNG", compress_level=6)
            paths.append(path)
            if step:
                step(len(paths) / max(1, len(keep)))
    finally:
        gen.close()
    return paths


def turn(path, clockwise=True):
    """A quarter turn of every frame, in place (the viewer's and the grid's turn buttons)."""
    import photoedit as pe
    n = info(path)[0]
    save_gif(path, path, dict(pe.defaults(), turn=1 if clockwise else 3), gif_defaults(n))
