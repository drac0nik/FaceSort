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

import fsx
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


def is_extra(name, media_lower, folder=""):
    """A file that comes with photos (notes, links, documents, archives) - not a photo or video, not
    one photo's own .xmp / .json / .aae (those go with their photo), not Windows' own clutter."""
    if _media(name) or name.startswith(".") or name.lower() in companions.JUNK or name.endswith(".part"):
        return False
    return not companions.is_companion(os.path.join(folder, name), media_lower)


def plan(dirs, files, lib, skip=(), keep=(), extras=()):
    """[(src, dst)] for what's being imported: the photos and videos first, then (1.58.1) the other
    files in the same folders - they go into the same ~Unsorted folder as the photos. skip: folders
    left out; keep: folders / files that stay where they are (watched folders, the library itself);
    extras: other files dropped on their own."""
    keep = [k for k in keep if k] + [lib]
    stay = lambda p: any(inside(p, k) for k in keep)
    out, more = [], []
    for f in files:
        if not stay(f):
            out.append((f, target(f, lib)))
    for f in extras:
        if not stay(f) and os.path.isfile(f):
            more.append((f, target(f, lib)))
    for d in dirs:
        if stay(d):
            continue
        for root, subdirs, names in os.walk(d):
            subdirs[:] = [x for x in subdirs if not x.startswith(("$", "."))
                          and not any(inside(os.path.join(root, x), s) for s in skip)
                          and not stay(os.path.join(root, x))]
            media_lower = [n.lower() for n in names if _media(n)]
            for n in names:
                p = os.path.join(root, n)
                if _media(n) and not n.startswith("."):
                    out.append((p, target(p, lib)))
                elif is_extra(n, media_lower, root):
                    more.append((p, target(p, lib)))
    return out + more


def _free(dst):
    if not fsx.exists(dst):
        return dst
    base, ext = os.path.splitext(dst)
    i = 2
    while fsx.exists(f"{base} ({i}){ext}"):
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
        if conn is not None and fsx.sealed_only(src, conn):
            done[store.norm_path(src)] = store.norm_path(_free(dst))   # (1.58) encrypted: FaceSort's record only
            if progress:
                progress(i + 1, len(moves))
            continue
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
    return [pid for pid, path in rows if path not in held and fsx.exists(path, conn)]


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
    remember_moves(conn, lib, st.get("moves") or [])
    extra = follow_extras(conn, lib)
    st["moves"] = list(st.get("moves") or []) + extra
    st["extra_files"] = len(extra)
    st["cleaned"] = remove_empty(unsorted_dir(lib))
    n = st.get("moved", 0)
    st["history_label"] = f"Moved {n:,} tagged photo{'s' if n != 1 else ''} out of {UNSORTED}" + \
        (f" (+{len(extra):,} other file{'s' if len(extra) != 1 else ''})" if extra else "")
    return st


