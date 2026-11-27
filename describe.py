"""What a non-photo file is (1.64): a one-line description for the Unassociated files list.

Web shortcuts (.url, .webloc, .desktop) and saved web pages give the address they point to, Windows
shortcuts (.lnk) the file or folder they open, documents their title, music its artist and title,
text files their first line. Archives only say what they are - FaceSort doesn't look inside them.
Everything is read from the first part of the file only, so it stays quick; nothing is opened or run."""
import html
import os
import plistlib
import re
import struct
import urllib.parse

READ = 256 * 1024

LINK_EXT = {".url", ".website", ".webloc", ".desktop"}
PAGE_EXT = {".html", ".htm", ".mht", ".mhtml", ".shtml", ".xhtml"}
TEXT_EXT = {".txt", ".md", ".csv", ".log", ".nfo", ".ini", ".json", ".srt", ".rtf"}
OFFICE_EXT = {".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp"}
MUSIC_EXT = {".mp3"}
TYPE_NAMES = {
    ".zip": "ZIP archive", ".7z": "7-Zip archive", ".rar": "RAR archive", ".tar": "TAR archive",
    ".gz": "GZip archive", ".bz2": "BZip2 archive", ".xz": "XZ archive", ".iso": "Disc image",
    ".pdf": "PDF document", ".doc": "Word document", ".docx": "Word document", ".xls": "Excel spreadsheet",
    ".xlsx": "Excel spreadsheet", ".ppt": "PowerPoint presentation", ".pptx": "PowerPoint presentation",
    ".odt": "OpenDocument text", ".ods": "OpenDocument spreadsheet", ".odp": "OpenDocument presentation",
    ".epub": "EPUB book", ".mobi": "Kindle book", ".txt": "Text file", ".rtf": "Rich text document",
    ".mp3": "MP3 audio", ".wav": "WAV audio", ".flac": "FLAC audio", ".m4a": "M4A audio", ".ogg": "OGG audio",
    ".aac": "AAC audio", ".wma": "WMA audio", ".psd": "Photoshop document", ".ai": "Illustrator document",
    ".xmp": "XMP sidecar", ".db": "Database file", ".torrent": "Torrent file", ".exe": "Program",
    ".msi": "Installer", ".bat": "Batch script", ".ps1": "PowerShell script", ".js": "Script",
    ".lnk": "Shortcut", ".url": "Internet shortcut", ".html": "Web page", ".htm": "Web page",
}
ARCHIVE_EXT = {".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz", ".iso"}

GROUPS = [("links", "Links and web pages"), ("documents", "Documents"), ("music", "Music"),
          ("archives", "Archives"), ("other", "Other")]


def group_of(path):
    e = os.path.splitext(path)[1].lower()
    if e in LINK_EXT or e in PAGE_EXT or e == ".lnk":
        return "links"
    if e in ARCHIVE_EXT:
        return "archives"
    if e in MUSIC_EXT or TYPE_NAMES.get(e, "").endswith("audio"):
        return "music"
    if e in OFFICE_EXT or e in TEXT_EXT or e in {".pdf", ".doc", ".xls", ".ppt", ".epub", ".mobi"}:
        return "documents"
    return "other"


def _head(path, n=READ):
    with open(path, "rb") as f:
        return f.read(n)


def _text(b):
    if b.startswith(b"\xff\xfe") or b.startswith(b"\xfe\xff"):
        return b.decode("utf-16", "replace")
    return b.decode("utf-8", "replace")


def _clean_url(u):
    u = html.unescape((u or "").strip().strip("'\""))
    return u if re.match(r"^(https?|ftp|file)://", u, re.I) else ""


def norm_url(u):
    """The same address however it was written: no #part, no trailing /, lower-case host, no www."""
    try:
        p = urllib.parse.urlsplit(u.strip())
    except ValueError:
        return u.strip().lower()
    host = (p.hostname or "").lower().removeprefix("www.")
    path = p.path.rstrip("/") or ""
    return f"{host}{path}" + (f"?{p.query}" if p.query else "")


