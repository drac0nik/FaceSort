"""Smart albums and sharing what FaceSort learned (1.48)."""
import json
import os
from datetime import datetime

import numpy as np

import content
import learned
import smart
import store
from conftest import add_photo, unit


def photo(c, name, seed, people=(), taken=None, tags=(), vec=None):
    rng = np.random.default_rng(seed)
    pid = add_photo(c, os.path.abspath(os.path.join(os.sep, "lib", "S", f"{name}.jpg")),
                    [unit(rng.normal(size=128)) for _ in people] or [], seed=seed, taken=taken)
    fids = [r[0] for r in c.execute("SELECT id FROM faces WHERE photo_id=? ORDER BY id", (pid,))]
    for fid, person in zip(fids, people):
        c.execute("UPDATE faces SET person_id=?, confirmed=1 WHERE id=?", (person, fid))
    for t in tags:
        store.tag_photo(c, pid, t, True)
    if vec is not None:
        c.execute("INSERT OR REPLACE INTO clip_emb(photo_id, vec) VALUES(?,?)", (pid, vec.astype(np.float16).tobytes()))
    c.commit()
    return pid


def test_smart_album_filter(library):
    c = library.conn
    anna, ben = store.get_or_create_person(c, "Anna S"), store.get_or_create_person(c, "Ben S")
    t1 = datetime(2024, 7, 10, 12).timestamp()
    a = photo(c, "a", 7001, [anna], t1, ["Beach"])
    ab = photo(c, "ab", 7002, [anna, ben], t1 + 86400, ["Beach", "Party"])
    b = photo(c, "b", 7003, [ben], datetime(2023, 1, 1).timestamp())
    nobody = photo(c, "n", 7004, [], t1)
    assert set(smart.photo_ids(c, {"people": [anna]})) == {a, ab}
    assert set(smart.photo_ids(c, {"people": [anna, ben], "people_mode": "all"})) == {ab}
    assert set(smart.photo_ids(c, {"people": [anna, ben]})) == {a, ab, b}
    assert set(smart.photo_ids(c, {"tags": ["Beach"], "not_tags": ["Party"]})) == {a}
    assert set(smart.photo_ids(c, {"tags": ["Beach", "Party"], "tags_mode": "all"})) == {ab}
    got = set(smart.photo_ids(c, {"date_from": "2024-07-10", "date_to": "2024-07-10"}))
    assert a in got and nobody in got and ab not in got and b not in got
    assert nobody in smart.photo_ids(c, {"faces": "without"}) and a not in smart.photo_ids(c, {"faces": "without"})
    # a locked vault's photos never show up
    c.execute("UPDATE photos SET sort_later=2 WHERE id=?", (a,))
    assert a not in smart.photo_ids(c, {"people": [anna]})
    c.execute("UPDATE photos SET sort_later=0 WHERE id=?", (a,))
    # folder: the photo's own folder, or someone placed in it
    fid = store.folder_chain(c, ["Family"])
    store.place_person(c, [ben], fid)
    assert set(smart.photo_ids(c, {"folder": fid})) == {ab, b}
    # saved, described, frozen into a normal album
    sid = smart.save(c, "", {"people": [anna], "people_names": ["Anna S"], "tags": ["Beach"]})
    name, f = smart.get(c, sid)
    assert "Anna S" in name and "Beach" in smart.describe(f)
    aid = smart.make_normal(c, sid)
    assert {p for (p,) in c.execute("SELECT photo_id FROM album_photos WHERE album_id=?", (aid,))} == {a, ab}
    smart.delete(c, sid)
    assert not smart.all_smart(c)


