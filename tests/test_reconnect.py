"""1.60: files left in ~Unsorted reconnected to their galleries by shoot evidence; jewelry marks."""
import os

import libmove
import reconnect
import store
from conftest import add_photo
from test_ui import window  # noqa: F401

T0 = 1.7e9


def _setup(root):
    conn = store.connect()
    lib = str(root / "My Library")
    gal = os.path.join(lib, "Models", "Jane", "Jane - Beach Set")
    jane = store.get_or_create_person(conn, "Jane")
    ids = []
    for k in (1, 2, 3, 5, 6, 8):
        ids.append(add_photo(conn, os.path.join(gal, f"beach_{k:03d}.jpg"), [], 100 + k, taken=T0 + k * 60))
    store.tag_photos(conn, ids, jane)
    other = os.path.join(lib, "Models", "Kim", "Studio")
    for k in (1, 2, 3):
        add_photo(conn, os.path.join(other, f"studio_{k:03d}.jpg"), [], 200 + k, taken=T0 + 86400 * 30 + k * 60)
    uns = libmove.unsorted_dir(lib)
    c = lambda *p: os.path.join(uns, *p)
    files = {
        # folder name + numbering: sure
        "sure": add_photo(conn, c("C", "Downloads", "Jane - Beach Set", "beach_004.jpg"), [], 301, taken=T0 + 4 * 60),
        # numbering only (folder named something else, no date): likely, not ticked
        "likely": add_photo(conn, c("C", "Downloads", "stuff", "beach_007.jpg"), [], 302, taken=T0 - 9e7),
        # taken during the shoot, nothing else: not enough on its own
        "time_only": add_photo(conn, c("C", "Downloads", "stuff", "x.jpg"), [], 303, taken=T0 + 5 * 60),
        # nothing at all: stays
        "none": add_photo(conn, c("C", "Downloads", "stuff", "holiday.jpg"), [], 304, taken=T0 - 9e7),
    }
    conn.commit()
    return conn, lib, gal, jane, files


def test_reconnect_finds_by_shoot_evidence(data_dir):
    conn, lib, gal, jane, files = _setup(data_dir.root)
    found, gals = reconnect.find(conn, lib)
    assert set(found) == {gal}
    by_id = {m["id"]: m for m in found[gal]}
    assert by_id[files["sure"]]["sure"]
    assert any("Jane - Beach Set" in r for r in by_id[files["sure"]]["reasons"])
    assert any("between 3 and 5" in r for r in by_id[files["sure"]]["reasons"])
    assert files["likely"] in by_id and not by_id[files["likely"]]["sure"]
    assert files["time_only"] not in by_id and files["none"] not in by_id
    assert gals[gal].person == jane
    picks = [(m["id"], gal, gals[gal].person) for m in found[gal] if m["sure"]]
    st = reconnect.reconnect_job(conn, lib, picks, tag=True)
    assert st["moved"] == 1 and st["tagged"] == 1 and st["moves"]
    path = conn.execute("SELECT path FROM photos WHERE id=?", (files["sure"],)).fetchone()[0]
    assert os.path.dirname(path) == store.norm_path(gal) and os.path.exists(path)
    assert conn.execute("SELECT 1 FROM photo_people WHERE photo_id=? AND person_id=?", (files["sure"], jane)).fetchone()
    # the rest stay put
    assert libmove.inside(conn.execute("SELECT path FROM photos WHERE id=?", (files["none"],)).fetchone()[0],
                          libmove.unsorted_dir(lib))
    conn.close()


def test_recorded_move_from_the_same_folder(data_dir):
    conn, lib, gal, jane, files = _setup(data_dir.root)
    uns_dir = os.path.dirname(conn.execute("SELECT path FROM photos WHERE id=?", (files["none"],)).fetchone()[0])
    libmove._moves_table(conn)
    conn.execute("INSERT INTO unsorted_moves(src_dir, dest_dir) VALUES(?,?)", (uns_dir, store.norm_path(gal)))
    conn.commit()
    found, _g = reconnect.find(conn, lib)
    m = {x["id"]: x for x in found[gal]}
    assert m[files["none"]]["sure"] and "same folder" in m[files["none"]]["reasons"][0]   # (siblings went there)
    conn.close()


def test_jewelry_marks(data_dir):
    conn = store.connect()
    p = add_photo(conn, os.path.join(str(data_dir.root), "My Library", "a.jpg"), [], 7)
    who = store.get_or_create_person(conn, "Jane")
    store.add_tattoo(conn, who, p, (0.4, 0.5, 0.1, 0.1), kind="jewelry", label="Navel piercing")
    k, lab = conn.execute("SELECT kind, label FROM tattoos").fetchone()
    assert (k, lab) == ("jewelry", "Navel piercing")
    assert store.split_mark_src(store.mark_src("mark", "jewelry", "Navel piercing")) == ("mark", "jewelry",
                                                                                         "Navel piercing")
    assert store.split_mark_src("match") == ("match", "tattoo", None)
    import known
    known.sync_from_library(conn)                         # (1.60.0 broke this - saving who's who failed)
    assert known.tattoo_marks_for(conn.execute("SELECT path FROM photos WHERE id=?", (p,)).fetchone()[0])[0][5] \
        == "mark|jewelry|Navel piercing"
    conn.execute("INSERT INTO tattoo_suggestions(photo_id, person_id, x, y, w, h, inliers, kind) "
                 "VALUES(?,?,0.1,0.1,0.1,0.1,30,'jewelry')", (p, who))
    assert store.confirm_tattoo_matches(conn, [p]) == 1
    assert conn.execute("SELECT kind FROM tattoos WHERE src='match'").fetchone()[0] == "jewelry"
    conn.close()


def test_reconnect_from_the_window(window, qapp, tmp_path):
    from conftest import pump
    w = window
    conn, lib, gal, jane, files = _setup(tmp_path)
    conn.close()
    w.settings["library_root"] = store.norm_path(lib)
    w._reconnect_result = None
    assert w.reconnect_galleries(answers={"pick": "sure", "tag": False})
    for _ in range(100):
        pump(qapp, 0.1)
        if w._reconnect_result is not None and not w.busy():
            break
    assert w._reconnect_result == 1
    path = w.conn.execute("SELECT path FROM photos WHERE id=?", (files["sure"],)).fetchone()[0]
    assert os.path.dirname(path) == store.norm_path(gal)
    from reconnect_ui import ReconnectDialog
    found, gals = reconnect.find(w.conn, lib)
    dlg = ReconnectDialog(w, found, gals, lib)
    assert dlg.picks() == []                                  # only the likely one left: not ticked
    top = dlg.tree.topLevelItem(0)
    from PySide6.QtCore import Qt
    top.child(0).setCheckState(0, Qt.Checked)
    assert len(dlg.picks()) == 1 and dlg.go.isEnabled()
    dlg.tree.setCurrentItem(top.child(0))
    assert dlg.file_panel.items
    dlg.reject()
    pump(qapp, 0.3)
    w.idle()
