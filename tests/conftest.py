"""Shared test fixtures: a temporary data folder and a small synthetic library.

The library is built without any face models: each "person" is a random direction in
embedding space and their faces are noisy copies of it, so grouping, naming, duplicates,
albums and the window can all be tested quickly and the same way every time.
"""
import io
import json
import os
import random
import sys
import time
import types

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FACESORT_NO_PROMPT"] = "1"
os.environ["FACESORT_NO_UPDATE_CHECK"] = "1"
os.environ["FACESORT_NO_STALLWATCH"] = "1"

from PIL import Image  # noqa: E402

DIM = 128


def unit(v):
    v = np.asarray(v, np.float32)
    return v / (np.linalg.norm(v) or 1)


def jpeg_bytes(img, q=85):
    b = io.BytesIO()
    img.save(b, "JPEG", quality=q)
    return b.getvalue()


def write_photo(path, seed, size=(320, 240)):
    """A unique little picture (random blocks of colour) saved as JPEG."""
    rnd = np.random.default_rng(seed)
    small = rnd.integers(0, 255, (6, 8, 3), dtype=np.uint8)
    img = Image.fromarray(small).resize(size, Image.NEAREST)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, "JPEG", quality=90)
    return img


def face_dict(emb, box=(0.35, 0.25, 0.3, 0.4), clarity=0.9):
    thumb = Image.new("RGB", (48, 48), (200, 160, 140))
    return {"x": box[0], "y": box[1], "w": box[2], "h": box[3], "score": 0.95, "px": 96,
            "emb": unit(emb), "thumb": jpeg_bytes(thumb), "quality": 0.8, "clarity": clarity}


def img_info(img, taken):
    return types.SimpleNamespace(width=img.width, height=img.height, orientation=1, taken=taken, cover_t=None)


def add_photo(conn, path, emb_list, seed, hints=(), taken=None, settings=None, size=(320, 240), clarity=0.9):
    import store
    img = write_photo(path, seed, size)
    st = os.stat(path)
    cl = clarity if isinstance(clarity, (list, tuple)) else [clarity] * len(emb_list)
    faces = [face_dict(e, box=(0.05 + 0.3 * i, 0.25, 0.25, 0.4), clarity=c) for i, (e, c) in enumerate(zip(emb_list, cl))]
    return store.store_photo(conn, path, st.st_mtime, st.st_size, img_info(img, taken or 1.6e9 + seed),
                             faces, hints=hints, settings=settings or {})


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """An empty FaceSort data folder + library folder, with settings that never scan or prompt."""
    import dbgate
    d = tmp_path / "data"
    lib = tmp_path / "lib"
    d.mkdir()
    lib.mkdir()
    monkeypatch.setenv("FACESORT_DATA", str(d))
    settings = {"folders": [str(lib)], "scan_on_start": False, "engine": "sface", "settings_version": 99,
                "first_run_done": True, "engine_asked": True, "watch_folders": False}
    (d / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    dbgate.GATE.reset_stats()
    yield types.SimpleNamespace(root=tmp_path, data=d, lib=lib)
    held = dbgate.GATE.owner is not None
    where = dbgate.GATE.where
    if held:                                   # don't let one failure hang every later test
        with dbgate.GATE.cond:
            dbgate.GATE.owner = None
            dbgate.GATE.cond.notify_all()
    assert not held, f"write gate still held by {where}"


class Library(types.SimpleNamespace):
    pass


def build_library(dd, people=("Alice", "Bob", "Carol"), per_person=6, strangers=2, per_stranger=4,
                  named_each=2, folders=("Holiday 2019", "Family/Birthday"), seed=7):
    """people: named (the first named_each photos of each carry a name tag);
    strangers: unnamed people who should end up as look-alike groups."""
    import store
    rnd = np.random.default_rng(seed)
    random.seed(seed)
    conn = store.connect()
    settings = store.load_settings()
    who = {}                                   # photo id -> person label
    base = {}
    labels = list(people) + [f"stranger{i}" for i in range(strangers)]
    for lab in labels:
        base[lab] = unit(rnd.normal(size=DIM))
    n = 0
    for lab in labels:
        count = per_person if lab in people else per_stranger
        for k in range(count):
            emb = base[lab] + unit(rnd.normal(size=DIM)) * 0.55
            folder = folders[n % len(folders)]
            path = os.path.join(str(dd.lib), *folder.split("/"), f"{lab}_{k}.jpg")
            hints = ()
            if lab in people and k < named_each:
                hints = ((lab, 0.05, 0.25, 0.25, 0.4),)
            pid = add_photo(conn, path, [emb], seed=1000 + n, hints=hints, settings=settings)
            who[pid] = lab
            n += 1
    conn.commit()
    store.regroup(conn, store.threshold(settings))
    conn.commit()
    return Library(conn=conn, settings=settings, who=who, people=list(people), base=base, dd=dd)


@pytest.fixture
def library(data_dir):
    lib = build_library(data_dir)
    yield lib
    lib.conn.close()


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app
    # tidy up while Qt is still fully alive (left to interpreter shutdown, the order in which
    # Python and Qt free things is random, and can crash on the way out)
    import gc
    from PySide6.QtCore import QCoreApplication, QEvent
    for w in app.topLevelWidgets():
        w.close()
        w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    gc.collect()


def pump(app, seconds=0.1):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)
