"""What Max Image Saver's background worker did, done by FaceSort (1.56): fetching pages and
images with your browser session, measuring images, Flickr's size lookups, and the downloads -
with the date taken written into the file and the folders the extension builds kept.

Network requests carry the cookies of FaceSort's browser (so signed-in galleries work), its
user agent, and a Referer from the page they came from."""
import json
import os
import re
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from io import BytesIO

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/140.0.0.0 Safari/537.36")
FLICKR_ORDER = ["o", "6k", "5k", "4k", "3k", "k", "h", "l", "b", "c", "z"]


# ------------------------------------------------------------------ cookies
class CookieJar:
    """The browser's cookies, kept in step with its cookie store (domain, path, name -> value)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.c = {}

    def add(self, domain, path, name, value, secure=False, expires=None):
        with self.lock:
            self.c[(domain.lower(), path or "/", name)] = (value, secure, expires)

    def remove(self, domain, path, name):
        with self.lock:
            self.c.pop((domain.lower(), path or "/", name), None)

    def header(self, url):
        p = urllib.parse.urlparse(url)
        host, path, https = (p.hostname or "").lower(), p.path or "/", p.scheme == "https"
        now = time.time()
        out = []
        with self.lock:
            for (dom, cpath, name), (value, secure, exp) in self.c.items():
                d = dom.lstrip(".")
                if not (host == d or host.endswith("." + d)):
                    continue
                if not path.startswith(cpath) or (secure and not https) or (exp and exp < now):
                    continue
                out.append(f"{name}={value}")
        return "; ".join(out)


JAR = CookieJar()
USER_AGENT = [UA]


def request(url, referer=None, timeout=30, head=None, limit=None):
    """(status, final url, content type, bytes)"""
    if url.startswith("data:"):
        import base64
        h, _, body = url.partition(",")
        data = base64.b64decode(body) if ";base64" in h else urllib.parse.unquote_to_bytes(body)
        return 200, url, (h[5:].split(";")[0] or "application/octet-stream"), data
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https"):
        raise ValueError("only web addresses")
    hdr = {"User-Agent": USER_AGENT[0], "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"}
    if referer:
        try:
            r = urllib.parse.urlparse(referer)
            if r.scheme in ("http", "https"):
                hdr["Referer"] = f"{r.scheme}://{r.netloc}/"
        except ValueError:
            pass
    ck = JAR.header(url)
    if ck:
        hdr["Cookie"] = ck
    if head:
        hdr.update(head)
    req = urllib.request.Request(url, headers=hdr)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            ctype = (res.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            data = res.read(limit) if limit else res.read()
            return res.status, res.geturl(), ctype, data
    except urllib.error.HTTPError as e:
        return e.code, url, "", b""


# ------------------------------------------------------------------ handlers (chrome.runtime.sendMessage)
def handle(msg):
    t = msg.get("type")
    ref = msg.get("referer")
    if t == "fetch-text":
        st, final, ctype, data = request(msg["url"], ref, limit=4_000_000)
        if st >= 400:
            return {"ok": False}
        if ctype.startswith("image/"):
            return {"ok": True, "type": ctype, "url": final, "text": ""}
        return {"ok": True, "type": ctype, "url": final, "text": data.decode("utf-8", "replace")}
    if t == "fetch-json":
        st, _final, _ct, data = request(msg["url"], ref)
        try:
            return {"ok": st < 400, "json": json.loads(data.decode("utf-8", "replace"))}
        except ValueError:
            return {"ok": False, "json": None}
    if t == "probe":
        return {"ok": True, "results": probe_all((msg.get("urls") or [])[:24], ref)}
    if t == "flickr-sizes":
        return {"ok": True, "urls": flickr_sizes(msg.get("urls") or [])}
    if t in ("cap-relay", "save-link"):
        return {"ok": True}
    return None                                   # (download: done by the caller, which knows where)


def image_size(data):
    try:
        from PIL import Image
        with Image.open(BytesIO(data)) as im:
            return im.size
    except Exception:
        return None


def probe_one(url, referer):
    try:
        st, _f, ctype, data = request(url, referer, timeout=15)
        if st >= 400 or (ctype and not ctype.startswith("image/")):
            return None
        wh = image_size(data)
        return {"url": url, "w": wh[0], "h": wh[1]} if wh else None
    except Exception:
        return None


def probe_all(urls, referer):
    out = [None] * len(urls)

    def one(i, u):
        out[i] = probe_one(u, referer)
    ts = [threading.Thread(target=one, args=(i, u), daemon=True) for i, u in enumerate(urls)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(20)
    return out


_flickr = {}


def flickr_sizes(urls):
    found = []
    for u in urls:
        m = re.search(r"staticflickr\.com/(?:\d+/)*(\d{5,})_[0-9a-f]+", u)
        if m:
            pid = m.group(1)
            if pid not in _flickr:
                _flickr[pid] = lookup_flickr(pid)
            found += _flickr[pid]
    return found


def lookup_flickr(pid):
    pages = [f"https://www.flickr.com/photo.gne?id={pid}"]
    found = set()
    for page in pages:
        try:
            st, final, _ct, data = request(page, "https://www.flickr.com/")
            if st >= 400:
                continue
            html = data.decode("utf-8", "replace").replace("\\/", "/")
            html = re.sub(r"\\u002[fF]", "/", html)
            rx = re.compile(r"(?:https?:)?//(?:live|farm\d+|c\d+)\.staticflickr\.com/(?:\d+/)+" + pid +
                            r"_[0-9a-f]+(?:_[a-z0-9]{1,2})?\.(?:jpg|png|gif)", re.I)
            for m in rx.findall(html):
                found.add("https:" + m if m.startswith("//") else m)
            if not any(re.search(r"_o\.\w+$", u) for u in found):
                own = re.search(r"flickr\.com/photos/([^/]+)/\d+", final)
                if own and len(pages) == 1:
                    pages.append(f"https://www.flickr.com/photos/{own.group(1)}/{pid}/sizes/o/")
        except Exception:
            pass

    def rank(u):
        m = re.search(r"_([a-z0-9]{1,2})\.\w+$", u)
        s = m.group(1) if m else "m"
        return FLICKR_ORDER.index(s) if s in FLICKR_ORDER else 99
    return sorted(found, key=rank)[:8]


# ------------------------------------------------------------------ saving
def sniff_ext(data, ctype=""):
    import webdrop
    e = webdrop.sniff(data)
    if e:
        return e.lstrip(".")
    sub = (ctype.split("/")[1] if "/" in ctype else "") or "jpg"
    return sub.replace("jpeg", "jpg").replace("svg+xml", "svg")


def name_prefix(date):
    m = re.match(r"^(\d{4})[-:](\d{2})[-:](\d{2})[ T](\d{2}):(\d{2})", str(date or ""))
    if not m or m.group(1) == "0000" or m.group(2) == "00":
        return ""
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}_{m.group(4)}{m.group(5)}_"


def exif_date(date):
    m = re.match(r"^(\d{4})[-:](\d{2})[-:](\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?", str(date or ""))
    if not m:
        return None
    return f"{m.group(1)}:{m.group(2)}:{m.group(3)} {m.group(4) or '00'}:{m.group(5) or '00'}:{m.group(6) or '00'}"


def jpeg_date_of(data):
    try:
        from PIL import Image
        with Image.open(BytesIO(data[:524288] if len(data) > 524288 else data)) as im:
            ex = im.getexif()
            sub = ex.get_ifd(0x8769)
            return sub.get(0x9003) or sub.get(0x9004) or ex.get(0x0132) or ""
    except Exception:
        return ""


def embed_jpeg_date(data, date):
    """The date taken written into a JPEG that has none (a new EXIF block; the picture isn't
    re-compressed). Returns the bytes unchanged when it has a date already or isn't a JPEG."""
    d = exif_date(date)
    if not d or data[:2] != b"\xff\xd8" or jpeg_date_of(data):
        return data, bool(d and jpeg_date_of(data))
    try:
        from PIL import Image
        ex = Image.Exif()
        ex[0x0132] = d
        ex.get_ifd(0x8769)[0x9003] = d
        ex.get_ifd(0x8769)[0x9004] = d
        blob = ex.tobytes()
        if not blob.startswith(b"Exif"):
            blob = b"Exif\x00\x00" + blob
        if len(blob) + 2 > 65535:
            return data, False
        # after SOI and any JFIF APP0
        i = 2
        if data[2:4] == b"\xff\xe0":
            i = 4 + struct.unpack(">H", data[4:6])[0]
        seg = b"\xff\xe1" + struct.pack(">H", len(blob) + 2) + blob
        return data[:i] + seg + data[i:], True
    except Exception:
        return data, False


