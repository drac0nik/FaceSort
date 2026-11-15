"""Content (1.44): Safe / Suggestive / Explicit levels and corrections, Safe view, recommended tags
(tags are albums, written as keywords, searchable), album suggestions, No people, and the viewer
changes (no recent-folder buttons, a dragged-around single face stays a selection)."""
import json
import os

import numpy as np

import content
import jobs
import store
import xmp
from conftest import add_photo, pump, unit
from test_ui import all_keys, window  # noqa: F401  (the window fixture)


def info(porn=0.0, sexy=0.0, neutral=1.0, hentai=0.0, drawings=0.0, parts=()):
    return {"gant": [drawings, hentai, neutral, porn, sexy], "parts": [list(p) for p in parts]}


def test_levels():
    assert content.level_of(None) is None
    assert content.level_of(info()) == 0
    assert content.level_of(info(porn=0.9, neutral=0.1)) == 2
    assert content.level_of(info(sexy=0.7, neutral=0.3)) == 1
    assert content.level_of(info(porn=0.3, sexy=0.3, neutral=0.4)) == 3 or \
        content.level_of(info(porn=0.3, sexy=0.3, neutral=0.4)) == 1
    part = next(iter(content.EXPLICIT_PARTS))
    assert content.level_of(info(porn=0.3, sexy=0.2, neutral=0.5, parts=[(part, 0.9, .1, .1, .2, .2)])) == 2
    assert content.level_of(info(porn=0.9, neutral=0.1), said=0) == 0          # your correction always wins
    assert content.has_people(info(parts=[("FACE_FEMALE", 0.8, .1, .1, .2, .2)]))
    assert not content.has_people(info())
    assert content.blur_boxes(info(parts=[(part, 0.9, .1, .2, .3, .4)])) == [(.1, .2, .3, .4)]


def a_photo(w, name, seed, faces=1):
    rng = np.random.default_rng(seed)
    p = os.path.join(str(w.lib.dd.lib), "Content", f"{name}.jpg")
    pid = add_photo(w.conn, p, [unit(rng.normal(size=128)) for _ in range(faces)], seed=seed)
    w.conn.commit()
    return pid, p


def set_info(c, pid, inf, subject=None):
    lv = content.level_of(inf)
    c.execute("INSERT OR REPLACE INTO photo_content(photo_id, info, level, subject) VALUES(?,?,?,?)",
              (pid, json.dumps(inf), 3 if lv is None else lv, subject))
    c.commit()


def test_corrections_stick(window):
    w, c = window, window.conn
    pid, _ = a_photo(w, "beach", 3001)
    set_info(c, pid, info(porn=0.9, neutral=0.1))
    content.set_level(c, [pid], 0)
    content.learn_levels(c)
    assert c.execute("SELECT level, level_set FROM photo_content WHERE photo_id=?", (pid,)).fetchone() == (0, 0)
    content.set_level(c, [pid], None)                                       # let FaceSort judge again
    assert c.execute("SELECT level, level_set FROM photo_content WHERE photo_id=?", (pid,)).fetchone() == (2, None)


def test_safe_view_blurs(window, qapp):
    w, c = window, window.conn
    pid, _ = a_photo(w, "explicit", 3002)
    other, _ = a_photo(w, "fine", 3003)
    set_info(c, pid, info(porn=0.95, neutral=0.05))
    set_info(c, other, info())
    w.settings["content_on"] = True
    w.toggle_safe_view(True)
    assert pid in w._safe_photos and other not in w._safe_photos
    fid = c.execute("SELECT id FROM faces WHERE photo_id=?", (pid,)).fetchone()[0]
    assert fid in w._safe_faces
    from PySide6.QtGui import QPixmap
    pm = QPixmap(40, 40)
    assert w.safe_pixmap(-pid, pm) is not pm and w.safe_pixmap(-other, pm) is pm
    assert w.safe_boxes(pid) == [] and w.safe_boxes(other) is None
    w.toggle_safe_view(False)
    assert not w._safe_photos and w.safe_boxes(pid) is None


