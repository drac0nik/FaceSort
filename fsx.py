r"""Encrypted photo and video files (1.58) - optional, off unless you turn it on (Settings › Privacy).

When it's on, each photo and video is kept as an encrypted file with a random name in a hidden vault
folder (<library>\.facesort-vault\ab\<random>). FaceSort's own encrypted data keeps where each one
belongs - its folder and name - so the library looks the same inside FaceSort, while on disk there are
only random names and no folder names.

- photos.path stays the photo's place in the library ("logical" path); the vault table maps it to
  its encrypted file.
- plain(path): a readable copy for anything that needs a real file (pictures, videos, tools). It's
  decrypted into FaceSort's private "open" folder, reused while it's there, and wiped when FaceSort
  locks or closes.
- outside(path): a copy with its real name for opening in another app - wiped the same way.
- The originals stay where they were until you confirm they can go (Review originals).
- Locked: nothing can be decrypted (the key isn't there).

Everything here does nothing for a library that isn't using it: plain(path) is path."""
import contextlib
import hashlib
import os
import secrets
import shutil
import threading
import time

import cryptbox
import store

VAULT = ".facesort-vault"
OPEN_DIR = "open"
OUTSIDE_DIR = "opened outside"
CACHE_BYTES = 4 << 30           # decrypted working copies kept at most (oldest go first)
_lock = threading.Lock()
_local = threading.local()


# ------------------------------------------------------------------ on / off, where
def enabled(data=None):
    """Turned on (new photos are encrypted as they come in)."""
    i = cryptbox.info(data)
    return bool(i and i.get("encrypt_files"))


def vault_dir(data=None):
    i = cryptbox.info(data) or {}
    return i.get("vault_dir") or None


def set_enabled(on, vault=None, data=None):
    i = cryptbox.info(data)
    if i is None:
        raise ValueError("set a password first")
    i["encrypt_files"] = bool(on)
    if not on:
        i.pop("extras_walked", None)            # (turned on again later: every folder looked through again)
    if vault:
        i["vault_dir"] = vault
    cryptbox._write(i, data)


def choose_vault(settings):
    """Where the encrypted files go: the library folder (same drive: quick), else FaceSort's data."""
    import libmove
    lib = libmove.library(settings)
    return os.path.join(lib, VAULT) if lib else os.path.join(store.data_dir(), "vault")


def _hide(path):
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x2)   # hidden
        except Exception:
            pass


def blob_path(blob, data=None):
    v = vault_dir(data)
    return os.path.join(v, blob[:2], blob) if v and blob else None


def _conn(conn=None):
    if conn is not None:
        return conn
    c = getattr(_local, "conn", None)
    d = store.data_dir()
    if c is None or getattr(_local, "dir", None) != d:
        c = store.open_db(os.path.join(d, "library.db"), timeout=30)
        _local.conn, _local.dir = c, d
    return c


def _has_table(c):
    try:
        return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='vault'").fetchone() is not None
    except Exception:
        return False


