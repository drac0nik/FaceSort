"""1.56: FaceSort's browser (Max Image Saver inside it, saved logins) and the gallery watcher."""
import http.server
import os
import threading
import time
from io import BytesIO

import pytest
from PIL import Image

from conftest import pump

web = pytest.importorskip("PySide6.QtWebEngineWidgets")


def _jpg(size, color):
    b = BytesIO()
    Image.new("RGB", size, color).save(b, "JPEG")
    return b.getvalue()


@pytest.fixture(scope="module")
def site():
    files = {}
    for i in range(6):
        files[f"/img/big{i}.jpg"] = _jpg((640, 480), (i * 40, 100, 200))
        files[f"/img/thumb{i}.jpg"] = _jpg((200, 150), (i * 40, 100, 200))
    state = {"n": 4, "logins": []}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in files:
                body, ct = files[self.path], "image/jpeg"
            elif self.path.startswith("/login"):
                body, ct = (b"<html><body><form method=post action='/login'><input type=text name=u>"
                            b"<input type=password name=p><button type=submit>Sign in</button></form></body></html>",
                            "text/html")
            else:
                body = ("<html><head><title>Test gallery</title></head><body>" + "".join(
                    f'<a href="/img/big{i}.jpg"><img src="/img/thumb{i}.jpg" width=200 height=150></a>'
                    for i in range(state["n"])) + "</body></html>").encode()
                ct = "text/html"
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            state["logins"].append(self.rfile.read(n).decode())
            self.send_response(302)
            self.send_header("Location", "/gallery")
            self.end_headers()

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", state
    srv.shutdown()


def test_bridge_parts(tmp_path):
    import misbridge
    jar = misbridge.CookieJar()
    jar.add(".example.com", "/", "sid", "abc", secure=True)
    jar.add("other.org", "/", "x", "1")
    assert jar.header("https://www.example.com/a") == "sid=abc"
    assert jar.header("http://www.example.com/a") == ""                     # secure only over https
    assert misbridge.item_key("https://live.staticflickr.com/65535/5234567890_ab12cd_k.jpg") == "flickr:5234567890"
    assert misbridge.item_key("https://x.com/a/b.jpg?sig=1") == misbridge.item_key("https://x.com/a/b.jpg?sig=2")
    data, dated = misbridge.embed_jpeg_date(_jpg((50, 40), (1, 2, 3)), "2023-05-06 07:08:09")
    assert dated and misbridge.jpeg_date_of(data) == "2023:05:06 07:08:09"
    with Image.open(BytesIO(data)) as im:
        assert im.size == (50, 40)
    assert misbridge.name_prefix("2023-05-06 07:08:09") == "2023-05-06_0708_"
    assert misbridge.clean_rel("Flickr/Bad:Name/../x?.jpg") == os.path.join("Flickr", "Bad_Name", "x_.jpg")


def test_logins_are_sealed(library):
    import logins
    logins.save(library.conn, "www.Example.com", "me", "s3cret")
    assert logins.get(library.conn, "photos.example.com") == ("me", "s3cret")
    raw = library.conn.execute("SELECT secret FROM site_logins").fetchone()[0]
    assert "s3cret" not in raw
    import cryptbox
    cryptbox.setup("pw")
    try:
        logins.save(library.conn, "example.com", "me", "other")
        assert library.conn.execute("SELECT secret FROM site_logins").fetchone()[0].startswith("fs:")
        assert logins.get(library.conn, "example.com") == ("me", "other")
    finally:
        cryptbox.lock()
        os.remove(cryptbox.lock_path())


def _web_window(qapp):
    import facesort
    w = facesort.MainWindow()
    w.show()
    pump(qapp, 0.4)
    return w


def _wait(qapp, cond, secs=90):
    end = time.time() + secs
    while not cond():
        pump(qapp, 0.1)
        assert time.time() < end, "timed out"