def test_smart_suggestions(library, monkeypatch):
    c = library.conn
    monkeypatch.setattr(smart, "PAIR_MIN", 3)
    monkeypatch.setattr(smart, "EVENT_MIN", 4)
    monkeypatch.setattr(smart, "SCENE_MIN", 2)
    x, y = store.get_or_create_person(c, "Xena Sg"), store.get_or_create_person(c, "Yuri Sg")
    t0 = datetime(2022, 5, 1, 10).timestamp()
    for i in range(4):
        photo(c, f"xy{i}", 7100 + i, [x, y], t0 + i * 3600, ["Studio"] if i < 2 else ())
    photo(c, "x_alone", 7110, [x], datetime(2021, 1, 1).timestamp())
    got = smart.suggestions(c)
    keys = {s["key"] for s in got}
    lo, hi = sorted((x, y))
    assert f"pair:{lo}:{hi}" in keys
    assert any(k.startswith("event:2022-05-01") for k in keys)
    assert f"scene:{x}:Studio" in keys
    pair = next(s for s in got if s["key"].startswith("pair:"))
    assert len(smart.photo_ids(c, pair["filter"])) == 4
    smart.dismiss(c, pair["key"])                         # no thanks: not suggested again
    assert pair["key"] not in {s["key"] for s in smart.suggestions(c)}
    ev = next(s for s in got if s["key"].startswith("event:"))
    smart.save(c, ev["name"], ev["filter"])               # made: not suggested again either
    assert ev["key"] not in {s["key"] for s in smart.suggestions(c)}


class FakeScorer:
    def __init__(self, probs):
        self.probs = probs

    def probabilities(self, tag, ids):
        return [(p, self.probs.get((p, tag), 0.0)) for p in ids]


def test_near_misses(library):
    c = library.conn
    anna = store.get_or_create_person(c, "Anna N")
    inside = photo(c, "in", 7201, [anna], tags=["Beach"])
    likely = photo(c, "likely", 7202, [anna])
    unlikely = photo(c, "unlikely", 7203, [anna])
    f = {"people": [anna], "tags": ["Beach"]}
    sc = FakeScorer({(likely, "Beach"): 0.9, (unlikely, "Beach"): 0.2})
    near = smart.near_misses(c, f, sc)
    assert [(p, t) for p, t, _ in near] == [(likely, "Beach")]
    smart.accept_near(c, [(likely, "Beach")])
    assert set(smart.photo_ids(c, f)) == {inside, likely}
    sc.probs[(unlikely, "Beach")] = 0.8
    smart.reject_near(c, [(unlikely, "Beach")])          # no: learned from, never suggested again
    assert not smart.near_misses(c, f, sc)


def test_learned_export_import(library, data_dir, monkeypatch, tmp_path):
    c = library.conn
    monkeypatch.setattr(content, "clip_ok", lambda: True)
    rng = np.random.default_rng(1)
    beach_dir = unit(rng.normal(size=512))
    ids_in, ids_vault = [], []
    for i in range(40):
        on = i < 12
        v = unit(rng.normal(size=512) * 0.3 + (beach_dir if on else 0))
        pid = photo(c, f"l{i}", 7300 + i, tags=["Beach", "Anna's secret tag"] if on else (), vec=v)
        (ids_in if on else []).append(pid)
    # a vault photo tagged Beach - never learned from for the export
    vp = photo(c, "vault", 7399, tags=["Beach"], vec=unit(-beach_dir))
    c.execute("UPDATE photos SET sort_later=2 WHERE id=?", (vp,))
    c.commit()
    assert vp in learned.vault_photo_ids(c)
    shared = {n: b for n, _k, b in learned.shareable_tags(c)}
    assert shared["Beach"] is True and shared["Anna's secret tag"] is False
    out = tmp_path / "x.fslearn"
    res = learned.export(c, str(out), ["Beach"], levels=True,
                         tag_list=[{"name": "Beach", "menu": True}, {"name": "Anna's secret tag", "menu": True}])
    assert res["tags"] == 1
    text = out.read_text(encoding="utf-8")
    data = json.loads(text)
    assert set(data["tags"]) == {"Beach"} and data["format"] == learned.FORMAT
    assert "secret" not in text and "Anna" not in text and "S" + os.sep not in text      # nothing personal
    assert [t["name"] for t in data["tag_list"]] == ["Beach"]
    # a fresh library imports it: the model is there, its tag joins the tag list
    c.execute("DELETE FROM album_photos")
    c.commit()
    settings = {"tag_list": [{"name": "Studio", "menu": True}]}
    got = learned.import_file(c, str(out), settings)
    assert got["tags"] == 1 and "Beach" in got["new_tags"]
    assert any(t["name"] == "Beach" for t in settings["tag_list"])
    w, b, n = learned.imported(c, "tag", "Beach")
    assert w.shape == (512,) and n == 12
    # it ranks beach-like photos above the rest
    sc = content.TagScorer(c, ["Beach"])
    sc.clip = True
    probs = dict(sc.probabilities("Beach", [ids_in[0], vp]))
    assert probs[ids_in[0]] > 0.5 > probs[vp]
    # blended with a local model once there is one
    local = (np.zeros(512, np.float32), 0.0)
    bw, bb = learned.blend(local, 12, (w, b, n))
    assert np.allclose(bw, w * 12 / 24, atol=1e-5)
    learned.forget_imported(c)
    assert learned.imported(c, "tag", "Beach") is None
    # a file that isn't one
    bad = tmp_path / "bad.fslearn"
    bad.write_text('{"format": "something else"}')
    try:
        learned.import_file(c, str(bad))
        assert False
    except ValueError:
        pass


