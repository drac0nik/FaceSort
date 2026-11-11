"""Face detection and recognition. Everything runs locally with OpenCV.

Detection:   YuNet  (face_detection_yunet_2023mar.onnx)
Recognition: SFace  (face_recognition_sface_2021dec.onnx) -> 128-d embedding
"""
import os
import sys
import threading

import cv2
import numpy as np
from PIL import Image, ImageOps

try:
    import pillow_heif  # iPhone HEIC/HEIF photos

    pillow_heif.register_heif_opener()
    HEIF_OK = True
except Exception:  # pragma: no cover - optional
    HEIF_OK = False

Image.MAX_IMAGE_PIXELS = 400_000_000  # allow big panoramas

IMAGE_EXTS = {".jpg", ".jpeg", ".jpe", ".png", ".bmp", ".tif", ".tiff", ".webp"}
if HEIF_OK:
    IMAGE_EXTS |= {".heic", ".heif"}
# less common formats Pillow can read in this build (AVIF, GIF, JPEG 2000, Photoshop, Targa...)
_PIL_EXTS = set(Image.registered_extensions())
IMAGE_EXTS |= {e for e in (".avif", ".gif", ".jfif", ".jp2", ".j2k", ".jpf", ".jpx", ".psd", ".tga", ".dib",
                           ".ppm", ".pgm", ".pbm", ".pcx", ".qoi", ".dds", ".ico", ".icns") if e in _PIL_EXTS}
# camera RAW files (read with LibRaw through rawpy)
RAW_EXTS = {".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".raf", ".orf", ".rw2", ".rwl",
            ".pef", ".ptx", ".dng", ".srw", ".x3f", ".3fr", ".fff", ".iiq", ".erf", ".mef", ".mos", ".mrw",
            ".kdc", ".dcr", ".k25", ".raw", ".gpr"}
try:
    import importlib.util
    RAW_OK = importlib.util.find_spec("rawpy") is not None
except Exception:  # pragma: no cover - optional
    RAW_OK = False
if RAW_OK:
    IMAGE_EXTS |= RAW_EXTS


def dhash(bgr):
    """64-bit picture fingerprint for finding duplicates: survives resizing, re-saving and small
    edits (similar pictures differ in only a few bits)."""
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr
    small = cv2.resize(g, (9, 8), interpolation=cv2.INTER_AREA).astype(np.int16)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    v = 0
    for b in bits:
        v = (v << 1) | int(b)
    return v - (1 << 64) if v >= (1 << 63) else v      # as a signed 64-bit number (SQLite INTEGER)


def is_raw(path):
    return os.path.splitext(path)[1].lower() in RAW_EXTS


def _raw_preview(path, max_side):
    """(RGB array, full width, full height, taken) of a camera RAW file. Uses the camera's
    own embedded preview when it's big enough (fast); otherwise develops the raw data."""
    import io
    import rawpy
    with rawpy.imread(path) as r:
        flip = int(getattr(r.sizes, "flip", 0) or 0)
        W, H = int(r.sizes.width), int(r.sizes.height)
        if flip in (5, 6):
            W, H = H, W
        arr, taken = None, None
        try:
            th = r.extract_thumb()
            if th.format == rawpy.ThumbFormat.JPEG:
                with Image.open(io.BytesIO(th.data)) as im:
                    exif = im.getexif()
                    taken = exif_taken(exif)
                    orient = int(exif.get(0x0112, 1) or 1)
                    if max(im.size) >= min(max_side, 1200):
                        im = ImageOps.exif_transpose(im) if orient != 1 else im
                        arr = np.asarray(im.convert("RGB"))
                        if orient == 1 and flip:
                            code = {3: cv2.ROTATE_180, 5: cv2.ROTATE_90_COUNTERCLOCKWISE, 6: cv2.ROTATE_90_CLOCKWISE}.get(flip)
                            if code is not None:
                                arr = cv2.rotate(arr, code)
            elif th.format == rawpy.ThumbFormat.BITMAP and max(th.data.shape[:2]) >= min(max_side, 1200):
                arr = np.asarray(th.data)
        except Exception:
            arr = None
        if arr is None:   # no usable preview: develop the raw (half size is plenty here)
            arr = r.postprocess(half_size=True, use_camera_wb=True, output_bps=8)
    return np.ascontiguousarray(arr), W, H, taken


VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".wmv", ".webm", ".3gp", ".mts", ".m2ts", ".mpg", ".mpeg",
              # older formats (late 90s / 2000s): Flash, Windows Media, RealMedia, DVD, DivX, camcorders...
              ".flv", ".f4v", ".asf", ".rm", ".rmvb", ".vob", ".divx", ".xvid", ".ogv", ".ogm", ".dv", ".m1v",
              ".m2v", ".mpe", ".mod", ".tod", ".3g2", ".qt", ".ts", ".amv"}
VIDEO_FRAMES = 7          # frames looked at per video, spread through it
VIDEO_SPOTS = (0.08, 0.22, 0.36, 0.50, 0.64, 0.78, 0.92)


def is_video(path):
    return os.path.splitext(path)[1].lower() in VIDEO_EXTS


def resource_path(*parts):
    """Works both from source and from the PyInstaller one-file exe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


# Same YuNet/SFace weights as the OpenCV Model Zoo, with flexible input sizes so the
# GPU (ONNX Runtime) path can take any photo size and batch faces. OpenCV loads them too.
DET_MODEL = resource_path("models", "yunet_dynamic.onnx")
REC_MODEL = resource_path("models", "sface_dynamic.onnx")
SCORE_THRESHOLD = 0.75   # default; Settings > Detection sensitivity changes it
NMS_THRESHOLD = 0.30

MAX_SIDE = 1600   # photos are downscaled to this before detection
THUMB = 192       # stored face thumbnail size (px); bigger views load from the photo

# Face clarity (0-1): how clearly a face can be recognised. Read from the standard
# recognizer (SFace): the length of its raw fingerprint drops steadily as a face gets
# blurrier, smaller, darker or more compressed - and so does how well it matches other
# photos of the same person (tested: correlation 0.81 for SFace's own matches, 0.68 for
# the high-accuracy engine's). The high-accuracy recognizer's own length doesn't track
# this, so both engines take clarity from SFace (a small, quick model).
CLARITY_LO, CLARITY_HI = 9.5, 13.5


def clarity_from_norm(n):
    return float(min(1.0, max(0.0, (float(n) - CLARITY_LO) / (CLARITY_HI - CLARITY_LO))))


_SF_LOCAL = None


def _sface_cpu():
    """A CPU SFace recognizer for this thread (clarity readings)."""
    global _SF_LOCAL
    import threading
    if _SF_LOCAL is None:
        _SF_LOCAL = threading.local()
    r = getattr(_SF_LOCAL, "rec", None)
    if r is None:
        r = _SF_LOCAL.rec = cv2.FaceRecognizerSF.create(REC_MODEL, "")
    return r


def clarity_of(bgr, face):
    """Clarity of one detected face (a YuNet-format row: box, 5 landmarks, score)."""
    try:
        rec = _sface_cpu()
        feat = rec.feature(rec.alignCrop(bgr, np.asarray(face, np.float32)))
        return clarity_from_norm(np.linalg.norm(feat))
    except Exception:
        return None


_THUMB_DET = None


def clarity_of_thumb(jpeg):
    """Clarity of a face from its stored thumbnail (for faces scanned before 1.36): the face is
    found again in the thumbnail, then read as during a scan. None if it can't be read."""
    global _THUMB_DET
    try:
        img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return None
        h, w = img.shape[:2]
        if _THUMB_DET is None:
            _THUMB_DET = cv2.FaceDetectorYN.create(DET_MODEL, "", (w, h), 0.5, NMS_THRESHOLD, 50)
        _THUMB_DET.setInputSize((w, h))
        _, faces = _THUMB_DET.detect(img)
        if faces is None or not len(faces):
            return 0.1                        # not even found again in its own close-up
        cx, cy = w / 2, h / 2
        f = min(faces, key=lambda f: (f[0] + f[2] / 2 - cx) ** 2 + (f[1] + f[3] / 2 - cy) ** 2)
        return clarity_of(img, f)
    except Exception:
        return None


