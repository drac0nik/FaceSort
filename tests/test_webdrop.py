"""1.54: pictures dragged in from a web browser."""
import base64
import http.server
import os
import struct
import threading
from io import BytesIO

import pytest
from PIL import Image


def _png(color=(10, 200, 30), size=(40, 30)):
    b = BytesIO()
    Image.new("RGB", size, color).save(b, "PNG")
    return b.getvalue()


@pytest.fixture
def server():
    png = _png()

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/pic"):
                body, ct = png, "image/png"
            elif self.path.startswith("/named"):
                body, ct = png, "application/octet-stream"
            else:
                body, ct = b"<html>sign in</html>", "text/html"
            self.send_response(200)
            self.send_header("Content-Type", ct)
            if self.path.startswith("/named"):
                self.send_header("Content-Disposition", 'attachment; filename="Holiday shot.png"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_download_and_checks(server):
    import webdrop
    data, name = webdrop.download(server + "/pic/cat.png?x=1")
    assert webdrop.sniff(data) == ".png" and name == "cat.png"
    data, name = webdrop.download(server + "/named/123")
    assert name == "Holiday shot.png"
    with pytest.raises(ValueError, match="web page"):
        webdrop.download(server + "/page")
    with pytest.raises(ValueError):
        webdrop.download("file:///etc/passwd")
    durl = "data:image/png;base64," + base64.b64encode(_png()).decode()
    data, name = webdrop.download(durl)
    assert name.endswith(".png")


def test_payload_and_save(qapp, tmp_path, server):
    import webdrop
    from PySide6.QtCore import QMimeData, QUrl
    from PySide6.QtGui import QImage
    # a picture inside a link: the picture's address comes first, the page's after
    md = QMimeData()
    md.setHtml(f'<a href="{server}/page"><img src="{server}/pic/dog.png?a=1&amp;b=2"></a>')
    md.setUrls([QUrl(server + "/page")])
    assert webdrop.has_web(md)
    pl = webdrop.payload(md)
    assert pl["urls"][0] == server + "/pic/dog.png?a=1&b=2"
    res = webdrop.save_all(pl, str(tmp_path / "Web drops"))
    assert res["how"] == ["download"] and os.path.basename(res["saved"][0]) == "dog.png"
    # the browser handed over the file itself (Windows): used as it is, no download
    md = QMimeData()
    desc = struct.pack("<I", 1) + b"\x00" * 72 + "beach.png".encode("utf-16-le").ljust(520, b"\x00")
    desc = desc.ljust(4 + 592, b"\x00")
    md.setData(webdrop.FILE_GROUP, desc)
    md.setData(webdrop.FILE_CONTENTS, _png((1, 2, 3)))
    md.setUrls([QUrl(server + "/page")])
    pl = webdrop.payload(md)
    res = webdrop.save_all(pl, str(tmp_path / "Web drops"), fetch=lambda u: pytest.fail("no download needed"))
    assert res["how"] == ["file"] and os.path.basename(res["saved"][0]) == "beach.png"
    # the download fails (needs signing in): the picture as the browser showed it
    md = QMimeData()
    md.setUrls([QUrl(server + "/page")])
    md.setImageData(QImage(20, 10, QImage.Format_RGB32))
    res = webdrop.save_all(webdrop.payload(md), str(tmp_path / "Web drops"))
    assert res["how"] == ["picture"] and res["errors"] and "web page" in res["errors"][0][1]
    # nothing usable: a page with no picture
    md = QMimeData()
    md.setUrls([QUrl(server + "/page")])
    res = webdrop.save_all(webdrop.payload(md), str(tmp_path / "x"))
    assert not res["saved"] and res["errors"]


def test_window_takes_a_web_drop(edit_window_like, qapp, server, monkeypatch):
    import time
    import webdrop
    from PySide6.QtCore import QMimeData, QUrl
    from conftest import pump
    w = edit_window_like
    got = []
    monkeypatch.setattr(w, "handle_drop", lambda paths, *a, **k: got.append(paths))
    md = QMimeData()
    md.setUrls([QUrl(server + "/pic/face.png")])
    w.web_drop(webdrop.payload(md))
    end = time.time() + 20
    while not got:
        pump(qapp, 0.05)
        assert time.time() < end
    p = got[0][0]
    assert os.path.exists(p) and os.path.basename(os.path.dirname(p)) == "Web drops"
    assert p.startswith(w.settings["folders"][0])          # inside the library: it stays in FaceSort


@pytest.fixture
def edit_window_like(data_dir, qapp):
    import facesort
    from conftest import pump
    w = facesort.MainWindow()
    w.show()
    pump(qapp, 0.3)
    yield w
    w.close()
    pump(qapp, 0.3)