# ------------------------------------------------------------------ in the window
from test_ui import all_keys, window  # noqa: E402,F401  (the window fixture)


def test_smart_albums_in_window(window, qapp, monkeypatch):
    w, c = window, window.conn
    alice = c.execute("SELECT id FROM persons WHERE name='Alice'").fetchone()[0]
    sid = smart.save(c, "Alice smart", {"people": [alice], "people_names": ["Alice"]})
    w.rebuild_tree()
    assert ("smart", sid) in all_keys(w, w.treeA) and ("smart_sugg", None) in all_keys(w, w.treeA)
    w.show_view(("smart", sid))
    w.idle()
    want = set(smart.photo_ids(c, {"people": [alice]}))
    assert want and {-r[0] for r in w.model.rows if r[0]} == want
    assert w.title.text() == "Smart album · Alice smart"
    # near misses at the end, answered from the right-click menu
    store.tag_photo(c, sorted(want)[0], "Beach", True)
    smart.save(c, "Alice smart", {"people": [alice], "people_names": ["Alice"], "tags": ["Beach"]}, sid)
    other = sorted(want)[1]

    class Sc:
        def probabilities(self, tag, ids):
            return [(p, 0.9 if p == other else 0.1) for p in ids]
    monkeypatch.setattr(type(w), "album_scorer", lambda self: Sc())
    import clip_search
    monkeypatch.setattr(clip_search, "available", lambda: True)
    w.settings["clip_on"] = True
    w.refresh_grid()
    rows = w.model.rows
    head = next(i for i, r in enumerate(rows) if r[0] == 0)
    assert [r[0] for r in rows[head + 1:]] == [-other] and w._smart_near == {other: "Beach"}
    w.smart_near_answer([other], True)
    assert other in smart.photo_ids(c, smart.get(c, sid)[1])
    # tree menu, suggestions view, make normal, delete
    it = w.find_tree_item(("smart", sid))
    texts = [a.text() for a in w.build_tree_menu(it).actions()]
    assert any(t.startswith("Make a normal album") for t in texts)
    w.show_view(("smart_sugg", None))
    w.idle()
    assert w.title.text() == "Suggested smart albums"
    w.smart_to_album(sid)
    assert c.execute("SELECT 1 FROM albums WHERE name IN ('Alice smart', 'Alice smart (album)')").fetchone()
    monkeypatch.setattr("mw_albums.QMessageBox.question", lambda *a, **k: __import__("mw_albums").QMessageBox.Yes)
    w.delete_smart_album(sid)
    assert ("smart", sid) not in all_keys(w, w.treeA)


def test_learned_dialog(window, qapp, tmp_path, monkeypatch):
    from learned_ui import LearnedDialog
    w = window
    d = LearnedDialog(w, w.conn, w.settings)
    assert "Nothing imported" in d.state.text()
    f = tmp_path / "in.fslearn"
    f.write_text(json.dumps({"format": learned.FORMAT, "space": learned.SPACE,
                             "tags": {"Sunset": {"w": [0.01] * 512, "b": -1.0, "n": 30}}, "levels": {}}))
    monkeypatch.setattr("learned_ui.QFileDialog.getOpenFileName", lambda *a, **k: (str(f), ""))
    monkeypatch.setattr("learned_ui.QMessageBox.information", lambda *a, **k: None)
    d.do_import()
    assert d.changed and "1 tag model" in d.state.text()
    assert any(t["name"] == "Sunset" for t in w.settings["tag_list"])
    d.do_forget()
    assert "Nothing imported" in d.state.text()
    d.close()