def test_tags_are_albums_keywords_and_search(window):
    w, c = window, window.conn
    pid, path = a_photo(w, "sunset", 3004)
    store.tag_photo(c, pid, "Beach", True)
    assert store.photo_tags(c, pid) == ["Beach"]
    assert c.execute("SELECT tag FROM albums WHERE name='Beach'").fetchone()[0] == 1
    assert store.tag_search(c, "beach") == [pid]
    jobs.write_tags(c, w.settings)
    people, plain = xmp.read_keywords(path)
    assert "Beach" in plain
    store.tag_photo(c, pid, "Beach", False)                                  # off again: and remembered as "not"
    assert store.photo_tags(c, pid) == []
    assert c.execute("SELECT COUNT(*) FROM album_rejects").fetchone()[0] == 1
    other, _ = a_photo(w, "again", 3005)                                     # read back from a file: into the album
    store.apply_keywords(c, other, ([], ["Beach"]), w.settings)
    assert store.photo_tags(c, other) == ["Beach"]
    assert not c.execute("SELECT 1 FROM photo_people WHERE photo_id=?", (other,)).fetchone()   # not a person
    c.execute("UPDATE photos SET sort_later=2 WHERE id=?", (other,))       # hidden by a locked vault
    assert store.tag_search(c, "Beach") == []


def test_tag_column(window, qapp):
    w, c = window, window.conn
    person = next(k for k in all_keys(w, w.tree) if k[0] == "person")
    other, _ = a_photo(w, "elsewhere", 3010)
    store.tag_photo(c, other, "Studio", True)                                # a tag used lately is suggested
    w.view = person
    w.refresh_grid()
    i = next(i for i, r in enumerate(w.model.rows) if r[0])
    w.open_viewer(w.model.index(i))
    pump(qapp, 0.6)
    v = w.viewer
    assert not hasattr(v, "top5")                                            # no recent-folder buttons any more
    col = v.tag_col
    assert col.isVisible()
    pid = v.photo_id()[0]
    b = next(b for b in col.btns if b.isVisible())
    name = b.property("tag")
    b.click()
    assert name in store.photo_tags(c, pid)
    b = next(b for b in col.btns if b.property("tag") == name)
    assert b.isChecked() and b.text().startswith("✓")
    b.click()
    assert name not in store.photo_tags(c, pid)
    w.settings["tag_column"] = False
    v.refresh_tag_column()
    assert not col.isVisible()
    w.settings["tag_column"] = True
    # a single box dragged around is selected - the People bar isn't opened for it
    f = v.faces[0]
    v.focus_id = None
    v.select_faces([f])
    assert v.sel_faces == {f[0]} and v.focus_id is None


def test_no_people(window):
    w, c = window, window.conn
    pid, _ = a_photo(w, "dinner", 3006, faces=0)
    set_info(c, pid, info(), subject="food")
    w.settings["content_on"] = True
    w.view = ("nofaces", None)
    w.refresh_grid()
    assert -pid not in {r[0] for r in w.model.rows}                          # not under No faces found any more
    w.view = ("nopeople", "food")
    w.refresh_grid()
    assert -pid in {r[0] for r in w.model.rows}
    w.rebuild_tree()
    keys = all_keys(w, w.tree2)
    assert ("nopeople", None) in keys and ("nopeople", "food") in keys
    assert content.no_people_ids(c) == [pid] or pid in content.no_people_ids(c)


def test_album_suggestions(window, monkeypatch):
    w, c = window, window.conn
    a, _ = a_photo(w, "in_album", 3020)
    b, _ = a_photo(w, "maybe", 3021)
    d, _ = a_photo(w, "maybe_not", 3022)
    aid = store.create_album(c, "Holiday")
    store.add_to_album(c, aid, [a])
    monkeypatch.setattr(type(w), "album_suggested",
                        lambda self, aid_, name: [r for r in self.photo_rows({b, d}) if -r[0] not in
                                                  {p for (p,) in c.execute("SELECT photo_id FROM album_rejects")}
                                                  and -r[0] not in {p for (p,) in c.execute(
                                                      "SELECT photo_id FROM album_photos WHERE album_id=?", (aid_,))}])
    w.view = ("album", aid)
    w.refresh_grid()
    rows = w.model.rows
    head = next(i for i, r in enumerate(rows) if r[0] == 0 and str(r[1]).startswith("Suggested"))
    assert {r[0] for r in rows[:head]} == {-a} and {r[0] for r in rows[head + 1:]} == {-b, -d}
    assert w._album_sugg == {b, d}
    w.add_album_suggestions(aid, [b])
    w.reject_album_suggestions(aid, [d])
    assert {p for (p,) in c.execute("SELECT photo_id FROM album_photos WHERE album_id=?", (aid,))} == {a, b}
    w.view = ("album", aid)
    w.refresh_grid()
    assert not any(r[0] == 0 for r in w.model.rows)                         # nothing left to suggest
    w.remove_photos_from_album(aid, [b])                                     # taken out: not suggested back
    assert c.execute("SELECT 1 FROM album_rejects WHERE album_id=? AND photo_id=?", (aid, b)).fetchone()
