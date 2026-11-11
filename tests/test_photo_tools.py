"""Photo viewer tools: red eye, upscaling, enhancing, restoring, shadow smoothing."""
import os

import cv2
import numpy as np
import pytest

import engine
import photo_tools as pt


def face_with_red_eyes(w=400, h=300):
    rgb = np.full((h, w, 3), (205, 160, 135), np.uint8)              # skin
    box = (0.3, 0.2, 0.4, 0.6)                                       # face box (0-1)
    bx, by, bw, bh = box[0] * w, box[1] * h, box[2] * w, box[3] * h
    eyes = [(int(bx + bw * 0.32), int(by + bh * 0.40)), (int(bx + bw * 0.68), int(by + bh * 0.40))]
    yy, xx = np.mgrid[0:h, 0:w]
    for ex, ey in eyes:
        rgb[(xx - ex) ** 2 + (yy - ey) ** 2 <= 8 ** 2] = (235, 235, 235)   # the white of the eye
        rgb[(xx - ex) ** 2 + (yy - ey) ** 2 <= 5 ** 2] = (210, 25, 30)     # red pupil
    return rgb, box, eyes


def test_red_eye_fixes_both_pupils():
    rgb, box, eyes = face_with_red_eyes()
    out, fixed = pt.red_eye(rgb, hints=[box])
    assert fixed == 2
    for ex, ey in eyes:
        r, g, b = out[ey, ex].astype(int)
        assert r - max(g, b) < 30, out[ey, ex]                      # no longer red
        assert out[ey, ex - 7].astype(int).mean() > 200              # the white is left alone
    assert np.array_equal(out[5:40, 5:40], rgb[5:40, 5:40])         # skin untouched


def test_red_eye_leaves_normal_eyes():
    rgb = np.full((300, 400, 3), (205, 160, 135), np.uint8)
    out, fixed = pt.red_eye(rgb, hints=[(0.3, 0.2, 0.4, 0.6)])
    assert fixed == 0 and np.array_equal(out, rgb)


def test_upscale_choices():
    ch = pt.upscale_choices(1000, 750)
    assert [(f, w, h) for f, w, h, _ok in ch] == [(2, 2000, 1500), (4, 4000, 3000), (8, 8000, 6000)]
    assert all(ok for *_x, ok in ch)
    big = pt.upscale_choices(6000, 4000)
    assert big[0][3] and not big[2][3]                                # x8 would be 1.5 gigapixels


@pytest.mark.skipif(not os.path.exists(pt.UPSCALE_MODEL), reason="upscaling model not present")
@pytest.mark.parametrize("factor", [2, 4, 8])
def test_upscale_sizes(factor):
    rgb = np.random.default_rng(1).integers(0, 255, (24, 32, 3), dtype=np.uint8)
    out = pt.upscale(rgb, factor)
    assert out.shape == (24 * factor, 32 * factor, 3) and out.dtype == np.uint8


def test_upscale_refuses_huge():
    with pytest.raises(ValueError):
        pt.upscale(np.zeros((20000, 20000, 3), np.uint8), 8)


def photo(seed=0, h=240, w=320):
    rnd = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    rgb = np.dstack([xx / w * 200 + 20, yy / h * 180 + 30, (xx + yy) / (w + h) * 160 + 40])
    return np.clip(rgb + rnd.normal(0, 6, rgb.shape), 0, 255).astype(np.uint8)


@pytest.mark.parametrize("fn", [pt.auto_enhance, pt.restore_faded, lambda x: pt.smooth_shadows(x),
                                lambda x: pt.smooth_shadows(x, lift=True)])
def test_enhancers_keep_shape_and_change_something(fn):
    rgb = photo()
    out = fn(rgb)
    assert out.shape == rgb.shape and out.dtype == np.uint8
    assert not np.array_equal(out, rgb)


def test_restore_faded_brings_back_contrast():
    faded = (photo(2).astype(np.float32) * 0.35 + 140).astype(np.uint8)      # washed out
    out = pt.restore_faded(faded)
    assert out.astype(np.float32).std() > faded.astype(np.float32).std() * 1.5


def test_monochrome_detection():
    gray = np.repeat(photo()[:, :, :1], 3, axis=2)
    sepia = (gray.astype(np.float32) * np.array([1.0, 0.85, 0.65])).astype(np.uint8)
    assert pt.is_monochrome(gray) and pt.is_monochrome(sepia)
    assert not pt.is_monochrome(photo())


def test_smooth_shadows_smooths_dark_noise_and_keeps_black():
    rnd = np.random.default_rng(5)
    rgb = np.zeros((240, 320, 3), np.float32)
    rgb[:, 160:] = 25                                                   # a dark area
    noise = cv2.GaussianBlur(rnd.normal(0, 6, (240, 160, 3)).astype(np.float32), (0, 0), 2)
    rgb[:, 160:] += noise * (6 / noise.std())                           # blotchy noise
    rgb[60:180, 200:280] = 200                                          # something bright
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    out = pt.smooth_shadows(rgb)
    dark_in = rgb[10:50, 170:310].astype(np.float32)
    dark_out = out[10:50, 170:310].astype(np.float32)
    assert dark_out.std() < dark_in.std() * 0.7                         # smoother
    assert abs(dark_out.mean() - dark_in.mean()) < 6                    # not brightened
    assert out[:, :150].max() <= 6                                      # black stays black
    assert abs(out[100:140, 220:260].astype(float).mean() - 200) < 6     # bright parts unchanged


@pytest.mark.skipif(not pt.color_ready(), reason="colorizing model not downloaded")
def test_colorize_gives_colour():
    gray = np.repeat(photo()[:, :, :1], 3, axis=2)
    out = pt.colorize(gray)
    assert out.shape == gray.shape and not pt.is_monochrome(out)


def test_save_and_out_path(tmp_path):
    src = tmp_path / "a.jpg"
    rgb = photo()
    pt.save(rgb, str(src))
    p1 = pt.out_path(str(src), "enhanced")
    assert os.path.dirname(p1) == str(tmp_path) and "enhanced" in os.path.basename(p1)
    pt.save(rgb, p1)
    assert pt.out_path(str(src), "enhanced") != p1                       # never over an existing file
    back = pt.load_full(p1)
    back = back[0] if isinstance(back, tuple) else back
    assert back.shape == rgb.shape
