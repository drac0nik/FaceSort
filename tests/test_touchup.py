"""The touch-up brush: painted areas are filled in from around them."""
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
