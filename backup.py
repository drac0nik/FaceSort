"""Library backups (1.52).

A backup is everything FaceSort knows - not the photo files themselves (those belong in your
normal file backup):
- library.db: every face and fingerprint, names, groups, tags, albums, smart albums, rules,
  history, vaults, learned tag models…
- known_people.db: who's who, kept apart from the library (survives Clear library)
- settings.json, never_track.json, the move logs
Thumbnails and AI models are left out (they're made again / downloaded again by themselves).

One .zip per backup, compressed (the databases shrink to about half). The databases are copied
with SQLite's own backup (a consistent snapshot even while FaceSort is running) and the zip is
written in a stream, so any size of library works in little memory. A manifest inside records
the version, when, the library folders, and a checksum of every file.

Settings > Backups: where, how often (every time FaceSort closes, once a day, once a week, or
only by hand), how many to keep. A backup on close runs in its own small process after the
window has closed, so closing isn't slowed down; nothing changed since the last backup: none
is made.

Restoring: Settings > Backups > Restore… checks the backup, then FaceSort restarts: the current
library is first moved aside to "Before restore <date>" in the data folder (a safety copy),
then the backup's files are put in place. If the photos have moved since (D:\\Photos is now
E:\\Photos), FaceSort asks where they are now and updates every path - nothing is rescanned.
"""
import hashlib
import json
import os
import shutil
from dbshim import sqlite3
import sys
import time
import zipfile

import store

FORMAT = "facesort-backup-1"
DBS = ("library.db", "known_people.db")
FILES = ("settings.json", "never_track.json", "profile_lock.json")   # (1.55: the lock goes with an encrypted library)
FOLDERS = ("move logs",)
PREFIX = "FaceSort backup "
PENDING = "restore_pending.json"
FREQUENCIES = [("close", "Every time FaceSort closes"), ("daily", "Once a day (when FaceSort closes)"),
               ("weekly", "Once a week (when FaceSort closes)"), ("manual", "Only when I click Back up now")]
LOCK = "backup.lock"


def default_folder():
    home = os.path.expanduser("~")
    docs = os.path.join(home, "Documents")
    return os.path.join(docs if os.path.isdir(docs) else home, "FaceSort backups")


def folder(settings):
    return (settings or {}).get("backup_folder") or default_folder()


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _snapshot(src, dst):
    """A consistent copy of a SQLite database (works while it's in use)."""
    import cryptbox
    import store
    if store.encrypted_now() and not cryptbox.unlocked():
        # (1.55) the backup on close of an encrypted library: FaceSort has closed (so the database
        # is complete in its file) and the file is encrypted already - copied as it is
        for ext in ("", "-wal"):
            if os.path.exists(src + ext):
                shutil.copy2(src + ext, dst + ext)
        if os.path.exists(dst + "-wal"):
            os.replace(dst + "-wal", dst + ".wal-part")      # (kept with it; SQLite reads it back on open)
            os.replace(dst + ".wal-part", dst + "-wal")
        return
    a = store.open_db(src, timeout=60)       # (only read: SQLite's backup copies page by page)
    b = store.open_db(dst)                   # (an encrypted library's backup stays encrypted)
    try:
        a.backup(b, pages=4096)
    finally:
        b.close()
        a.close()


def change_mark(data=None):
    """Something that changes whenever the library does (to skip a backup when nothing changed)."""
    data = data or store.data_dir()
    parts = []
    for name in DBS + FILES:
        p = os.path.join(data, name)
        for q in (p, p + "-wal"):
            try:
                st = os.stat(q)
                parts.append(f"{os.path.basename(q)}:{st.st_size}:{int(st.st_mtime)}")
            except OSError:
                pass
    return "|".join(parts)


def _state_path(data):
    return os.path.join(data, "backup_state.json")