# ── links ──
def url_of_shortcut(path):
    e = os.path.splitext(path)[1].lower()
    b = _head(path, 64 * 1024)
    if e == ".webloc":
        try:
            d = plistlib.loads(b)
            return _clean_url(d.get("URL", ""))
        except Exception:
            m = re.search(rb"<string>(.*?)</string>", b, re.S)
            return _clean_url(m.group(1).decode("utf-8", "replace")) if m else ""
    m = re.search(r"^\s*URL\s*=\s*(\S.*?)\s*$", _text(b), re.M | re.I)
    return _clean_url(m.group(1)) if m else ""


PAGE_PATTERNS = [
    r"<!--\s*saved from url=\(\d+\)\s*(\S+?)\s*-->",                                   # Internet Explorer / Edge
    r"^Snapshot-Content-Location:\s*(\S+)",                                            # .mht
    r"<meta[^>]+http-equiv=[\"']?refresh[\"']?[^>]+content=[\"'][^\"'>]*url=([^\"'>\s]+)",
    r"<meta[^>]+content=[\"'][^\"'>]*url=([^\"'>\s]+)[^>]+http-equiv=[\"']?refresh",
    r"<link[^>]+rel=[\"']canonical[\"'][^>]+href=[\"']([^\"']+)",
    r"<link[^>]+href=[\"']([^\"']+)[\"'][^>]+rel=[\"']canonical",
    r"<meta[^>]+property=[\"']og:url[\"'][^>]+content=[\"']([^\"']+)",
    r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+property=[\"']og:url",
    r"^Content-Location:\s*(https?://\S+)",                                            # .mht, first part
    r"(?:window|document)\.location(?:\.href)?\s*=\s*[\"']([^\"']+)",                  # script redirect
]


def page_info(path):
    """(address, title) of a saved web page or shortcut page; either may be ''."""
    t = _text(_head(path))
    url = ""
    for p in PAGE_PATTERNS:
        m = re.search(p, t, re.I | re.M)
        if m and _clean_url(m.group(1)):
            url = _clean_url(m.group(1))
            break
    m = re.search(r"<title[^>]*>(.*?)</title>", t, re.I | re.S)
    title = re.sub(r"\s+", " ", html.unescape(m.group(1))).strip()[:200] if m else ""
    if not title:
        m = re.search(r"^Subject:\s*(.+)$", t, re.M)          # .mht
        title = m.group(1).strip()[:200] if m else ""
    return url, title


def lnk_target(path):
    """Where a Windows shortcut points (its local or network path)."""
    b = _head(path, 64 * 1024)
    if len(b) < 0x4C or b[:4] != b"\x4c\x00\x00\x00":
        return ""
    flags = struct.unpack_from("<I", b, 0x14)[0]
    pos = 0x4C
    if flags & 0x01:                              # HasLinkTargetIDList
        pos += 2 + struct.unpack_from("<H", b, pos)[0]
    target = ""
    if flags & 0x02 and pos + 28 <= len(b):       # HasLinkInfo
        size, hsize, liflags, _vol, local, net, suffix = struct.unpack_from("<7I", b, pos)

        def cstr(off, wide=False):
            s = pos + off
            if wide:
                e = s
                while e + 1 < len(b) and b[e:e + 2] != b"\0\0":
                    e += 2
                return b[s:e].decode("utf-16-le", "replace")
            e = b.find(b"\0", s)
            return b[s:e if e >= 0 else None].decode("mbcs" if os.name == "nt" else "latin-1", "replace")
        if hsize >= 0x24 and pos + 36 <= len(b):
            local_u, suffix_u = struct.unpack_from("<2I", b, pos + 28)
            if liflags & 1 and local_u:
                target = cstr(local_u, True) + (cstr(suffix_u, True) if suffix_u else "")
        if not target and liflags & 1 and local:
            target = cstr(local) + (cstr(suffix) if suffix else "")
        if not target and liflags & 2 and net:
            nb = pos + net
            name_off = struct.unpack_from("<I", b, nb + 8)[0] if nb + 12 <= len(b) else 0
            share = b[nb + name_off:b.find(b"\0", nb + name_off)].decode("latin-1", "replace") if name_off else ""
            target = share + ("\\" + cstr(suffix) if suffix and cstr(suffix) else "")
        pos += size
    if not target:                                # StringData: name, then relative path
        try:
            p = pos
            if flags & 0x04:                      # HasName
                n = struct.unpack_from("<H", b, p)[0]
                p += 2 + n * (2 if flags & 0x80 else 1)
            if flags & 0x08:                      # HasRelativePath
                n = struct.unpack_from("<H", b, p)[0]
                raw = b[p + 2:p + 2 + n * (2 if flags & 0x80 else 1)]
                target = raw.decode("utf-16-le" if flags & 0x80 else "latin-1", "replace")
        except struct.error:
            pass
    return target.strip("\0 ")


# ── documents, music, text ──
def _pdf_str(s):
    if s.startswith(b"<") and s.endswith(b">"):
        try:
            raw = bytes.fromhex(re.sub(rb"\s", b"", s[1:-1]).decode())
        except ValueError:
            return ""
    else:
        raw = s[1:-1]
        raw = re.sub(rb"\\([nrtbf()\\])", lambda m: {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"",
                                                      b"f": b"", b"(": b"(", b")": b")", b"\\": b"\\"}[m.group(1)], raw)
        raw = re.sub(rb"\\(\d{1,3})", lambda m: bytes([int(m.group(1), 8) & 255]), raw)
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", "replace")
    return raw.decode("latin-1", "replace")


def pdf_title(path):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        b = f.read(READ)
        if size > READ:
            f.seek(max(0, size - READ))
            b += f.read()
    m = re.search(rb"<dc:title>.*?<rdf:li[^>]*>(.*?)</rdf:li>", b, re.S)
    if m:
        return html.unescape(m.group(1).decode("utf-8", "replace")).strip()
    m = re.search(rb"/Title\s*(\((?:\\.|[^\\)])*\)|<[0-9A-Fa-f\s]*>)", b)
    return _pdf_str(m.group(1)).strip() if m else ""


def office_title(path):
    """The title (or failing that, the subject) saved in an Office / OpenDocument file's properties -
    just that one small part of the file is read."""
    import zipfile
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            part = "docProps/core.xml" if "docProps/core.xml" in names else ("meta.xml" if "meta.xml" in names else None)
            if not part or z.getinfo(part).file_size > 1024 * 1024:
                return ""
            x = z.read(part).decode("utf-8", "replace")
    except Exception:
        return ""
    for tag in ("dc:title", "dc:subject"):
        m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", x, re.S)
        if m and m.group(1).strip():
            return html.unescape(m.group(1)).strip()
    return ""


def _id3_text(frame):
    if not frame:
        return ""
    enc, data = frame[0], frame[1:]
    try:
        if enc == 0:
            return data.decode("latin-1").strip("\0 ")
        if enc == 1:
            return data.decode("utf-16").strip("\0 ")
        if enc == 2:
            return data.decode("utf-16-be").strip("\0 ")
        return data.decode("utf-8").strip("\0 ")
    except UnicodeDecodeError:
        return ""


def mp3_tags(path):
    """'Artist - Title' from ID3v2 (or ID3v1 at the end)."""
    b = _head(path, 128 * 1024)
    got = {}
    if b[:3] == b"ID3" and len(b) > 10:
        ver = b[3]
        size = (b[6] << 21) | (b[7] << 14) | (b[8] << 7) | b[9]
        pos, end = 10, min(len(b), 10 + size)
        while pos + 10 <= end:
            fid = b[pos:pos + 4]
            if not fid.strip(b"\0"):
                break
            raw = b[pos + 4:pos + 8]
            fsz = ((raw[0] << 21) | (raw[1] << 14) | (raw[2] << 7) | raw[3]) if ver >= 4 else struct.unpack(">I", raw)[0]
            if fid in (b"TIT2", b"TPE1"):
                got[fid] = _id3_text(b[pos + 10:pos + 10 + fsz])
            pos += 10 + fsz
    if not got:
        try:
            with open(path, "rb") as f:
                f.seek(-128, 2)
                t = f.read(128)
            if t[:3] == b"TAG":
                got = {b"TIT2": t[3:33].decode("latin-1").strip("\0 "), b"TPE1": t[33:63].decode("latin-1").strip("\0 ")}
        except OSError:
            pass
    a, t = got.get(b"TPE1", ""), got.get(b"TIT2", "")
    return f"{a} - {t}" if a and t else (t or a)


def first_line(path):
    t = _text(_head(path, 16 * 1024))
    if path.lower().endswith(".rtf"):
        t = re.sub(r"\\[a-z]+-?\d* ?|[{}]", "", t)
    for line in t.splitlines():
        line = line.strip().strip("\ufeff")
        if line:
            return line[:160]
    return ""


def describe(path):
    """{"desc": one line, "url": address for links ('' otherwise), "title": a page's title}."""
    e = os.path.splitext(path)[1].lower()
    out = {"desc": "", "url": "", "title": ""}
    try:
        if e in LINK_EXT:
            out["url"] = url_of_shortcut(path)
        elif e in PAGE_EXT:
            out["url"], out["title"] = page_info(path)
            if not out["url"]:
                out["desc"] = out["title"]
        elif e == ".lnk":
            t = lnk_target(path)
            out["desc"] = f"→ {t}" if t else ""
        elif e == ".pdf":
            out["desc"] = pdf_title(path)
        elif e in OFFICE_EXT:
            out["desc"] = office_title(path)
        elif e in MUSIC_EXT:
            out["desc"] = mp3_tags(path)
        elif e in TEXT_EXT:
            out["desc"] = first_line(path)
    except (OSError, ValueError, struct.error):
        pass
    if out["url"]:
        out["desc"] = out["url"]
    if not out["desc"]:
        out["desc"] = TYPE_NAMES.get(e, (e[1:].upper() + " file") if e else "File")
    out["desc"] = re.sub(r"[\r\n\t]+", " ", out["desc"])[:500]
    return out


# ── the cache: read once per file version ──
SCHEMA = """CREATE TABLE IF NOT EXISTS file_desc(path TEXT PRIMARY KEY, mtime REAL, size INTEGER, descr TEXT,
    url TEXT, title TEXT)"""


def ready(conn):
    conn.execute(SCHEMA)


def cached(conn, paths):
    """{path: (mtime, size, desc, url, title)} for the paths already described."""
    ready(conn)
    out = {}
    for i in range(0, len(paths), 500):
        chunk = paths[i:i + 500]
        q = ",".join("?" * len(chunk))
        for p, mt, sz, d, u, t in conn.execute(
                f"SELECT path, mtime, size, descr, url, title FROM file_desc WHERE path IN ({q})", chunk):
            out[p] = (mt, sz, d, u or "", t or "")
    return out


def store(conn, rows):
    """rows: (path, mtime, size, desc, url, title)."""
    ready(conn)
    conn.executemany("INSERT OR REPLACE INTO file_desc(path, mtime, size, descr, url, title) VALUES(?,?,?,?,?,?)", rows)
    conn.commit()


def folder_tokens(url):
    """Words in an address that could be a folder's name: path parts and the site name, with -, _, +
    and %20 read as spaces."""
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return set()
    parts = [urllib.parse.unquote_plus(x) for x in p.path.split("/") if x]
    host = (p.hostname or "").lower().removeprefix("www.")
    parts += [host.split(".")[0]] if host else []
    out = set()
    for x in parts:
        x = re.sub(r"\.\w{2,5}$", "", x)
        x = re.sub(r"[-_+.]+", " ", x).strip().casefold()
        if len(x) >= 3 and not x.isdigit():
            out.add(re.sub(r"\s+", " ", x))
    return out
