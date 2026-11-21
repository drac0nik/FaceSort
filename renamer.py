"""Bulk rename (1.53): give photos new file names from a pattern.

Pattern parts (anything else is kept as typed):
  {name}            the current file name (without its ending)
  {person}          the person: the one whose gallery you're in, else the main person on the photo
  {people}          everyone on the photo ("Anna & Ben", "Anna, Ben & 2 more")
  {folder}          the photo's FaceSort folder, else the folder it's in on disk
  {date}            the date it was taken (2024-07-15) - or {date:%d %b %Y} with your own layout
  {time}            the time it was taken (14-05-33)
  {n}               a number counting up (start and digits are options)
The file ending (.jpg, .mp4…) is always kept. Photos only get new names; they stay in their folders.
Their own extra files (.xmp, Takeout .json, .aae, .thm) are renamed with them, and every rename
can be undone from History."""
import os
import re
import time

import companions
import store
import xmp

TOKEN = re.compile(r"\{(name|person|people|folder|date|time|n)(?::([^{}]*))?\}", re.I)
BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
EXAMPLES = ["{person} {date} {n}", "{date} {person}", "{folder} - {n}", "{person} - {name}", "{name}"]
ORDERS = [("date", "Date taken"), ("gallery", "Order in the gallery"), ("name", "Current file name")]
MAX_LEN = 180


def photo_info(conn, pids, person=None):
    """pid -> dict(path, taken, people, folder) for the photos (in no particular order)."""
    out = {}
    pids = list(pids)
    for i in range(0, len(pids), 900):
        chunk = pids[i:i + 900]
        q = ",".join("?" * len(chunk))
        for pid, path, taken, mtime, cat in conn.execute(
                f"SELECT p.id, p.path, p.taken, p.mtime, c.name FROM photos p LEFT JOIN categories c "
                f"ON c.id=p.category_id WHERE p.id IN ({q})", chunk):
            out[pid] = {"path": path, "taken": taken or mtime or 0, "people": [], "folder": cat, "big": {}}
        for pid, name, area in conn.execute(
                f"""SELECT f.photo_id, pe.name, f.w * f.h FROM faces f JOIN persons pe ON pe.id=f.person_id
                    WHERE f.photo_id IN ({q}) AND f.confirmed=1 AND f.ignored=0""", chunk):
            d = out.get(pid)
            if d is not None:
                d["big"][name] = max(d["big"].get(name, 0), area or 0)
        for pid, name in conn.execute(f"""SELECT pp.photo_id, pe.name FROM photo_people pp JOIN persons pe
                                          ON pe.id=pp.person_id WHERE pp.photo_id IN ({q})""", chunk):
            d = out.get(pid)
            if d is not None:
                d["big"].setdefault(name, 0)
    for d in out.values():
        d["people"] = sorted(d["big"], key=lambda n: (-d["big"][n], n.casefold()))
        d["person"] = person if person else (d["people"][0] if d["people"] else "")
        if not d["folder"]:
            d["folder"] = os.path.basename(os.path.dirname(d["path"]))
    return out


def _people(names):
    if not names:
        return ""
    if len(names) <= 3:
        return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " & " + names[-1]
    return f"{', '.join(names[:2])} & {len(names) - 2} more"


def clean(name):
    """Safe as a Windows file name."""
    name = BAD.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(". ")
    if name.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} \
            | {f"LPT{i}" for i in range(1, 10)}:
        name = "_" + name
    return name[:MAX_LEN].rstrip(". ")


def format_name(pattern, info, n, digits=0):
    """The new file name (without its ending) for one photo."""
    stem = os.path.splitext(os.path.basename(info["path"]))[0]

    def part(m):
        key, arg = m.group(1).lower(), m.group(2)
        if key == "name":
            return stem
        if key == "person":
            return info.get("person") or "Unnamed"
        if key == "people":
            return _people(info.get("people") or []) or "Unnamed"
        if key == "folder":
            return info.get("folder") or ""
        if key in ("date", "time"):
            t = info.get("taken") or 0
            fmt = arg or ("%Y-%m-%d" if key == "date" else "%H-%M-%S")
            try:
                return time.strftime(fmt, time.localtime(t))
            except (ValueError, OSError):
                return ""
        if key == "n":
            d = int(arg) if arg and arg.isdigit() else digits
            return str(n).zfill(d)
        return m.group(0)

    name = clean(TOKEN.sub(part, pattern))
    return name or stem


