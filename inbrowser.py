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
        def createWindow(self, kind):
            # (1.58.2) a link opened in a new tab or window (target=_blank, middle / Ctrl+click, pop-ups)
            # gets its own tab when the window has tabs; otherwise it opens right here
            ui = getattr(host, "ui", None)
            if ui is not None and hasattr(ui, "new_tab"):
                bg = kind == QWebEnginePage.WebWindowType.WebBrowserBackgroundTab
                v = ui.new_tab(None, switch=False)
                if not bg:                         # (switched to once the page has taken it)
                    from PySide6.QtCore import QTimer
                    QTimer.singleShot(0, lambda: ui.tabs.indexOf(v) >= 0 and ui.tabs.setCurrentWidget(v))
                return v.page()
            return self

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
    checked), the watched galleries and their settings on the right.
    (1.58.2) The browser has tabs: + or Ctrl+T for a new one, links meant for a new tab or window open in
    one, Ctrl+W closes, Ctrl+Shift+T brings back the last closed; the tabs you had open come back next time."""
    saved = Signal(list)
    HOME = "https://www.google.com/"

    def __init__(self, win, url=None):
        super().__init__(None)
        from PySide6.QtGui import QKeySequence, QShortcut
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWidgets import QSplitter, QTabWidget, QToolButton
        import watcher_ui
        self.win = win
        self.setWindowTitle("Gallery Watcher")
        self.setWindowIcon(win.windowIcon())
        self.resize(1500, 900)
        self.host = BrowserHost(win, self)
        self._closed = []                          # (address, title) of closed tabs, for Ctrl+Shift+T
        tb = QToolBar()
        tb.setMovable(False)
        self.addToolBar(tb)
        self.a_back = tb.addAction("◀", lambda: self.view.back())
        self.a_back.setToolTip("Back (Alt+Left)")
        self.a_fwd = tb.addAction("▶", lambda: self.view.forward())
        self.a_fwd.setToolTip("Forward (Alt+Right)")
        self.a_reload = tb.addAction("⟳", lambda: self.view.reload())
        self.a_reload.setToolTip("Reload (F5)")
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
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setElideMode(Qt.ElideRight)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.tabBar().tabMoved.connect(lambda *_: self._keep_checked_last())
        self.tabs.tabBar().setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabs.tabBar().customContextMenuRequested.connect(self._tab_menu)
        plus = QToolButton()
        plus.setText("+")
        plus.setAutoRaise(True)
        plus.setMinimumSize(30, 26)
        plus.setStyleSheet("QToolButton { font-size: 17px; font-weight: 600; padding: 0 6px; }")
        plus.setToolTip("New tab (Ctrl+T)")
        plus.clicked.connect(lambda: self.new_tab(self.HOME, focus_address=True))
        self.tabs.setCornerWidget(plus, Qt.TopRightCorner)
        self.check_view = QWebEngineView()
        self.tabs.addTab(self.check_view, "Being checked")
        self._fix_checked_tab()
        self.panel = watcher_ui.WatcherPanel(win, self.check_view, self)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.tabs)
        split.addWidget(self.panel)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([1040, 460])
        self.setCentralWidget(split)
        self.saved.connect(self._saved)
        self._batch, self._timer = [], None
        self.profile = profile()
        self.profile.downloadRequested.connect(self._download_requested)
        for keys, fn in (("Ctrl+T", lambda: self.new_tab(self.HOME, focus_address=True)),
                         ("Ctrl+W", lambda: self.close_tab(self.tabs.currentIndex())),
                         ("Ctrl+Shift+T", self.reopen_tab),
                         ("Ctrl+Tab", lambda: self._step(1)), ("Ctrl+Shift+Tab", lambda: self._step(-1)),
                         ("Ctrl+L", lambda: (self.addr.setFocus(), self.addr.selectAll())),
                         ("F5", lambda: self.view.reload()), ("Alt+Left", lambda: self.view.back()),
                         ("Alt+Right", lambda: self.view.forward())):
            QShortcut(QKeySequence(keys), self, fn)
        saved = [u for u in (win.settings.get("browser_tabs") or []) if isinstance(u, str) and u]
        for u in saved[:30]:
            self.new_tab(u, switch=False)
        if url or not saved:
            self.new_tab(url or self.HOME)
        else:
            self.tabs.setCurrentIndex(min(int(win.settings.get("browser_tab", 0) or 0), len(saved) - 1))

    # ------------------------------------------------------------ tabs
    def _fix_checked_tab(self):
        """The "Being checked" tab: last, and can't be closed."""
        from PySide6.QtWidgets import QTabBar
        i = self.tabs.indexOf(self.check_view)
        bar = self.tabs.tabBar()
        for side in (QTabBar.RightSide, QTabBar.LeftSide):
            if bar.tabButton(i, side):
                bar.tabButton(i, side).hide()
                bar.setTabButton(i, side, None)

    def _keep_checked_last(self):
        i = self.tabs.indexOf(self.check_view)
        if i != self.tabs.count() - 1:
            self.tabs.tabBar().moveTab(i, self.tabs.count() - 1)

    def browse_views(self):
        return [self.tabs.widget(i) for i in range(self.tabs.count()) if self.tabs.widget(i) is not self.check_view]

    @property
    def view(self):
        """The tab you're looking at (or the last one you were, while "Being checked" is showing)."""
        w = self.tabs.currentWidget()
        if w is None or w is self.check_view:
            w = getattr(self, "_last_view", None)
            views = self.browse_views()
            if w not in views:
                w = views[-1] if views else None
        return w

    @property
    def page(self):
        v = self.view
        return v.page() if v is not None else None

    def new_tab(self, url=None, switch=True, focus_address=False):
        from PySide6.QtWebEngineWidgets import QWebEngineView
        v = QWebEngineView()
        pg = make_page(self.host, v)
        v.setPage(pg)
        i = self.tabs.insertTab(max(0, self.tabs.indexOf(self.check_view)), v, "New tab")
        v.titleChanged.connect(lambda t, v=v: self._title(v, t))
        v.iconChanged.connect(lambda ic, v=v: self.tabs.setTabIcon(self.tabs.indexOf(v), ic))
        v.urlChanged.connect(lambda u, v=v: v is self.view and self._url_changed(u))
        v.loadFinished.connect(lambda ok, v=v: v is self.view and self._loaded(ok))
        pg.loadingChanged.connect(lambda *_a, v=v: v is self.view and self._nav_state())
        if url:
            v.setUrl(QUrl(url))
        if switch:
            self.tabs.setCurrentIndex(i)
        if focus_address:
            self.addr.setFocus()
            self.addr.selectAll()
        return v

    def _title(self, v, t):
        i = self.tabs.indexOf(v)
        if i < 0:
            return
        t = t or v.url().host() or "New tab"
        self.tabs.setTabText(i, t if len(t) <= 26 else t[:25] + "…")
        self.tabs.setTabToolTip(i, t)
        if v is self.view:
            self.setWindowTitle(f"Gallery Watcher - {t}")

    def close_tab(self, i):
        v = self.tabs.widget(i)
        if v is None or v is self.check_view:
            return
        u = v.url().toString()
        if u and u != "about:blank":
            self._closed = (self._closed + [(u, v.title())])[-25:]
        self.tabs.removeTab(i)
        pg = v.page()
        v.setPage(None)
        if pg is not None:
            pg.deleteLater()
        v.deleteLater()
        if not self.browse_views():               # (closing the last one: a fresh tab instead)
            self.new_tab(self.HOME)

    def reopen_tab(self):
        if self._closed:
            u, _t = self._closed.pop()
            self.new_tab(u)

    def _step(self, d):
        n = self.tabs.count()
        if n:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + d) % n)

    def _tab_changed(self, i):
        w = self.tabs.widget(i)
        on_page = w is not None and w is not self.check_view
        if on_page:
            self._last_view = w
            self._url_changed(w.url())
            self.setWindowTitle(f"Gallery Watcher - {w.title()}" if w.title() else "Gallery Watcher")
        for a in (self.a_watch, self.a_reload):
            a.setEnabled(on_page)
        self.addr.setEnabled(on_page)
        self._nav_state()
        self._update_fill()

    def _nav_state(self):
        v = self.tabs.currentWidget()
        ok = v is not None and v is not self.check_view
        self.a_back.setEnabled(ok and v.history().canGoBack())
        self.a_fwd.setEnabled(ok and v.history().canGoForward())

    def _tab_menu(self, pos):
        from PySide6.QtWidgets import QMenu
        i = self.tabs.tabBar().tabAt(pos)
        v = self.tabs.widget(i) if i >= 0 else None
        m = QMenu(self)
        m.addAction("New tab", lambda: self.new_tab(self.HOME, focus_address=True))
        if v is not None and v is not self.check_view:
            m.addAction("Duplicate tab", lambda: self.new_tab(v.url().toString()))
            m.addAction("Reload", v.reload)
            m.addSeparator()
            m.addAction("Close tab", lambda: self.close_tab(self.tabs.indexOf(v)))
            m.addAction("Close other tabs", lambda: [self.close_tab(self.tabs.indexOf(o))
                                                     for o in self.browse_views() if o is not v])
        if self._closed:
            m.addAction("Reopen closed tab", self.reopen_tab)
        m.exec(self.tabs.tabBar().mapToGlobal(pos))

    def browse(self, url):
        """A watched gallery opened from the list: in a new tab."""
        self.new_tab(url)

    def checking(self, on):
        i = self.tabs.indexOf(self.check_view)
        self.tabs.setTabText(i, "Being checked  ●" if on else "Being checked")

    def go(self):
        t = self.addr.text().strip()
        if not t:
            return
        if not re.match(r"^[a-z]+://", t, re.I):
            t = ("https://" + t) if ("." in t and " " not in t) else "https://www.google.com/search?q=" + QUrl.toPercentEncoding(t).data().decode()
        v = self.view
        if v is None or self.tabs.currentWidget() is self.check_view:
            v = self.new_tab(None)
        v.setUrl(QUrl(t))
        v.setFocus()

    def _url_changed(self, u):
        self.addr.setText(u.toString())
        self._update_fill()
        self._nav_state()

    def _host(self):
        v = self.view
        return v.url().host() if v is not None else ""

    def _update_fill(self):
        import logins
        try:
            has = self.tabs.currentWidget() is not self.check_view and logins.get(self.win.conn, self._host()) is not None
        except Exception:
            has = False
        self.a_fill.setEnabled(has)

    def _loaded(self, ok):
        self._update_fill()
        self._nav_state()

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
        if d.page() not in [v.page() for v in self.browse_views()]:
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
        # (1.58.2) the tabs you had open come back next time
        views = self.browse_views()
        urls = [v.url().toString() for v in views]
        cur = self.view
        self.win.settings["browser_tabs"] = [u for u in urls if u and u != "about:blank"][:30]
        self.win.settings["browser_tab"] = views.index(cur) if cur in views else 0
        try:
            store.save_settings(self.win.settings)
        except Exception:
            pass
        for v in views:
            pg = v.page()
            v.setPage(None)
            if pg is not None:
                pg.deleteLater()
        super().closeEvent(e)
