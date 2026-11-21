r"""Your FaceSort library (1.57.3): what comes in goes straight into it, and moves to its person's
folder once the face is confirmed.

- Importing (a drop, Add folder… imported once, Web drops, the browser, watched galleries): the
  photos and videos are MOVED into <library>\~Unsorted\<where they were>, so
  C:\Downloads\a.jpg -> <library>\~Unsorted\C\Downloads\a.jpg. A photo's own extra files (.xmp,
  Takeout .json, .aae, .thm) go with it. Folders you keep watching aren't touched, and nothing
  already in the library moves.
- After tagging: photos in ~Unsorted with a confirmed person are moved (a pause after you stop
  working) to where Sort into folders would put them - the biggest face's folder, copies for the
  other people in the photo. Undo is in History.

The library is the Photos library folder (Settings), else the Sort into folders destination."""
import os
import shutil

import companions
import engine
import store
import xmp

UNSORTED = "~Unsorted"
PAUSE = 30                      # seconds without using FaceSort before tagged photos move


def library(settings):
    lib = settings.get("library_root") or settings.get("export_dir") or ""
    return store.norm_path(lib) if lib else None


def unsorted_dir(lib):
    return os.path.join(lib, UNSORTED)


def inside(path, root):
    if not root:
        return False
    a, b = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(root)).rstrip("\\/")
    return a == b or a.startswith(b + os.sep)


def target(path, lib):
    r"""C:\Downloads\a.jpg -> <lib>\~Unsorted\C\Downloads\a.jpg  (\\server\share\x -> ~Unsorted\server\share\x)"""
    full = os.path.abspath(path)
    drive, rest = os.path.splitdrive(full)
    parts = [x for x in drive.replace("/", "\\").split("\\") if x] if drive.startswith(("\\\\", "//")) else \
        ([drive.rstrip(":")] if drive else [])
    parts += [x for x in rest.replace("\\", "/").split("/") if x]
    return os.path.join(unsorted_dir(lib), *parts)


def _media(name):
    ext = os.path.splitext(name)[1].lower()
    return ext in engine.IMAGE_EXTS or ext in engine.VIDEO_EXTS


def plan(dirs, files, lib, skip=(), keep=()):
    """[(src, dst)] for the photos and videos being imported. skip: folders left out; keep: folders /
    files that stay where they are (watched folders, the library itself)."""
    keep = [k for k in keep if k] + [lib]
    stay = lambda p: any(inside(p, k) for k in keep)
    out = []
    for f in files:
        if not stay(f):
            out.append((f, target(f, lib)))
    for d in dirs:
        if stay(d):
            continue
        for root, subdirs, names in os.walk(d):
            subdirs[:] = [x for x in subdirs if not x.startswith(("$", "."))
                          and not any(inside(os.path.join(root, x), s) for s in skip)
                          and not stay(os.path.join(root, x))]
            for n in names:
                if _media(n) and not n.startswith("."):
                    p = os.path.join(root, n)
                    out.append((p, target(p, lib)))
    return out


def _free(dst):
    if not os.path.exists(dst):
        return dst
    base, ext = os.path.splitext(dst)
    i = 2
    while os.path.exists(f"{base} ({i}){ext}"):
        i += 1
    return f"{base} ({i}){ext}"


def _move(src, dst):
    """Rename on the same drive; across drives copy, check the size, then remove the original."""
    try:
        os.rename(src, dst)
        return
    except OSError:
        pass
    shutil.copy2(src, dst)
    if os.path.getsize(dst) != os.path.getsize(src):
        os.remove(dst)
        raise OSError("the copy came out a different size - the original was kept")
    os.remove(src)