def test_gallery_watcher_downloads_whats_new(library, qapp, site, monkeypatch):
    import watcher
    base, state = site
    library.conn.close()
    w = _web_window(qapp)
    got = []
    # (1.57.6) the Add photos window comes up, filled in with the gallery's tagging
    monkeypatch.setattr(w, "handle_drop", lambda paths, answers=None, defaults=None, source="", **k:
                        got.append((list(paths), defaults, source)))
    state["n"] = 4
    wid = watcher.save_watch(w.conn, {"url": base + "/gallery", "name": "Test", "mode": "gallery",
                                      "people": ["Alice"], "category": None, "later": 0, "ask": 0})
    ww = w.open_gallery_watcher()
    ww.panel.check(None)
    _wait(qapp, lambda: ww.panel.checker is None)
    pump(qapp, 0.3)
    assert len(got) == 1 and len(got[0][0]) == 4 and got[0][1]["people"] == ["Alice"]
    assert "Test" in got[0][2]
    assert all(os.path.exists(p) and p.startswith(watcher.base_folder(w.settings)) for p in got[0][0])
    first = watcher.get_watch(w.conn, wid)
    assert first["last_new"] == 4 and first["last_check"]
    # two more pictures on the site: only those come next time
    state["n"] = 6
    ww.panel.check(None)
    _wait(qapp, lambda: ww.panel.checker is None)
    assert len(got) == 2 and len(got[1][0]) == 2
    assert {os.path.basename(p) for p in got[1][0]} == {"big4.jpg", "big5.jpg"}
    ww.panel.check(None)
    _wait(qapp, lambda: ww.panel.checker is None)
    assert len(got) == 2 and "Nothing new" in watcher.get_watch(w.conn, wid)["last_msg"]
    # a site rule applies to its galleries without their own tagging
    watcher.save_site_rule(w.conn, {"host": "127.0.0.1", "mode": "all", "people": ["Bob"], "category": "Shoots",
                                    "later": 1, "ask": 0})
    g = watcher.get_watch(w.conn, wid)
    g.update(people=None, category=None, later=None, ask=None)
    watcher.save_watch(w.conn, g)
    eff = watcher.effective(w.conn, watcher.get_watch(w.conn, wid))
    assert eff["people"] == ["Bob"] and eff["category"] == "Shoots" and eff["mode"] == "gallery"
    ww.close()
    w.close()
    pump(qapp, 0.3)
    import store
    library.conn = store.connect()


def test_browser_saves_and_fills_logins(library, qapp, site, monkeypatch):
    import logins
    base, state = site
    library.conn.close()
    w = _web_window(qapp)
    offered = []
    import inbrowser
    monkeypatch.setattr(inbrowser.BrowserWindow, "offer_login", lambda self, h, u, p: offered.append((h, u, p)))
    b = w.open_browser(base + "/login")
    _wait(qapp, lambda: b.view.url().toString().endswith("/login") and not b.view.page().isLoading() if hasattr(
        b.view.page(), "isLoading") else True, 30)
    pump(qapp, 2.0)
    from PySide6.QtWebEngineCore import QWebEngineScript
    res = []
    b.page.runJavaScript("typeof window.__fsMIS", QWebEngineScript.ApplicationWorld, res.append)
    _wait(qapp, lambda: res, 20)
    assert res == ["object"]                                  # Max Image Saver is running in the page
    logins.save(w.conn, "127.0.0.1", "mj", "pa55")
    b.fill_login()
    pump(qapp, 0.5)
    b.page.runJavaScript("document.querySelector('form').requestSubmit()", QWebEngineScript.ApplicationWorld)
    _wait(qapp, lambda: state["logins"], 30)
    assert "u=mj" in state["logins"][-1] and "p=pa55" in state["logins"][-1]
    _wait(qapp, lambda: offered, 10)
    assert offered[-1] == ("127.0.0.1", "mj", "pa55")
    # 1.57.1: one window - the browser on the left, the watched galleries on the right
    assert w.open_gallery_watcher() is b and b.panel.table is not None
    assert b.windowTitle().startswith("Gallery Watcher") and w.a_browser.text() == "Gallery Watcher"
    texts = [a.text() for a in b.findChildren(__import__("PySide6.QtWidgets", fromlist=["QToolBar"]).QToolBar)[0].actions()]
    assert texts.index("Watch this page") < texts.index("🔑 Fill login") < texts.index("Logins…")
    b.close()
    assert w.web_window is None
    w.close()
    pump(qapp, 0.3)
    import store
    library.conn = store.connect()


