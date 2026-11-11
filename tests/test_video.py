"""Video copies: frame rates 24-60, the marked section only, naming that never overwrites."""
import os

import cv2
import numpy as np
import pytest

import engine
import jobs


def make_clip(path, seconds=3, fps=15, size=(160, 120)):
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    for i in range(int(seconds * fps)):
        f = np.zeros((size[1], size[0], 3), np.uint8)
        x = int(i / (seconds * fps) * (size[0] - 20))
        f[40:80, x:x + 20] = (60, 90, 200)                         # a moving block
        f += np.uint8(25)
        vw.write(f)
    vw.release()
    return str(path)


def frames_in(path):
    cap = cv2.VideoCapture(path)
    n = 0
    while cap.read()[0]:
        n += 1
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    return n, fps


def test_frame_span():
    assert engine.frame_span(100, 25) == (0, 100)
    assert engine.frame_span(100, 25, 1.0, 2.0) == (25, 50)
    assert engine.frame_span(100, 25, None, 2.0) == (0, 50)
    assert engine.frame_span(100, 25, 3.0, None) == (75, 100)
    assert engine.frame_span(100, 25, 10.0, 20.0) == (99, 100)      # past the end: still one frame
    assert engine.frame_span(100, 25, 2.0, 1.0) == (50, 51)         # end before start: one frame


def test_fps_choices_are_24_to_60():
    vals = [v for v, _ in jobs.FPS_CHOICES]
    assert min(vals) >= 24 and max(vals) <= 60 and 30.0 in vals and 60.0 in vals


def test_copy_name(tmp_path):
    src = str(tmp_path / "clip.mp4")
    assert os.path.basename(jobs.copy_name(src, {"strength": 1}, 30)) == "clip - low light.mp4"
    assert os.path.basename(jobs.copy_name(src, {"strength": 2, "fps": 60.0}, 30)) == "clip - low light strong 60fps.mp4"
    assert os.path.basename(jobs.copy_name(src, {"fps": 30.0}, 30)) == "clip - copy.mp4"      # same rate: not named
    name = os.path.basename(jobs.copy_name(src, {"start": 65, "end": 130}, 30))
    assert name == "clip - 1m05s-2m10s.mp4"
    open(tmp_path / "clip - low light.mp4", "w").close()
    assert os.path.basename(jobs.copy_name(src, {"strength": 1}, 30)) == "clip - low light (2).mp4"


def test_enhanced_copy_of_a_section_at_30fps(data_dir, tmp_path):
    import store
    src = make_clip(tmp_path / "clip.mp4", seconds=4, fps=15)
    conn = store.connect()
    out, done, total = jobs.enhance_video(conn, src, {"strength": 1, "fps": 30.0, "method": "blend",
                                                      "start": 1.0, "end": 3.0})
    conn.close()
    assert out and os.path.exists(out) and not os.path.exists(out + ".part.mp4")
    assert total == 30 and done == 30                                   # 2 s of the 15 fps original
    n, fps = frames_in(out)
    assert abs(fps - 30) < 0.5
    assert 54 <= n <= 66                                                # about 2 s at 30 fps


def test_cancel_leaves_nothing(data_dir, tmp_path):
    import store
    src = make_clip(tmp_path / "clip.mp4", seconds=2, fps=15)
    conn = store.connect()
    out, done, total = jobs.enhance_video(conn, src, {"strength": 1}, cancelled=lambda: True)
    conn.close()
    assert out is None
    assert [f for f in os.listdir(tmp_path) if f != "clip.mp4" and f != "data" and f != "lib"] == []