# ------------------------------------------------------------------ (1.58.1) other files follow
def _moves_table(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS unsorted_moves(src_dir TEXT, dest_dir TEXT, "
                 "PRIMARY KEY(src_dir, dest_dir))")


def remember_moves(conn, lib, moves):
    """Where photos from each ~Unsorted folder went (their other files follow once it's empty)."""
    _moves_table(conn)
    uns = unsorted_dir(lib)
    rows = {(store.norm_path(os.path.dirname(a)), store.norm_path(os.path.dirname(b)))
            for a, b in moves if inside(a, uns) and not inside(b, uns) and _media(a)}
    conn.executemany("INSERT OR IGNORE INTO unsorted_moves(src_dir, dest_dir) VALUES(?,?)", list(rows))
    conn.commit()


def _left_in(conn, d):
    """Photos or videos still waiting in this ~Unsorted folder (on disk, or kept encrypted)."""
    try:
        names = os.listdir(d)
    except OSError:
        names = []
    discarded = {r[0] for r in conn.execute("SELECT path FROM photos WHERE COALESCE(discarded, 0) = 1 "
                                            "AND substr(path, 1, ?) = ?", (len(d) + 1, d + os.sep))}
    if any(_media(n) and os.path.join(d, n) not in discarded for n in names):
        return True
    pre = d + os.sep
    for (p,) in conn.execute("""SELECT p.path FROM photos p JOIN vault v ON v.photo_id = p.id
                                WHERE substr(p.path, 1, ?) = ? AND COALESCE(p.is_other, 0) = 0
                                  AND COALESCE(p.discarded, 0) = 0""", (len(pre), pre)) if fsx._has_table(conn) else ():
        if os.path.dirname(p) == d:
            return True
    return False


def _extras_in(conn, d):
    """[(path, photo_id or None, kept encrypted)] other files in this folder."""
    out, seen = [], set()
    try:
        names = os.listdir(d)
    except OSError:
        names = []
    media_lower = [n.lower() for n in names if _media(n)]
    for n in names:
        p = os.path.join(d, n)
        if os.path.isfile(p) and is_extra(n, media_lower, d):
            row = conn.execute("SELECT id FROM photos WHERE path=?", (store.norm_path(p),)).fetchone()
            out.append((store.norm_path(p), row[0] if row else None, False))
            seen.add(store.norm_path(p))
    pre = d + os.sep
    for pid, p in conn.execute("SELECT id, path FROM photos WHERE COALESCE(is_other, 0) = 1 AND substr(path, 1, ?) = ?",
                               (len(pre), pre)):
        if os.path.dirname(p) == d and p not in seen and fsx.sealed_only(p, conn):
            out.append((p, pid, True))
    return out


def backfill_moves(conn, lib):
    """Where earlier moves out of ~Unsorted went (History), for photos moved before 1.58.1."""
    import json
    _moves_table(conn)
    try:
        rows = conn.execute("SELECT data FROM history WHERE undone=0 AND text LIKE ?",
                            (f"%out of {UNSORTED}%",)).fetchall()
    except Exception:
        return
    for (data,) in rows:
        try:
            remember_moves(conn, lib, [tuple(m) for m in json.loads(data).get("files") or []])
        except Exception:
            pass


def follow_extras(conn, lib):
    r"""Other files in an ~Unsorted folder whose photos have all moved on go where the photos went:
    <their folder>\~links, ~archives or ~files (the first folder gets the file, any others a copy).
    -> [(old, new)] moved."""
    import shutil
    _moves_table(conn)
    backfill_moves(conn, lib)
    uns = unsorted_dir(lib)
    moved = []
    for (d,) in conn.execute("SELECT DISTINCT src_dir FROM unsorted_moves").fetchall():
        if not inside(d, uns) or _left_in(conn, d):
            continue
        dests = [r[0] for r in conn.execute("SELECT dest_dir FROM unsorted_moves WHERE src_dir=? ORDER BY dest_dir",
                                            (d,))]
        for path, pid, sealed in _extras_in(conn, d):
            sub = companions.SUB.get(companions.kind_of(path), "~files")
            first = None
            for k, dest in enumerate(dests):
                to = _free(os.path.join(dest, sub, os.path.basename(path)))
                try:
                    if k == 0:
                        if sealed:
                            conn.execute("UPDATE photos SET path=? WHERE id=?", (store.norm_path(to), pid))
                        else:
                            os.makedirs(os.path.dirname(to), exist_ok=True)
                            _move(path, to)
                            if pid:
                                conn.execute("UPDATE photos SET path=? WHERE id=?", (store.norm_path(to), pid))
                            elif fsx.enabled():                       # (encrypted next, like the rest)
                                st = os.stat(to)
                                store.store_other(conn, store.norm_path(to), round(st.st_mtime, 3), st.st_size)
                        first = store.norm_path(to)
                        moved.append((path, first))
                    else:
                        os.makedirs(os.path.dirname(to), exist_ok=True)
                        shutil.copy2(fsx.plain(first, conn), to)
                        fsx.mark_made(conn, to)
                except OSError:
                    break
            conn.commit()
        conn.execute("DELETE FROM unsorted_moves WHERE src_dir=?", (d,))
        conn.commit()
    return moved


# ------------------------------------------------------------------ bringing older photos in
def strays(conn, settings, lib):
    """Photos FaceSort knows that live outside the library: [(path, "watched" / "imported")].
    "watched": in one of your photo folders (Keep watching); "imported": everything else (dropped,
    imported once, added as single files)."""
    if not lib:
        return []
    watched = [f for f in settings.get("folders") or [] if f and not inside(f, lib) and not inside(lib, f)]
    out, seen = [], set()
    for (path,) in conn.execute("SELECT path FROM photos"):
        if not path or inside(path, lib):
            continue
        group = "watched" if any(inside(path, f) for f in watched) else "imported"
        out.append((path, group))
        seen.add(path)
    try:                                       # (1.58.1) other files that came with a drop, left behind
        for _i, _b, path, _k, _r in store.drop_files(conn):
            if path and path not in seen and not inside(path, lib) and os.path.isfile(path):
                out.append((path, "watched" if any(inside(path, f) for f in watched) else "imported"))
                seen.add(path)
    except Exception:
        pass
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
    existing = [p for p in paths if os.path.isfile(p) or fsx.sealed_only(p, conn)]
    moves = [(p, target(p, lib)) for p in existing]

    def prog(i, n):
        progress(i, n, f"Moving into {UNSORTED}… {i:,} / {n:,}")
    done, errors = run(conn, moves, prog, cancelled)
    for old, new in done.items():              # (1.58.1) other files from a drop: their record follows
        conn.execute("UPDATE drop_files SET path=? WHERE path=?", (new, old))
    for (fid, root) in conn.execute("SELECT id, root FROM drop_files WHERE root IS NOT NULL").fetchall():
        if root and not inside(root, lib):
            conn.execute("UPDATE drop_files SET root=? WHERE id=?", (store.norm_path(target(root, lib)), fid))
    conn.commit()
    extra = follow_extras(conn, lib)
    n = len(done)
    return {"moved": n, "errors": errors, "missing": len(paths) - len(existing),
            "moves": list(done.items()) + extra,
            "history_label": f"Brought {n:,} older photo{'s' if n != 1 else ''} into the library ({UNSORTED})"}
