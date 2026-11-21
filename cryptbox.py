"""Profile password, recovery key and encryption (1.55).

One random master key per profile. It's stored twice in profile_lock.json, each time locked:
once with a key made from your password, once with a key made from your recovery key. Either
one opens it; changing the password only re-locks the master key (nothing is re-encrypted).
From the master key come the keys for the library database (SQLCipher), FaceSort's own files
(settings, thumbnails…) and - from 1.56 - the photo and video files.

Nothing here works without the password or the recovery key: if both are lost, the encrypted
library can't be opened by anyone, FaceSort included."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

LOCK_FILE = "profile_lock.json"
MAGIC = b"FSE1"
KDF = {"n": 2 ** 17, "r": 8, "p": 1}          # scrypt: about 0.3 s and 128 MB per try
RK_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # (no 0/O, 1/I)

_master = None                                 # the unlocked master key (bytes) - only in memory


class WrongPassword(Exception):
    pass


def _aes():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM


def _b64(b):
    return base64.b64encode(b).decode("ascii")


def _unb64(s):
    return base64.b64decode(s.encode("ascii"))


def kdf(secret, salt, params=None):
    p = params or KDF
    return hashlib.scrypt(secret.encode("utf-8"), salt=salt, n=p["n"], r=p["r"], p=p["p"],
                          maxmem=256 * 1024 * 1024, dklen=32)


def subkey(master, purpose):
    """A key for one use, made from the master key (HMAC-SHA256)."""
    return hmac.new(master, b"FaceSort " + purpose.encode("ascii"), hashlib.sha256).digest()


# ------------------------------------------------------------------ the lock file
def lock_path(data=None):
    import store
    return os.path.join(data or store.data_dir(), LOCK_FILE)


def info(data=None):
    """The lock file's contents (None if the profile has no password)."""
    try:
        with open(lock_path(data), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write(d, data=None):
    p = lock_path(data)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, p)


def has_password(data=None):
    return info(data) is not None


def data_encrypted(data=None):
    i = info(data)
    return bool(i and i.get("encrypt_data"))


def options(data=None):
    """Lock options: ask_on_open, idle_minutes (0 = never), on_minimize."""
    i = info(data) or {}
    o = {"ask_on_open": True, "idle_minutes": 0, "on_minimize": False}
    o.update(i.get("lock") or {})
    if i.get("encrypt_data"):
        o["ask_on_open"] = True                # (an encrypted library can't open without it)
    return o


def set_options(opts, data=None):
    i = info(data)
    if i is None:
        return
    i["lock"] = {k: opts[k] for k in ("ask_on_open", "idle_minutes", "on_minimize") if k in opts}
    _write(i, data)


def new_recovery_key():
    """25 characters in 5 groups (about 125 bits) - e.g. K7QM-2XRT-..."""
    raw = "".join(secrets.choice(RK_ALPHABET) for _ in range(25))
    return "-".join(raw[i:i + 5] for i in range(0, 25, 5))


def norm_rk(text):
    return "".join(ch for ch in (text or "").upper() if ch in RK_ALPHABET)


def _wrap(master, secret):
    salt, nonce = os.urandom(16), os.urandom(12)
    box = _aes()(kdf(secret, salt)).encrypt(nonce, master, b"FaceSort master key")
    return {"salt": _b64(salt), "nonce": _b64(nonce), "box": _b64(box), "kdf": dict(KDF)}


def _unwrap(w, secret):
    from cryptography.exceptions import InvalidTag
    try:
        return _aes()(kdf(secret, _unb64(w["salt"]), w.get("kdf"))).decrypt(
            _unb64(w["nonce"]), _unb64(w["box"]), b"FaceSort master key")
    except InvalidTag:
        raise WrongPassword()


def setup(password, data=None):
    """A password for the profile: returns the recovery key (shown once, to be written down)."""
    if not password:
        raise ValueError("the password can't be empty")
    global _master
    master = os.urandom(32)
    rk = new_recovery_key()
    _write({"version": 1, "made": time.time(), "pw": _wrap(master, password), "rk": _wrap(master, norm_rk(rk)),
            "encrypt_data": False, "lock": {"ask_on_open": True, "idle_minutes": 0, "on_minimize": False}}, data)
    _master = master
    return rk


def unlock(password, data=None):
    global _master
    i = info(data)
    if i is None:
        return True
    _master = _unwrap(i["pw"], password)
    return True


def unlock_with_recovery(rk, new_password, data=None):
    """Forgot the password: the recovery key opens it, and the new password replaces the old one."""
    global _master
    i = info(data)
    master = _unwrap(i["rk"], norm_rk(rk))
    i["pw"] = _wrap(master, new_password)
    _write(i, data)
    _master = master
    return True


def change_password(old, new, data=None):
    i = info(data)
    master = _unwrap(i["pw"], old)
    i["pw"] = _wrap(master, new)
    _write(i, data)


def new_recovery(password, data=None):
    """A new recovery key (the old one stops working)."""
    i = info(data)
    master = _unwrap(i["pw"], password)
    rk = new_recovery_key()
    i["rk"] = _wrap(master, norm_rk(rk))
    _write(i, data)
    return rk


def remove_password(password, data=None):
    """No password any more (only when the library isn't encrypted)."""
    global _master
    i = info(data)
    _unwrap(i["pw"], password)
    if i.get("encrypt_data") or i.get("encrypt_files"):
        raise ValueError("turn encryption off first")
    os.remove(lock_path(data))
    _master = None


def set_flag(key, on, data=None):
    i = info(data)
    i[key] = bool(on)
    _write(i, data)


