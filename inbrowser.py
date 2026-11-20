"""FaceSort's own web browser (1.56).

- It keeps you signed in: the browser's session (cookies) is saved in FaceSort's data folder, and
  any login you type into a site can be saved (encrypted - logins.py) and filled in next time.
- Max Image Saver runs inside it, the same as in Chrome: the download button on any picture, and
  the ALL / GALLERY / site buttons. What it saves goes into Recently dropped like any drop.
- The gallery watcher (watcher.py) uses the same browser to check the galleries you watch."""
import json
import os
import re
import threading

from PySide6.QtCore import QFile, QIODevice, QObject, Qt, QUrl, Signal, Slot
from PySide6.QtWidgets import QLineEdit, QMainWindow, QMessageBox, QToolBar

import misbridge
import store

HERE = os.path.dirname(os.path.abspath(__file__))
_PROFILE = None
_SCRIPTS = None


def _read(name):
    import sys
    base = getattr(sys, "_MEIPASS", HERE)
    for d in (base, HERE):
        p = os.path.join(d, "mis", name)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return f.read()
    raise FileNotFoundError(name)


def _qwebchannel_js():
    f = QFile(":/qtwebchannel/qwebchannel.js")
    if not f.open(QIODevice.ReadOnly):
        raise RuntimeError("Qt's web channel script is missing")
    return bytes(f.readAll()).decode("utf-8")


def available():
    try:
        import PySide6.QtWebEngineWidgets  # noqa: F401
        return True
    except Exception:
        return False


def profile():
    """The browser profile: its saved session, Max Image Saver in every page, cookies shared with
    FaceSort's own downloads."""
    global _PROFILE
    if _PROFILE is not None:
        return _PROFILE
    from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineScript
    from PySide6.QtWidgets import QApplication
    p = QWebEngineProfile("FaceSort", QApplication.instance())
    d = os.path.join(store.data_dir(), "browser")
    os.makedirs(d, exist_ok=True)
    p.setPersistentStoragePath(d)
    p.setCachePath(os.path.join(d, "cache"))
    p.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
    if store.encrypted_now():
        p.setHttpCacheType(QWebEngineProfile.MemoryHttpCache)     # (no pages left on disk)
    ua = re.sub(r"\s*QtWebEngine/\S+", "", p.httpUserAgent())      # (some sites refuse "QtWebEngine")
    p.setHttpUserAgent(ua)
    misbridge.USER_AGENT[0] = ua
    cs = p.cookieStore()

    def added(c):
        exp = c.expirationDate().toSecsSinceEpoch() if c.expirationDate().isValid() else None
        misbridge.JAR.add(c.domain() or "", c.path() or "/", bytes(c.name()).decode("utf-8", "replace"),
                          bytes(c.value()).decode("utf-8", "replace"), c.isSecure(), exp)

    def removed(c):
        misbridge.JAR.remove(c.domain() or "", c.path() or "/", bytes(c.name()).decode("utf-8", "replace"))
    cs.cookieAdded.connect(added)
    cs.cookieRemoved.connect(removed)
    cs.loadAllCookies()
    for name, src, point in (("fs-channel", _qwebchannel_js(), QWebEngineScript.DocumentCreation),
                             ("fs-shim", _read("shim.js"), QWebEngineScript.DocumentCreation),
                             ("max-image-saver", _read("content.js"), QWebEngineScript.DocumentReady)):
        s = QWebEngineScript()
        s.setName(name)
        s.setSourceCode(src)
        s.setWorldId(QWebEngineScript.ApplicationWorld)
        s.setInjectionPoint(point)
        s.setRunsOnSubFrames(True)
        p.scripts().insert(s)
    _PROFILE = p
    return p