def move_file(src, dst):
    """Move one photo / video (and its own extra files). Returns where it ended up."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    dst = _free(dst)
    sc = xmp.sidecar_path(src)
    try:
        comps = companions.companions(src)
    except OSError:
        comps = []
    _move(src, dst)
    if os.path.exists(sc):
        try:
            _move(sc, _free(xmp.sidecar_path(dst)))
        except OSError:
            pass
    for p, suffix in comps:
        try:
            if os.path.exists(p):
                to = companions.companion_target(dst, suffix, src)
                if not os.path.exists(to):
                    _move(p, to)
        except OSError:
            pass
    return dst


def run(conn, moves, progress=None, cancelled=None):
    """Do the moves; FaceSort follows the photos it already knows. -> ({src: dst}, [(src, why)])"""
    done, errors = {}, []
    for i, (src, dst) in enumerate(moves):
        if cancelled and cancelled():
            break
        try:
            new = move_file(src, dst)
        except OSError as e:
            errors.append((src, str(e)))
            continue
        done[store.norm_path(src)] = store.norm_path(new)
        if progress:
            progress(i + 1, len(moves))
    if done and conn is not None:
        for old, new in done.items():
            row = conn.execute("SELECT id FROM photos WHERE path=?", (old,)).fetchone()
            if row and not conn.execute("SELECT 1 FROM photos WHERE path=?", (new,)).fetchone():
                conn.execute("UPDATE photos SET path=?, outside=0 WHERE id=?", (new, row[0]))
        conn.commit()
    return done, errors


def remove_empty(root):
    """Empty folders left in ~Unsorted after their photos moved on."""
    if not root or not os.path.isdir(root):
        return 0
    n = 0
    for d, subdirs, names in os.walk(root, topdown=False):
        if d == root:
            continue
        rest = [x for x in os.listdir(d) if x.lower() not in ("thumbs.db", "desktop.ini", ".ds_store")]
        if not rest:
            try:
                for x in os.listdir(d):
                    os.remove(os.path.join(d, x))
                os.rmdir(d)
                n += 1
            except OSError:
                pass
    return n


# ------------------------------------------------------------------ after tagging
def tagged_waiting(conn, lib):
    """Photos in ~Unsorted with someone confirmed on them (not discarded, not Sort Later)."""
    if not lib:
        return []
    pre = store.norm_path(unsorted_dir(lib)).rstrip("\\/") + os.sep
    rows = conn.execute("""SELECT p.id, p.path FROM photos p
                           WHERE substr(p.path, 1, ?) = ? AND COALESCE(p.discarded, 0) = 0
                             AND COALESCE(p.sort_later, 0) = 0
                             AND (EXISTS (SELECT 1 FROM faces f WHERE f.photo_id = p.id AND f.confirmed = 1
                                          AND f.ignored = 0 AND f.person_id IS NOT NULL)
                                  OR EXISTS (SELECT 1 FROM photo_people pp WHERE pp.photo_id = p.id))""",
                        (len(pre), pre)).fetchall()
    held = held_back(conn)
    return [pid for pid, path in rows if path not in held and os.path.exists(path)]


def held_back(conn):
    """Photos whose move out of ~Unsorted you undid (History): they stay until you sort them yourself."""
    import json
    out = set()
    try:
        for (data,) in conn.execute("SELECT data FROM history WHERE undone=1 AND text LIKE ?",
                                    (f"%out of {UNSORTED}%",)):
            for m in json.loads(data).get("files") or []:
                out.add(store.norm_path(m[0]))
    except Exception:
        pass
    return out


def auto_move_job(conn, lib, pids, progress=lambda d, t, m: None, cancelled=lambda: False):
    """The job: Sort into folders (move) for just these photos, then tidy ~Unsorted."""
    import jobs
    st = jobs.export_folders(conn, lib, False, True, progress, cancelled, roots=(), convert_webp=False,
                             cleanup=False, include_dropped=True, only=pids)
    st["cleaned"] = remove_empty(unsorted_dir(lib))
    n = st.get("moved", 0)
    st["history_label"] = f"Moved {n:,} tagged photo{'s' if n != 1 else ''} out of {UNSORTED}"
    return st


# ------------------------------------------------------------------ bringing older photos in
def strays(conn, settings, lib):
    """Photos FaceSort knows that live outside the library: [(path, "watched" / "imported")].
    "watched": in one of your photo folders (Keep watching); "imported": everything else (dropped,
    imported once, added as single files)."""
    if not lib:
        return []
    watched = [f for f in settings.get("folders") or [] if f and not inside(f, lib) and not inside(lib, f)]
    out = []
    for (path,) in conn.execute("SELECT path FROM photos"):
        if not path or inside(path, lib):
            continue
        group = "watched" if any(inside(path, f) for f in watched) else "imported"
        out.append((path, group))
    return out


def other_drive(paths, lib):
    """How many of these are on another drive than the library (copied, not just renamed)."""
    def dev(p):
        try:
            return os.stat(os.path.dirname(p) or p).st_dev
        except OSError:
            return None
    home, seen, n = dev(os.path.join(lib, ".")), {}, 0
    for p in paths:
        d = os.path.dirname(p)
        if d not in seen:
            seen[d] = dev(p)
        if seen[d] is not None and seen[d] != home:
            n += 1
    return n


def bring_in_job(conn, lib, paths, progress=lambda d, t, m: None, cancelled=lambda: False):
    """The job: each photo (with its own extra files) into ~Unsorted\\<where it was>."""
    existing = [p for p in paths if os.path.isfile(p)]
    moves = [(p, target(p, lib)) for p in existing]

    def prog(i, n):
        progress(i, n, f"Moving into {UNSORTED}… {i:,} / {n:,}")
    done, errors = run(conn, moves, prog, cancelled)
    n = len(done)
    return {"moved": n, "errors": errors, "missing": len(paths) - len(existing),
            "moves": list(done.items()),
            "history_label": f"Brought {n:,} older photo{'s' if n != 1 else ''} into the library ({UNSORTED})"}