def unlocked():
    return _master is not None


def lock():
    global _master
    _master = None


def master():
    return _master


def set_master(m):
    """(helper processes: the key comes with each task)"""
    global _master
    _master = m


# ------------------------------------------------------------------ keys for things
def db_key():
    """SQLCipher's raw key (PRAGMA key) - None when the library isn't encrypted or is locked."""
    if _master is None:
        return None
    return "\"x'" + subkey(_master, "library database").hex() + "'\""


def encrypt(data, purpose="files", aad=b""):
    if _master is None:
        raise RuntimeError("the profile is locked")
    nonce = os.urandom(12)
    return MAGIC + nonce + _aes()(subkey(_master, purpose)).encrypt(nonce, data, aad)


def decrypt(blob, purpose="files", aad=b""):
    if _master is None:
        raise RuntimeError("the profile is locked")
    if blob[:4] != MAGIC:
        raise ValueError("not a FaceSort encrypted file")
    return _aes()(subkey(_master, purpose)).decrypt(blob[4:16], blob[16:], aad)


def is_encrypted_blob(blob):
    return blob[:4] == MAGIC


def write_private(path, data, purpose="files"):
    """Write bytes - encrypted when FaceSort's data is encrypted (and unlocked)."""
    if data_encrypted():
        data = encrypt(data, purpose)          # (raises when locked: never written in the clear)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def read_private(path, purpose="files"):
    with open(path, "rb") as f:
        blob = f.read()
    if blob[:4] == MAGIC:
        return decrypt(blob, purpose)
    return blob


# ------------------------------------------------------------------ logs
import re as _re
_MEDIA = r"jpe?g|jfif|png|gif|webp|heic|heif|avif|bmp|tiff?|dng|cr2|cr3|nef|arw|orf|rw2|raf|mp4|mov|m4v|avi|mkv|webm|wmv|3gp|mts|m2ts|xmp|json|fslib"
_PATHS = _re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|/(?=[^\s/]+/))[^\n\"'<>|]*?(?:\.(?:" + _MEDIA + r")\b|(?=[\"'\n)\]]|$|,\s|\s-\s|\s\())",
                     _re.I)
_NAMES = _re.compile(r"[^\s\"'/\\:]+\.(?:" + _MEDIA + r")\b", _re.I)


def scrub(msg):
    """(encrypted libraries) paths and file names out of a log line."""
    try:
        import store
        if not store.encrypted_now():
            return msg
    except Exception:
        return msg
    msg = _PATHS.sub("<file>", str(msg))
    return _NAMES.sub("<file>", msg)


# ------------------------------------------------------------------ photo and video files (1.58)
FILE_MAGIC = b"FSV1"
CHUNK = 1 << 20                                  # 1 MB pieces: big videos never sit in memory whole


def file_key():
    if _master is None:
        raise RuntimeError("the profile is locked")
    return subkey(_master, "photo and video files")


def _nonce(base, i):
    return base + i.to_bytes(4, "big")


def seal_stream(src, dst, key=None):
    """Encrypt the file src into dst (AES-GCM, piece by piece; each piece numbered and the last one
    marked, so pieces can't be swapped, dropped or cut off). Returns the SHA-256 of the plain file."""
    key = key or file_key()
    aes = _aes()(key)
    base = os.urandom(8)
    h = hashlib.sha256()
    tmp = dst + ".part"
    with open(src, "rb") as fi, open(tmp, "wb") as fo:
        fo.write(FILE_MAGIC + base + CHUNK.to_bytes(4, "big"))
        i = 0
        cur = fi.read(CHUNK)
        while True:
            nxt = fi.read(CHUNK) if cur else b""
            last = not nxt
            h.update(cur)
            aad = FILE_MAGIC + i.to_bytes(4, "big") + (b"\x01" if last else b"\x00")
            fo.write(aes.encrypt(_nonce(base, i), cur, aad))
            i += 1
            if last:
                break
            cur = nxt
        fo.flush()
        os.fsync(fo.fileno())
    os.replace(tmp, dst)
    return h.hexdigest()


def open_stream(src, key=None):
    """The plain pieces of a sealed file, in order (raises ValueError if it was changed or cut short)."""
    key = key or file_key()
    aes = _aes()(key)
    with open(src, "rb") as fi:
        head = fi.read(16)
        if len(head) < 16 or head[:4] != FILE_MAGIC:
            raise ValueError("not a FaceSort encrypted file")
        base, size = head[4:12], int.from_bytes(head[12:16], "big")
        i = 0
        cur = fi.read(size + 16)
        while True:
            nxt = fi.read(size + 16)
            last = not nxt
            aad = FILE_MAGIC + i.to_bytes(4, "big") + (b"\x01" if last else b"\x00")
            try:
                yield aes.decrypt(_nonce(base, i), cur, aad)
            except Exception:
                raise ValueError("the encrypted file is damaged or was changed") from None
            if last:
                return
            i += 1
            cur = nxt


def unseal_stream(src, dst, key=None):
    """Decrypt src into dst (written as dst.part first). Returns the SHA-256 of what was written."""
    h = hashlib.sha256()
    tmp = dst + ".part"
    try:
        with open(tmp, "wb") as fo:
            for piece in open_stream(src, key):
                h.update(piece)
                fo.write(piece)
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return h.hexdigest()


def sealed_sha(src, key=None):
    """SHA-256 of the plain contents of a sealed file (to check a copy before an original is removed)."""
    h = hashlib.sha256()
    for piece in open_stream(src, key):
        h.update(piece)
    return h.hexdigest()
