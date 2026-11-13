"""Smart Groups (1.42): Unknown faces / Partially tagged with Men / Unsure, untagged-only groups,
No faces found, the Other list's order, Not where they belong vs. the Sort into folders count."""
import os
import time

import numpy as np
import pytest

import face_attrs
import jobs
import store
from conftest import add_photo, pump, unit
from test_ui import all_keys, window  # noqa: F401  (the window fixture)


def face_ids(conn, photo):
    return [r[0] for r in conn.execute("SELECT id FROM faces WHERE photo_id=? ORDER BY x", (photo,))]


def button_texts(w):
    return [w.btn_row.itemAt(i).widget().toolTip() or w.btn_row.itemAt(i).widget().text()
            for i in range(w.btn_row.count()) if w.btn_row.itemAt(i).widget()]


def shown(w, key):
    w.view = key
    w.refresh_grid()
    return {r[0] for r in w.model.rows if r[0] > 0}


@pytest.fixture
def photos(window):
    """Four photos with faces nobody looks like (so they aren't grouped):
    solo (untagged), duo (2 faces, untagged), tagged (2 faces, one confirmed), folder (1 face, has a folder)."""
    w = window
    c = w.conn
    rng = np.random.default_rng(77)
    out = {}
    for name, n in (("solo", 1), ("duo", 2), ("tagged", 2), ("folder", 1)):
        p = os.path.join(str(w.lib.dd.lib), "Smart", f"{name}.jpg")
        out[name] = add_photo(c, p, [unit(rng.normal(size=128)) for _ in range(n)], seed=900 + len(out))
    c.commit()
    store.assign(c, face_ids(c, out["tagged"])[:1], store.get_or_create_person(c, "Dora"))
    c.execute("UPDATE photos SET category_id=? WHERE id=?", (store.create_category(c, "Parade", None, None),
                                                              out["folder"]))
    c.execute("UPDATE faces SET cluster=NULL, person_id=CASE WHEN confirmed=1 THEN person_id END "
              f"WHERE photo_id IN ({','.join(map(str, out.values()))})")
    c.commit()
    return out


def test_unknown_faces_and_partially_tagged(window, photos):
    w, c = window, window.conn
    unk = shown(w, ("unknown", None))
    for name in ("solo", "duo"):
        assert set(face_ids(c, photos[name])) <= unk                # untagged: any number of faces
    assert not set(face_ids(c, photos["tagged"])) & unk               # someone's tagged
    assert not set(face_ids(c, photos["folder"])) & unk               # has a folder = sorted
    part = shown(w, ("partial", None))
    assert part == set(face_ids(c, photos["tagged"])[1:])             # the other face on the group photo
    w.rebuild_tree()
    keys = list(all_keys(w, w.tree2))
    assert ("partial", None) in keys
    assert w.title.text() == "Partially tagged"