def clean_rel(path):
    """A relative path from the extension, safe on Windows."""
    import renamer
    parts = [renamer.clean(p) for p in re.split(r"[\\/]+", path or "") if p not in ("", ".", "..")]
    parts = [p for p in parts if p][:8]
    return os.path.join(*parts) if parts else ""


def free_path(path):
    base, ext = os.path.splitext(path)
    cand, i = path, 2
    while os.path.exists(cand):
        cand = f"{base} ({i}){ext}"
        i += 1
    return cand


def save_item(item, base, referer=None):
    """Download one item into base / its own relative path. Returns (path, dated)."""
    url = item["url"]
    st, final, ctype, data = request(url, item.get("referer") or referer, timeout=60)
    if st >= 400 or not data:
        raise RuntimeError(f"HTTP {st}")
    if ctype.startswith("text/"):
        raise RuntimeError("the site sent a web page, not the picture")
    rel = item.get("filename") or ""
    if not rel:
        rel = os.path.basename(urllib.parse.unquote(urllib.parse.urlparse(final).path)) or f"image-{int(time.time())}"
    rel = clean_rel(rel)
    ext = sniff_ext(data, ctype)
    if item.get("autoExt") or not os.path.splitext(rel)[1]:
        rel = rel + "." + ext
    date = item.get("dateTaken") or ""
    dated = False
    if date and ext == "jpg":
        data, dated = embed_jpeg_date(data, date)
    if item.get("datePrefix", True):
        pre = name_prefix(date or (exif_date(jpeg_date_of(data)) if ext == "jpg" else ""))
        if pre:
            head, tail = os.path.split(rel)
            if not tail.startswith(pre):
                rel = os.path.join(head, pre + tail)
    path = free_path(os.path.join(base, rel))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".part", "wb") as f:
        f.write(data)
    os.replace(path + ".part", path)
    tags = [str(x).strip() for x in (item.get("tags") or []) if str(x).strip()]
    if tags and ext == "jpg":
        # (1.57.1) e.g. MM+'s credited names - written as the photo's keywords, like the extension does,
        # so FaceSort reads them when the picture comes in
        try:
            import xmp
            xmp.update_file(path, tags, [], keep_mtime=False)
        except Exception:
            pass
    return path, dated