def plan(conn, pids, pattern, start=1, digits=0, order="date", person=None, gallery_order=None):
    """[(pid, old path, new path)] - every photo given a free name in its own folder. Photos whose
    name doesn't change are left out."""
    info = photo_info(conn, pids, person)
    ids = [p for p in (gallery_order or pids) if p in info]
    if order == "date":
        ids.sort(key=lambda p: (info[p]["taken"], os.path.basename(info[p]["path"]).casefold()))
    elif order == "name":
        ids.sort(key=lambda p: os.path.basename(info[p]["path"]).casefold())
    if not digits and TOKEN.search(pattern) and "{n}" in pattern.lower():
        digits = max(2, len(str(start + len(ids) - 1)))
    taken = {}                                # folder (normcase) -> names in use there afterwards
    leaving = {os.path.normcase(info[p]["path"]) for p in ids}
    out = []
    for k, pid in enumerate(ids):
        path = info[pid]["path"]
        folder, base = os.path.split(path)
        ext = os.path.splitext(base)[1]
        want = format_name(pattern, info[pid], start + k, digits)
        fk = os.path.normcase(folder)
        if fk not in taken:
            try:
                taken[fk] = {os.path.normcase(os.path.join(folder, n)) for n in os.listdir(folder)} - leaving
            except OSError:
                taken[fk] = set()
        cand, i = want, 2
        while os.path.normcase(os.path.join(folder, cand + ext)) in taken[fk]:
            cand = f"{want} ({i})"
            i += 1
        new = os.path.join(folder, cand + ext)
        taken[fk].add(os.path.normcase(new))
        if new != path:
            out.append((pid, path, new))
    return out


class sort_namer:
    """Sort into folders with new names: namer(pid, destination folder, source) -> new name; numbers
    count up per destination folder; {person} is the person whose folder it goes in."""

    def __init__(self, conn, pids, pattern):
        self.pattern = pattern
        self.info = photo_info(conn, pids)
        self.count = {}

    def taken(self, pid):
        return (self.info.get(pid) or {}).get("taken") or 0

    def __call__(self, pid, folder_name, src):
        d = self.info.get(pid)
        if d is None:
            return None
        d = dict(d, path=src)
        parts = {p.casefold() for p in re.split(r"[\\/]", folder_name)}
        d["person"] = next((n for n in d["people"] if n.casefold() in parts), d["person"])
        n = self.count[folder_name] = self.count.get(folder_name, 0) + 1
        return format_name(self.pattern, d, n, 3)


def _extras(path):
    """The photo's own extra files: [(path, suffix-or-None)] (None = its .xmp sidecar)."""
    out = [(p, s) for p, s in companions.companions(path)]
    sc = xmp.sidecar_path(path)
    if os.path.exists(sc) and os.path.normcase(sc) != os.path.normcase(path):
        out.append((sc, None))
    return out


def apply(conn, rows, progress=lambda d, t, m: None, cancelled=lambda: False):
    """Rename the files (two steps, so names can swap places), keep the library and everything
    remembered about each photo attached. Returns {"renamed": n, "moves": [(old, new)], "errors": [...]}."""
    import known
    stats = {"renamed": 0, "moves": [], "errors": []}
    staged = []                                # (pid, old, new, [(tmp, final)])
    tag = f".fsren{os.getpid()}~"
    for k, (pid, old, new) in enumerate(rows):
        if cancelled():
            break
        progress(k, len(rows) * 2, f"Renaming {os.path.basename(old)}…")
        import fsx
        if fsx.sealed_only(old, conn):          # (1.58) kept encrypted: the name is FaceSort's alone
            n_new = store.norm_path(new)
            if fsx.exists(n_new, conn):
                stats["errors"].append((old, f"{os.path.basename(new)} is already there"))
                continue
            with conn.write():
                conn.execute("UPDATE photos SET path=? WHERE id=?", (n_new, pid))
            try:
                known.move_marks(store.norm_path(old), n_new)
            except Exception:
                pass
            stats["moves"].append((old, new))
            stats["renamed"] += 1
            continue
        if not os.path.exists(old):
            stats["errors"].append((old, "file not found"))
            continue
        files = [(old, new)] + [(p, companions.companion_target(new, s, old) if s is not None
                                 else xmp.sidecar_path(new)) for p, s in _extras(old)]
        moved = []
        try:
            for src, dst in files:
                tmp = src + tag
                os.rename(src, tmp)
                moved.append((src, tmp, dst))
        except OSError as e:
            for src, tmp, _dst in reversed(moved):
                try:
                    os.rename(tmp, src)
                except OSError:
                    pass
            stats["errors"].append((old, str(e)))
            continue
        staged.append((pid, old, new, moved))
    for k, (pid, old, new, moved) in enumerate(staged):
        progress(len(rows) + k, len(rows) * 2, f"Renaming {os.path.basename(new)}…")
        try:
            for src, tmp, dst in moved:
                if os.path.exists(dst) and os.path.normcase(dst) != os.path.normcase(src):
                    raise OSError(f"{os.path.basename(dst)} is already there")
                os.rename(tmp, dst)
                if src != old:
                    stats["moves"].append((src, dst))
        except OSError as e:
            for src, tmp, _dst in moved:
                if os.path.exists(tmp):
                    try:
                        os.rename(tmp, src)
                    except OSError:
                        pass
            if not os.path.exists(new):
                stats["errors"].append((old, str(e)))
                continue
        n_new = store.norm_path(new)
        with conn.write():
            conn.execute("UPDATE photos SET path=? WHERE id=?", (n_new, pid))
        try:
            known.move_marks(store.norm_path(old), n_new)
        except Exception:
            pass
        stats["moves"].append((old, new))
        stats["renamed"] += 1
    progress(1, 1, "Renamed")
    return stats
