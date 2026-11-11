"""The second look at dark photos."""
import os

import cv2
import numpy as np
import pytest

import engine
import jobs
import store


def scene(level, seed=0, h=240, w=320):
    rnd = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.dstack([xx / w * 200 + 30, yy / h * 180 + 40, (xx + yy) / (w + h) * 150 + 60]) * level
    return np.clip(img + rnd.normal(0, 2, img.shape), 0, 255).astype(np.uint8)


def test_brightness_and_darkness():
    mean, p30 = engine.brightness(scene(1.0))
    assert mean > 0.4 and not engine.is_dark(mean, p30)
    mean, p30 = engine.brightness(scene(0.15))
    assert mean < 0.15 and engine.is_dark(mean, p30)
    backlit = scene(1.0)
    backlit[:, :200] = (backlit[:, :200] * 0.12).astype(np.uint8)       # people against a bright window
    assert engine.is_dark(*engine.brightness(backlit))


def test_brightened_copy():
    assert engine.brightened(scene(1.0)) is None                       # a normal photo isn't touched
    dark = scene(0.12)
    out = engine.brightened(dark)
    assert out is not None and out.shape == dark.shape and out.dtype == np.uint8
    assert out.mean() > dark.mean() * 2


class FakeEngine:
    """Finds a 'face' only where the picture is bright enough (like a real detector in the dark)."""
    threshold = 0.7

    def __init__(self, boxes):
        self.boxes = boxes

    def detect(self, bgr):
        out = []
        for x, y, w, h in self.boxes:
            if bgr[y:y + h, x:x + w].mean() > 60:
                row = np.zeros(15, np.float32)
                row[:4] = (x, y, w, h)
                row[14] = 0.9
                out.append(row)
        return out


def test_second_look_adds_faces_only_found_brightened(monkeypatch):
    monkeypatch.setattr(engine, "DARK_SECOND_LOOK", True)
    dark = scene(0.12)
    eng = FakeEngine([(40, 40, 50, 60), (200, 60, 50, 60)])
    known = np.zeros(15, np.float32)
    known[:4] = (42, 42, 48, 58)                                         # one already found normally
    extra, bright = engine.second_look(eng, dark, [(known, dark, None)], 0.78)
    assert bright is not None
    assert [tuple(int(v) for v in f[:4]) for f, _w, _r in extra] == [(200, 60, 50, 60)]
    assert all(w is bright for _f, w, _r in extra)


def test_second_look_off_or_bright(monkeypatch):
    eng = FakeEngine([(40, 40, 50, 60)])
    monkeypatch.setattr(engine, "DARK_SECOND_LOOK", False)
    assert engine.second_look(eng, scene(0.12), [], 0.78) == ([], None)
    monkeypatch.setattr(engine, "DARK_SECOND_LOOK", True)
    assert engine.second_look(eng, scene(1.0), [], 0.78) == ([], None)


def test_second_look_needs_a_surer_face(monkeypatch):
    monkeypatch.setattr(engine, "DARK_SECOND_LOOK", True)
    eng = FakeEngine([(200, 60, 50, 60)])
    extra, _ = engine.second_look(eng, scene(0.12), [], 0.95)          # 0.9 isn't sure enough
    assert extra == []


@pytest.mark.skipif(not os.path.exists(engine.DET_MODEL), reason="face models not present")
def test_real_engine_marks_photos(monkeypatch):
    monkeypatch.setattr(engine, "DARK_SECOND_LOOK", True)
    eng = engine.FaceEngine(min_face_px=16, gpu=None)
    img = engine.LoadedImage(scene(0.12), 320, 240, 1)
    eng.process_image(img)
    assert img.dark_seen == 1 and img.bright < 0.2
    vid = engine.LoadedImage(scene(0.12), 320, 240, 1)
    vid.dark_seen = -1                                                   # video frames: no second look
    eng.process_image(vid)
    assert vid.dark_seen == 0


def test_find_dark_photos(data_dir):
    conn = store.connect()
    paths = {}
    for name, level in (("dark.jpg", 0.12), ("light.jpg", 1.0), ("backlit.jpg", None)):
        p = os.path.join(str(data_dir.lib), name)
        img = scene(level or 1.0)
        if level is None:
            img[:, :200] = (img[:, :200] * 0.12).astype(np.uint8)
        cv2.imwrite(p, img[:, :, ::-1].copy())
        conn.execute("INSERT INTO photos(path, mtime, size, is_video) VALUES(?, 1, 1, 0)", (p,))
        paths[name] = p
    conn.commit()
    assert jobs.find_dark_photos(conn) == 2
    got = {os.path.basename(p): (m, d) for p, m, d in conn.execute("SELECT path, mtime, dark_seen FROM photos")}
    assert got["dark.jpg"][0] is None and got["backlit.jpg"][0] is None        # scanned again next time
    assert got["light.jpg"] == (1, 1)                                           # left alone, never checked again
    assert jobs.find_dark_photos(conn) == 2                                     # (until they're scanned)
    conn.close()


def test_scan_stores_brightness_and_gps(data_dir):
    import types
    conn = store.connect()
    p = os.path.join(str(data_dir.lib), "x.jpg")
    cv2.imwrite(p, scene(1.0))
    img = types.SimpleNamespace(width=320, height=240, orientation=1, taken=1.6e9, cover_t=None, bright=0.55,
                                dark_seen=1, gps=(51.5, -0.12))
    pid = store.store_photo(conn, p, 1.0, 10, img, [], settings={})
    conn.commit()
    assert conn.execute("SELECT bright, dark_seen, lat, lon, body_done FROM photos WHERE id=?", (pid,)).fetchone() == \
        (0.55, 1, 51.5, -0.12, 0)
    conn.close()


def test_exif_gps():
    from PIL import Image
    im = Image.new("RGB", (8, 8))
    ex = im.getexif()
    gps = ex.get_ifd(0x8825)
    gps.update({1: "N", 2: (51.0, 30.0, 0.0), 3: "W", 4: (0.0, 7.0, 30.0)})
    assert engine.exif_gps(ex) == (51.5, -0.125)
