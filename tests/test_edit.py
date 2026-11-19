"""1.54: edit mode - the picture math, the photo edit panel (save copy / replace original), video
edit (trim, cut, turn), Settings > Viewer."""
import os
import subprocess
import time

import numpy as np
import pytest

import photoedit
from conftest import build_library, pump


@pytest.fixture
def edit_window(data_dir, qapp):
    import sys
    import facesort
    from PySide6.QtCore import QRect
    lib = build_library(data_dir, per_person=3, strangers=0)
    lib.conn.close()
    errors = []
    old_hook = sys.excepthook
    sys.excepthook = lambda t, e, tb: errors.append("".join(__import__("traceback").format_exception(t, e, tb)))
    w = facesort.MainWindow()
    w.setGeometry(QRect(30, 30, 1400, 860))
    w.show()

    def idle():
        pump(qapp, 0.3)
        end = time.time() + 90
        while time.time() < end and (w.busy() or (w.regroup_worker and w.regroup_worker.isRunning())
                                     or w.regroup_timer.isActive() or w.bg_running()):
            pump(qapp, 0.1)
        pump(qapp, 0.2)
    idle()
    w.idle = idle
    w.lib = lib
    yield w
    idle()
    w.close()
    pump(qapp, 0.3)
    sys.excepthook = old_hook
    assert not errors, "\n".join(errors)


def test_photo_math():
    rgb = np.zeros((100, 200, 3), np.uint8)
    rgb[:, :100] = (40, 60, 200)            # blue half
    rgb[:, 100:] = (200, 40, 40)            # red half
    p = photoedit.defaults()
    assert (photoedit.render(rgb, p) == rgb).all()                       # nothing changed: the same
    p["turn"] = 1
    assert photoedit.render(rgb, p).shape == (200, 100, 3)
    p = photoedit.defaults()
    p["crop"] = (0.25, 0.0, 0.5, 1.0)
    assert photoedit.render(rgb, p).shape == (100, 100, 3)
    p = dict(photoedit.defaults(), straighten=5.0)
    assert photoedit.render(rgb, p).shape == rgb.shape                   # turned, no empty corners
    p = dict(photoedit.defaults(), pop={"hue": 220, "sens": 45})          # keep the blues
    out = photoedit.render(rgb, p)
    left, right = out[50, 50].astype(int), out[50, 150].astype(int)
    assert left[2] - left[0] > 100                                       # blue stays blue
    assert abs(right[0] - right[1]) < 3 and abs(right[1] - right[2]) < 3  # red went grey
    grey = np.full((50, 50, 3), 128, np.uint8)
    assert photoedit.render(grey, dict(photoedit.defaults(), vignette=-100)).max() == 0
    assert photoedit.render(grey, dict(photoedit.defaults(), vignette=100)).min() == 255
    half = photoedit.render(grey, dict(photoedit.defaults(), vignette=-40))
    assert half[25, 25].mean() > half[0, 0].mean()                        # corners darker than the middle
    assert photoedit.render(grey, dict(photoedit.defaults(), exposure=50)).mean() > 200
    bw = photoedit.render(rgb, dict(photoedit.defaults(), bw=True))
    assert (bw[..., 0] == bw[..., 1]).all()
    vib = photoedit.render(np.full((10, 10, 3), (120, 110, 100), np.uint8), dict(photoedit.defaults(), vibrance=80))
    assert vib[0, 0, 0] - vib[0, 0, 2] > 20
    # the white balance eyedropper makes the sampled color neutral
    t, tint = photoedit.wb_from_sample((200, 180, 150))
    fixed = photoedit.render(np.full((4, 4, 3), (200, 180, 150), np.uint8), dict(photoedit.defaults(), temp=t, tint=tint))
    assert np.ptp(fixed[0, 0].astype(int)) <= 6
    assert photoedit.aspect_crop(400, 300, 1.0) == (0.125, 0.0, 0.75, 1.0)
    x, y, w, h = photoedit.fit_aspect((0.1, 0.1, 0.5, 0.2), 400, 300, 1.0)
    assert abs(w * 400 - h * 300) < 1e-6