def state(data=None):
    try:
        with open(_state_path(data or store.data_dir()), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(data, st):
    try:
        with open(_state_path(data), "w", encoding="utf-8") as f:
            json.dump(st, f)
    except OSError:
        pass


def due(settings, data=None):
    """Should a backup be made now (on close)?"""
    freq = (settings or {}).get("backup_frequency", "close")
    if freq == "manual":
        return False
    st = state(data)
    if st.get("mark") and st.get("mark") == change_mark(data):
        return False                                  # nothing changed since the last one
    last = st.get("at", 0)
    gap = {"close": 0, "daily": 86400 - 3600, "weekly": 7 * 86400 - 3600}.get(freq, 0)
    return time.time() - last >= gap


def make(settings=None, dest=None, progress=lambda d, t, m: None, cancelled=lambda: False, data=None, keep=None):
    """Make a backup. Returns its path."""
    data = data or store.data_dir()
    settings = settings if settings is not None else store.load_settings()
    dest = dest or folder(settings)
    os.makedirs(dest, exist_ok=True)
    lock = os.path.join(dest, LOCK)
    try:
        if os.path.exists(lock) and time.time() - os.path.getmtime(lock) < 3600:
            raise RuntimeError("another backup is being made right now")
        open(lock, "w").close()
    except OSError:
        pass
    stamp = time.strftime("%Y-%m-%d %H%M%S")
    final = os.path.join(dest, f"{PREFIX}{stamp}.zip")
    part = final + ".part"
    tmp_dir = os.path.join(dest, f".facesort-backup-{os.getpid()}")
    os.makedirs(tmp_dir, exist_ok=True)
    mark = change_mark(data)
    try:
        items = []                                    # (path on disk, name in zip)
        for i, name in enumerate(DBS):
            src = os.path.join(data, name)
            if os.path.exists(src):
                progress(i, 4, f"Copying {name}…")
                snap = os.path.join(tmp_dir, name)
                _snapshot(src, snap)
                items.append((snap, name))
        for name in FILES:
            src = os.path.join(data, name)
            if os.path.exists(src):
                items.append((src, name))
        for fold in FOLDERS:
            top = os.path.join(data, fold)
            for root, _dirs, files in os.walk(top):
                for fn in files:
                    p = os.path.join(root, fn)
                    items.append((p, os.path.relpath(p, data).replace(os.sep, "/")))
        if cancelled():
            raise RuntimeError("cancelled")
        info = {"format": FORMAT, "made": time.strftime("%Y-%m-%d %H:%M:%S"), "at": time.time(),
                "app": _app_version(), "folders": list(settings.get("folders") or []),
                "library_root": settings.get("library_root"), "photos": _count(os.path.join(tmp_dir, "library.db")),
                "files": {}}
        total = sum(os.path.getsize(p) for p, _n in items) or 1
        done = 0
        with zipfile.ZipFile(part, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
            for p, name in items:
                if cancelled():
                    raise RuntimeError("cancelled")
                progress(done, total, f"Compressing {name}…")
                info["files"][name] = {"size": os.path.getsize(p), "sha256": _sha256(p)}
                z.write(p, name)
                done += os.path.getsize(p)
            z.writestr("manifest.json", json.dumps(info, indent=1))
        os.replace(part, final)
        st = state(data)
        st.update(at=time.time(), mark=mark, last=final)
        _save_state(data, st)
        prune(dest, keep if keep is not None else int(settings.get("backup_keep", 10)))
        progress(total, total, "Backup made")
        return final
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        for p in (part, lock):
            try:
                os.remove(p)
            except OSError:
                pass


def _count(db):
    try:
        import store
        c = store.open_db(f"file:{db}?mode=ro", uri=True)
        try:
            return c.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
        finally:
            c.close()
    except Exception:
        return None


def _app_version():
    try:
        from ui_common import APP_VERSION
        return APP_VERSION
    except Exception:
        return "?"


def listing(dest):
    """[(path, made (time), size, manifest or None)], newest first."""
    out = []
    try:
        names = os.listdir(dest)
    except OSError:
        return out
    for n in names:
        if n.startswith(PREFIX) and n.endswith(".zip"):
            p = os.path.join(dest, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            out.append((p, st.st_mtime, st.st_size, None))
    out.sort(key=lambda r: -r[1])
    return out


def manifest(path):
    with zipfile.ZipFile(path) as z:
        return json.loads(z.read("manifest.json").decode("utf-8"))


def prune(dest, keep):
    """Keep the newest `keep` backups."""
    if keep <= 0:
        return []
    gone = []
    for p, _t, _s, _m in listing(dest)[keep:]:
        try:
            os.remove(p)
            gone.append(p)
        except OSError:
            pass
    return gone


def verify(path):
    """The manifest if the backup is whole and every file matches its checksum; else raises."""
    with zipfile.ZipFile(path) as z:
        bad = z.testzip()
        if bad:
            raise ValueError(f"damaged: {bad}")
        info = json.loads(z.read("manifest.json").decode("utf-8"))
        if info.get("format") != FORMAT:
            raise ValueError("this isn't a FaceSort backup")
        for name, meta in info.get("files", {}).items():
            h = hashlib.sha256()
            with z.open(name) as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != meta.get("sha256"):
                raise ValueError(f"{name} doesn't match its checksum")
        if "library.db" not in info.get("files", {}):
            raise ValueError("there's no library in it")
    return info


# ------------------------------------------------------------------ restoring
def ask_restore(path, data=None):
    """Mark a backup to be put in place at the next start (FaceSort restarts for it)."""
    data = data or store.data_dir()
    verify(path)
    with open(os.path.join(data, PENDING), "w", encoding="utf-8") as f:
        json.dump({"path": path, "asked": time.time()}, f)


def pending(data=None):
    try:
        with open(os.path.join(data or store.data_dir(), PENDING), encoding="utf-8") as f:
            return json.load(f).get("path")
    except (OSError, ValueError):
        return None


def _wait_free(path, wait=20.0):
    """Wait until the FaceSort that asked for the restore has let go of the library (Windows locks open files)."""
    end = time.time() + wait
    while os.path.exists(path):
        try:
            os.replace(path, path + ".check")
            os.replace(path + ".check", path)
            return
        except OSError:
            if time.time() > end:
                raise RuntimeError("the library is still open in another FaceSort window - close it and try again")
            time.sleep(0.5)


def restore(path, data=None):
    """Put a backup in place (FaceSort mustn't have the library open). The current files are moved
    to "Before restore <date>" first. Returns (manifest, safety folder)."""
    data = data or store.data_dir()
    info = verify(path)
    _wait_free(os.path.join(data, "library.db"))
    safety = os.path.join(data, "Before restore " + time.strftime("%Y-%m-%d %H%M%S"))
    os.makedirs(safety, exist_ok=True)
    for name in DBS + FILES:
        for suffix in ("", "-wal", "-shm"):
            p = os.path.join(data, name + suffix)
            if os.path.exists(p):
                shutil.move(p, os.path.join(safety, name + suffix))
    for fold in FOLDERS:
        p = os.path.join(data, fold)
        if os.path.isdir(p):
            shutil.move(p, os.path.join(safety, fold))
    with zipfile.ZipFile(path) as z:
        for name in info.get("files", {}):
            target = os.path.normpath(os.path.join(data, name))
            if not target.startswith(os.path.normpath(data) + os.sep):
                continue                              # (never outside the data folder)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(name) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
    try:
        os.remove(os.path.join(data, PENDING))
    except OSError:
        pass
    shutil.rmtree(os.path.join(data, "thumbs"), ignore_errors=True)   # (made again for the restored library)
    return info, safety


def missing_roots(settings):
    """Library folders (and the sorted library) that don't exist on this PC now."""
    roots = list(settings.get("folders") or [])
    if settings.get("library_root"):
        roots.append(settings["library_root"])
    return [r for r in dict.fromkeys(roots) if r and not os.path.isdir(r)]


def remap(conn, settings, old, new):
    """Photos moved from folder `old` to `new` (a new drive letter, say): every path is updated."""
    old_n = store.norm_path(old).rstrip("\\/")
    new_n = store.norm_path(new).rstrip("\\/")
    n = 0
    for table, col in (("photos", "path"), ("drop_files", "path")):
        try:
            rows = conn.execute(f"SELECT rowid, {col} FROM {table}").fetchall()
        except sqlite3.Error:
            continue
        upd = []
        for rid, p in rows:
            if p and (p == old_n or p.startswith(old_n + os.sep)):
                upd.append((new_n + p[len(old_n):], rid))
        conn.executemany(f"UPDATE {table} SET {col}=? WHERE rowid=?", upd)
        n += len(upd)
    conn.commit()
    for key in ("folders", "imported"):
        settings[key] = [new_n + f[len(old_n):] if f == old_n or f.startswith(old_n + os.sep) else f
                         for f in settings.get(key) or []]
    if settings.get("library_root") and (settings["library_root"] == old_n
                                         or settings["library_root"].startswith(old_n + os.sep)):
        settings["library_root"] = new_n + settings["library_root"][len(old_n):]
    return n


def run_detached_backup(settings=None):
    """Start a backup in its own process (used when FaceSort closes). Where it goes and how many
    are kept come along (an encrypted library's settings can't be read by that process)."""
    args = ["--backup-now"]
    if settings is not None:
        args += ["--folder", folder(settings), "--keep", str(int(settings.get("backup_keep", 10)))]
    _spawn(args)


def relaunch():
    """Open FaceSort again (after a restore was asked for)."""
    _spawn([])


def _spawn(args):
    import subprocess
    if getattr(sys, "frozen", False):
        cmd = [sys.executable] + args
    else:
        cmd = [sys.executable, os.path.abspath(os.path.join(os.path.dirname(__file__), "facesort.py"))] + args
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = 0x00000008 | 0x00000200 | (0x08000000 if args else 0)   # detached, own group
    else:
        kw["start_new_session"] = True
    env = dict(os.environ)
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, env=env, **kw)


def backup_now_main():
    """`FaceSort.exe --backup-now`: make a backup (no window) and end."""
    try:
        from ui_common import log
    except Exception:
        log = print
    try:
        settings = store.load_settings()
        argv = sys.argv
        for flag, key, conv in (("--folder", "backup_folder", str), ("--keep", "backup_keep", int)):
            if flag in argv and argv.index(flag) + 1 < len(argv):
                settings[key] = conv(argv[argv.index(flag) + 1])
        time.sleep(1.0)                               # (the window's process lets go of the library)
        path = make(settings)
        log(f"backup made: {path}")
        return 0
    except Exception as e:
        try:
            log(f"backup failed: {e}")
        except Exception:
            pass
        return 1
