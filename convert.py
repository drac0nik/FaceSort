"""Making the library open everywhere: which file becomes what when you Sort into folders.

  real JPEG ................................. stays as it is
  WebP / HEIC / AVIF / BMP / TIFF photo ..... JPEG, quality 95 (also when misnamed ".jpg")
  anything with see-through parts, and
  screenshots / graphics .................... PNG (lossless)
  PNG ....................................... stays (a PNG named ".jpg" just gets ".png")
  animated GIF / WebP ....................... stays as it is
  RAW ....................................... stays (right-click > Make a JPEG copy)

What's really inside the file decides, not its name."""
import os
from io import BytesIO

from PIL import Image, ImageOps

import rotate
import xmp

JPEG_EXT = {".jpg", ".jpeg", ".jfif", ".jpe"}


def _open(path):
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception:
        pass
    return Image.open(path)


def has_transparency(im):
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        a = im.convert("RGBA").getchannel("A")
        lo, _ = a.getextrema()
        return lo < 250
    return False


def looks_like_graphic(im):
    """Screenshots, text, logos, drawings: few colours and big flat areas (unlike a photo)."""
    s = im.convert("RGB")
    s.thumbnail((256, 256))
    colors = s.getcolors(256 * 256) or []
    n = len(colors)
    total = s.width * s.height or 1
    if n < 1500:
        return True
    top = sum(c for c, _ in sorted(colors, reverse=True)[:32])
    return top / total > 0.6               # most of it in a handful of exact colours: a screenshot


def plan_for(path):
    """None (leave it) or ("JPEG" | "PNG", why)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in rotate.RAW_EXT:
        return None
    try:
        import engine
        if engine.is_video(path):
            return None
    except Exception:
        pass
    fmt = rotate.real_format(path)
    if fmt in ("JPEG", "MPO"):
        return None if ext in JPEG_EXT else ("JPEG", "a JPEG with the wrong name")
    if fmt == "PNG":
        return None if ext == ".png" else ("PNG", "a PNG with the wrong name")
    if fmt not in ("WEBP", "HEIF", "AVIF", "BMP", "TIFF", "GIF"):
        return None
    try:
        with _open(path) as im:
            if getattr(im, "n_frames", 1) > 1:
                return None                      # animated: leave it alone
            im.load()
            if has_transparency(im):
                return "PNG", "has see-through parts"
            if looks_like_graphic(im):
                return "PNG", "a screenshot / graphic"
    except Exception:
        return None
    return "JPEG", f"a {fmt.title()} photo"


def new_name(path, target):
    return os.path.splitext(os.path.basename(path))[0] + (".jpg" if target == "JPEG" else ".png")


def converted_bytes(path, target):
    """The file as a real JPEG or PNG: upright, colour profile and EXIF kept, and the names
    from its .xmp sidecar (if any) written inside."""
    with open(path, "rb") as f:
        raw = f.read()
    fmt = rotate.real_format(path)
    if (target == "JPEG" and fmt in ("JPEG", "MPO")) or (target == "PNG" and fmt == "PNG"):
        data = raw                                  # only the name was wrong: not a bit changes
    else:
        with _open(path) as im:
            im.seek(0)
            exif = im.getexif()
            icc = im.info.get("icc_profile")
            im = ImageOps.exif_transpose(im)        # bake the turn in (the new file has no flag)
            if exif:
                exif[0x0112] = 1
            kw = {}
            if icc:
                kw["icc_profile"] = icc
            if exif:
                kw["exif"] = exif.tobytes()
            buf = BytesIO()
            if target == "JPEG":
                if im.mode in ("RGBA", "LA", "P"):
                    rgba = im.convert("RGBA")
                    rgb = Image.new("RGB", rgba.size, (255, 255, 255))
                    rgb.paste(rgba, mask=rgba.getchannel("A"))
                else:
                    rgb = im.convert("RGB")
                rgb.save(buf, "JPEG", quality=95, subsampling=0, **kw)
            else:
                if im.mode not in ("RGB", "RGBA", "L", "LA", "P", "I;16"):
                    im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
                im.save(buf, "PNG", optimize=True, **kw)
            data = buf.getvalue()
    try:
        packet = xmp.read_xmp(path, sidecar=True)
        if packet:
            data = (xmp._jpeg_write if target == "JPEG" else xmp._png_write)(data, packet.encode("utf-8"))
    except Exception:
        pass
    return data


def check(data, target, size):
    """The new file opens, is what it says, and has the same picture size."""
    with Image.open(BytesIO(data)) as im:
        im.load()
        ok = im.format == target and (sorted(ImageOps.exif_transpose(im).size) == sorted(size))
    return ok


def raw_to_jpeg(path, dest):
    """A full-quality JPEG developed from a RAW file (camera white balance), with the
    camera's EXIF (date taken, camera, lens) when the RAW has an embedded preview."""
    import rawpy
    with rawpy.imread(path) as r:
        rgb = r.postprocess(use_camera_wb=True, output_bps=8, no_auto_bright=False)
        exif = None
        try:
            th = r.extract_thumb()
            if th.format == rawpy.ThumbFormat.JPEG:
                with Image.open(BytesIO(th.data)) as t:
                    ex = t.getexif()
                    if ex:
                        ex[0x0112] = 1
                        exif = ex.tobytes()
        except Exception:
            pass
    im = Image.fromarray(rgb)
    kw = {"quality": 95, "subsampling": 0}
    if exif:
        kw["exif"] = exif
    im.save(dest, "JPEG", **kw)
    return dest
