"""Files that come with photos.

Per photo ("companions" - same name, different ending): .xmp tags, Google Takeout .json
(date taken, people), Apple .aae edits, .thm video thumbnails. They travel with their photo.

Everything else in a dropped folder - notes, links, documents, archives... - is kept with
the photos it came with: when the drop is sorted it goes into a sub-folder under the person
(or folder) those photos go to:  ~links (web links, saved pages, shortcuts), ~archives
(zip, 7z, rar... never opened) and ~files (everything else)."""
import json
import os

LINK_EXT = {".url", ".webloc", ".website", ".html", ".htm", ".mht", ".mhtml", ".lnk", ".desktop"}
ARCHIVE_EXT = {".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".tbz", ".xz", ".txz", ".zst", ".cab",
               ".iso", ".lz", ".lzma", ".arj", ".z"}
JUNK = {"thumbs.db", "desktop.ini", ".ds_store", "ehthumbs.db", "folder.jpg.db"}
SUB = {"link": "~links", "archive": "~archives", "file": "~files"}


def kind_of(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in LINK_EXT:
        return "link"
    if ext in ARCHIVE_EXT or path.lower().endswith((".tar.gz", ".tar.bz2", ".tar.xz")):
        return "archive"
    return "file"


def _names(folder, cache):
    if folder not in cache:
        try:
            cache[folder] = os.listdir(folder)
        except OSError:
            cache[folder] = []
    return cache[folder]


def companions(photo_path, cache=None):
    """The photo's own extra files next to it: [(path, suffix)] where suffix is what follows the
    photo's name ("IMG_1.jpg" + ".supplemental-metadata.json", or "IMG_1" + ".AAE")."""
    cache = {} if cache is None else cache
    folder, name = os.path.split(photo_path)
    stem = os.path.splitext(name)[0]
    out = []
    lname, lstem = name.lower(), stem.lower()
    for n in _names(folder, cache):
        ln = n.lower()
        if ln == lname:
            continue
        if ln.startswith(lname + ".") and ln.endswith(".json"):            # Takeout: IMG_1.jpg.json
            out.append((os.path.join(folder, n), n[len(name):]))
        elif ln.startswith(lname[:46]) and ln.endswith(".json") and len(name) > 46:   # Takeout cut long names
            out.append((os.path.join(folder, n), n[len(name[:46]):]))
        elif ln.startswith(lstem + ".") and os.path.splitext(ln)[1] in (".aae", ".thm") \
                and ln.count(".") == lstem.count(".") + 1:
            out.append((os.path.join(folder, n), n[len(stem):]))
    return out


def companion_target(photo_target, suffix, photo_src):
    """Where a companion goes when its photo moves to photo_target."""
    folder, name = os.path.split(photo_target)
    if suffix.lower().endswith((".aae", ".thm")):
        return os.path.join(folder, os.path.splitext(name)[0] + suffix)
    return os.path.join(folder, name + suffix)


def is_companion(path, media_names_lower):
    """A file in a dropped folder that belongs to one of the photos there (so not listed apart)."""
    ln = os.path.basename(path).lower()
    ext = os.path.splitext(ln)[1]
    if ext == ".xmp":
        stem = ln[:-4]
        return any(os.path.splitext(m)[0] == stem for m in media_names_lower)
    if ext == ".json":
        return any(ln.startswith(m + ".") or (len(m) > 46 and ln.startswith(m[:46])) for m in media_names_lower)
    if ext in (".aae", ".thm"):
        stem = ln[:-4]
        return any(os.path.splitext(m)[0] == stem for m in media_names_lower)
    return False


def takeout_info(photo_path, cache=None):
    """Google Takeout's .json next to a photo: (date taken as unix time or None, [people names])."""
    for p, suffix in companions(photo_path, cache):
        if not suffix.lower().endswith(".json"):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        taken = None
        try:
            taken = float((d.get("photoTakenTime") or {}).get("timestamp")) or None
        except (TypeError, ValueError):
            taken = None
        people = [x.get("name", "").strip() for x in d.get("people") or [] if isinstance(x, dict)]
        return taken, [n for n in people if n]
    return None, []