def test_men_and_unsure(window, photos, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    w, c = window, window.conn
    solo, (d1, d2) = face_ids(c, photos["solo"])[0], face_ids(c, photos["duo"])
    c.execute("UPDATE faces SET male=0.97, yaw=5, px=120 WHERE id=?", (solo,))       # a man
    c.execute("UPDATE faces SET male=0.5, yaw=0, px=120 WHERE id=?", (d1,))          # can't tell
    c.execute("UPDATE faces SET male=0.95, yaw=80, px=120 WHERE id=?", (d2,))        # turned right round
    c.commit()
    face_attrs.update_guesses(c)
    w.settings["face_details"] = True
    w.rebuild_tree()
    keys = list(all_keys(w, w.tree2))
    assert ("unknown", "men") in keys and ("unknown", "unsure") in keys
    assert shown(w, ("unknown", "men")) == {solo}
    assert shown(w, ("unknown", "unsure")) == {d1, d2}
    assert w.title.text() == "Unknown faces · Unsure"
    assert any("Ignore all" in t for t in button_texts(w))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    w.ignore_all_shown()
    assert {r[0] for r in c.execute(f"SELECT ignored FROM faces WHERE id IN ({d1}, {d2})")} == {1}
    assert c.execute("SELECT ignored FROM faces WHERE id=?", (solo,)).fetchone()[0] == 0
    # no Men / Unsure parts when face details are off
    w.settings["face_details"] = False
    w.rebuild_tree()
    assert ("unknown", "men") not in list(all_keys(w, w.tree2))
    w.idle()


def test_ignore_all_only_on_men_and_unsure(window, photos):
    w = window
    for key in (("unknown", None), ("partial", None)):
        shown(w, key)
        assert not any("Ignore all" in t for t in button_texts(w))


def test_untagged_only_groups_and_no_faces(window, photos):
    w, c = window, window.conn
    lib = str(w.lib.dd.lib)
    bare = add_photo(c, os.path.join(lib, "Smart", "bare.jpg"), [], seed=950)
    drawn = add_photo(c, os.path.join(lib, "Smart", "drawn.jpg"), [], seed=951)
    filed = add_photo(c, os.path.join(lib, "Smart", "filed.jpg"), [], seed=952)
    c.execute("""INSERT INTO faces(photo_id, x, y, w, h, score, px, emb, manual, ignored, confirmed)
                 VALUES(?, 0.3, 0.3, 0.2, 0.2, 1, 80, ?, 1, 0, 0)""", (drawn, unit(np.ones(128)).tobytes()))
    c.execute("UPDATE photos SET category_id=(SELECT id FROM categories WHERE name='Parade') WHERE id=?", (filed,))
    c.commit()
    w.view = ("nofaces", None)
    w.refresh_grid()
    ids = {-r[0] for r in w.model.rows if r[0] < 0}
    assert bare in ids and drawn not in ids and filed not in ids
    # tattoo matches: only on photos nobody is tagged on
    dora = c.execute("SELECT id FROM persons WHERE name='Dora'").fetchone()[0]
    for ph in (photos["solo"], photos["tagged"]):
        c.execute("INSERT INTO tattoo_suggestions(photo_id, person_id, inliers) VALUES(?, ?, 30)", (ph, dora))
    c.commit()
    w.view = ("tattoos", None)
    w.refresh_grid()
    assert {-r[0] for r in w.model.rows if r[0] < 0} == {photos["solo"]}


def test_other_list_order(window, photos):
    w = window
    sec = None

    def other_names():
        nonlocal sec
        for i in range(w.tree2.topLevelItemCount()):
            t = w.tree2.topLevelItem(i)
            if t.text(0).startswith("Other"):
                sec = t
        return [sec.child(i).text(0) for i in range(sec.childCount())]
    w.settings["other_sort"] = "default"
    w.rebuild_tree()
    usual = other_names()
    assert usual[0].startswith("Unknown faces")
    w.settings["other_sort"] = "az"
    w.rebuild_tree()
    names = [n.lstrip("~").split("   ")[0].casefold() for n in other_names()]
    assert names == sorted(names)
    w.settings["other_sort"] = "most"
    w.rebuild_tree()
    counts = [int(n.replace(",", "").split()[-1]) if n.split()[-1].replace(",", "").isdigit() else
              int(n.replace(",", "").split()[-2]) for n in other_names()]
    assert counts == sorted(counts, reverse=True)
    w.settings["other_sort"] = "default"
    w.rebuild_tree()


def test_not_where_they_belong_only_for_files_moved_outside(data_dir):
    """A photo FaceSort put in the library and since tagged differently is just waiting to be sorted
    (it's counted on the Sort into folders button); one that turned up there by hand is listed."""
    from conftest import build_library
    lib = build_library(data_dir)
    c = lib.conn
    dest = data_dir.root / "Sorted"
    (dest / "Somebody").mkdir(parents=True)
    rng = np.random.default_rng(5)
    alice = c.execute("SELECT id FROM persons WHERE name='Alice'").fetchone()[0]
    placed = add_photo(c, str(dest / "Somebody" / "placed.jpg"), [unit(rng.normal(size=128))], seed=960)
    by_hand = add_photo(c, str(dest / "Somebody" / "byhand.jpg"), [unit(rng.normal(size=128))], seed=961)
    c.execute("UPDATE photos SET outside=0 WHERE id=?", (placed,))       # (as Sort into folders leaves it)
    for ph in (placed, by_hand):
        store.assign(c, face_ids(c, ph), alice)
    c.commit()
    assert c.execute("SELECT outside FROM photos WHERE id=?", (by_hand,)).fetchone()[0] == 1
    mis = {pid for pid, _, _ in jobs.misplaced(c, str(dest))}
    assert by_hand in mis and placed not in mis
    assert jobs.pending_moves(c, str(dest)) >= 2                         # both belong in Alice's folder
    # Sort into folders writes are remembered, so the scan doesn't call them "outside"
    store.mark_placed(c, str(dest / "Alice" / "x.jpg"))
    assert store.was_placed(c, str(dest / "Alice" / "x.jpg"))
    lib.conn.close()


def test_sort_into_folders_count_on_the_button(window, photos, tmp_path, qapp):
    w = window
    w.settings["export_dir"] = str(tmp_path)
    w.start_misplaced()
    end = time.time() + 15
    while (w.mis_worker.isRunning() or getattr(w, "_pending_moves", None) is None) and time.time() < end:
        pump(qapp, 0.05)
    assert w._pending_moves and f"({w._pending_moves:,})" in w.a_export.text()
    w.settings["export_dir"] = ""
    w.start_misplaced()
    assert w.a_export.text() == "Sort into folders…"


# ------------------------------------------------------------------ the face details models
def test_head_angle_from_points():
    p = np.zeros((68, 3))
    p[36], p[45], p[27], p[8] = (-30, 0, 0), (30, 0, 0), (0, -10, 0), (0, 60, 0)
    yaw, pitch, roll = face_attrs.head_angle(p)
    assert abs(yaw) < 1 and abs(pitch) < 1 and abs(roll) < 1
    a = np.radians(40)                                   # turned 40 degrees about the vertical axis
    R = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])
    yaw, pitch, roll = face_attrs.head_angle(p @ R.T)
    assert abs(abs(yaw) - 40) < 1 and abs(roll) < 1
    a = np.radians(20)                                   # tilted 20 degrees
    R = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
    assert abs(abs(face_attrs.head_angle(p @ R.T)[2]) - 20) < 1


