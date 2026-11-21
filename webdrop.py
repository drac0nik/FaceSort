"""Pictures dragged in from a web browser (1.54).

A browser offers what you drag in up to three ways; the best one is used:
  1. the file itself (Chrome / Edge on Windows hand over the real image, as "Save image as" would)
  2. the picture's web address - FaceSort downloads that one picture (just like the browser did to
     show it; nothing is opened)
  3. the picture as the browser drew it (when the download doesn't work)
The pictures are saved in the "Web drops" folder and go into Recently dropped like any other drop.
Only http(s) addresses are fetched, and only pictures and videos are kept."""
import base64
import os

import fsx
import re
import struct
import urllib.parse
import urllib.request
from email.message import Message
from io import BytesIO

FILE_CONTENTS = 'application/x-qt-windows-mime;value="FileContents"'
FILE_GROUP = 'application/x-qt-windows-mime;value="FileGroupDescriptorW"'
MOZ_URL = "text/x-moz-url"
MOZ_PROMISE = "application/x-moz-file-promise-url"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
LIMIT = 500 << 20
IMAGE_CT = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp",
            "image/avif": ".avif", "image/heic": ".heic", "image/heif": ".heic", "image/bmp": ".bmp",
            "image/tiff": ".tif", "video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov"}


# ------------------------------------------------------------------ what was dropped
def group_names(data):
    """File names from a Windows FileGroupDescriptorW."""
    try:
        n = struct.unpack("<I", data[:4])[0]
        out = []
        for i in range(min(n, 64)):
            off = 4 + i * 592 + 72
            raw = data[off:off + 520]
            out.append(raw.decode("utf-16-le").split("\x00", 1)[0])
        return out
    except Exception:
        return []


def img_sources(text):
    import html as _html
    return [_html.unescape(m) for m in re.findall(r"""<img[^>]+src\s*=\s*["']([^"']+)["']""", text or "", re.I)]


def payload(md):
    """Everything usable from a QMimeData, read now (it's gone after the drop):
    {"files": [(bytes, name)], "urls": [...], "image": PNG bytes or None, "page": page address or None}."""
    out = {"files": [], "urls": [], "image": None, "page": None}
    fmts = set(md.formats())
    if FILE_CONTENTS in fmts:
        data = bytes(md.data(FILE_CONTENTS))
        names = group_names(bytes(md.data(FILE_GROUP))) if FILE_GROUP in fmts else []
        if data:
            out["files"].append((data, names[0] if names else ""))
    urls = []
    if md.hasHtml():
        urls += img_sources(md.html())
    if MOZ_PROMISE in fmts:
        urls.append(bytes(md.data(MOZ_PROMISE)).decode("utf-8", "replace").strip())
    if MOZ_URL in fmts:
        raw = bytes(md.data(MOZ_URL))
        txt = raw.decode("utf-16-le", "replace") if raw[1:2] == b"\x00" else raw.decode("utf-8", "replace")
        urls += [ln.strip() for ln in txt.splitlines()[:1] if ln.strip()]
    if md.hasUrls():
        urls += [u.toString() for u in md.urls() if not u.isLocalFile()]
    if md.hasText() and not urls:
        t = md.text().strip()
        if re.match(r"^(https?://|data:image/)\S+$", t):
            urls.append(t)
    seen = []
    for u in urls:
        if (u.startswith(("http://", "https://")) or u.startswith("data:image/")) and u not in seen:
            seen.append(u)
    # a picture's address before a page's (a picture inside a link gives both)
    seen.sort(key=lambda u: 0 if u.startswith("data:") or _looks_like_media(u) else 1)
    out["urls"] = seen
    if md.hasImage():
        try:
            from PySide6.QtCore import QBuffer, QIODevice
            img = md.imageData()
            buf = QBuffer()
            buf.open(QIODevice.WriteOnly)
            img.save(buf, "PNG")
            out["image"] = bytes(buf.data())
        except Exception:
            out["image"] = None
    return out


def has_web(md):
    if md.hasUrls() and any(not u.isLocalFile() and u.scheme() in ("http", "https") for u in md.urls()):
        return True
    fmts = set(md.formats())
    return bool(FILE_CONTENTS in fmts or MOZ_URL in fmts or MOZ_PROMISE in fmts or md.hasImage()
                or (md.hasHtml() and "<img" in md.html().lower()))


def _looks_like_media(url):
    path = urllib.parse.urlparse(url).path.lower()
    return os.path.splitext(path)[1] in set(IMAGE_CT.values()) | {".jpeg", ".jfif", ".tiff", ".m4v", ".mkv"}


