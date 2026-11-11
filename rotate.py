"""Turning photos a quarter turn left or right (the format inside the file decides how).

JPEG: lossless - only the EXIF orientation flag changes (every viewer, Windows included,
turns the picture by it); the picture data is never re-compressed.
PNG, BMP, TIFF, WebP and single-frame GIF: the pixels are turned and the file saved again
(PNG/BMP/TIFF/GIF stay lossless; WebP is saved at high quality, or lossless if it was).
Not turned: RAW files, HEIC/AVIF, animated GIFs and videos (FaceSort says so instead)."""
import os
import struct

from PIL import Image, ImageOps

LOSSLESS_EXT = {".jpg", ".jpeg", ".jfif", ".jpe"}
PIXEL_EXT = {".png", ".bmp", ".tif", ".tiff", ".webp", ".gif", ".heic", ".heif", ".avif"}
# what's really inside the file decides (a ".jpg" from a website is often a WebP or PNG)
PIXEL_FORMATS = {"PNG", "BMP", "TIFF", "WEBP", "GIF", "HEIF", "AVIF"}
RAW_EXT = {".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".raf", ".orf", ".rw2", ".rwl",
           ".pef", ".ptx", ".dng", ".srw", ".x3f", ".3fr", ".fff", ".iiq", ".erf", ".mef", ".mos", ".mrw",
           ".kdc", ".dcr", ".k25", ".raw", ".gpr"}


def can_rotate(path):
    return os.path.splitext(path)[1].lower() not in RAW_EXT


def real_format(path):
    """The file's actual picture format ("JPEG", "PNG", "WEBP"...), whatever its name says."""
    with open(path, "rb") as f:
        head = f.read(16)
    if head[:2] == b"\xff\xd8":
        return "JPEG"
    try:
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except Exception:
            pass
        with Image.open(path) as im:
            return (im.format or "").upper()
    except Exception:
        return None


# ---- EXIF orientation arithmetic: which flag shows the picture turned a further quarter turn
def _orientation_table():
    base = Image.new("L", (3, 2))
    base.putdata([1, 2, 3, 4, 5, 6])
    shown = {}
    for o in range(1, 9):
        ex = Image.Exif()
        ex[0x0112] = o
        im = base.copy()
        im.info["exif"] = ex.tobytes()
        im.getexif()[0x0112] = o
        shown[o] = ImageOps.exif_transpose(im)
    table = {}
    for o in range(1, 9):
        for cw in (True, False):
            want = shown[o].transpose(Image.Transpose.ROTATE_270 if cw else Image.Transpose.ROTATE_90)
            table[(o, cw)] = next(n for n in range(1, 9)
                                  if shown[n].size == want.size and list(shown[n].getdata()) == list(want.getdata()))
    return table


_TABLE = None


def next_orientation(o, clockwise):
    global _TABLE
    if _TABLE is None:
        _TABLE = _orientation_table()
    return _TABLE[(o if o in range(1, 9) else 1, bool(clockwise))]