def test_analyse_and_backfill(data_dir, monkeypatch):
    from conftest import build_library
    lib = build_library(data_dir)
    monkeypatch.setattr(face_attrs, "ready", lambda: True)

    def fake_run(name, x):
        n = x.shape[0]
        if name == "genderage.onnx":
            return np.tile(np.array([[0.0, 3.0, 0.3]], np.float32), (n, 1))     # clearly male
        pts = np.zeros((n, 1103 * 3), np.float32).reshape(n, -1, 3)
        pts[:, -68:][:, 36], pts[:, -68:][:, 45] = (-0.3, 0, 0), (0.3, 0, 0)
        pts[:, -68:][:, 27], pts[:, -68:][:, 8] = (0, -0.1, 0), (0, 0.6, 0)
        return pts.reshape(n, -1)
    monkeypatch.setattr(face_attrs, "_run", fake_run)
    n = face_attrs.read_missing(lib.conn)
    assert n == lib.conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0] > 0
    male, yaw = lib.conn.execute("SELECT MIN(male), MAX(ABS(yaw)) FROM faces").fetchone()
    assert male > 0.9 and yaw < 1
    assert face_attrs.read_missing(lib.conn) == 0                           # carries on where it left off
    lib.conn.close()


def test_side_on_faces_need_a_closer_match(data_dir):
    """The same face, once front-on and once side-on: only the front-on one is suggested."""
    from conftest import build_library
    lib = build_library(data_dir)
    c = lib.conn
    alice = c.execute("SELECT id FROM persons WHERE name='Alice'").fetchone()[0]
    rows = c.execute("SELECT id FROM faces WHERE person_id=? AND confirmed=0", (alice,)).fetchall()
    assert rows
    # turn every suggested Alice face fully side-on: the bar goes up by SIDE_ON_EXTRA
    c.execute("UPDATE faces SET yaw=90 WHERE person_id=? AND confirmed=0", (alice,))
    c.commit()
    before = len(rows)
    store.regroup(c, store.threshold(lib.settings))
    c.commit()
    after = c.execute("SELECT COUNT(*) FROM faces WHERE person_id=? AND confirmed=0", (alice,)).fetchone()[0]
    assert after <= before
    lib.conn.close()


# ------------------------------------------------------------------ corrections and learning (1.43)
def test_decide_rules():
    d = face_attrs.decide
    assert d(0.97, None, None, None, 120, 0) == 1 and d(0.05, None, None, None, 120, 0) == 0
    assert d(0.5, None, None, None, 120, 0) == 2 and d(0.97, None, None, None, 20, 0) == 2      # small
    assert d(0.97, None, None, 0, 120, 0) == 0                     # what you said wins
    assert d(0.97, None, None, None, 120, 0, person_sex=0) == 0    # then the person's Man / Woman
    assert d(0.97, None, 0, None, 120, 0) == 0                     # then a corrected look-alike
    assert d(0.97, 0.1, None, None, 120, 0) == 2                   # the two models disagree: unsure
    assert d(0.97, 0.9, None, None, 120, 0) == 1
    assert d(0.6, 0.99, None, None, 120, 0) == 1 and d(0.4, 0.01, None, None, 120, 0) == 0
    assert d(None, None, None, None, 120, 0) is None