# ------------------------------------------------------------------ downloading
def sniff(data):
    """The file ending its first bytes say, or None if it isn't a picture / video."""
    h = data[:32]
    if h.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if h.startswith(b"\x89PNG"):
        return ".png"
    if h[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if h[:4] == b"RIFF" and h[8:12] == b"WEBP":
        return ".webp"
    if h[4:8] == b"ftyp":
        brand = h[8:12]
        if brand in (b"avif", b"avis"):
            return ".avif"
        if brand in (b"heic", b"heix", b"mif1", b"msf1", b"hevc"):
            return ".heic"
        if brand == b"qt  ":
            return ".mov"
        return ".mp4"
    if h[:4] == b"\x1a\x45\xdf\xa3":
        return ".webm"
    if h[:2] == b"BM":
        return ".bmp"
    if h[:4] in (b"II*\x00", b"MM\x00*"):
        return ".tif"
    return None


def download(url, timeout=30):
    """(bytes, file name) of a dropped address; raises ValueError with a reason."""
    if url.startswith("data:"):
        head, _, body = url.partition(",")
        data = base64.b64decode(body) if ";base64" in head else urllib.parse.unquote_to_bytes(body)
        ext = sniff(data)
        if not ext:
            raise ValueError("it isn't a picture")
        return data, "web image" + ext
    parts = urllib.parse.urlparse(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError("only web addresses can be fetched")
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "image/avif,image/webp,image/apng,image/*,video/*,*/*;q=0.8",
        "Referer": f"{parts.scheme}://{parts.netloc}/"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype.startswith("text/"):
            raise ValueError("the site sent a web page instead of the picture (it may need you to be signed in)")
        buf = BytesIO()
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            buf.write(chunk)
            if buf.tell() > LIMIT:
                raise ValueError("it's too big")
        data = buf.getvalue()
        disp = r.headers.get("Content-Disposition") or ""
    ext = sniff(data) or IMAGE_CT.get(ctype)
    if not ext or not sniff(data):
        raise ValueError("what came back isn't a picture or video")
    name = ""
    if disp:
        m = Message()
        m["content-disposition"] = disp
        name = m.get_filename() or ""
    if not name:
        name = os.path.basename(urllib.parse.unquote(parts.path)) or parts.netloc
    return data, name


def clean_name(name, ext):
    import renamer
    stem = os.path.splitext(name)[0] if os.path.splitext(name)[1].lower() in (
        set(IMAGE_CT.values()) | {".jpeg", ".jfif", ".tiff", ".php", ".aspx"}) else name
    stem = renamer.clean(stem)[:120] or "web image"
    return stem + ext


def folder(settings):
    """Where pictures dropped from the web are saved."""
    if settings.get("web_drop_folder"):
        return settings["web_drop_folder"]
    import libmove
    lib = libmove.library(settings)
    if lib and settings.get("import_into_library", True):        # (1.57.3) straight into ~Unsorted
        return os.path.join(libmove.unsorted_dir(lib), "Web drops")
    root = settings.get("library_root") or (settings.get("folders") or [None])[0]
    if root:
        return os.path.join(root, "Web drops")
    home = os.path.expanduser("~")
    pics = os.path.join(home, "Pictures")
    return os.path.join(pics if os.path.isdir(pics) else home, "FaceSort web drops")


def _free(path):
    base, ext = os.path.splitext(path)
    cand, i = path, 2
    while fsx.exists(cand):
        cand = f"{base} ({i}){ext}"
        i += 1
    return cand


def save_all(pl, dest, fetch=download):
    """Save what was dropped. Returns {"saved": [paths], "errors": [(what, reason)], "how": [...]}."""
    os.makedirs(dest, exist_ok=True)
    res = {"saved": [], "errors": [], "how": []}

    def keep(data, name, how):
        ext = sniff(data)
        if not ext:
            return False
        path = _free(os.path.join(dest, clean_name(name or "web image", ext)))
        with open(path, "wb") as f:
            f.write(data)
        res["saved"].append(path)
        res["how"].append(how)
        return True

    for data, name in pl.get("files", []):
        if keep(data, name, "file"):
            return res
    for url in pl.get("urls", []):
        try:
            data, name = fetch(url)
            if keep(data, name, "download"):
                return res
        except Exception as e:
            res["errors"].append((url, str(e) or e.__class__.__name__))
    if pl.get("image") and keep(pl["image"], "web image", "picture"):
        return res
    return res