def item_key(url):
    """The same picture next time (its address without the changing parts)."""
    m = re.search(r"staticflickr\.com/(?:\d+/)*(\d{5,})_", url or "")
    if m:
        return "flickr:" + m.group(1)
    p = urllib.parse.urlparse(url or "")
    path = re.sub(r"/(?:\d{2,4}x\d{2,4}|w\d{2,4}|s\d{2,4})/", "/", p.path)
    return (p.hostname or "") + path


def run_bulk(items, base, referer, progress, cancelled, skip=None, threads=3):
    """The extension's bulk queue: three at a time. skip(item) -> True leaves an item out
    (already downloaded before). Returns (saved paths, done, failed, dated, skipped)."""
    todo = [it for it in items if not (skip and skip(it))]
    skipped = len(items) - len(todo)
    lock = threading.Lock()
    state = {"next": 0, "done": 0, "failed": 0, "dated": 0}
    saved = []

    def worker():
        while True:
            if cancelled():
                return
            with lock:
                if state["next"] >= len(todo):
                    return
                it = todo[state["next"]]
                state["next"] += 1
            try:
                p, dated = save_item(it, base, referer)
                with lock:
                    saved.append((it, p))
                    state["done"] += 1
                    state["dated"] += int(dated)
            except Exception:
                with lock:
                    state["failed"] += 1
            progress(state["done"], state["failed"], len(todo))
    ts = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, threads))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return saved, state["done"], state["failed"], state["dated"], skipped