def test_correcting_a_face_takes_it_out_of_men(window, photos, monkeypatch):
    w, c = window, window.conn
    solo = face_ids(c, photos["solo"])[0]
    c.execute("UPDATE faces SET male=0.97, yaw=0, px=120 WHERE id=?", (solo,))
    c.commit()
    face_attrs.update_guesses(c)
    w.settings["face_details"] = True
    assert solo in shown(w, ("unknown", "men"))
    w.grid.setCurrentIndex(w.model.index(next(i for i, r in enumerate(w.model.rows) if r[0] == solo)))
    w.set_looks([solo], 0)
    assert solo not in {r[0] for r in w.model.rows}                 # out of the list at once
    assert c.execute("SELECT male_set, sex_guess FROM faces WHERE id=?", (solo,)).fetchone() == (0, 0)
    assert solo in shown(w, ("unknown", None)) and solo not in shown(w, ("unknown", "men"))
    w.set_looks([solo], None)                                        # let FaceSort judge again
    assert c.execute("SELECT male_set, sex_guess FROM faces WHERE id=?", (solo,)).fetchone() == (None, 1)
    w.idle()


def test_learning_from_corrections(data_dir):
    """Two kinds of people: once FaceSort is told about some of each, look-alikes follow and its own
    model sorts the rest (the stored estimate is deliberately wrong for everyone here)."""
    from conftest import build_library
    lib = build_library(data_dir, people=("Ann", "Bea", "Cal", "Dan"), per_person=20, named_each=20, strangers=0)
    c = lib.conn
    rng = np.random.default_rng(3)
    # women's and men's fingerprints lean different ways (as real ones do)
    lean = unit(rng.normal(size=128))
    for (fid, person), in [((r[0], r[1]),) for r in c.execute("SELECT f.id, p.name FROM faces f JOIN persons p ON p.id=f.person_id")]:
        e = store.emb_from_blob(c.execute("SELECT emb FROM faces WHERE id=?", (fid,)).fetchone()[0]).astype(np.float32)
        e = unit(e + (1.5 if person in ("Cal", "Dan") else -1.5) * lean)
        c.execute("UPDATE faces SET emb=?, male=0.5, yaw=0, px=120 WHERE id=?", (e.astype(np.float32).tobytes(), fid))
    c.commit()
    ids = {n: c.execute("SELECT id FROM persons WHERE name=?", (n,)).fetchone()[0] for n in ("Ann", "Bea", "Cal", "Dan")}
    face_attrs.set_person_sex(c, [ids["Ann"]], 0)
    face_attrs.set_person_sex(c, [ids["Cal"]], 1)
    res = face_attrs.learn(c, "sface", store.threshold(lib.settings, "sface"))
    assert res["trained"] and res["labelled"] >= 40
    bea = [r[0] for r in c.execute("SELECT male_learned FROM faces WHERE person_id=?", (ids["Bea"],))]
    dan = [r[0] for r in c.execute("SELECT male_learned FROM faces WHERE person_id=?", (ids["Dan"],))]
    assert np.mean(bea) < 0.3 and np.mean(dan) > 0.7               # people it was never told about
    # a corrected unnamed face: its near-twin takes the same answer
    f1, f2 = [r[0] for r in c.execute("SELECT id FROM faces WHERE person_id=? LIMIT 2", (ids["Dan"],))]
    c.execute("UPDATE faces SET emb=(SELECT emb FROM faces WHERE id=?) WHERE id=?", (f1, f2))
    c.execute("UPDATE faces SET person_id=NULL, confirmed=0 WHERE id IN (?, ?)", (f1, f2))
    c.commit()
    face_attrs.set_said(c, [f1], 0)
    face_attrs.learn(c, "sface", store.threshold(lib.settings, "sface"))
    assert c.execute("SELECT male_carried, sex_guess FROM faces WHERE id=?", (f2,)).fetchone() == (0, 0)
    lib.conn.close()


def test_looks_menu(window, photos):
    from PySide6.QtWidgets import QMenu
    w, c = window, window.conn
    ids = face_ids(c, photos["duo"])
    m = QMenu()
    sub = w.looks_menu(m, ids)
    texts = [a.text() for a in sub.actions() if a.text()]
    assert texts == ["Looks female   (2 faces)", "Looks male   (2 faces)"]
    w.set_looks(ids, 1)
    m2 = QMenu()
    sub = w.looks_menu(m2, ids)
    acts = {a.text(): a for a in sub.actions() if a.text()}
    assert acts["Looks male   (2 faces)"].isChecked() and "Let FaceSort judge again   (2 faces)" in acts
    w.idle()