# ---- lossless JPEG: change the orientation flag in place
def _segments(data):
    """[(marker, start, length incl. marker)] of the JPEG header segments up to the picture data."""
    if data[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG file")
    out, i = [], 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            raise ValueError("damaged JPEG header")
        marker = data[i + 1]
        if marker == 0xDA or marker == 0xD9:          # start of picture data / end
            break
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            i += 2
            continue
        ln = struct.unpack(">H", data[i + 2:i + 4])[0]
        out.append((marker, i, ln + 2))
        i += ln + 2
    return out


def _patch_orientation(tiff, rel, cw):
    """Find tag 0x0112 in IFD0 of a TIFF/EXIF block; return (absolute offset of its value, endian)."""
    endian = "<" if tiff[:2] == b"II" else ">"
    ifd = struct.unpack(endian + "I", tiff[4:8])[0]
    n = struct.unpack(endian + "H", tiff[ifd:ifd + 2])[0]
    for k in range(n):
        e = ifd + 2 + 12 * k
        tag, typ, cnt = struct.unpack(endian + "HHI", tiff[e:e + 8])
        if tag == 0x0112 and typ == 3 and cnt == 1:
            return rel + e + 8, endian
    return None, endian


def rotate_jpeg(path, clockwise):
    with open(path, "rb") as f:
        data = bytearray(f.read())
    segs = _segments(data)
    exif_seg = next(((st, ln) for m, st, ln in segs if m == 0xE1 and data[st + 4:st + 10] == b"Exif\x00\x00"), None)
    if exif_seg:
        st, ln = exif_seg
        tiff_at = st + 10
        tiff = bytes(data[tiff_at:st + ln])
        at, endian = _patch_orientation(tiff, tiff_at, clockwise)
        if at is not None:
            cur = struct.unpack(endian + "H", data[at:at + 2])[0]
            data[at:at + 2] = struct.pack(endian + "H", next_orientation(cur, clockwise))
            _write(path, bytes(data))
            return
        # EXIF without an orientation tag: rebuild that block with one added
        ex = Image.Exif()
        ex.load(bytes(data[st + 4:st + ln]))
        ex[0x0112] = next_orientation(1, clockwise)
        payload = ex.tobytes()
        if not payload.startswith(b"Exif"):
            payload = b"Exif\x00\x00" + payload
        seg = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
        _write(path, bytes(data[:st]) + seg + bytes(data[st + ln:]))
        return
    # no EXIF at all: add a small one right after the start (and the JFIF header, if any)
    ex = Image.Exif()
    ex[0x0112] = next_orientation(1, clockwise)
    payload = ex.tobytes()
    if not payload.startswith(b"Exif"):
        payload = b"Exif\x00\x00" + payload
    seg = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    at = 2
    if segs and segs[0][0] == 0xE0:
        at = segs[0][1] + segs[0][2]
    _write(path, bytes(data[:at]) + seg + bytes(data[at:]))


def _write(path, data):
    tmp = path + ".fs-rotate.tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def rotate_pixels(path, clockwise):
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception:
        pass
    with Image.open(path) as im:
        if getattr(im, "n_frames", 1) > 1:
            raise ValueError("animated pictures can't be turned")
        fmt = im.format
        ext = {"PNG": ".png", "WEBP": ".webp", "TIFF": ".tif", "GIF": ".gif", "BMP": ".bmp",
               "HEIF": ".heic", "AVIF": ".avif"}.get((fmt or "").upper(), "")
        info = dict(im.info)
        im.load()
        exif = im.getexif()
        upright = ImageOps.exif_transpose(im)          # bake in any old flag first
        out = upright.transpose(Image.Transpose.ROTATE_270 if clockwise else Image.Transpose.ROTATE_90)
    kw = {}
    if info.get("icc_profile"):
        kw["icc_profile"] = info["icc_profile"]
    if exif:
        exif[0x0112] = 1
        kw["exif"] = exif.tobytes()
    if ext == ".png":
        from PIL.PngImagePlugin import PngInfo
        meta = PngInfo()
        for k, v in info.items():                      # keep text chunks (e.g. AI generation settings)
            if isinstance(v, str) and k not in ("exif", "icc_profile"):
                meta.add_text(k, v)
        kw["pnginfo"] = meta
    elif ext == ".webp":
        kw["lossless"] = bool(info.get("lossless"))
        kw["quality"] = 100 if kw["lossless"] else 95
        kw["method"] = 6
    elif ext in (".tif", ".tiff"):
        if info.get("compression") and info["compression"] != "raw":
            kw["compression"] = info["compression"]
        kw.pop("exif", None)
    elif ext == ".gif":
        kw.pop("exif", None)
        if "transparency" in info:
            kw["transparency"] = info["transparency"]
    elif ext in (".heic", ".avif"):
        kw["quality"] = 95
    tmp = path + ".fs-rotate.tmp"
    out.save(tmp, format=fmt, **kw)
    os.replace(tmp, path)


def rotate_file(path, clockwise=True):
    """Turn the file a quarter turn. Raises ValueError (with a reason) when it can't.
    Goes by what's really inside the file, not its name."""
    if os.path.splitext(path)[1].lower() in RAW_EXT:
        raise ValueError("RAW files can't be turned (their sidecar / editor handles that)")
    fmt = real_format(path)
    if fmt == "JPEG":
        rotate_jpeg(path, clockwise)
    elif fmt in PIXEL_FORMATS:
        rotate_pixels(path, clockwise)
    elif fmt == "MPO":                                 # some cameras' JPEGs (with a 3D / depth part)
        rotate_jpeg(path, clockwise)
    else:
        raise ValueError(f"this kind of file ({fmt or 'unknown'}) can't be turned here")


def turn_box(x, y, w, h, clockwise):
    """A box (fractions of the picture) after the picture turns a quarter turn."""
    if clockwise:
        return 1 - (y + h), x, h, w
    return y, 1 - (x + w), h, w