def ensure_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS vault(            -- 1.58: a photo's encrypted file
            photo_id INTEGER PRIMARY KEY REFERENCES photos(id) ON DELETE CASCADE,
            blob TEXT NOT NULL, size INTEGER, mtime REAL, sha TEXT, at REAL);
        CREATE TABLE IF NOT EXISTS vault_originals(  -- originals waiting for you to confirm they can go
            path TEXT PRIMARY KEY, photo_id INTEGER, at REAL);
        CREATE TABLE IF NOT EXISTS vault_gone(blob TEXT PRIMARY KEY);   -- encrypted files to delete
        CREATE TABLE IF NOT EXISTS vault_made(path TEXT PRIMARY KEY);   -- readable files FaceSort made itself
        CREATE TRIGGER IF NOT EXISTS vault_forget AFTER DELETE ON vault
            BEGIN INSERT OR IGNORE INTO vault_gone(blob) VALUES (old.blob); END;
    """)


# ------------------------------------------------------------------ looking up
def entry(path, conn=None):
    """(blob, size, mtime, sha) for a photo kept encrypted, else None."""
    if not path:
        return None
    if conn is None and not vault_dir():
        return None                           # (never turned on: nothing to look up)
    try:
        c = _conn(conn)
        return c.execute("SELECT v.blob, v.size, v.mtime, v.sha FROM photos p JOIN vault v ON v.photo_id = p.id "
                         "WHERE p.path = ?", (store.norm_path(path),)).fetchone()
    except Exception:
        return None


def any_sealed(conn=None):
    try:
        c = _conn(conn)
        return _has_table(c) and c.execute("SELECT 1 FROM vault LIMIT 1").fetchone() is not None
    except Exception:
        return False


def sealed_only(path, conn=None):
    """Kept encrypted and no longer on disk as a normal file (moves / deletes are FaceSort-only)."""
    return bool(path) and not os.path.exists(path) and entry(path, conn) is not None


def exists(path, conn=None):
    return os.path.exists(path) or entry(path, conn) is not None


def size(path, conn=None):
    try:
        return os.path.getsize(path)
    except OSError:
        e = entry(path, conn)
        return e[1] if e else None


def sealed_paths(conn):
    """Every photo path kept encrypted (a scan doesn't count them as missing)."""
    if not _has_table(conn):
        return set()
    return {r[0] for r in conn.execute("SELECT p.path FROM photos p JOIN vault v ON v.photo_id = p.id")}


# ------------------------------------------------------------------ readable copies
def open_dir(data=None):
    return os.path.join(data or store.data_dir(), OPEN_DIR)


def _trim(d):
    try:
        files = [(os.path.getatime(os.path.join(d, f)), os.path.getsize(os.path.join(d, f)), f)
                 for f in os.listdir(d) if not f.endswith(".part")]
    except OSError:
        return
    total = sum(s for _a, s, _f in files)
    for _a, s, f in sorted(files):
        if total <= CACHE_BYTES:
            break
        try:
            os.remove(os.path.join(d, f))
            total -= s
        except OSError:
            pass


def plain(path, conn=None):
    """A real, readable file for path: path itself when it's a normal file (or not kept encrypted),
    else a decrypted working copy (wiped when FaceSort locks or closes)."""
    if not path or os.path.exists(path):
        return path
    e = entry(path, conn)
    if e is None:
        return path
    blob, sz = e[0], e[1]
    src = blob_path(blob)
    d = open_dir()
    ext = os.path.splitext(path)[1].lower()
    out = os.path.join(d, hashlib.sha256(blob.encode()).hexdigest()[:24] + ext)
    with _lock:
        if os.path.exists(out) and (sz is None or os.path.getsize(out) == sz):
            try:
                os.utime(out, None)
            except OSError:
                pass
            return out
        os.makedirs(d, exist_ok=True)
        # (several scan helpers can open the same photo at once: each writes its own, then one wins)
        mine = f"{out}.{os.getpid()}-{threading.get_ident()}"
        cryptbox.unseal_stream(src, mine)
        try:
            os.replace(mine, out)
        except OSError:                       # (Windows: the other copy is open right now)
            return mine
        _trim(d)
    return out


def outside(path, conn=None):
    """A copy with the photo's real name for another app (wiped when FaceSort locks or closes)."""
    src = plain(path, conn)
    if src == path:
        return path
    d = os.path.join(store.data_dir(), OUTSIDE_DIR, secrets.token_hex(4))
    os.makedirs(d, exist_ok=True)
    out = os.path.join(d, os.path.basename(path))
    shutil.copyfile(src, out)
    return out


def wipe(data=None):
    """The decrypted copies go (FaceSort locked, closing or starting). Files still open elsewhere stay
    until the next time."""
    base = data or store.data_dir()
    for name in (OPEN_DIR, OUTSIDE_DIR):
        d = os.path.join(base, name)
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------ sealing / unsealing
def seal(conn, photo_id, path, key=None):
    """Encrypt one photo into the vault (checked by reading it back). The original stays, listed in
    vault_originals until you confirm. Returns the blob name."""
    ensure_tables(conn)
    v = vault_dir()
    if not v:
        raise RuntimeError("no vault folder")
    if not os.path.isdir(v):
        os.makedirs(v, exist_ok=True)
        _hide(v)
    blob = secrets.token_hex(16)
    dst = os.path.join(v, blob[:2], blob)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    st = os.stat(path)
    sha = cryptbox.seal_stream(path, dst, key)
    if cryptbox.sealed_sha(dst, key) != sha:                 # read back before anything relies on it
        os.remove(dst)
        raise OSError("the encrypted copy didn't read back the same - the original was kept")
    old = conn.execute("SELECT blob FROM vault WHERE photo_id=?", (photo_id,)).fetchone()
    conn.execute("INSERT OR REPLACE INTO vault(photo_id, blob, size, mtime, sha, at) VALUES(?,?,?,?,?,?)",
                 (photo_id, blob, st.st_size, st.st_mtime, sha, time.time()))
    if old and old[0] != blob:
        conn.execute("INSERT OR IGNORE INTO vault_gone(blob) VALUES(?)", (old[0],))
    conn.execute("INSERT OR REPLACE INTO vault_originals(path, photo_id, at) VALUES(?,?,?)",
                 (store.norm_path(path), photo_id, time.time()))
    return blob


def mark_made(conn, path):
    """A readable file FaceSort itself made (an edited copy, a frame, a copy for another person) while
    encryption is on: once encrypted it's removed straight away - there's no original of yours to keep."""
    if enabled():
        ensure_tables(conn)
        conn.execute("INSERT OR IGNORE INTO vault_made(path) VALUES(?)", (store.norm_path(path),))


def made_paths(conn):
    if not _has_table(conn):
        return set()
    return {r[0] for r in conn.execute("SELECT path FROM vault_made")}


def record_extras(conn, roots=(), full=False):
    """(1.58.1) Other files (notes, links, documents, archives) get a record in FaceSort so they can be
    encrypted too: those in folders with photos not encrypted yet, ones FaceSort made, ones that came
    with a drop - or, the first time, every one in your folders and library. -> how many new."""
    import libmove
    dirs = set()
    for (p,) in conn.execute("SELECT p.path FROM photos p LEFT JOIN vault v ON v.photo_id = p.id "
                             "WHERE v.photo_id IS NULL"):
        dirs.add(os.path.dirname(p))
    for p in made_paths(conn):
        dirs.add(os.path.dirname(p))
    try:
        for _i, _b, p, _k, _r in store.drop_files(conn):
            dirs.add(os.path.dirname(p))
    except Exception:
        pass
    if full:
        for r in roots:
            for root, subdirs, _names in os.walk(r):
                subdirs[:] = [x for x in subdirs if not x.startswith(("$", "."))]
                dirs.add(root)
    known = {r[0] for r in conn.execute("SELECT path FROM photos")}
    n = 0
    for d in dirs:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        media_lower = [x.lower() for x in names if libmove._media(x)]
        for x in names:
            p = store.norm_path(os.path.join(d, x))
            if p in known or not os.path.isfile(p) or not libmove.is_extra(x, media_lower, d):
                continue
            st = os.stat(p)
            store.store_other(conn, p, round(st.st_mtime, 3), st.st_size)
            known.add(p)
            n += 1
    conn.commit()
    return n


def to_seal(conn):
    """[(photo_id, path)] not encrypted yet whose file is there (photos and videos, not other files)."""
    ensure_tables(conn)
    rows = conn.execute("""SELECT p.id, p.path FROM photos p LEFT JOIN vault v ON v.photo_id = p.id
                           WHERE v.photo_id IS NULL""").fetchall()             # (1.58.1: other files too)
    return [(i, p) for i, p in rows if os.path.isfile(p)]


def unseal(conn, photo_id, path, key=None):
    """Back to a normal file at its place in the library (if it isn't there already), then out of the
    vault."""
    e = conn.execute("SELECT blob, mtime, sha FROM vault WHERE photo_id=?", (photo_id,)).fetchone()
    if e is None:
        return False
    blob, mtime, sha = e
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        got = cryptbox.unseal_stream(blob_path(blob), path, key)
        if sha and got != sha:
            os.remove(path)
            raise OSError("the decrypted file didn't match - it was left encrypted")
        if mtime:
            try:
                os.utime(path, (mtime, mtime))
            except OSError:
                pass
    conn.execute("DELETE FROM vault WHERE photo_id=?", (photo_id,))
    conn.execute("DELETE FROM vault_originals WHERE photo_id=?", (photo_id,))
    return True


def collect_gone(conn):
    """Delete encrypted files nothing uses any more (a photo removed from FaceSort, an edit saved)."""
    if not _has_table(conn):
        return 0
    n = 0
    for (blob,) in conn.execute("SELECT blob FROM vault_gone").fetchall():
        if conn.execute("SELECT 1 FROM vault WHERE blob=?", (blob,)).fetchone():
            conn.execute("DELETE FROM vault_gone WHERE blob=?", (blob,))
            continue
        p = blob_path(blob)
        try:
            if p and os.path.exists(p):
                os.remove(p)
            conn.execute("DELETE FROM vault_gone WHERE blob=?", (blob,))
            n += 1
        except OSError:
            pass
    conn.commit()
    return n


# ------------------------------------------------------------------ originals
def originals(conn):
    """[(path, photo_id)] originals still on disk, waiting for you to confirm they can go."""
    if not _has_table(conn):
        return []
    rows = conn.execute("""SELECT o.path, o.photo_id FROM vault_originals o
                           JOIN vault v ON v.photo_id = o.photo_id""").fetchall()
    gone = [p for p, _i in rows if not os.path.exists(p)]
    if gone:
        conn.executemany("DELETE FROM vault_originals WHERE path=?", [(p,) for p in gone])
        conn.commit()
    return [(p, i) for p, i in rows if os.path.exists(p)]


def remove_originals(conn, items, roots=(), progress=None, cancelled=None):
    """Delete these originals (and their .xmp / .json / .aae extras, which hold the same details) -
    only those whose encrypted copy is there. Empty folders they leave go too, up to roots."""
    import companions
    import xmp
    done, kept = 0, []
    dirs = set()
    for n, (path, pid) in enumerate(items, 1):
        if cancelled and cancelled():
            break
        e = conn.execute("SELECT blob, size, mtime FROM vault WHERE photo_id=?", (pid,)).fetchone()
        bp = blob_path(e[0]) if e else None
        if not e or not bp or not os.path.exists(bp):
            kept.append((path, "its encrypted copy isn't there"))
            continue
        try:
            st = os.stat(path)
            if st.st_size != e[1] or abs(st.st_mtime - (e[2] or 0)) > 0.01:
                seal(conn, pid, path)            # changed since it was encrypted: the newer one is kept
                conn.commit()
        except OSError as ex:
            kept.append((path, str(ex)))
            continue
        try:
            extras = [xmp.sidecar_path(path)] + [c for c, _s in companions.companions(path)]
        except Exception:
            extras = []
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as ex:
            kept.append((path, str(ex)))
            continue
        for x in extras:
            try:
                if x and os.path.exists(x):
                    os.remove(x)
            except OSError:
                pass
        conn.execute("DELETE FROM vault_originals WHERE path=?", (path,))
        dirs.add(os.path.dirname(path))
        done += 1
        if progress:
            progress(n, len(items))
    conn.commit()
    roots = [os.path.normcase(os.path.abspath(r)).rstrip("\\/") for r in roots if r]
    for d in sorted(dirs, key=len, reverse=True):
        cur = d
        while cur and os.path.normcase(os.path.abspath(cur)).rstrip("\\/") not in roots:
            try:
                left = [x for x in os.listdir(cur) if x.lower() not in ("thumbs.db", "desktop.ini", ".ds_store")]
                if left:
                    break
                for x in os.listdir(cur):
                    os.remove(os.path.join(cur, x))
                os.rmdir(cur)
            except OSError:
                break
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
    return done, kept


# ------------------------------------------------------------------ changing a sealed file
@contextlib.contextmanager
def editing(path, conn):
    """For tools that rewrite a file in place (turning, saving over the original): yields a real
    path to work on. A photo kept encrypted is decrypted to a private copy, and re-encrypted
    afterwards if it changed."""
    if not sealed_only(path, conn):
        yield path
        return
    e = entry(path, conn)
    work = os.path.join(open_dir(), "edit-" + secrets.token_hex(6) + os.path.splitext(path)[1].lower())
    os.makedirs(os.path.dirname(work), exist_ok=True)
    cryptbox.unseal_stream(blob_path(e[0]), work)
    before = os.stat(work)
    try:
        yield work
        after = os.stat(work)
        if (after.st_size, after.st_mtime) != (before.st_size, before.st_mtime):
            pid = conn.execute("SELECT id FROM photos WHERE path=?", (store.norm_path(path),)).fetchone()[0]
            seal(conn, pid, work)
            conn.execute("DELETE FROM vault_originals WHERE path=?", (store.norm_path(work),))
            conn.commit()
            collect_gone(conn)
    finally:
        try:
            os.remove(work)
        except OSError:
            pass


def remove(path, conn):
    """A photo kept encrypted is deleted: its encrypted file goes (when the row goes, via vault_gone)."""
    e = entry(path, conn)
    if e is None:
        return False
    conn.execute("DELETE FROM vault WHERE blob=?", (e[0],))
    conn.commit()
    collect_gone(conn)
    return True


# ------------------------------------------------------------------ the jobs
def auto_remove(data=None):
    """Remove originals as soon as they're encrypted, without the review (off unless you choose it)."""
    i = cryptbox.info(data)
    return bool(i and i.get("remove_originals_auto"))


def library_roots(settings):
    import libmove
    roots = list(settings.get("folders") or [])
    lib = libmove.library(settings)
    if lib:
        roots.append(lib)
    return roots


def seal_job(conn, roots=(), progress=lambda d, t, m: None, cancelled=lambda: False):
    """Encrypt every photo and video that isn't yet. Readable files FaceSort made itself go straight
    away; your originals wait for Review originals (or go straight away too if you chose that)."""
    ensure_tables(conn)
    i = cryptbox.info() or {}
    record_extras(conn, roots, full=not i.get("extras_walked"))     # (1.58.1) other files too
    if not i.get("extras_walked"):
        i["extras_walked"] = True
        cryptbox._write(i)
    items = to_seal(conn)
    made = made_paths(conn)
    auto = auto_remove()
    stats = {"sealed": 0, "errors": [], "removed": 0}
    key = cryptbox.file_key()
    for n, (pid, path) in enumerate(items, 1):
        if cancelled():
            break
        progress(n, len(items), f"Encrypting {n:,} of {len(items):,}…")
        try:
            seal(conn, pid, path, key)
            conn.commit()
            stats["sealed"] += 1
        except Exception as e:
            conn.rollback()
            stats["errors"].append((path, str(e)))
            continue
        if path in made or auto:
            d, _k = remove_originals(conn, [(path, pid)], roots)
            stats["removed"] += d
            conn.execute("DELETE FROM vault_made WHERE path=?", (path,))
            conn.commit()
    collect_gone(conn)
    stats["waiting"] = len(originals(conn))
    return stats


def originals_job(conn, items, roots=(), progress=lambda d, t, m: None, cancelled=lambda: False):
    def prog(i, n):
        progress(i, n, f"Removing originals… {i:,} / {n:,}")
    done, kept = remove_originals(conn, items, roots, prog, cancelled)
    return {"removed": done, "kept": kept, "waiting": len(originals(conn))}


def unseal_job(conn, progress=lambda d, t, m: None, cancelled=lambda: False):
    """Every encrypted photo back to a normal file at its place in the library, then the vault goes."""
    ensure_tables(conn)
    rows = conn.execute("SELECT v.photo_id, p.path FROM vault v JOIN photos p ON p.id = v.photo_id").fetchall()
    stats = {"unsealed": 0, "errors": []}
    key = cryptbox.file_key()
    for n, (pid, path) in enumerate(rows, 1):
        if cancelled():
            break
        progress(n, len(rows), f"Decrypting {n:,} of {len(rows):,}…")
        try:
            unseal(conn, pid, path, key)
            conn.commit()
            stats["unsealed"] += 1
        except Exception as e:
            conn.rollback()
            stats["errors"].append((path, str(e)))
    collect_gone(conn)
    left = conn.execute("SELECT COUNT(*) FROM vault").fetchone()[0]
    stats["left"] = left
    if not left:
        conn.execute("DELETE FROM vault_originals")
        conn.execute("DELETE FROM vault_made")
        conn.commit()
        v = vault_dir()
        if v and os.path.isdir(v):
            for root, _dirs, files in os.walk(v, topdown=False):
                if not files:
                    try:
                        os.rmdir(root)
                    except OSError:
                        pass
    return stats


def counts(conn):
    if not _has_table(conn):
        return 0, 0
    n = conn.execute("SELECT COUNT(*) FROM vault").fetchone()[0]
    w = conn.execute("SELECT COUNT(*) FROM vault_originals o JOIN vault v ON v.photo_id = o.photo_id").fetchone()[0]
    return n, w
