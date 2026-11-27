"""Gallery watcher (1.56): checks the galleries you watch for pictures and videos that are new
since the last check, downloads them and adds them to FaceSort - tagged the way you set for that
site or that gallery.

Each gallery is opened in FaceSort's browser (signed in with your saved session or login), the
page is scrolled so it loads everything, and Max Image Saver lists it exactly as its ALL (or
GALLERY: each thumbnail's own page) button would. Pictures downloaded before are skipped."""
import json
import os
import time
import urllib.parse

from PySide6.QtCore import QObject, QTimer, QUrl, Signal

SCHEMA = """
CREATE TABLE IF NOT EXISTS gallery_watches(id INTEGER PRIMARY KEY, url TEXT UNIQUE NOT NULL, name TEXT,
    mode TEXT, people TEXT, category TEXT, later INTEGER, ask INTEGER, enabled INTEGER DEFAULT 1,
    last_check REAL, last_new INTEGER, last_msg TEXT, added REAL);
CREATE TABLE IF NOT EXISTS gallery_seen(watch_id INTEGER NOT NULL, key TEXT NOT NULL, at REAL,
    PRIMARY KEY(watch_id, key));
CREATE TABLE IF NOT EXISTS site_rules(host TEXT PRIMARY KEY, mode TEXT, people TEXT, category TEXT,
    later INTEGER, ask INTEGER);
"""
MODES = [("all", "Pictures on the page (like the ALL button)"),
         ("gallery", "Each thumbnail's own page (like the GALLERY button)")]
# (1.57.1) Max Image Saver's own ways of reading particular sites - listed under "Read:" for those sites.
SITE_MODES = [("mm", "modelmayhem.com", "ModelMayhem: whole portfolio + credited photos (like MM+)"),
              ("sg", "suicidegirls.com", "SuicideGirls: the set, or every set on a model's page (like Download all sets)"),
              ("patreon", "patreon.com", "Patreon: the post's whole gallery, full size (like P+)"),
              ("flickr", "flickr.com", "Flickr: the photostream or album, largest size, with date taken"),
              ("youpic", "youpic.com", "YouPic: the photos, with date taken and name tags"),
              ("ms", "modelsociety.com", "Model Society: the owner's whole gallery, every page"),
              # (1.63.3) new in Max Image Saver 2.15
              ("vb", "viewbug.com", "ViewBug: the member's whole gallery (every scroll page), with titles and month"),
              ("rc", "r-cover.net", "r-cover: every grid page of the profile, large versions")]
SCROLLS = 25


def _on(host, site):
    host = (host or "").lower()
    return host == site or host.endswith("." + site)


def modes_for(host):
    """The site's own ways of reading first (if any), then the two general ones."""
    return [(k, label) for k, site, label in SITE_MODES if _on(host, site)] + MODES


def site_default(host):
    return next((k for k, site, _l in SITE_MODES if _on(host, site)), "all")


def js_mode(mode):
    """What __fsMIS.run is asked for: the site modes Max Image Saver doesn't run by name read like ALL."""
    return mode if mode in ("all", "gallery", "mm", "patreon", "sg") else "all"
LIST_TIMEOUT = 60 * 60


def ready(conn):
    conn.executescript(SCHEMA)