def test_view_stays_after_redraw(window, qapp):
    """(1.49.1) Albums, search results and a stale row in another list never send you to a group."""
    from conftest import pump
    from ui_common import key_of
    w, c = window, window.conn
    ph = [r[0] for r in c.execute("SELECT id FROM photos LIMIT 3")]
    aid = store.create_album(c, "Stay")
    store.add_to_album(c, aid, ph)
    w.rebuild_tree()
    g = next(x for x in w.iter_items() if key_of(x) and key_of(x)[0] == "group")
    g.treeWidget().setCurrentItem(g)                       # a group is current in Smart Groups
    pump(qapp, 0.2)
    w.side_tabs.setCurrentIndex(w.trees.index(w.treeA))
    pump(qapp, 0.2)
    w.treeA.setCurrentItem(w.find_tree_item(("album", aid)))
    pump(qapp, 0.3)
    assert w.view == ("album", aid)
    for t in w.trees:                                      # selections cleared everywhere
        t.clearSelection()
    pump(qapp, 0.3)
    w._active_tree = w.tree2 if hasattr(w, "tree2") else w._active_tree
    w._schedule_view_sync()
    pump(qapp, 0.3)
    assert w.view == ("album", aid)
    w.rebuild_tree()
    assert w.view == ("album", aid)
    w.view = ("search", "beach")
    w.rebuild_tree()
    assert w.view == ("search", "beach")


def test_view_moves_to_next_not_a_group(window, qapp):
    """(1.49.2) Done checking a dropped batch, deleting an album: the next row in that list is shown."""
    from ui_common import key_of
    w, c = window, window.conn
    ph = [r[0] for r in c.execute("SELECT id FROM photos LIMIT 4")]
    a1, a2 = store.create_album(c, "AAA one"), store.create_album(c, "AAB two")
    store.add_to_album(c, a1, ph[:2])
    store.add_to_album(c, a2, ph[2:])
    w.rebuild_tree()
    w.select_item(w.find_tree_item(("album", a1)))
    w.view = ("album", a1)
    import mw_albums
    mw_albums.QMessageBox.question, old = (lambda *a, **k: mw_albums.QMessageBox.Yes), mw_albums.QMessageBox.question
    try:
        w.delete_album(a1)
    finally:
        mw_albums.QMessageBox.question = old
    assert w.view == ("album", a2)
    # a dropped batch, done checking
    b1 = store.new_drop_batch(c, "first")
    c.execute("UPDATE photos SET dropped=? WHERE id=?", (b1, ph[0]))
    c.commit()
    w.rebuild_tree()
    it = w.find_tree_item(("dropped", b1))
    if it is None:
        assert 0, "not listed"
    w.select_item(it)
    w.view = ("dropped", b1)
    w.done_checking(b1)
    assert w.view[0] not in ("group",), w.view
    assert key_of(w.current_item()) == tuple(w.view)


def test_location_buckets_only_unsorted_and_ctrl_a(window, qapp):
    """(1.50.1) By original location: only photos nobody is tagged on and with no folder.
    Ctrl+A in a side list selects the gallery, not the list."""
    import os
    import numpy as np
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent
    from conftest import add_photo, unit
    w, c = window, window.conn
    d = os.path.join(str(w.lib.dd.lib), "Loc")
    rng = np.random.default_rng(81)
    loose = add_photo(c, os.path.join(d, "loose.jpg"), [unit(rng.normal(size=128))], seed=8101)
    tagged = add_photo(c, os.path.join(d, "tagged.jpg"), [unit(rng.normal(size=128)), unit(rng.normal(size=128))],
                       seed=8102)
    alice = c.execute("SELECT id FROM persons WHERE name='Alice'").fetchone()[0]
    f1 = c.execute("SELECT id FROM faces WHERE photo_id=? ORDER BY id", (tagged,)).fetchone()[0]
    c.execute("UPDATE faces SET person_id=?, confirmed=1 WHERE id=?", (alice, f1))   # one face named, one not
    c.commit()
    b = store.photo_buckets(c, "location", w.sorted_photo_sql())
    ids = {p for _l, _o, pids in b.values() for p in pids}
    assert loose in ids and tagged not in ids
    assert tagged in {p for _l, _o, pids in store.photo_buckets(c, "taken").values() for p in pids}
    # Ctrl+A on a side list
    w.view = ("person", alice)
    w.refresh_grid()
    t = w.tree
    t.setFocus()
    t.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_A, Qt.ControlModifier))
    assert len(t.selectedItems()) <= 1
    n = sum(1 for r in w.model.rows if r[0])
    assert len({i.row() for i in w.grid.selectionModel().selectedIndexes()}) >= n


