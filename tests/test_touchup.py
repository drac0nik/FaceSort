"""The touch-up brush: painted areas are filled in from around them."""
import os
import time

import cv2
import numpy as np

import photo_tools
from conftest import pump


def scene(h=240, w=360, seed=3):
    rng = np.random.default_rng(seed)
    base = np.zeros((h, w, 3), np.float32)
    base[:] = (90, 140, 200)                                    # sky
    base[h // 2:] = (70, 120, 60)                               # grass
    base += rng.normal(0, 6, base.shape)
    return np.clip(base, 0, 255).astype(np.uint8)


def test_heal_removes_a_date_stamp():
    img = scene()
    clean = img.copy()
    cv2.putText(img, "98 12 25", (200, 220), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 140, 0), 2)
    mask = np.zeros(img.shape[:2], np.uint8)
    cv2.putText(mask, "98 12 25", (200, 220), cv2.FONT_HERSHEY_SIMPLEX, 0.8, 255, 8)
    out = photo_tools.heal(img, mask)
    area = mask > 0
    assert np.abs(out[area].astype(int) - clean[area]).mean() < 12      # back to grass
    assert (out[~cv2.dilate(mask, np.ones((15, 15), np.uint8)).astype(bool)] ==
            img[~cv2.dilate(mask, np.ones((15, 15), np.uint8)).astype(bool)]).all()  # nothing else touched
    assert photo_tools.heal(img, np.zeros_like(mask)) is img


def test_heal_wide_area_keeps_each_side():
    img = scene(480, 640)
    mask = np.zeros(img.shape[:2], np.uint8)
    cv2.circle(mask, (320, 240), 90, 255, -1)                   # across the horizon
    out = photo_tools.heal(img, mask)
    top, bottom = out[175:200, 300:340].reshape(-1, 3).mean(0), out[290:310, 300:340].reshape(-1, 3).mean(0)
    assert top[2] > 150 and bottom[1] > bottom[2]                # sky stays sky, grass stays grass
    assert out[200:280, 300:340].std() > 2                        # with grain, not a flat smear


def test_brush_window(qapp):
    import touchup
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    img = scene()
    cv2.circle(img, (100, 180), 6, (250, 250, 250), -1)           # a white spot on the grass
    d = touchup.TouchUpDialog(None, img, "test")
    d.resize(900, 700)
    d.show()
    pump(qapp, 0.2)
    c = d.canvas
    ox, oy = c.origin()
    s = c.scale()
    at = QPoint(int(ox + 100 * s), int(oy + 180 * s))
    d.size.setValue(int(30 * s))
    QTest.mousePress(c, Qt.LeftButton, Qt.NoModifier, at)
    QTest.mouseMove(c, at + QPoint(2, 0))
    QTest.mouseRelease(c, Qt.LeftButton, Qt.NoModifier, at + QPoint(2, 0))
    end = time.time() + 20
    while (d.busy() or not d.fills) and time.time() < end:
        pump(qapp, 0.05)
    assert d.fills == 1 and d.changed()
    assert d.rgb[174:186, 94:106].mean() < 160                   # the spot is gone
    d.do_undo()
    assert not d.changed() and d.rgb[180, 100].mean() > 240
    d.do_redo()
    assert d.changed()
    d.reject()


# ------------------------------------------------------------------ face clarity
def test_clarity_without_a_face_sharpens_only_where_painted(monkeypatch):
    monkeypatch.setattr(photo_tools, "clarity_ready", lambda: False)
    img = cv2.GaussianBlur(scene(), (0, 0), 2)
    mask = np.zeros(img.shape[:2], np.uint8)
    mask[60:180, 60:200] = 255
    out, faces = photo_tools.face_clarity(img, mask, 1.0)
    assert faces == 0

    def detail(a):
        return cv2.Laplacian(cv2.cvtColor(a, cv2.COLOR_RGB2GRAY), cv2.CV_32F).var()
    assert detail(out[80:160, 80:180]) > detail(img[80:160, 80:180]) * 1.3
    far = np.ones(mask.shape, bool)
    far[30:210, 30:230] = False
    assert (out[far] == img[far]).all()
    assert photo_tools.face_clarity(img, np.zeros_like(mask))[0] is img


def test_clarity_model_download_is_checked(tmp_path, monkeypatch):
    import hashlib
    import http.server
    import threading
    payload = b"model" * 1000
    (tmp_path / "gfpgan_1.4.onnx").write_bytes(payload)
    (tmp_path / "gfpgan_1.4.onnx.sha256").write_text(hashlib.sha256(payload).hexdigest() + "  gfpgan_1.4.onnx\n")

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(tmp_path), **k)

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/gfpgan_1.4.onnx"
    dest = tmp_path / "models" / "gfpgan_1.4.onnx"
    monkeypatch.setattr(photo_tools, "clarity_model_path", lambda: str(dest))
    monkeypatch.setattr(photo_tools, "CLARITY_URL", base)
    monkeypatch.setattr(photo_tools, "CLARITY_SHA_URL", base + ".sha256")
    assert photo_tools.download_clarity() and dest.read_bytes() == payload
    dest.unlink()
    (tmp_path / "gfpgan_1.4.onnx.sha256").write_text("00" * 32)
    import pytest
    with pytest.raises(RuntimeError, match="checksum"):
        photo_tools.download_clarity()
    assert not dest.exists() and not os.path.exists(str(dest) + ".part")
    srv.shutdown()


def test_brush_window_clarity_mode(qapp, monkeypatch):
    import touchup
    monkeypatch.setattr(photo_tools, "clarity_ready", lambda: True)
    monkeypatch.setattr(photo_tools, "face_clarity", lambda rgb, mask, k, cancelled=None: (rgb // 2, 1))
    img = scene()
    d = touchup.TouchUpDialog(None, img, "test", mode="clarity")
    d.show()
    pump(qapp, 0.1)
    assert d.clarity_mode() and d.strength.isVisible() and d.size.value() >= 60
    d.canvas.strokes, d.canvas.radii = [[(50, 50), (80, 60)]], [20]
    d.fill()
    end = time.time() + 10
    while (d.busy() or not d.fills) and time.time() < end:
        pump(qapp, 0.05)
    assert d.changed() and "made clearer" in d.status.text() and d.label() == "face clarity"
    d.heal_rb.setChecked(True)
    assert not d.strength.isVisible()
    d.reject()