def host_of(url):
    h = (urllib.parse.urlparse(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def watches(conn):
    ready(conn)
    cols = ("id", "url", "name", "mode", "people", "category", "later", "ask", "enabled", "last_check", "last_new",
            "last_msg", "added")
    out = []
    for r in conn.execute(f"SELECT {','.join(cols)} FROM gallery_watches ORDER BY name COLLATE NOCASE, url"):
        d = dict(zip(cols, r))
        d["people"] = json.loads(d["people"]) if d["people"] else None
        out.append(d)
    return out


def get_watch(conn, wid):
    return next((w for w in watches(conn) if w["id"] == wid), None)


def save_watch(conn, w):
    ready(conn)
    people = json.dumps(w["people"]) if w.get("people") is not None else None
    vals = (w["url"].strip(), w.get("name") or host_of(w["url"]), w.get("mode"), people, w.get("category"),
            w.get("later"), w.get("ask"), int(w.get("enabled", 1)))
    if w.get("id"):
        conn.execute("UPDATE gallery_watches SET url=?, name=?, mode=?, people=?, category=?, later=?, ask=?, "
                     "enabled=? WHERE id=?", vals + (w["id"],))
        wid = w["id"]
    else:
        cur = conn.execute("INSERT OR IGNORE INTO gallery_watches(url, name, mode, people, category, later, ask, "
                           "enabled, added) VALUES(?,?,?,?,?,?,?,?,?)", vals + (time.time(),))
        wid = cur.lastrowid or conn.execute("SELECT id FROM gallery_watches WHERE url=?", (vals[0],)).fetchone()[0]
    conn.commit()
    return wid


def remove_watch(conn, wid):
    ready(conn)
    conn.execute("DELETE FROM gallery_seen WHERE watch_id=?", (wid,))
    conn.execute("DELETE FROM gallery_watches WHERE id=?", (wid,))
    conn.commit()


def site_rule(conn, host):
    ready(conn)
    r = conn.execute("SELECT mode, people, category, later, ask FROM site_rules WHERE host=?", (host,)).fetchone()
    if not r:
        return {"host": host, "mode": None, "people": None, "category": None, "later": None, "ask": None}
    return {"host": host, "mode": r[0], "people": json.loads(r[1]) if r[1] else None, "category": r[2],
            "later": r[3], "ask": r[4]}


def site_rules(conn):
    ready(conn)
    return [site_rule(conn, h) for (h,) in conn.execute("SELECT host FROM site_rules ORDER BY host")]


def save_site_rule(conn, rule):
    ready(conn)
    conn.execute("INSERT OR REPLACE INTO site_rules(host, mode, people, category, later, ask) VALUES(?,?,?,?,?,?)",
                 (rule["host"], rule.get("mode"), json.dumps(rule["people"]) if rule.get("people") is not None else None,
                  rule.get("category"), rule.get("later"), rule.get("ask")))
    conn.commit()


def remove_site_rule(conn, host):
    ready(conn)
    conn.execute("DELETE FROM site_rules WHERE host=?", (host,))
    conn.commit()


def effective(conn, w):
    """The gallery's own settings, else its site's, else the defaults."""
    host = host_of(w["url"])
    rule = site_rule(conn, host)
    out = {}
    for k, dflt in (("mode", site_default(host)), ("people", []), ("category", None), ("later", 0), ("ask", 0)):
        v = w.get(k)
        if v is None:
            v = rule.get(k)
        out[k] = dflt if v is None else v
    return out


def seen_keys(conn, wid):
    ready(conn)
    return {k for (k,) in conn.execute("SELECT key FROM gallery_seen WHERE watch_id=?", (wid,))}


def add_seen(conn, wid, keys):
    ready(conn)
    now = time.time()
    conn.executemany("INSERT OR IGNORE INTO gallery_seen(watch_id, key, at) VALUES(?,?,?)", [(wid, k, now) for k in keys])
    conn.commit()


def base_folder(settings):
    """Where downloaded galleries go: "Web galleries" in your library."""
    if settings.get("gallery_folder"):
        return settings["gallery_folder"]
    import libmove
    lib = libmove.library(settings)
    if lib and settings.get("import_into_library", True):        # (1.57.3) straight into ~Unsorted
        return os.path.join(libmove.unsorted_dir(lib), "Web galleries")
    root = settings.get("library_root") or (settings.get("folders") or [None])[0]
    if root:
        return os.path.join(root, "Web galleries")
    home = os.path.expanduser("~")
    pics = os.path.join(home, "Pictures")
    return os.path.join(pics if os.path.isdir(pics) else home, "FaceSort web galleries")


def due(settings, conn):
    """Should a check start by itself now? (Settings: every so many hours, while FaceSort is open.)"""
    hours = float(settings.get("gallery_check_hours", 0) or 0)
    if hours <= 0:
        return False
    ws = [w for w in watches(conn) if w["enabled"]]
    if not ws:
        return False
    last = min((w["last_check"] or 0) for w in ws)
    return time.time() - last >= hours * 3600


# ------------------------------------------------------------------ checking
class WatchHost:
    """Max Image Saver driven by the watcher: skips what was downloaded before."""

    def __init__(self, checker):
        self.c = checker

    def base_folder(self):
        return self.c.base

    def skip_fn(self):
        seen = self.c.seen
        import misbridge
        return lambda it: misbridge.item_key(it.get("url")) in seen

    def download_one(self, msg):
        return {"ok": False, "error": "not while checking galleries"}

    def bulk_saved(self, saved, skipped, failed):
        self.c.got.emit([(it.get("url"), p) for it, p in saved], skipped, failed)

    def login_captured(self, host, user, password):
        pass


class Checker(QObject):
    """Checks the watched galleries one after another in a browser page (shown in its window)."""
    status = Signal(str)
    gallery_done = Signal(int, int, str)            # watch id, new files, message
    finished = Signal(int)                          # new files in all
    got = Signal(list, int, int)                    # [(url, path)], skipped, failed (from the page's thread)

    def __init__(self, win, view, ids=None):
        super().__init__(view)
        import inbrowser
        self.win, self.view = win, view
        self.host = WatchHost(self)
        self.page = inbrowser.make_page(self.host, view)
        view.setPage(self.page)
        self.queue = [w["id"] for w in watches(win.conn) if w["enabled"] and (ids is None or w["id"] in ids)]
        self.total_new = 0
        self.stopped = False
        self.w = None
        self.seen = set()
        self.base = base_folder(win.settings)
        self.new = []
        self.skipped = self.failed = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.got.connect(self._got)
        self.page.loadFinished.connect(self._loaded)
        self.phase = None

    def start(self):
        self._next()

    def stop(self):
        self.stopped = True
        self._js("window.__fsMIS && __fsMIS.stop()")
        self._finish_gallery("Stopped")

    def _js(self, code, cb=None):
        from PySide6.QtWebEngineCore import QWebEngineScript
        try:
            if cb:
                self.page.runJavaScript(code, QWebEngineScript.ApplicationWorld, cb)
            else:
                self.page.runJavaScript(code, QWebEngineScript.ApplicationWorld)
        except RuntimeError:
            pass

    def _next(self):
        if self.stopped or not self.queue:
            self.timer.stop()
            self.finished.emit(self.total_new)
            return
        wid = self.queue.pop(0)
        self.w = get_watch(self.win.conn, wid)
        if self.w is None:
            return self._next()
        self.rule = effective(self.win.conn, self.w)
        self.seen = seen_keys(self.win.conn, wid)
        self.new, self.skipped, self.failed = [], 0, 0
        self.phase, self.t0, self.logged_in, self.scrolls, self.last_h, self.stable = "load", time.time(), False, 0, 0, 0
        self.status.emit(f"Opening {self.w['name']}…")
        self.page.setUrl(QUrl(self.w["url"]))
        self.timer.start(1000)

    def _loaded(self, ok):
        if self.phase == "load":
            self.phase, self.t0 = "settle", time.time()
        elif self.phase == "login-wait":
            self.phase, self.t0 = "reload", time.time()

    def _tick(self):
        if self.w is None or self.stopped:
            return
        el = time.time() - self.t0
        if self.phase == "load" and el > 45:
            self.phase, self.t0 = "settle", time.time()
        elif self.phase == "settle" and el > 2.5:
            self.phase = "check-login"
            self._js("!!document.querySelector('input[type=password]')", self._login_check)
        elif self.phase == "reload" and el > 2:
            self.phase, self.t0 = "load", time.time()
            self.page.setUrl(QUrl(self.w["url"]))
        elif self.phase == "scroll":
            self.phase = "scrolling"
            self._js("(() => { window.scrollTo(0, document.documentElement.scrollHeight); "
                     "return document.documentElement.scrollHeight; })()", self._scrolled)
        elif self.phase == "listing":
            self._js("window.__fsMIS ? [__fsMIS.state(), __fsMIS.message()] : ['missing', '']", self._state)
        elif self.phase == "login-wait" and el > 30:
            self.phase, self.t0 = "reload", time.time()

    def _login_check(self, has_pw):
        import logins
        if has_pw and not self.logged_in:
            cred = logins.get(self.win.conn, self.page.url().host())
            if cred:
                self.logged_in = True
                self.status.emit(f"Signing in to {logins.host_key(self.page.url().host())}…")
                self.phase, self.t0 = "login-wait", time.time()
                self._js(logins.fill_script(*cred) + " && (() => { const pw = document.querySelector("
                         "'input[type=password]'); const f = pw && pw.form; if (!f) return false; "
                         "const b = f.querySelector('button[type=submit],input[type=submit]') || f.querySelector('button');"
                         " if (b) b.click(); else f.requestSubmit ? f.requestSubmit() : f.submit(); return true; })()")
                return
        self.phase = "scroll"
        self.status.emit(f"Loading everything on {self.w['name']}…")

    def _scrolled(self, h):
        if self.stopped:
            return
        self.scrolls += 1
        h = h or 0
        self.stable = self.stable + 1 if h <= self.last_h else 0
        self.last_h = max(self.last_h, h)
        if self.stable >= 3 or self.scrolls >= SCROLLS:
            self._js("window.scrollTo(0, 0)")
            self.phase, self.t0, self.started = "listing", time.time(), False
            self.status.emit(f"Finding pictures on {self.w['name']}…")
            self._js(f"window.__fsMIS ? __fsMIS.run({json.dumps(js_mode(self.rule['mode']))}) : false")
        else:
            self.phase = "scroll"

    def _state(self, r):
        if self.phase != "listing":
            return
        st, msg = (r or ["missing", ""])[:2]
        el = time.time() - self.t0
        if st == "missing":
            if el > 20:
                self._finish_gallery("Max Image Saver didn't start on this page")
            return
        if st != "idle":
            self.started = True
            if msg:
                self.status.emit(f"{self.w['name']}: {msg}")
        elif self.started or el > 25:
            self._finish_gallery((msg or "").strip())
        if el > LIST_TIMEOUT:
            self._js("__fsMIS.stop()")
            self._finish_gallery("Took too long - stopped")

    def _got(self, pairs, skipped, failed):
        import misbridge
        keys = [misbridge.item_key(u) for u, _p in pairs]
        if self.w is not None:
            add_seen(self.win.conn, self.w["id"], keys)
        self.seen.update(keys)
        self.new += [p for _u, p in pairs]
        self.skipped += skipped
        self.failed += failed

    def _finish_gallery(self, msg):
        if self.w is None:
            return
        w, self.w, self.phase = self.w, None, None
        n = len(self.new)
        text = (f"{n:,} new" if n else "Nothing new") + (f" · {self.skipped:,} already had" if self.skipped else "") + \
            (f" · {self.failed:,} couldn't be downloaded" if self.failed else "")
        if not n and not self.skipped and msg:
            text = msg
        c = self.win.conn
        c.execute("UPDATE gallery_watches SET last_check=?, last_new=?, last_msg=? WHERE id=?",
                  (time.time(), n, text, w["id"]))
        c.commit()
        self.total_new += n
        if self.new:
            # (1.57.6) the Add photos window opens without holding up the next gallery's check
            new, rule, name = list(self.new), dict(self.rule), w.get("name") or ""
            QTimer.singleShot(0, lambda: self.win.import_gallery_files(new, rule, name))
        self.gallery_done.emit(w["id"], n, text)
        if not self.stopped:
            QTimer.singleShot(500, self._next)
        else:
            self.finished.emit(self.total_new)