def test_side_search_leaves_the_gallery_alone(window, qapp):
    """(1.57) The search box over the side lists only filters the side lists - Smart Groups too;
    the gallery's own filter box is what narrows the gallery."""
    import os
    from conftest import pump
    from ui_common import key_of
    w = window
    w.view = ("unknown", None)
    w.refresh_grid()
    rows = [r for r in w.model.rows if r[0]]
    assert len(rows) >= 2
    word = os.path.splitext(os.path.basename(rows[0][1]))[0].lower()
    w.search.setText(word.upper())
    pump(qapp, 0.5)
    assert len([r for r in w.model.rows if r[0]]) == len(rows)       # the gallery: untouched
    assert "Showing" not in w.subtitle.text()
    people = [it for it in w.iter_items() if it.treeWidget() is w.tree and key_of(it)
              and key_of(it)[0] == "person"]
    assert any(it.isHidden() for it in people)
    smart = [it for it in w.iter_items() if it.treeWidget() is w.tree2 and key_of(it)]
    assert any(it.isHidden() for it in smart)                      # Smart Groups are filtered as lists
    w.search.setText("")
    pump(qapp, 0.5)
    assert not any(it.isHidden() for it in w.iter_items() if it.treeWidget() is w.tree2 and key_of(it))


def test_gallery_file_name_filter(window, qapp):
    """(1.50.6) The gallery's own filter box narrows any gallery (a person's too) by file name, and
    is cleared when you pick something else on the left."""
    import os
    from conftest import pump
    w, c = window, window.conn
    alice = c.execute("SELECT id FROM persons WHERE name='Alice'").fetchone()[0]
    w.view = ("person", alice)
    w.refresh_grid()
    rows = [r for r in w.model.rows if r[0]]
    assert len(rows) >= 2
    word = os.path.splitext(os.path.basename(rows[0][1]))[0]
    expect = {r[0] for r in rows if word.lower() in w.match_name(r[1])}
    w.gallery_filter.setText(word)
    pump(qapp, 0.5)
    got = {r[0] for r in w.model.rows if r[0]}
    assert got == expect and len(got) < len(rows)
    assert "Showing" in w.subtitle.text()
    bob = c.execute("SELECT id FROM persons WHERE name='Bob'").fetchone()[0]
    it = w.find_tree_item(("person", bob))
    w.select_item(it)
    pump(qapp, 0.4)
    assert w.view == ("person", bob) and w.gallery_filter.text() == ""


def test_empty_person_folders_stay_in_the_folder_bar(window, qapp, monkeypatch):
    """(1.51.1) With only some library folders shown (or Pictures / Video), a person's empty
    folders still get their heading - and button in the folder bar - to drop photos onto."""
    w, c = window, window.conn
    alice = c.execute("SELECT id FROM persons WHERE name='Alice'").fetchone()[0]
    top = store.create_category(c, "Shoots", None, alice)
    store.create_category(c, "Empty one", top, None)
    c.commit()
    monkeypatch.setattr(type(w), "scope_on", lambda self: True)
    monkeypatch.setattr(type(w), "in_scope", lambda self, path: True)
    for mode in ("all", "videos"):
        w.settings["media_filter"] = mode
        w.view = ("person", alice)
        w.refresh_grid()
        bar = [b.text() for _i, b in w._bar_heads]
        assert any("Empty one" in t for t in bar), (mode, bar)
    w.settings["media_filter"] = "all"
    w.gallery_filter.setText("zzznomatch")
    w.refresh_grid()
    assert any("Empty one" in b.text() for _i, b in w._bar_heads)
    w.gallery_filter.setText("")