class LoadedImage:
    __slots__ = ("bgr", "width", "height", "orientation", "taken", "cover_t", "bright", "dark_seen", "gps")

    def __init__(self, bgr, width, height, orientation, taken=None):
        self.cover_t = None         # videos: the frame (seconds) used as the video's picture
        self.bright = None          # how bright the photo is overall (0-1), measured while scanning
        self.dark_seen = 0          # 1 = scanned with the second look at dark photos
        self.gps = None             # (latitude, longitude) from the photo's EXIF, if it has one
        self.bgr = bgr              # oriented, downscaled image (BGR)
        self.width = width          # full-size width after orientation
        self.height = height        # full-size height after orientation
        self.orientation = orientation
        self.taken = taken          # when the photo was taken (unix time) from EXIF, or None


def exif_taken(exif):
    """Date taken from EXIF (DateTimeOriginal, else DateTimeDigitized, else DateTime)."""
    import datetime
    vals = []
    try:
        sub = exif.get_ifd(0x8769)
        vals += [sub.get(0x9003), sub.get(0x9004)]
    except Exception:
        pass
    vals.append(exif.get(0x0132))
    for v in vals:
        if not v:
            continue
        try:
            t = datetime.datetime.strptime(str(v).strip()[:19].replace("-", ":"), "%Y:%m:%d %H:%M:%S")
            if t.year >= 1990:
                return t.timestamp()
        except Exception:
            continue
    return None


def read_taken(path):
    """Date taken without decoding the image (only the header is read)."""
    if is_video(path):
        return None
    if is_raw(path) and RAW_OK:
        try:
            return _raw_preview(path, 256)[3]
        except Exception:
            return None
    try:
        with Image.open(path) as im:
            return exif_taken(im.getexif())
    except Exception:
        return None


# ---------------------------------------------------------------------------- videos
def _video_open(path):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise ValueError("this video can't be opened (unsupported format or damaged)")
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    return cap, fps if 0 < fps < 1000 else 0, n


# ---- reading videos OpenCV can't: FFmpeg itself (the same decoder the player uses) takes over
_FF = [None]
_FF_INFO = {}


def ffmpeg_exe():
    if _FF[0] is None:
        exe = ""
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            import shutil
            exe = shutil.which("ffmpeg") or ""
        _FF[0] = exe
    return _FF[0] or None


def _run_ff(args, timeout=60):
    import subprocess
    exe = ffmpeg_exe()
    if not exe:
        raise ValueError("this video can't be read (and FFmpeg isn't available to try)")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run([exe, "-hide_banner"] + args, capture_output=True, timeout=timeout, creationflags=flags)


def ffmpeg_info(path):
    """(width, height, fps, duration seconds) as FFmpeg reads the file."""
    import re
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        key = (path, 0)
    if key in _FF_INFO:
        return _FF_INFO[key]
    err = _run_ff(["-i", path], timeout=30).stderr.decode("utf-8", "replace")
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", err)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0
    v = re.search(r"Stream[^\n]*Video:[^\n]*", err)
    if not v:
        raise ValueError("no video picture found in this file")
    wh = re.search(r"[ ,](\d{2,5})x(\d{2,5})[ ,\[]", v.group(0))
    fr = re.search(r"([\d.]+) fps", v.group(0)) or re.search(r"([\d.]+) tbr", v.group(0))
    W, H = (int(wh.group(1)), int(wh.group(2))) if wh else (0, 0)
    fps = float(fr.group(1)) if fr else 25.0
    out = (W, H, fps if 1 <= fps <= 240 else 25.0, dur)
    _FF_INFO[key] = out
    return out


def ffmpeg_frame(path, t):
    """One frame (BGR) at t seconds, decoded by FFmpeg."""
    r = _run_ff(["-v", "error", "-ss", f"{max(0.0, float(t)):.3f}", "-i", path, "-frames:v", "1",
                 "-f", "image2pipe", "-vcodec", "png", "-"], timeout=60)
    if not r.stdout:
        return None
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)