def test_watch_name_is_the_tag(library, qapp):
    """1.56.1: the gallery's name is who its pictures are tagged as (unless other names are typed)."""
    import watcher_ui

    class W:
        conn = library.conn
        settings = library.settings
    from PySide6.QtWidgets import QWidget
    host = QWidget()
    d = watcher_ui.WatchDialog(W(), parent=host, url="https://example.com/g")
    d.name.setText("")
    d.name.textEdited.emit("Jane Doe")
    assert d.fields.people.text() == "Jane Doe" and d.fields.own.isChecked()
    d.fields.people.setText("Jane Doe, Ann")
    d.fields.people.textEdited.emit("Jane Doe, Ann")
    d.name.textEdited.emit("Jane D")
    assert d.fields.people.text() == "Jane Doe, Ann"                  # typed names stay
    d.ok()
    import watcher
    assert watcher.get_watch(library.conn, d.w["id"])["people"] == ["Jane Doe", "Ann"]
    # an existing gallery with no names gets its name as the tag when opened
    wid = watcher.save_watch(library.conn, {"url": "https://example.com/h", "name": "Kim", "mode": "all"})
    d2 = watcher_ui.WatchDialog(W(), watcher.get_watch(library.conn, wid), parent=host)
    assert d2.fields.people.text() == "Kim"
    d2.reject()


def test_site_read_modes_and_tags(library, tmp_path, monkeypatch):
    """1.57.1: a site's own Max Image Saver reading (MM+, SuicideGirls sets, P+…) is under "Read:" for
    that site, and is what its galleries use unless set otherwise; MM+'s credited names become keywords."""
    import io
    import misbridge
    import watcher
    import xmp
    from PIL import Image
    keys = [k for k, _l in watcher.modes_for("www.modelmayhem.com".removeprefix("www."))]
    assert keys[0] == "mm" and "all" in keys and "gallery" in keys
    assert [k for k, _l in watcher.modes_for("suicidegirls.com")][0] == "sg"
    assert [k for k, _l in watcher.modes_for("example.com")] == ["all", "gallery"]
    w = {"url": "https://www.modelmayhem.com/123456", "mode": None}
    assert watcher.effective(library.conn, w)["mode"] == "mm"
    assert watcher.js_mode("mm") == "mm" and watcher.js_mode("flickr") == "all"
    buf = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(buf, "JPEG")
    monkeypatch.setattr(misbridge, "request", lambda *a, **k: (200, a[0], "image/jpeg", buf.getvalue()))
    path, _d = misbridge.save_item({"url": "https://x.example/p.jpg", "filename": "Modelmayhem/Ann/p.jpg",
                                    "tags": ["Ann Lee", "Bo Chen"], "datePrefix": False}, str(tmp_path))
    doc = xmp.XmpDoc(xmp.read_xmp(path))
    assert doc.get_bag(xmp.q("dc", "subject")) == ["Ann Lee", "Bo Chen"]


def test_read_list_follows_the_address(library, qapp):
    import watcher_ui

    class W:
        conn = library.conn
        settings = library.settings
    from PySide6.QtWidgets import QWidget
    host = QWidget()
    d = watcher_ui.WatchDialog(W(), parent=host, url="https://example.com/g")
    items = [d.fields.mode.itemData(i) for i in range(d.fields.mode.count())]
    assert items == [None, "all", "gallery"]
    d.url.setText("https://www.modelmayhem.com/portfolio/123456")
    items = [d.fields.mode.itemData(i) for i in range(d.fields.mode.count())]
    assert items == [None, "mm", "all", "gallery"]
    assert "MM+" in d.fields.mode.itemText(1)