# ------------------------------------------------------------------ the page <-> FaceSort
class Bridge(QObject):
    """Max Image Saver's messages from the page, answered by FaceSort."""
    _out = Signal(str)
    _login = Signal(str, str, str)

    def __init__(self, page, host):
        super().__init__(page)
        self.page, self.host = page, host
        self.ports = {}
        self._out.connect(self._run)
        self._login.connect(lambda h, u, p: self.host.login_captured(h, u, p))

    def _run(self, js):
        from PySide6.QtWebEngineCore import QWebEngineScript
        try:
            self.page.runJavaScript(js, QWebEngineScript.ApplicationWorld)
        except RuntimeError:
            pass                                   # (the page is gone)

    def reply(self, mid, r):
        self._out.emit(f"window.__fsShim && __fsShim.reply({int(mid)}, {json.dumps(r)})")

    def port_msg(self, pid, m):
        self._out.emit(f"window.__fsShim && __fsShim.portMsg({int(pid)}, {json.dumps(m)})")

    @Slot(str)
    def send(self, s):
        try:
            o = json.loads(s)
        except ValueError:
            return
        kind = o.get("kind")
        if kind == "msg":
            msg = o.get("msg") or {}
            mid = o.get("id")
            if not msg.get("referer"):
                msg["referer"] = o.get("page")
            threading.Thread(target=self._answer, args=(mid, msg), daemon=True).start()
        elif kind == "connect":
            self.ports[o["port"]] = {"name": o.get("name"), "cancel": False}
        elif kind == "port":
            pid, m = o["port"], o.get("msg") or {}
            st = self.ports.setdefault(pid, {"name": o.get("name"), "cancel": False})
            if m.get("type") == "cancel":
                st["cancel"] = True
            elif m.get("type") == "start":
                threading.Thread(target=self._bulk, args=(pid, m.get("items") or [], m.get("referer") or o.get("page")),
                                 daemon=True).start()
        elif kind == "disconnect":
            st = self.ports.get(o.get("port"))
            if st:
                st["cancel"] = True
        elif kind == "login":
            if o.get("pass"):
                self._login.emit(o.get("host") or "", o.get("user") or "", o.get("pass") or "")

    def _answer(self, mid, msg):
        try:
            if msg.get("type") == "download":
                r = self.host.download_one(msg)
            else:
                r = misbridge.handle(msg)
            if r is None:
                r = {"ok": False, "error": "not available in FaceSort"}
        except Exception as e:
            r = {"ok": False, "error": str(e)}
        self.reply(mid, r)

    def _bulk(self, pid, items, referer):
        st = self.ports.get(pid) or {"cancel": False}
        total = len(items)

        def progress(done, failed, n):
            self.port_msg(pid, {"type": "progress", "done": done + skipped_box[0], "failed": failed,
                                "total": total})
        skipped_box = [0]
        skip = self.host.skip_fn()
        if skip:
            skipped_box[0] = sum(1 for it in items if skip(it))
        try:
            saved, done, failed, dated, skipped = misbridge.run_bulk(
                items, self.host.base_folder(), referer, progress, lambda: st.get("cancel"), skip=skip)
        except Exception:
            saved, done, failed, dated, skipped = [], 0, total, 0, 0
        self.host.bulk_saved(saved, skipped, failed)
        self.port_msg(pid, {"type": "finished", "done": done + skipped, "failed": failed, "total": total,
                            "cancelled": bool(st.get("cancel")), "misplaced": 0, "dated": dated,
                            "withDates": sum(1 for it in items if it.get("dateTaken"))})


def make_page(host, parent=None):
    """A web page in FaceSort's profile, with Max Image Saver connected to host."""
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineScript

    class FsPage(QWebEnginePage):
        def createWindow(self, _type):
            return self                            # (pop-ups and new tabs open here)

        def javaScriptConsoleMessage(self, level, message, line, source):
            pass

    page = FsPage(profile(), parent)
    ch = QWebChannel(page)
    bridge = Bridge(page, host)
    ch.registerObject("fs", bridge)
    page.setWebChannel(ch, QWebEngineScript.ApplicationWorld)
    page._fs_bridge, page._fs_channel = bridge, ch
    return page


# ------------------------------------------------------------------ the browser window
class BrowserHost:
    """You browsing: Max Image Saver's downloads go to the Web drops folder, then Recently dropped."""

    def __init__(self, win, ui):
        self.win, self.ui = win, ui
        self.pending = []

    def base_folder(self):
        import webdrop
        return webdrop.folder(self.win.settings)

    def skip_fn(self):
        return None

    def download_one(self, msg):
        p, dated = misbridge.save_item({"url": msg.get("url"), "filename": msg.get("filename"),
                                        "autoExt": msg.get("autoExt"), "dateTaken": msg.get("dateTaken"),
                                        "datePrefix": msg.get("datePrefix", True), "referer": msg.get("referer")},
                                       self.base_folder(), msg.get("referer"))
        self.ui.saved.emit([p])
        return {"ok": True, "dated": dated, "tagged": False}

    def bulk_saved(self, saved, skipped, failed):
        if saved:
            self.ui.saved.emit([p for _it, p in saved])

    def login_captured(self, host, user, password):
        self.ui.offer_login(host, user, password)


