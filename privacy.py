"""Profile lock and library encryption (1.55) - the parts that aren't windows.

Turning encryption on or off happens the next time FaceSort starts (nothing may have the library
open while it's rewritten): the databases are copied into their encrypted (or plain) form,
checked table by table, and only then swapped in."""
import os
import shutil

import cryptbox
import store
from dbshim import sqlite3

DBS = ("library.db", "known_people.db")
LEAKY = ("facesort.log", "speed.log", "Missing files.txt")     # (paths and names in the clear)


def pending(data=None):
    i = cryptbox.info(data) or {}
    return i.get("pending")


def request(encrypt, data=None):
    """Encrypt (or decrypt) at the next start."""
    i = cryptbox.info(data)
    if i is None:
        raise ValueError("a password is needed first")
    i["pending"] = {"encrypt_data": bool(encrypt)}
    cryptbox._write(i, data)


def cancel_request(data=None):
    i = cryptbox.info(data)
    if i and "pending" in i:
        del i["pending"]
        cryptbox._write(i, data)


def _q(path):
    return "'" + path.replace("'", "''") + "'"


def _counts(c):
    names = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    return {n: c.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}


def convert_db(path, encrypt, key_hex):
    """Rewrite one database encrypted (or plain), checked before it replaces the old one."""
    if not os.path.exists(path):
        return False
    tmp = path + ".converting"
    for p in (tmp, tmp + "-wal", tmp + "-shm"):
        if os.path.exists(p):
            os.remove(p)
    key = f"\"x'{key_hex}'\""
    c = sqlite3.connect(path, timeout=60)
    try:
        if not encrypt:
            c.execute(f"PRAGMA key = {key}")
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        before = _counts(c)
        uv = c.execute("PRAGMA user_version").fetchone()[0]
        c.execute(f"ATTACH DATABASE {_q(tmp)} AS other KEY {key if encrypt else chr(39) * 2}")
        c.execute("SELECT sqlcipher_export('other')")
        c.execute(f"PRAGMA other.user_version = {int(uv)}")
        c.execute("DETACH DATABASE other")
    finally:
        c.close()
    d = sqlite3.connect(tmp)
    try:
        if encrypt:
            d.execute(f"PRAGMA key = {key}")
        after = _counts(d)
    finally:
        d.close()
    if after != before:
        os.remove(tmp)
        raise RuntimeError(f"{os.path.basename(path)}: the copy didn't match ({before} / {after})")
    old = path + ".before-conversion"
    os.replace(path, old)
    os.replace(tmp, path)
    for p in (path + "-wal", path + "-shm", old):
        try:
            os.remove(p)
        except OSError:
            pass
    return True


def convert(encrypt, data=None, progress=lambda m: None):
    """Do the pending change now (FaceSort's library isn't open). The profile must be unlocked."""
    data = data or store.data_dir()
    if not cryptbox.unlocked():
        raise store.LockedError("FaceSort is locked")
    key_hex = cryptbox.subkey(cryptbox.master(), "library database").hex()
    i = cryptbox.info(data)
    if bool(i.get("encrypt_data")) == bool(encrypt):
        cancel_request(data)
        return
    # the small files, read while the old way still applies
    small = {}
    for name, purpose in (("settings.json", "settings"), ("never_track.json", "never track")):
        p = os.path.join(data, name)
        if os.path.exists(p):
            small[name] = (cryptbox.read_private(p, purpose), purpose)
    for name in DBS:
        progress(f"{'Encrypting' if encrypt else 'Decrypting'} {name}…")
        convert_db(os.path.join(data, name), encrypt, key_hex)
    # the library's reference files in your Photos folders: written again the new way later
    try:
        settings = store.load_settings()
        import known
        for root in set(filter(None, [settings.get("library_root")] + list(settings.get("folders") or []))):
            ref = os.path.join(root, known.REF_NAME)
            if os.path.exists(ref):
                os.remove(ref)
    except Exception:
        pass
    i = cryptbox.info(data)
    i["encrypt_data"] = bool(encrypt)
    i.pop("pending", None)
    cryptbox._write(i, data)
    store._ENC_CACHE[0] = None
    for name, (raw, purpose) in small.items():
        cryptbox.write_private(os.path.join(data, name), raw, purpose)
    shutil.rmtree(os.path.join(data, "thumbs"), ignore_errors=True)     # (made again, the new way)
    if encrypt:
        for name in LEAKY:
            try:
                os.remove(os.path.join(data, name))
            except OSError:
                pass
        shutil.rmtree(os.path.join(data, "move logs"), ignore_errors=True)


def old_backups(settings):
    """Backups made before encryption (not encrypted) - offered for deleting."""
    import backup
    import zipfile
    out = []
    for p, _t, _s, _m in backup.listing(backup.folder(settings)):
        try:
            with zipfile.ZipFile(p) as z:
                head = z.open("library.db").read(16)
            if head.startswith(b"SQLite format 3"):
                out.append(p)
        except Exception:
            pass
    return out
