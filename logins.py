"""Saved sign-ins for websites (1.56), for FaceSort's browser and gallery watcher.

Kept in the library (so they're encrypted with it when the library is) and each password is
encrypted on its own as well: with the profile's key when it has a password, else with Windows'
own protection for your Windows account (DPAPI). FaceSort only fills them into the site they were
saved for, when you sign in there - they're never sent anywhere else."""
import base64
import sys
import time

SCHEMA = """CREATE TABLE IF NOT EXISTS site_logins(host TEXT PRIMARY KEY, username TEXT, secret TEXT,
            saved REAL, used REAL)"""


def _dpapi(data, protect):
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    crypt = ctypes.windll.crypt32
    k32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    src = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    fn = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(BLOB), ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                   wintypes.DWORD, ctypes.POINTER(BLOB)]
    if not fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(out)):   # CRYPTPROTECT_UI_FORBIDDEN
        raise OSError("Windows couldn't " + ("protect" if protect else "read") + " the saved password")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        k32.LocalFree.argtypes = [ctypes.c_void_p]
        k32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def seal(password):
    import cryptbox
    raw = password.encode("utf-8")
    if cryptbox.unlocked():
        return "fs:" + base64.b64encode(cryptbox.encrypt(raw, "logins")).decode()
    if sys.platform == "win32":
        return "dpapi:" + base64.b64encode(_dpapi(raw, True)).decode()
    return "plain:" + base64.b64encode(raw).decode()        # (only off Windows, without a password: tests)


def unseal(secret):
    import cryptbox
    kind, _, body = (secret or "").partition(":")
    blob = base64.b64decode(body)
    if kind == "fs":
        return cryptbox.decrypt(blob, "logins").decode("utf-8")
    if kind == "dpapi":
        return _dpapi(blob, False).decode("utf-8")
    return blob.decode("utf-8")


def host_key(host):
    h = (host or "").lower().strip(".")
    return h[4:] if h.startswith("www.") else h


def save(conn, host, username, password):
    conn.execute(SCHEMA)
    conn.execute("INSERT OR REPLACE INTO site_logins(host, username, secret, saved, used) VALUES(?,?,?,?,NULL)",
                 (host_key(host), username or "", seal(password), time.time()))
    conn.commit()


def get(conn, host):
    """(username, password) saved for this site (or a site it's part of), else None."""
    conn.execute(SCHEMA)
    h = host_key(host)
    parts = h.split(".")
    for i in range(len(parts) - 1):
        cand = ".".join(parts[i:])
        row = conn.execute("SELECT username, secret FROM site_logins WHERE host=?", (cand,)).fetchone()
        if row:
            try:
                return row[0], unseal(row[1])
            except Exception:
                return None
    return None


def listing(conn):
    conn.execute(SCHEMA)
    return conn.execute("SELECT host, username, saved FROM site_logins ORDER BY host").fetchall()


def forget(conn, host):
    conn.execute(SCHEMA)
    conn.execute("DELETE FROM site_logins WHERE host=?", (host_key(host),))
    conn.commit()


def fill_script(username, password):
    """JavaScript that fills (never sends) the sign-in form on the page."""
    import json
    return """(() => {
      const u = %s, p = %s;
      const set = (el, v) => { const d = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
        d.set.call(el, v); el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true})); };
      const pw = [...document.querySelectorAll('input[type=password]')].find(e => e.offsetParent !== null)
                 || document.querySelector('input[type=password]');
      const scope = (pw && pw.form) || document;
      const users = [...scope.querySelectorAll('input')].filter(e =>
        ['text','email','tel',''].includes((e.getAttribute('type') || '').toLowerCase()) && e.offsetParent !== null);
      if (users.length && u) set(users[users.length - 1], u);
      if (pw) { set(pw, p); pw.focus(); }
      return !!(pw || users.length);
    })()""" % (json.dumps(username or ""), json.dumps(password or ""))