def _clip(tmp_path, name="v.mp4", secs=4):
    import engine
    exe = engine.ffmpeg_exe()
    if not exe:
        pytest.skip("no ffmpeg")
    p = str(tmp_path / name)
    subprocess.run([exe, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
                    "-f", "lavfi", "-i", "sine=f=440", "-t", str(secs), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-shortest", p], check=True)
    return p


def test_video_trim_cut_turn(tmp_path):
    import engine
    import videoedit
    src = _clip(tmp_path)
    assert videoedit.kept_parts(4, 0.5, 3.5, [(1, 2)]) == [(0.5, 1), (2, 3.5)]
    assert videoedit.kept_parts(4, None, None, [(0, 4)]) == []
    out = videoedit.edit_video(None, src, {"keep": [(0.5, 1), (2, 3.5)], "turn": 1})
    assert out.endswith("v (edited).mp4") and os.path.exists(src)
    W, H, fps, dur = engine.ffmpeg_info(out)
    assert (W, H) == (240, 320) and abs(dur - 2.0) < 0.1                  # turned; 0.5 s + 1.5 s kept
    out2 = videoedit.edit_video(None, src, {"keep": [(0, 1)], "strength": 1, "steady": True})
    assert out2.endswith("(edited) (2).mp4") and abs(engine.ffmpeg_info(out2)[3] - 1.0) < 0.15
    assert not [f for f in os.listdir(tmp_path) if f.startswith(".fsedit") or f.endswith(".part.mp4")]


def _open_viewer(w, qapp, pid=None):
    alice = w.conn.execute("SELECT id FROM persons WHERE name='Alice'").fetchone()[0]
    w.view = ("person", alice)
    w.refresh_grid()
    i = next(i for i, r in enumerate(w.model.rows) if r[0])
    w.open_viewer(w.model.index(i))
    pump(qapp, 0.8)
    return w.viewer


def _wait(qapp, cond, secs=20):
    end = time.time() + secs
    while not cond():
        pump(qapp, 0.05)
        assert time.time() < end, "timed out"


def test_photo_edit_mode(edit_window, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    w = edit_window
    v = _open_viewer(w, qapp)
    path = v.path
    QTest.keyClick(v.canvas, Qt.Key_E)
    assert v.editing == "photo" and v.ed_panel.isVisible() and not v.tag_col.isVisible()
    assert v.boxes_hidden()
    before = v.image
    v.ed_panel.rows["exposure"].set_value(40)
    _wait(qapp, lambda: v.image is not before and v.ed_qimg is not None)
    assert len(v.ed_undo_stack) == 2
    v.ed_undo()
    assert v.ed_panel.p["exposure"] == 0
    v.ed_redo()
    assert v.ed_panel.p["exposure"] == 40
    # the eyedropper and the crop
    v.ed_panel.pop_pick.setChecked(True)
    assert v.ed_pick == "pop"
    _wait(qapp, lambda: v.ed_geo is not None)
    v.ed_picked(0.5, 0.5)
    assert v.ed_panel.p["pop"] and v.ed_pick == ""
    v.ed_panel.crop_b.setChecked(True)
    v.ed_crop_drawn((0.1, 0.1, 0.5, 0.5))
    assert v.ed_panel.p["crop"]
    v.ed_panel.crop_b.setChecked(False)
    v.ed_save(False)
    _wait(qapp, lambda: v.editing is None)
    copy = os.path.join(os.path.dirname(path), os.path.splitext(os.path.basename(path))[0] + " (edited).jpg")
    assert os.path.exists(copy) and os.path.exists(path)
    assert w.conn.execute("SELECT copy_of FROM photos WHERE path=?", (copy,)).fetchone()[0]
    # replace the original (the question answered yes)
    from PySide6.QtWidgets import QMessageBox
    orig_size = os.path.getsize(path)
    QMessageBox_question = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    try:
        v.enter_edit()
        v.ed_panel.rows["contrast"].set_value(30)
        v.ed_panel.bw.setChecked(True)
        v.ed_save(True)
        _wait(qapp, lambda: v.editing is None)
    finally:
        QMessageBox.question = QMessageBox_question
    stem, ext = os.path.splitext(path)
    backup = f"{stem} (original){ext}"
    assert os.path.exists(backup) and os.path.getsize(backup) == orig_size and os.path.exists(path)
    assert w.conn.execute("SELECT discarded FROM photos WHERE path=?", (backup,)).fetchone()[0] == 1
    from PIL import Image
    with Image.open(path) as im:
        px = im.convert("RGB").getpixel((5, 5))
    assert px[0] == px[1] == px[2] or max(px) - min(px) <= 2              # black & white now
    v.close_viewer()


def test_video_edit_mode(edit_window, qapp, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    import store
    w = edit_window
    src = _clip(w.lib.dd.lib, "clip.mp4")
    w.conn.execute("INSERT INTO photos(path, is_video, mtime, size) VALUES(?, 1, 1, 1)", (store.norm_path(src),))
    w.conn.commit()
    pid = w.conn.execute("SELECT id FROM photos WHERE path=?", (store.norm_path(src),)).fetchone()[0]
    v = _open_viewer(w, qapp)
    v.open_items([(-pid, store.norm_path(src))])
    pump(qapp, 0.5)
    assert v.is_video
    QTest.keyClick(v.canvas, Qt.Key_E)
    assert v.editing == "video" and v.ved_panel.isVisible()
    vp = v.ved_panel
    vp.set_start(1.0)
    vp.set_end(3.0)
    vp.cut_here(1.5)
    vp.cut_here(2.0)
    vp.set_turn(3)
    assert vp.keep() == [(1.0, 1.5), (2.0, 3.0)] and v.seek.cuts == [(1.5, 2.0)]
    v.ved_save()
    assert v.editing is None
    w.idle()
    out = os.path.join(os.path.dirname(src), "clip (edited).mp4")
    _wait(qapp, lambda: os.path.exists(out), 60)
    import engine
    W, H, _f, dur = engine.ffmpeg_info(out)
    assert (W, H) == (240, 320) and abs(dur - 1.5) < 0.15


def test_viewer_settings_tab(edit_window):
    from dialogs import SettingsDialog
    w = edit_window
    d = SettingsDialog(w, w.settings)
    names = [d.tabs.tabText(i) for i in range(d.tabs.count())]
    assert "Viewer" in names
    vp = d.viewer_page
    vp.slide.setCurrentIndex(vp.slide.findData(5))
    vp.keep_speed.setCurrentIndex(1)
    vp.copy_name.setText("{name} - mine")
    s = d.result_settings()
    assert s["viewer_slideshow_s"] == 5 and s["video_keep_speed"] is True and s["edit_copy_name"] == "{name} - mine"
    d.reject()
