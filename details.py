"""What's shown in the Details panel: file details, then EXIF / other metadata."""
import datetime


def _when(t):
    if not t:
        return None
    try:
        return datetime.datetime.fromtimestamp(t).strftime("%d %b %Y, %H:%M")
    except (OSError, OverflowError, ValueError):
        return None


def _size(n):
    if n is None:
        return None
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.0f} {unit}" if unit == "bytes" else f"{n:,.1f} {unit}"
        n /= 1024


def _clean(v, limit=300):
    if isinstance(v, bytes):
        try:
            v = v.decode("utf-8", "replace")
        except Exception:
            v = repr(v[:40])
    if isinstance(v, tuple) and len(v) <= 6:
        v = ", ".join(str(_clean(x, 60)) for x in v)
    v = str(v).replace("\x00", "").strip()
    return v if len(v) <= limit else v[:limit] + "…"


def _gps(g):
    try:
        def deg(x):
            d, m, s = [float(a) for a in x]
            return d + m / 60 + s / 3600
        lat, lon = deg(g[2]), deg(g[4])
        if g.get(1) == "S":
            lat = -lat
        if g.get(3) == "W":
            lon = -lon
        return f"{lat:.5f}, {lon:.5f}"
    except Exception:
        return None


def metadata(path):
    """[(name, value)] - EXIF (camera, lens, exposure, GPS...), PNG text (e.g. AI generation
    settings), or video properties."""
    import engine
    out = []
    if engine.is_video(path):
        try:
            import cv2
            cap = cv2.VideoCapture(path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 0
            n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            cc = int(cap.get(cv2.CAP_PROP_FOURCC) or 0)
            cap.release()
            out += [("Video size", f"{w} × {h}"), ("Frames per second", f"{fps:.2f}" if fps else None),
                    ("Length", f"{int(n / fps // 60)}:{int(n / fps % 60):02d}" if fps and n else None),
                    ("Codec", "".join(chr((cc >> 8 * i) & 0xFF) for i in range(4)).strip() if cc else None)]
        except Exception:
            pass
        return [(k, v) for k, v in out if v]
    try:
        from io import BytesIO
        from PIL import ExifTags, Image
        if engine.is_raw(path) and engine.RAW_OK:
            import rawpy
            with rawpy.imread(path) as r:
                th = r.extract_thumb()
                out.append(("Camera RAW", f"{r.sizes.width} × {r.sizes.height} sensor pixels"))
                if th.format == rawpy.ThumbFormat.JPEG:
                    im = Image.open(BytesIO(th.data))
                else:
                    return out
        else:
            im = Image.open(path)
        with im:
            exif = im.getexif()
            tags = dict(exif)
            for ifd in (0x8769, 0xA005):            # Exif and Interop sub-blocks
                try:
                    tags.update(exif.get_ifd(ifd))
                except Exception:
                    pass
            for k, v in sorted(tags.items(), key=lambda kv: ExifTags.TAGS.get(kv[0], str(kv[0]))):
                name = ExifTags.TAGS.get(k)
                if not name or name in ("MakerNote", "UserComment", "ExifOffset", "GPSInfo", "PrintImageMatching",
                                        "InteropOffset", "ComponentsConfiguration") or k >= 0xC000 and name is None:
                    continue
                if name.startswith("XP"):
                    try:
                        v = bytes(v).decode("utf-16-le", "ignore")
                    except Exception:
                        pass
                out.append((name, _clean(v)))
            try:
                g = exif.get_ifd(0x8825)
                if g:
                    out.append(("GPS position", _gps(g)))
            except Exception:
                pass
            for k, v in (im.info or {}).items():          # PNG text, comments, ICC...
                if k in ("exif", "icc_profile", "xmp", "transparency", "dpi", "gamma", "aspect"):
                    continue
                if isinstance(v, (str, bytes)):
                    out.append((str(k), _clean(v, 1200)))
            if im.info.get("icc_profile"):
                out.append(("Colour profile", "embedded"))
    except Exception as e:
        out.append(("Couldn't read the metadata", str(e)[:120]))
    return [(k, v) for k, v in out if v not in (None, "")]