class BrowserWindow(QMainWindow):
    """(1.57.1) The Gallery Watcher window: the browser on the left (with a tab for the page being
    checked), the watched galleries and their settings on the right."""
    saved = Signal(list)

    def __init__(self, win, url=None):
        super().__init__(None)
        from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
        from PySide6.QtWidgets import QSplitter, QTabWidget
        import watcher_ui
        self.win = win
        self.setWindowTitle("Gallery Watcher")
        self.setWindowIcon(win.windowIcon())
        self.resize(1500, 900)
        self.host = BrowserHost(win, self)
        self.view = QWebEngineView()
        self.page = make_page(self.host, self.view)
        self.view.setPage(self.page)
        tb = QToolBar()
        tb.setMovable(False)
        self.addToolBar(tb)
        from PySide6.QtWebEngineCore import QWebEnginePage
        for text, act in (("◀", QWebEnginePage.Back), ("▶", QWebEnginePage.Forward), ("⟳", QWebEnginePage.Reload)):
            a = self.page.action(act)
            a.setText(text)
            tb.addAction(a)
        self.addr = QLineEdit()
        self.addr.setPlaceholderText("Web address")
        self.addr.returnPressed.connect(self.go)
        tb.addWidget(self.addr)
        self.a_watch = tb.addAction("Watch this page", self.watch_page)
        self.a_watch.setToolTip("Add this page to the watched galleries (checked for new pictures and videos)")
        self.a_fill = tb.addAction("🔑 Fill login", self.fill_login)
        self.a_fill.setToolTip("Fill in the login saved for this site (you press Sign in)")
        tb.addAction("Logins…", self.manage_logins)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.view, "Browse")
        self.check_view = QWebEngineView()
        self.tabs.addTab(self.check_view, "Being checked")
        self.panel = watcher_ui.WatcherPanel(win, self.check_view, self)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.tabs)
        split.addWidget(self.panel)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([1040, 460])
        self.setCentralWidget(split)
        self.view.urlChanged.connect(self._url_changed)
        self.view.titleChanged.connect(lambda t: self.setWindowTitle(f"Gallery Watcher - {t}" if t else "Gallery Watcher"))
        self.view.loadFinished.connect(self._loaded)
        self.saved.connect(self._saved)
        self._batch, self._timer = [], None
        self.profile = profile()
        self.profile.downloadRequested.connect(self._download_requested)
        self.view.setUrl(QUrl(url or "https://www.google.com/"))

    def browse(self, url):
        self.tabs.setCurrentIndex(0)
        self.view.setUrl(QUrl(url))

    def checking(self, on):
        self.tabs.setTabText(1, "Being checked  ●" if on else "Being checked")

    def go(self):
        t = self.addr.text().strip()
        if not t:
            return
        if not re.match(r"^[a-z]+://", t, re.I):
            t = ("https://" + t) if ("." in t and " " not in t) else "https://www.google.com/search?q=" + QUrl.toPercentEncoding(t).data().decode()
        self.view.setUrl(QUrl(t))

    def _url_changed(self, u):
        self.addr.setText(u.toString())
        self._update_fill()

    def _host(self):
        return self.view.url().host()

    def _update_fill(self):
        import logins
        try:
            has = logins.get(self.win.conn, self._host()) is not None
        except Exception:
            has = False
        self.a_fill.setEnabled(has)

    def _loaded(self, ok):
        self._update_fill()

    def fill_login(self):
        import logins
        from PySide6.QtWebEngineCore import QWebEngineScript
        cred = logins.get(self.win.conn, self._host())
        if not cred:
            return
        self.page.runJavaScript(logins.fill_script(*cred), QWebEngineScript.ApplicationWorld)

    def offer_login(self, host, user, password):
        import logins
        old = logins.get(self.win.conn, host)
        if old and old == (user, password):
            return
        q = QMessageBox.question(self, "Save login",
                                 f"Save the login for {logins.host_key(host)}" + (f" ({user})" if user else "") +
                                 "?\n\nIt's kept encrypted on this PC and filled in only on that site - also when "
                                 "the gallery watcher checks a gallery there.")
        if q == QMessageBox.Yes:
            logins.save(self.win.conn, host, user, password)
            self._update_fill()

    def manage_logins(self):
        import watcher_ui
        watcher_ui.LoginsDialog(self.win, self).exec()

    def watch_page(self):
        import watcher_ui
        watcher_ui.edit_watch(self.win, self, url=self.view.url().toString(), name=self.view.title())

    def _saved(self, paths):
        """Max Image Saver saved something: gathered for a moment, then into Recently dropped."""
        from PySide6.QtCore import QTimer
        self._batch += paths
        if self._timer is None:
            self._timer = QTimer(self, singleShot=True, interval=1500)
            self._timer.timeout.connect(self._flush)
        self._timer.start()
        self.statusBar().showMessage(f"Saved {len(self._batch)} - adding to FaceSort…", 4000)

    def _flush(self):
        paths, self._batch = self._batch, []
        if paths:
            self.win.handle_drop(paths)

    def _download_requested(self, d):
        """A link the site offers as a download (in this window): saved in Web drops, then added."""
        if d.page() is not self.page:
            return
        import webdrop
        folder = webdrop.folder(self.win.settings)
        os.makedirs(folder, exist_ok=True)
        d.setDownloadDirectory(folder)
        d.accept()

        def done():
            from PySide6.QtWebEngineCore import QWebEngineDownloadRequest
            if d.state() == QWebEngineDownloadRequest.DownloadCompleted:
                p = os.path.join(d.downloadDirectory(), d.downloadFileName())
                import engine
                if os.path.splitext(p)[1].lower() in engine.IMAGE_EXTS or engine.is_video(p):
                    self.saved.emit([p])
        d.isFinishedChanged.connect(done)

    def closeEvent(self, e):
        self.panel.shutdown()
        if getattr(self.win, "web_window", None) is self:
            self.win.web_window = None
        try:
            self.profile.downloadRequested.disconnect(self._download_requested)
        except (RuntimeError, TypeError):
            pass
        self.view.setPage(None)
        self.page.deleteLater()
        super().closeEvent(e)