class VideoSource:
    """Frames from a video: OpenCV when it can read the file, FFmpeg when it can't (some codecs,
    odd or damaged files). n = frame count, fps, W x H."""

    def __init__(self, path):
        self.path, self.cap, self.ff = path, None, False
        cap = cv2.VideoCapture(path)
        if cap.isOpened():
            fps = cap.get(cv2.CAP_PROP_FPS) or 0
            n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            ok, fr = cap.read()
            if ok and fr is not None:
                self.cap = cap
                self.fps = fps if 0 < fps < 1000 else 0
                self.n = n
                self.W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0) or fr.shape[1]
                self.H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0) or fr.shape[0]
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                if not self.fps or n <= 0:            # no length from OpenCV: ask FFmpeg
                    try:
                        _w, _h, f2, d2 = ffmpeg_info(path)
                        self.fps = self.fps or f2
                        self.n = self.n if self.n > 0 else int(d2 * self.fps)
                    except Exception:
                        pass
                return
            cap.release()
        W, H, fps, dur = ffmpeg_info(path)
        self.ff, self.W, self.H, self.fps, self.n = True, W, H, fps, int(dur * fps)

    def read_at(self, f):
        """Frame number f (BGR), or None."""
        if self.cap is not None:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, int(f))
            ok, fr = self.cap.read()
            if ok and fr is not None:
                return fr
        try:
            return ffmpeg_frame(self.path, f / (self.fps or 25.0))
        except Exception:
            return None

    def frames(self, start=0, end=None):
        """Every frame in order (BGR) - or only frames start … end-1 (a marked section)."""
        start = max(0, int(start or 0))
        if self.cap is not None:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, start)
            got, f = False, start
            while end is None or f < end:
                ok, fr = self.cap.read()
                if not ok or fr is None:
                    break
                got = True
                f += 1
                yield fr
            if got:
                return
        import subprocess
        exe = ffmpeg_exe()
        if not exe or not self.W or not self.H:
            return
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        fps = self.fps or 25.0
        span = []
        if start:
            span += ["-ss", f"{start / fps:.3f}"]
        args = [exe, "-hide_banner", "-v", "quiet"] + span + ["-i", self.path]
        if end is not None:
            args += ["-frames:v", str(max(1, int(end) - start))]
        proc = subprocess.Popen(args + ["-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, creationflags=flags)
        size = self.W * self.H * 3
        try:
            while True:
                buf = proc.stdout.read(size)
                if len(buf) < size:
                    break
                yield np.frombuffer(buf, np.uint8).reshape(self.H, self.W, 3).copy()
        finally:
            proc.kill()
            proc.wait()

    def release(self):
        if self.cap is not None:
            self.cap.release()
            self.cap = None


def _fit(bgr, max_side):
    h, w = bgr.shape[:2]
    k = max_side / max(w, h)
    if k < 1.0:
        bgr = cv2.resize(bgr, (max(1, round(w * k)), max(1, round(h * k))), interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(bgr)


def _usable(bgr):
    """Not a black/white fade, title card or motion blur."""
    g = cv2.cvtColor(cv2.resize(bgr, (160, max(1, 160 * bgr.shape[0] // max(1, bgr.shape[1])))), cv2.COLOR_BGR2GRAY)
    return 18 < float(g.mean()) < 240 and float(cv2.Laplacian(g, cv2.CV_64F).var()) > 12


def _has_content(bgr):
    """Something is in the picture: not a black / white / single-colour screen (a face isn't needed)."""
    g = cv2.cvtColor(cv2.resize(bgr, (160, max(1, 160 * bgr.shape[0] // max(1, bgr.shape[1])))), cv2.COLOR_BGR2GRAY)
    return 14 < float(g.mean()) < 245 and float(g.std()) > 12 and float(cv2.Laplacian(g, cv2.CV_64F).var()) > 8


def frame_span(n, fps, t0=None, t1=None):
    """(first frame, end frame) of a marked section (seconds), or of the whole video."""
    f0 = 0 if t0 is None else max(0, min(n - 1, int(round(t0 * fps))))
    f1 = n if t1 is None else max(f0 + 1, min(n, int(round(t1 * fps))))
    return f0, f1


def clock(v):
    v = int(v)
    return f"{v // 3600}:{v // 60 % 60:02d}:{v % 60:02d}" if v >= 3600 else f"{v // 60}:{v % 60:02d}"


def video_collage(path, count=12, max_width=3200, cancelled=lambda: False, progress=lambda i, n: None, strength=0,
                  t0=None, t1=None):
    """count frames spread evenly through the video - each one checked for something actually
    in it (a black, white or blank screen is skipped for a moment nearby) - laid out in a 3 x 4
    grid (4 across for a wide video, 3 across for a tall one), each with its time in the corner.
    Returns (JPEG bytes, [seconds of each frame])."""
    src = VideoSource(path)
    fps, n = src.fps, src.n
    try:
        if n <= 0 or not fps:
            raise ValueError("this video doesn't say how long it is, so frames can't be spread through it")
        duration = n / fps
        f0, f1 = frame_span(n, fps, t0, t1)
        section = t0 is not None or t1 is not None

        def look(f, full=False):
            fr = src.read_at(f)
            if fr is not None and strength:        # low light on: judged (and shown) brightened
                small = fr if full else _fit(fr, 480)
                rgb = np.ascontiguousarray(small[:, :, ::-1])
                fr = np.ascontiguousarray(low_light(rgb, strength, fast=not full)[:, :, ::-1])
            return fr
        # 1) look through the whole video for frames with something in them (black, white and
        #    blank screens are skipped - however long they last)
        good = {}
        for density in (6, 24):
            m = min(f1 - f0, count * density)
            for k in range(m):
                if cancelled():
                    raise RuntimeError("cancelled")
                progress(k, m)
                f = f0 + int((k + 0.5) * (f1 - f0) / m)
                if f in good:
                    continue
                fr = look(f)
                good[f] = fr is not None and _has_content(fr)
            if sum(good.values()) >= count * 2:
                break
        frames = sorted(f for f, ok in good.items() if ok)
        if not frames:                     # nothing passes: take the least empty ones instead
            frames = sorted(good)
        # 2) pick count of them spread evenly through the parts with something in them
        k = min(count, len(frames))
        chosen = [frames[min(len(frames) - 1, int((i + 0.5) * len(frames) / k))] for i in range(k)]
        tiles, times = [], []
        for f in sorted(chosen):
            fr = look(f, full=True)
            if fr is not None:
                tiles.append(fr)
                times.append(f / fps)
        if not tiles:
            raise ValueError("no frames could be read from this video")
    finally:
        src.release()
    h0, w0 = tiles[0].shape[:2]
    cols, rows = (4, 3) if w0 >= h0 else (3, 4)
    gap = 6
    tw = max(160, min(w0, (max_width - gap * (cols + 1)) // cols))
    th = max(1, round(tw * h0 / w0))
    sheet = np.full((rows * th + gap * (rows + 1), cols * tw + gap * (cols + 1), 3), 22, np.uint8)
    for j, (fr, t) in enumerate(zip(tiles, times)):
        r, c = divmod(j, cols)
        if r >= rows:
            break
        im = cv2.resize(fr, (tw, th), interpolation=cv2.INTER_AREA if fr.shape[1] > tw else cv2.INTER_CUBIC)
        x, y = gap + c * (tw + gap), gap + r * (th + gap)
        sheet[y:y + th, x:x + tw] = im
        v = int(t)
        label = f"{v // 3600}:{v // 60 % 60:02d}:{v % 60:02d}" if v >= 3600 else f"{v // 60}:{v % 60:02d}"
        scale = max(0.45, tw / 640)
        (lw, lh), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
        px, py = x + tw - lw - int(10 * scale), y + th - int(10 * scale)
        cv2.rectangle(sheet, (px - 5, py - lh - 6), (px + lw + 5, py + 6), (0, 0, 0), -1)
        cv2.putText(sheet, label, (px, py), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), max(1, round(scale * 1.6)),
                    cv2.LINE_AA)
    name = os.path.basename(path)
    if section:
        name += f"   (section {clock(f0 / fps)} – {clock(f1 / fps)})"
    sheet = _collage_header(sheet, name, w0, h0, duration)
    ok, buf = cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 93])
    if not ok:
        raise ValueError("the collage couldn't be made")
    return buf.tobytes(), times


def _font(size, bold=False):
    from PIL import ImageFont
    names = (("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf") if bold else
             ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"))
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _collage_header(sheet, name, w, h, duration):
    """A band across the top of the collage: the file name, then its size in pixels and its length."""
    from PIL import Image, ImageDraw
    W = sheet.shape[1]
    big = max(18, W // 60)
    small = max(14, int(big * 0.72))
    pad = max(10, big // 2)
    band = pad * 2 + big + small + pad // 2
    v = int(round(duration))
    length = f"{v // 3600}:{v // 60 % 60:02d}:{v % 60:02d}" if v >= 3600 else f"{v // 60}:{v % 60:02d}"
    info = f"{w} × {h}   ·   {length}"
    im = Image.new("RGB", (W, band + sheet.shape[0]), (22, 22, 22))
    im.paste(Image.fromarray(sheet[:, :, ::-1]), (0, band))
    d = ImageDraw.Draw(im)
    fb, fs = _font(big, True), _font(small)
    text = name
    while len(text) > 4 and d.textlength(text, font=fb) > W - 2 * pad:     # too long: shorten in the middle
        k = len(text) // 2
        text = text[:k - 2] + "…" + text[k + 1:] if "…" not in text else text.replace("…", "")[:k - 2] + "…" + \
            text.replace("…", "")[k + 1:]
    d.text((pad, pad), text, font=fb, fill=(255, 255, 255))
    d.text((pad, pad + big + pad // 2), info, font=fs, fill=(170, 190, 220))
    return np.ascontiguousarray(np.asarray(im)[:, :, ::-1])


def video_fps(path):
    try:
        src = VideoSource(path)
        src.release()
        return src.fps or 30.0
    except Exception:
        return 30.0


_CLAHE = {}


def dark_levels(Y):
    """(black, white) of a frame's brightness (0-255): black is the camera's dark floor when most
    of the picture sits on one low value (a night recording: that floor and its noise / block
    steps are really black), else the darkest 0.5%; white is the brightest real detail."""
    small = cv2.resize(Y, (320, 180), interpolation=cv2.INTER_AREA) if Y.shape[1] > 320 else Y
    s8 = np.clip(small, 0, 255).astype(np.uint8)
    hist = np.bincount(s8.ravel(), minlength=256)
    mode = int(np.argmax(hist[:64]))
    near = hist[max(0, mode - 3):mode + 4].sum() / max(1, s8.size)
    low = float(np.percentile(s8, 0.5))
    if mode <= 40 and near > 0.35 and mode - low <= 4:      # the bottom of the range, not a dark grey
        black = mode + 1.5
    else:
        black = low
    white = max(black + 10.0, float(np.percentile(s8, 99.9)))
    return black, white


_NOISE_K = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], np.float32)


def noise_sigma(Y):
    """How noisy a picture is (standard deviation of the noise, in brightness levels) - the quick
    estimate from J. Immerkaer, "Fast Noise Variance Estimation" (1996)."""
    small = Y if Y.shape[1] <= 640 else cv2.resize(Y, (640, max(2, 640 * Y.shape[0] // Y.shape[1])),
                                                   interpolation=cv2.INTER_NEAREST)
    r = cv2.filter2D(small.astype(np.float32), -1, _NOISE_K)[1:-1, 1:-1]
    return float(np.sqrt(np.pi / 2) * np.abs(r).mean() / 6.0)


def frame_level(rgb):
    """dark_levels() of an RGB frame - pass a smoothed value to low_light while a video plays."""
    y = cv2.cvtColor(cv2.resize(rgb, (320, 180), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2GRAY)
    return dark_levels(y.astype(np.float32))


def low_light(rgb, strength=1, level=None, fast=False):
    """Low-light recovery for a dark video frame (RGB uint8 in, RGB uint8 out). See low_light_yuv."""
    ycc = cv2.cvtColor(rgb, cv2.COLOR_RGB2YCrCb)
    Y = ycc[:, :, 0].astype(np.float32)
    if float(Y.mean()) >= (0.40 + 0.06 * (strength - 1)) * 255:
        return rgb                                    # well lit: left as it is
    return low_light_yuv(Y, ycc[:, :, 2], ycc[:, :, 1], strength, level, fast)


def low_light_yuv(Y, U, V, strength=1, level=None, fast=False):
    """Low-light recovery from a frame's own brightness and colour planes (Y full size, U = Cb and
    V = Cr any size, all on a 0-255 scale). Returns RGB uint8.

    1. the camera's dark floor (and its noise / compression blocks) becomes black, lone sensor
       speckles are removed, and what's above the floor is stretched to the full range;
    2. the noise that shows is smoothed (non-local means: edges kept), then a gamma curve and
       gentle local contrast lift the midtones;
    3. colour - mostly noise this dark - comes from a heavily smoothed copy, with the colour
       cast taken out, and only modestly boosted.
    level: dark_levels() of the frame (pass a smoothed value while playing so it doesn't pump).
    fast: quicker smoothing for playback."""
    Y = Y.astype(np.float32)
    H0, W0 = Y.shape
    black, white = level if level is not None else dark_levels(Y)
    span = max(6.0, white - black)
    gain = 255.0 / span
    # very dark: only a dozen or so levels, and the camera's resolution is mostly noise and
    # compression blocks. Averaging neighbouring pixels (working at a smaller size, kept in
    # fractions) gives back in-between levels and halves the noise - then it's scaled back up.
    if gain > 4:
        Y = cv2.medianBlur(Y, 5 if gain > 8 else 3)       # lone speckles would be blown up too
    if gain > 12:
        tw = 480 if strength > 1 else 640
        if W0 > tw * 1.2:
            k = tw / W0
            Y = cv2.resize(Y, (tw, max(2, round(H0 * k))), interpolation=cv2.INTER_AREA)
            U = cv2.resize(U.astype(np.float32), (Y.shape[1], Y.shape[0]), interpolation=cv2.INTER_AREA)
            V = cv2.resize(V.astype(np.float32), (Y.shape[1], Y.shape[0]), interpolation=cv2.INTER_AREA)
    h, w = Y.shape
    target = 0.30 + 0.07 * (strength - 1)
    base = black
    if gain > 12:
        # the dark floor isn't the same everywhere (the far side of a room, the edge of the lens
        # are darker still): where it's lower, black is set lower too, so faint shapes there
        # (a person in the shadows) aren't cut off with the floor
        tiny = cv2.resize(Y, (160, max(2, 160 * h // w)), interpolation=cv2.INTER_AREA)
        hist = np.bincount(np.clip(tiny * 4, 0, 1023).astype(np.int32).ravel(), minlength=1024)
        gfloor = float(np.argmax(hist[:400])) / 4.0
        lfloor = cv2.GaussianBlur(cv2.erode(tiny, np.ones((7, 7), np.uint8)), (0, 0), 5)
        fmap = np.minimum(lfloor + 0.3, min(gfloor, black)) + (0.2 if strength > 1 else 0.6)    # strong: more shadow detail (a dark grey instead of black)
        base = cv2.resize(fmap, (w, h), interpolation=cv2.INTER_CUBIC)
    y8 = np.clip((Y - base) * gain + 0.5, 0, 255).astype(np.uint8)
    if gain > 2.5:
        work = 640 if fast else 960
        k = min(1.0, work / w)
        small = cv2.resize(y8, (int(w * k), int(h * k)), interpolation=cv2.INTER_AREA) if k < 1 else y8
        # as strong as the noise the stretch shows (a clean dark picture is barely touched)
        # (only a dozen or so levels to begin with: compression blocks and steps count as noise too)
        floor = 0.9 * gain if gain > 12 else 3.0
        hh = min(26.0, max(floor, 1.6 * noise_sigma(Y) * gain)) * (1.15 if strength > 1 else 1.0)
        small = cv2.fastNlMeansDenoising(small, None, h=hh, templateWindowSize=5 if fast else 7,
                                         searchWindowSize=11 if fast else 17)
        y8 = cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC) if k < 1 else small
    m = float(y8.mean()) / 255.0
    if m < target:
        gm = max(0.35 if strength > 1 else 0.45, np.log(target) / np.log(max(m, 1e-3)))
        y8 = cv2.LUT(y8, (np.power(np.arange(256) / 255.0, gm) * 255 + 0.5).astype(np.uint8))
    clip = round(1.0 + 0.35 * strength * min(1.0, gain / 6), 1)
    if clip > 1.15:
        if clip not in _CLAHE:
            _CLAHE[clip] = cv2.createCLAHE(clipLimit=clip, tileGridSize=(8, 8))
        y8 = _CLAHE[clip].apply(y8)
    cb = U.astype(np.float32) - 128.0
    cr = V.astype(np.float32) - 128.0
    if gain > 2.5:
        sm = (max(8, w // 40), max(8, h // 40))
        cb = cv2.resize(cv2.resize(cb, sm, interpolation=cv2.INTER_AREA), (w, h), interpolation=cv2.INTER_LINEAR)
        cr = cv2.resize(cv2.resize(cr, sm, interpolation=cv2.INTER_AREA), (w, h), interpolation=cv2.INTER_LINEAR)
        cg = min(2.5, 0.3 * gain)
        cb = (cb - 0.7 * float(cb.mean())) * cg
        cr = (cr - 0.7 * float(cr.mean())) * cg
    elif cb.shape != (h, w):
        cb = cv2.resize(cb, (w, h), interpolation=cv2.INTER_LINEAR)
        cr = cv2.resize(cr, (w, h), interpolation=cv2.INTER_LINEAR)
    out = np.dstack([y8.astype(np.float32), np.clip(cr + 128, 0, 255), np.clip(cb + 128, 0, 255)]).astype(np.uint8)
    rgb = cv2.cvtColor(out, cv2.COLOR_YCrCb2RGB)
    if (w, h) != (W0, H0):
        rgb = cv2.resize(rgb, (W0, H0), interpolation=cv2.INTER_CUBIC)
    return rgb


def video_frames(path, max_side=MAX_SIDE, spots=VIDEO_SPOTS):
    """Frames spread through the video: [(seconds, bgr)], skipping dark or blurry ones
    (the next frames are tried instead). Very dark frames are brightened (low-light recovery)
    so faces in them can still be found. Returns (frames, width, height, duration)."""
    src = VideoSource(path)
    try:
        W, H, fps, n = src.W, src.H, src.fps, src.n
        out = []
        if n > 0 and fps:
            for frac in spots:
                target = int(n * frac)
                got = None
                for nudge in (0, int(fps * 0.7), int(fps * 1.5)):   # dark/blurry: try a moment later
                    f = min(n - 1, target + nudge)
                    fr = src.read_at(f)
                    if fr is None:
                        continue
                    got = (f / fps, fr)
                    if _usable(fr):
                        break
                if got:
                    out.append((round(got[0], 2), _fit(got[1], max_side)))
            duration = n / fps
        else:   # no frame count (some streams): read along and keep a few
            frames, i = [], 0
            for fr in src.frames():
                if i >= 3000:
                    break
                if i % 30 == 0:
                    frames.append((i, fr))
                i += 1
            step = max(1, len(frames) // len(spots))
            out = [(round(k / (fps or 30), 2), _fit(fr, max_side)) for k, fr in frames[step // 2::step]][:len(spots)]
            duration = i / (fps or 30)
        if not out:
            raise ValueError("no picture could be read from this video")
        if not W or not H:
            H, W = out[0][1].shape[:2]
        bright = []
        for t, fr in out:                      # too dark to see faces: brightened for the search
            rgb = np.ascontiguousarray(fr[:, :, ::-1])
            if float(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY).mean()) < 0.12 * 255:
                fr = np.ascontiguousarray(low_light(rgb, 1)[:, :, ::-1])
            bright.append((t, fr))
        return bright, W, H, duration
    finally:
        src.release()


def video_frame(path, t=None, max_side=MAX_SIDE):
    """One frame (BGR) at t seconds (default: the middle)."""
    src = VideoSource(path)
    try:
        fps = src.fps or 25.0
        f = (src.n // 2 if src.n > 0 else 0) if t is None else int(round(float(t) * fps))
        fr = src.read_at(f)
        if fr is None:
            fr = src.read_at(0)
        if fr is None:
            raise ValueError("no picture could be read from this video")
        return _fit(fr, max_side)
    finally:
        src.release()


def load_image(path, max_side=MAX_SIDE, t=None):
    if is_video(path):
        bgr = video_frame(path, t, max_side)
        try:
            cap = cv2.VideoCapture(path)
            W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            cap.release()
        except Exception:
            W = H = 0
        h, w = bgr.shape[:2]
        return LoadedImage(bgr, W or w, H or h, 1, None)
    if is_raw(path) and RAW_OK:
        arr, W, H, taken = _raw_preview(path, max_side)
        bgr = _fit(cv2.cvtColor(arr, cv2.COLOR_RGB2BGR), max_side)
        return LoadedImage(bgr, W, H, 1, taken)
    with Image.open(path) as im:
        w0, h0 = im.size
        gps = None
        try:
            exif = im.getexif()
            orientation = int(exif.get(0x0112, 1) or 1)
            taken = exif_taken(exif)
            gps = exif_gps(exif)
        except Exception:
            orientation, taken = 1, None
        if im.format == "JPEG":
            # Fast JPEG decode at reduced size (keeps at least max_side).
            im.draft("RGB", (max_side, max_side))
        if getattr(im, "n_frames", 1) > 1:
            im.seek(0)
        im = ImageOps.exif_transpose(im)
        if im.mode != "RGB":
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1])
                im = bg
            else:
                im = im.convert("RGB")
        arr = np.asarray(im)
    if orientation in (5, 6, 7, 8):
        w0, h0 = h0, w0
    h, w = arr.shape[:2]
    scale = max_side / max(w, h)
    if scale < 1.0:
        arr = cv2.resize(arr, (max(1, round(w * scale)), max(1, round(h * scale))),
                         interpolation=cv2.INTER_AREA)
    bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    li = LoadedImage(np.ascontiguousarray(bgr), w0, h0, orientation, taken)
    li.gps = gps
    return li


def exif_gps(exif):
    """(latitude, longitude) in degrees from EXIF, or None."""
    try:
        g = exif.get_ifd(0x8825)
    except Exception:
        return None
    if not g or 2 not in g or 4 not in g:
        return None

    def deg(v, ref):
        d, m, sec = (float(x) for x in v)
        val = d + m / 60 + sec / 3600
        return -val if str(ref).upper().startswith(("S", "W")) else val
    try:
        lat, lon = deg(g[2], g.get(1, "N")), deg(g[4], g.get(3, "E"))
    except Exception:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return None
    return round(lat, 6), round(lon, 6)


# ---------------------------------------------------------------- second look at dark photos
# Dim party, restaurant and evening photos are where the face finder misses the most: faces
# there are a few levels above black, buried in noise. Those photos are looked at a second time,
# brightened (the viewer's low-light recovery; for backlit photos a shadow lift), and any face
# found only there is added. A face found in both is fingerprinted from whichever version reads
# more clearly.
DARK_SECOND_LOOK = True          # Settings: "Take a second look at dark photos"
DARK_MEAN = 0.25                 # photos darker than this overall (0-1) ...
SHADOW_P30 = 25                  # ... or with a third of the picture below this level (backlit)
DARK_EXTRA_SCORE = 0.08          # faces found only in the brightened copy must be this much surer


def brightness(bgr):
    """(mean brightness 0-1, 30th percentile 0-255) of a picture."""
    small = cv2.resize(bgr, (160, max(1, 160 * bgr.shape[0] // max(1, bgr.shape[1]))), interpolation=cv2.INTER_AREA)
    y = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return float(y.mean()) / 255.0, float(np.percentile(y, 30))


def is_dark(mean, p30):
    return mean < DARK_MEAN or p30 < SHADOW_P30


def brightened(bgr):
    """A brightened copy of a dark photo for face finding (same size), or None if it isn't dark."""
    mean, p30 = brightness(bgr)
    if not is_dark(mean, p30):
        return None
    rgb = np.ascontiguousarray(bgr[:, :, ::-1])
    if mean < DARK_MEAN:
        out = low_light(rgb, 2 if mean < 0.15 else 1)
        if out is not rgb:
            if out.shape[:2] != bgr.shape[:2]:
                out = cv2.resize(out, (bgr.shape[1], bgr.shape[0]), interpolation=cv2.INTER_LINEAR)
            return np.ascontiguousarray(out[:, :, ::-1])
    # backlit / deep shadows in an otherwise normal photo: lift the shadows only
    ycc = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)
    Y = ycc[:, :, 0]
    lut = np.clip(255.0 * (np.arange(256) / 255.0) ** 0.55, 0, 255).astype(np.uint8)
    Y = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(cv2.LUT(Y, lut))
    ycc[:, :, 0] = Y
    return cv2.cvtColor(ycc, cv2.COLOR_YCrCb2BGR)


def second_look(eng, bgr, found, min_score):
    """Faces found only in the brightened copy of a dark photo: [(row, brightened bgr, None)],
    plus the brightened copy itself (None if the photo isn't dark)."""
    if not DARK_SECOND_LOOK:
        return [], None
    bright = brightened(bgr)
    if bright is None:
        return [], None
    boxes = [_orig_box(f, rot, bgr.shape[1], bgr.shape[0]) for f, _w, rot in found]
    extra = []
    for f in eng.detect(bright):
        if float(f[14]) < min_score:
            continue
        box = tuple(float(v) for v in f[:4])
        if any(_iou_px(box, b) > 0.3 for b in boxes):
            continue
        extra.append((f, bright, None))
        boxes.append(box)
    return extra, bright


# ============================================================================ GPU backend
class GpuBackend:
    """Face detection + recognition on the GPU through ONNX Runtime.

    Windows: DirectML (any DirectX 12 GPU - NVIDIA, AMD or Intel). CUDA is used if an
    onnxruntime-gpu build is installed instead. One shared instance; calls are
    serialized with a lock while photo decoding keeps running on CPU threads.
    Produces the same results as the OpenCV path (verified box-for-box)."""

    def __init__(self, force_cpu_provider=False):
        import onnxruntime as ort
        avail = ort.get_available_providers()
        candidates = []
        if "DmlExecutionProvider" in avail:
            candidates.append(("DmlExecutionProvider", "DirectML"))
        if "CUDAExecutionProvider" in avail:
            candidates.append(("CUDAExecutionProvider", "CUDA"))
        if force_cpu_provider:  # testing aid: exercise this code path without a GPU
            candidates.append(("CPUExecutionProvider", "ONNX Runtime CPU (test)"))
        if not candidates:
            raise RuntimeError("no GPU support in this ONNX Runtime build "
                               f"(available: {', '.join(avail) or 'none'})")
        last_err = None
        for prov, label in candidates:
            option_sets = [{}]
            if prov == "DmlExecutionProvider":
                # prefer the dedicated/high-performance GPU on laptops with two GPUs
                option_sets = [{"performance_preference": "high_performance", "device_filter": "gpu"}, {}]
            for opts in option_sets:
                try:
                    so = ort.SessionOptions()
                    so.log_severity_level = 3
                    so.enable_mem_pattern = False                       # required by DirectML
                    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL  # required by DirectML
                    provs = [(prov, opts), "CPUExecutionProvider"] if prov != "CPUExecutionProvider" else [prov]
                    det = ort.InferenceSession(DET_MODEL, so, providers=provs)
                    rec = ort.InferenceSession(REC_MODEL, so, providers=provs)
                    if det.get_providers()[0] != prov or rec.get_providers()[0] != prov:
                        raise RuntimeError(f"{label} could not run the models")
                    det.run(None, {"input": np.zeros((1, 3, 640, 640), np.float32)})   # warm-up / sanity
                    rec.run(None, {"data": np.zeros((1, 3, 112, 112), np.float32)})
                    self.det, self.rec, self.label, self.provider = det, rec, label, prov
                    self.det_outputs = [o.name for o in det.get_outputs()]
                    self.lock = threading.Lock()
                    return
                except Exception as e:  # try the next option set / provider
                    last_err = e
        raise RuntimeError(f"GPU could not be used: {last_err}")

    def detect(self, bgr, threshold=SCORE_THRESHOLD):
        h, w = bgr.shape[:2]
        # pad to multiples of 128 so only a handful of distinct input shapes ever reach the GPU
        ph, pw = -(-h // 128) * 128, -(-w // 128) * 128
        pad = np.zeros((ph, pw, 3), np.uint8)
        pad[:h, :w] = bgr
        blob = pad.transpose(2, 0, 1)[None].astype(np.float32)
        with self.lock:
            res = self.det.run(None, {"input": blob})
        outs = dict(zip(self.det_outputs, res))
        found = []
        for st in (8, 16, 32):
            cols = pw // st
            cls = np.clip(outs[f"cls_{st}"][0, :, 0], 0, 1)
            obj = np.clip(outs[f"obj_{st}"][0, :, 0], 0, 1)
            score = np.sqrt(cls * obj)
            keep = np.where(score >= threshold)[0]
            if not len(keep):
                continue
            r, c = keep // cols, keep % cols
            b = outs[f"bbox_{st}"][0, keep]
            k = outs[f"kps_{st}"][0, keep]
            cx, cy = (c + b[:, 0]) * st, (r + b[:, 1]) * st
            bw, bh = np.exp(b[:, 2]) * st, np.exp(b[:, 3]) * st
            kx = (k[:, 0::2] + c[:, None]) * st
            ky = (k[:, 1::2] + r[:, None]) * st
            lm = np.stack([kx, ky], axis=2).reshape(-1, 10)
            found.append(np.column_stack([cx - bw / 2, cy - bh / 2, bw, bh, lm, score[keep]]))
        if not found:
            return []
        F = np.concatenate(found).astype(np.float32)
        idx = cv2.dnn.NMSBoxes(F[:, :4].tolist(), F[:, 14].tolist(), threshold, NMS_THRESHOLD, top_k=5000)
        return [F[i] for i in np.array(idx).flatten()] if len(idx) else []

    def embed_batch(self, aligned):
        """aligned: list of 112x112 BGR face crops -> (n,128) normalized embeddings."""
        x = np.stack([a[:, :, ::-1] for a in aligned]).transpose(0, 3, 1, 2).astype(np.float32)  # RGB, as OpenCV does
        with self.lock:
            feats = self.rec.run(None, {"data": x})[0].astype(np.float32)
        n = np.linalg.norm(feats, axis=1, keepdims=True)
        self.last_norms = n[:, 0].copy()
        n[n == 0] = 1
        return feats / n


_gpu = None
_gpu_error = None
_gpu_lock = threading.Lock()


def get_gpu(enabled=True):
    """The shared GPU backend, or None (then the CPU path is used)."""
    global _gpu, _gpu_error
    if not enabled:
        return None
    with _gpu_lock:
        if _gpu is None and _gpu_error is None:
            try:
                _gpu = GpuBackend(force_cpu_provider=os.environ.get("FACESORT_TEST_ORT") == "1")
            except Exception as e:
                _gpu_error = str(e) or e.__class__.__name__
        return _gpu


_hq_label = None


def gpu_status():
    """(active, text) for display."""
    if _hq_label:
        return _hq_label != "CPU", f"High accuracy · {_hq_label}"
    if _gpu is not None:
        return True, f"GPU ({_gpu.label})"
    if _gpu_error is not None:
        return False, f"CPU - GPU not available: {_gpu_error}"
    return False, "not started yet"


def reset_gpu():
    """Forget a previous GPU failure so the next scan tries again."""
    global _gpu, _gpu_error
    with _gpu_lock:
        _gpu, _gpu_error = None, None


def disable_gpu(reason):
    global _gpu, _gpu_error
    with _gpu_lock:
        _gpu, _gpu_error = None, reason


# ============================================================================ engine
class FaceEngine:
    model = "sface"
    dim = 128

    """Finds faces and computes their fingerprints.

    Detection runs at three sizes and merges the results: the full working image
    (up to 1600 px, best for small faces in group shots), a 640 px copy and a 320 px
    copy (YuNet misses faces that fill most of the frame - selfies, close portraits -
    at large sizes; at 320 px even a face spanning the whole photo is found). Photos where nothing is found are also tried turned left and right, for
    sideways pictures that have no rotation flag."""

    SMALL_SIDES = (640, 320)

    def __init__(self, min_face_px=32, gpu=None, threshold=SCORE_THRESHOLD, rotations=True):
        if not (os.path.exists(DET_MODEL) and os.path.exists(REC_MODEL)):
            raise FileNotFoundError("Face models are missing from the 'models' folder.")
        self.gpu = gpu
        self.min_face_px = min_face_px
        self.threshold = float(threshold)
        self.rotations = rotations
        if gpu is None:
            self.det = cv2.FaceDetectorYN.create(DET_MODEL, "", (320, 320), self.threshold, NMS_THRESHOLD, 5000)
        # alignCrop (a cheap CPU warp) comes from OpenCV in both modes
        self.rec = cv2.FaceRecognizerSF.create(REC_MODEL, "")

    def _detect_once(self, bgr):
        if self.gpu is not None:
            return list(self.gpu.detect(bgr, self.threshold))
        h, w = bgr.shape[:2]
        self.det.setInputSize((w, h))
        _, faces = self.det.detect(bgr)
        return [] if faces is None else list(faces)

    def detect(self, bgr):
        """All faces in a (working-size) image, as YuNet rows in that image's pixels."""
        h, w = bgr.shape[:2]
        found = self._detect_once(bgr)
        long_side = max(w, h)
        for side in self.SMALL_SIDES:
            if long_side <= side * 1.4:
                continue
            k = side / long_side
            small = cv2.resize(bgr, (max(1, round(w * k)), max(1, round(h * k))), interpolation=cv2.INTER_AREA)
            for f in self._detect_once(small):
                g = np.array(f, np.float32)
                g[:14] /= k  # back to working-image pixels
                found.append(g)
        if not found:
            return []
        F = np.stack([np.asarray(f, np.float32) for f in found])
        idx = cv2.dnn.NMSBoxes(F[:, :4].tolist(), F[:, 14].tolist(), self.threshold, NMS_THRESHOLD, top_k=5000)
        F = F[np.array(idx).flatten()] if len(idx) else F[:0]
        # the same face seen at two sizes can give a big box and a smaller one inside it
        kept = []
        for f in sorted(F, key=lambda f: -float(f[14])):
            area = float(f[2] * f[3])
            if any(_inter(f, g) > 0.6 * min(area, float(g[2] * g[3])) for g in kept):
                continue
            kept.append(f)
        return [f for f in kept if min(f[2], f[3]) >= self.min_face_px]

    def embed(self, bgr, face):
        return self.embed_many(bgr, [face])[0]

    def embed_many(self, bgr, faces):
        return [e for e, _c in self.embed_with_clarity(bgr, faces)]

    def embed_with_clarity(self, bgr, faces):
        """[(fingerprint, clarity 0-1)]"""
        aligned = [self.rec.alignCrop(bgr, f) for f in faces]
        if not aligned:
            return []
        if self.gpu is not None:
            embs = list(self.gpu.embed_batch(aligned))
            norms = getattr(self.gpu, "last_norms", None)
            if norms is None or len(norms) != len(embs):
                return [(e, None) for e in embs]
            return [(e, clarity_from_norm(n)) for e, n in zip(embs, norms)]
        out = []
        for a in aligned:
            feat = self.rec.feature(a).astype(np.float32).flatten()
            n = np.linalg.norm(feat)
            out.append((feat / n if n > 0 else feat, clarity_from_norm(n)))
        return out

    @staticmethod
    def thumbnail(bgr, face, size=None):
        h, w = bgr.shape[:2]
        x, y, fw, fh = [float(v) for v in face[:4]]
        cx, cy = x + fw / 2, y + fh / 2
        side = max(fw, fh) * 1.5
        x0, y0 = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
        x1, y1 = int(min(w, cx + side / 2)), int(min(h, cy + side / 2))
        crop = bgr[y0:y1, x0:x1]
        if crop.size == 0:
            crop = bgr
        ch, cw = crop.shape[:2]
        s = max(ch, cw)
        pad = cv2.copyMakeBorder(crop, (s - ch) // 2, s - ch - (s - ch) // 2,
                                 (s - cw) // 2, s - cw - (s - cw) // 2,
                                 cv2.BORDER_CONSTANT, value=(40, 40, 40))
        size = size or THUMB
        pad = cv2.resize(pad, (size, size), interpolation=cv2.INTER_AREA if s >= size else cv2.INTER_CUBIC)
        ok, buf = cv2.imencode(".jpg", pad, [cv2.IMWRITE_JPEG_QUALITY, 88])
        return buf.tobytes() if ok else b""

    SURE = 0.92   # below this, a face might be sideways; also try the photo turned

    def process(self, path):
        """Return (image_info, [face dicts]) for one photo (or video)."""
        if is_video(path):
            return process_video(self, path)
        return self.process_image(load_image(path))

    def process_image(self, img):
        h, w = img.bgr.shape[:2]
        cands = [(f, img.bgr, None) for f in self.detect(img.bgr)]
        if self.rotations and (not cands or min(float(f[14]) for f, _, _ in cands) < self.SURE):
            # Maybe sideways (no rotation flag) or someone lying down. YuNet still "finds" a
            # sideways face, just less confidently, so try the photo turned both ways and keep
            # each face in the orientation where the detector is most sure of it.
            for code in (cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE):
                turned = cv2.rotate(img.bgr, code)
                cands += [(f, turned, code) for f in self.detect(turned)]
        cands.sort(key=lambda c: -float(c[0][14]))
        found, boxes = [], []
        for f, work, rot in cands:
            box = _orig_box(f, rot, w, h)
            if any(_iou_px(box, b) > 0.3 for b in boxes):
                continue
            found.append((f, work, rot))
            boxes.append(box)
        img.bright = brightness(img.bgr)[0]
        extra, bright = ([], None) if img.dark_seen == -1 else \
            second_look(self, img.bgr, found, min(0.99, self.threshold + DARK_EXTRA_SCORE))
        img.dark_seen = 1 if DARK_SECOND_LOOK and img.dark_seen != -1 else 0
        found += extra
        out = []
        for f, work, rot in found:
            emb, clarity = self.embed_with_clarity(work, [f])[0]
            if bright is not None and work is img.bgr:
                # a face in a dark photo: read it from the brightened copy too, keep the clearer
                e2, c2 = self.embed_with_clarity(bright, [f])[0]
                if c2 is not None and (clarity is None or c2 > clarity + 0.05):
                    emb, clarity, work = e2, c2, bright
            x, y, fw, fh = _orig_box(f, rot, w, h)
            x, y = max(0.0, x), max(0.0, y)
            fw, fh = min(fw, w - x), min(fh, h - y)
            if fw <= 1 or fh <= 1:
                continue
            out.append({
                "x": x / w, "y": y / h, "w": fw / w, "h": fh / h,  # normalized, as the photo is displayed
                "score": float(f[14]),
                "px": int(min(fw, fh) * img.width / w),            # face size in the original
                "emb": emb,
                "thumb": self.thumbnail(work, f),                  # upright face, even if the photo isn't
                "clarity": clarity,
            })
        return img, out


def _inter(a, b):
    ix = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    return float(ix * iy)


def _orig_box(f, rot, W, H):
    x, y, w, h = [float(v) for v in f[:4]]
    return (x, y, w, h) if rot is None else _unrotate_box(x, y, w, h, rot, W, H)


def _iou_px(a, b):
    ix = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def _unrotate_box(x, y, w, h, code, W, H):
    """Map a box found in a 90°-turned copy back onto the original (W x H) image."""
    if code == cv2.ROTATE_90_CLOCKWISE:      # original (u,v) -> turned (H-1-v, u)
        return y, H - x - w, h, w
    return W - y - h, x, h, w                 # counter-clockwise: (u,v) -> (v, W-1-u)


_local = threading.local()


def thread_engine(min_face_px=32, use_gpu=True, threshold=SCORE_THRESHOLD, rotations=True, model="sface"):
    """One engine per worker thread (OpenCV objects are not thread-safe);
    the GPU backend itself is shared."""
    global _hq_label
    if model in ("arcface", "adaface"):
        import engine_hq
        gpu_ok = use_gpu and _gpu_error is None
        hq_thr = 0.5 + (float(threshold) - SCORE_THRESHOLD) * 0.8   # same slider, SCRFD's scale
        eng = getattr(_local, "hq", None)
        key = (min_face_px, gpu_ok, round(hq_thr, 3), rotations, model)
        if eng is None or getattr(eng, "_key", None) != key:
            eng = engine_hq.HQFaceEngine(min_face_px=min_face_px, use_gpu=gpu_ok, threshold=hq_thr, rotations=rotations,
                                         model=model)
            eng._key = key
            _local.hq = eng
        _hq_label = eng.s.label
        return eng
    _hq_label = None
    gpu = get_gpu(use_gpu)
    eng = getattr(_local, "engine", None)
    if (eng is None or eng.min_face_px != min_face_px or eng.gpu is not gpu
            or eng.threshold != float(threshold) or eng.rotations != rotations):
        eng = FaceEngine(min_face_px=min_face_px, gpu=gpu, threshold=threshold, rotations=rotations)
        _local.engine = eng
    return eng


def face_from_box(eng, path, box, t=None):
    """A face you drew by hand (Tag Person): look for the face inside the box with a more
    sensitive setting to get a proper fingerprint. Returns a face dict like process() does;
    "emb" is None when no face could be measured there (it is still tagged)."""
    img = load_image(path, t=t)
    bgr = img.bgr
    H, W = bgr.shape[:2]
    bx, by, bw, bh = box[0] * W, box[1] * H, box[2] * W, box[3] * H
    pad = 0.45 * max(bw, bh)
    x0, y0 = int(max(0, bx - pad)), int(max(0, by - pad))
    x1, y1 = int(min(W, bx + bw + pad)), int(min(H, by + bh + pad))
    crop = np.ascontiguousarray(bgr[y0:y1, x0:x1])
    k = 1.0
    if max(crop.shape[:2]) < 320:          # small faces: enlarge so the detector can see them
        k = 320 / max(1, max(crop.shape[:2]))
        crop = cv2.resize(crop, (max(1, round(crop.shape[1] * k)), max(1, round(crop.shape[0] * k))),
                          interpolation=cv2.INTER_CUBIC)
    ch, cw = crop.shape[:2]
    best, best_iou = None, 0.15
    old_thr = getattr(eng, "threshold", None)
    old_min = None
    try:
        if old_thr is not None:
            eng.threshold = min(old_thr, 0.45)
            det = getattr(eng, "det", None)
            if det is not None and hasattr(det, "setScoreThreshold"):
                det.setScoreThreshold(eng.threshold)
        old_min = getattr(eng, "min_face_px", None)
        if old_min is not None:
            eng.min_face_px = 8
        li = LoadedImage(crop, cw, ch, 1, None)
        li.dark_seen = -1
        _, faces = eng.process_image(li)
    except Exception:
        faces = []
    finally:
        if old_thr is not None:
            eng.threshold = old_thr
            det = getattr(eng, "det", None)
            if det is not None and hasattr(det, "setScoreThreshold"):
                det.setScoreThreshold(old_thr)
        if getattr(eng, "min_face_px", None) is not None and old_min is not None:
            eng.min_face_px = old_min
    for f in faces:
        fx, fy = x0 + f["x"] * cw / k, y0 + f["y"] * ch / k
        fw, fh = f["w"] * cw / k, f["h"] * ch / k
        iou = _iou_px((fx, fy, fw, fh), (bx, by, bw, bh))
        if iou > best_iou:
            best, best_iou = (f, fx, fy, fw, fh), iou
    if best:
        f, fx, fy, fw, fh = best
        return {"x": fx / W, "y": fy / H, "w": fw / W, "h": fh / H, "score": f["score"],
                "px": int(min(fw, fh) * img.width / W), "emb": f["emb"], "thumb": f["thumb"],
                "quality": f.get("quality"), "clarity": f.get("clarity"), "t": t}
    return {"x": box[0], "y": box[1], "w": box[2], "h": box[3], "score": 0.0,
            "px": int(min(bw, bh) * img.width / W), "emb": None,
            "thumb": FaceEngine.thumbnail(bgr, (bx, by, bw, bh)), "quality": None, "t": t}


# same person in two frames of one video if their fingerprints are at least this alike
VIDEO_SAME = {"sface": 0.40, "arcface": 0.30, "adaface": 0.30}


def process_video(eng, path):
    """Faces in a video: look at frames spread through it and keep each person once (their
    clearest frame). Every face remembers its frame time in "t"."""
    frames, W, H, _dur = video_frames(path)
    model = getattr(eng, "model", "sface") if type(eng).__name__.startswith("HQ") else "sface"
    found = []
    for t, bgr in frames:
        li = LoadedImage(bgr, W, H, 1, None)
        li.dark_seen = -1                # (the frames chosen are already the well-lit ones)
        _img, faces = eng.process_image(li)
        for f in faces:
            f["t"] = t
            found.append(f)
    found.sort(key=lambda f: -((f.get("clarity") if f.get("clarity") is not None else 0.5) * 1000
                               + f["score"] * min(f["px"], 900)))
    keep = []
    for f in found:
        e = f["emb"] / (np.linalg.norm(f["emb"]) or 1)
        if any(float(e @ (k["emb"] / (np.linalg.norm(k["emb"]) or 1))) >= VIDEO_SAME[model] for k in keep):
            continue          # someone already kept from a clearer frame
        keep.append(f)
    cover = keep[0]["t"] if keep else frames[len(frames) // 2][0]
    img = LoadedImage(None, W, H, 1, None)
    img.cover_t = cover
    return img, keep


def face_crop(path, box, size):
    """A sharp face crop straight from the photo (used for large thumbnails).
    box = (x, y, w, h) or, for a video, (x, y, w, h, frame seconds)."""
    t = box[4] if len(box) > 4 else None
    img = load_image(path, max_side=max(1600, size * 12), t=t)
    h, w = img.bgr.shape[:2]
    x, y, fw, fh = box[:4]
    data = FaceEngine.thumbnail(img.bgr, (x * w, y * h, fw * w, fh * h), size)
    return data


def photo_thumb(path, size, t=None):
    """A square thumbnail of the whole photo (for photos where no face was found)."""
    img = load_image(path, max_side=max(256, size * 2), t=t)
    bgr = img.bgr
    h, w = bgr.shape[:2]
    k = size / max(w, h)
    small = cv2.resize(bgr, (max(1, round(w * k)), max(1, round(h * k))), interpolation=cv2.INTER_AREA)
    sh, sw = small.shape[:2]
    canvas = np.full((size, size, 3), 40, np.uint8)
    y0, x0 = (size - sh) // 2, (size - sw) // 2
    canvas[y0:y0 + sh, x0:x0 + sw] = small
    ok, buf = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return buf.tobytes() if ok else b""


def store_data_dir():
    import store
    return store.data_dir()
