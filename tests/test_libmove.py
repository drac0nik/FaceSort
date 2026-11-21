"""1.57.3: what you import goes straight into the library (~Unsorted\\<where it was>), and moves on to its
person's folder once someone's confirmed on it."""
import os

import libmove
import store
from conftest import write_photo
from test_import import drop_tree, pid_of
from test_ui import window  # noqa: F401  (the window fixture)


def test_target_paths(tmp_path):
    lib = str(tmp_path / "My Library")
    if os.name != "nt":
        t = libmove.target("/home/mj/Downloads/a.jpg", lib)
        assert t == os.path.join(lib, "~Unsorted", "home", "mj", "Downloads", "a.jpg")
        assert libmove.inside(t, lib) and not libmove.inside("/home/mj/a.jpg", lib)
    else:
        assert libmove.target(r"\\nas\share\x.jpg", lib) == os.path.join(lib, "~Unsorted", "nas", "share", "x.jpg")
        assert libmove.target(r"C:\Downloads\a.jpg", lib) == os.path.join(lib, "~Unsorted", "C", "Downloads", "a.jpg")


def test_drop_moves_into_unsorted(window, tmp_path):
    w, c = window, window.conn
    lib = tmp_path / "FaceSort Library"
    lib.mkdir()
    w.settings["library_root"] = store.norm_path(str(lib))
    d = drop_tree(tmp_path)
    write_photo(str(d / "Anna" / "a2.jpg"), 931)
    open(str(d / "Anna" / "a2.xmp"), "w").write("<x/>")
    open(str(d / "notes.txt"), "w").write("keep me here")
    s, j = str(d), os.path.join
    assert w.handle_drop([s], answers={"folders": [(j(s, "Anna"), "person", "Anna"),
                                                   (j(s, "Junk"), "skip", "Junk")]})
    new = libmove.target(s, str(lib))
    assert os.path.exists(j(new, "Anna", "a1.jpg")) and not os.path.exists(j(s, "Anna", "a1.jpg"))
    assert os.path.exists(j(new, "Anna", "a2.xmp"))                       # its sidecar came along
    assert os.path.exists(j(s, "Junk", "j1.jpg")) and not os.path.exists(j(new, "Junk"))   # left out: stays
    assert os.path.exists(j(s, "notes.txt"))                              # other files aren't moved
    assert store.norm_path(str(lib)) in w.settings["folders"]
    w.idle()
    a1 = pid_of(c, j(new, "Anna", "a1.jpg"))
    assert a1 and pid_of(c, j(new, "top.jpg"))
    anna = c.execute("SELECT id FROM persons WHERE name='Anna'").fetchone()[0]
    assert c.execute("SELECT 1 FROM photo_people WHERE photo_id=? AND person_id=?", (a1, anna)).fetchone()
    # a folder you keep watching isn't moved
    cam = tmp_path / "Camera"
    write_photo(str(cam / "x.jpg"), 941)
    w.start_scan = lambda **k: None
    assert w.handle_drop([str(cam)], watch=True, answers={})
    assert os.path.exists(str(cam / "x.jpg"))
    # turned off: imported where it is
    w.settings["import_into_library"] = False
    other = tmp_path / "Other"
    write_photo(str(other / "o.jpg"), 951)
    assert w.handle_drop([str(other)], answers={})
    assert os.path.exists(str(other / "o.jpg"))


def test_tagged_photos_leave_unsorted(window, tmp_path, monkeypatch):
    import history
    w, c = window, window.conn
    lib = tmp_path / "My Library"
    lib.mkdir()
    w.settings["library_root"] = store.norm_path(str(lib))
    d = tmp_path / "Downloads"
    write_photo(str(d / "solo.jpg"), 961)
    write_photo(str(d / "nobody.jpg"), 962)
    assert w.handle_drop([str(d)], answers={})
    w.idle()
    new = libmove.target(str(d), str(lib))
    solo, nobody = pid_of(c, os.path.join(new, "solo.jpg")), pid_of(c, os.path.join(new, "nobody.jpg"))
    assert solo and nobody
    assert w.automove_tick(force=True) is False                            # nobody confirmed yet
    alice = c.execute("SELECT id FROM persons WHERE name='Alice'").fetchone()[0]
    c.execute("INSERT OR IGNORE INTO photo_people(photo_id, person_id, src) VALUES(?,?,'manual')", (solo, alice))
    c.commit()
    w._last_input = __import__("time").monotonic()
    assert w.automove_tick() is False                                      # still using FaceSort: waits
    assert w.automove_tick(force=True)
    w.idle()
    path = c.execute("SELECT path FROM photos WHERE id=?", (solo,)).fetchone()[0]
    assert os.path.exists(path) and not libmove.inside(path, libmove.unsorted_dir(str(lib)))
    assert "Alice" in path and libmove.inside(path, str(lib))
    assert os.path.exists(os.path.join(new, "nobody.jpg"))                 # untagged: stays in ~Unsorted
    e = next(e for e in history.entries(c) if "~Unsorted" in e["text"])
    assert history.undo(c, e["id"])[0] >= 1                                # Undo puts it back
    assert os.path.exists(os.path.join(new, "solo.jpg"))
    assert libmove.inside(c.execute("SELECT path FROM photos WHERE id=?", (solo,)).fetchone()[0],
                          libmove.unsorted_dir(str(lib)))
    assert solo not in libmove.tagged_waiting(c, store.norm_path(str(lib)))
    assert w.automove_tick(force=True) is False                            # ...and it stays put


def test_made_files_are_imported(window, tmp_path, monkeypatch):
    """1.57.4: pictures FaceSort makes are in FaceSort straight away; outside the library they go into
    ~Unsorted first; a contact sheet starts out in the library."""
    import contactsheet_ui
    from PIL import Image
    w, c = window, window.conn
    lib = tmp_path / "My Library"
    lib.mkdir()
    w.settings["library_root"] = store.norm_path(str(lib))
    w.settings.pop("sheet_dest", None)
    scanned = []
    monkeypatch.setattr(w, "scan_paths", lambda paths: scanned.extend(paths))
    out = tmp_path / "Desktop" / "sheet 1.jpg"
    out.parent.mkdir()
    Image.new("RGB", (60, 40), "white").save(str(out))
    made = w.import_made([str(out), str(tmp_path / "missing.jpg")])
    assert made == [store.norm_path(libmove.target(str(out), str(lib)))] and os.path.exists(made[0])
    assert pid_of(c, made[0]) and scanned == made
    d = contactsheet_ui.ContactSheetDialog(w, 3, "Alice")
    assert d.dest.text() == os.path.join(libmove.unsorted_dir(store.norm_path(str(lib))), "Contact sheets")


def test_bring_older_photos_into_the_library(window, tmp_path, monkeypatch):
    """1.57.5: photos added before (imported once / watched) brought into ~Unsorted; tagged ones go on."""
    import history
    w, c = window, window.conn
    old = tmp_path / "Old drop"
    write_photo(str(old / "o1.jpg"), 971)
    write_photo(str(old / "o2.jpg"), 972)
    assert w.handle_drop([str(old)], answers={})                           # no library yet: stays put
    w.idle()
    o1 = pid_of(c, old / "o1.jpg")
    assert o1
    lib = tmp_path / "My Library"
    lib.mkdir()
    w.settings["library_root"] = store.norm_path(str(lib))
    w.settings["folders"].append(store.norm_path(str(lib)))
    found = libmove.strays(c, w.settings, store.norm_path(str(lib)))
    groups = {g for _p, g in found}
    assert "imported" in groups and "watched" in groups                    # the test library's photos are watched
    watched_one = next(p for p, g in found if g == "watched")
    alice = c.execute("SELECT id FROM persons WHERE name='Alice'").fetchone()[0]
    c.execute("INSERT OR IGNORE INTO photo_people(photo_id, person_id, src) VALUES(?,?,'manual')", (o1, alice))
    c.commit()
    monkeypatch.setattr(w, "automove_tick", lambda force=False: None)       # (checked on its own below)
    assert w.bring_into_library(answers={"watched": False})
    w.idle()
    new = libmove.target(str(old), str(lib))
    assert os.path.exists(os.path.join(new, "o2.jpg")) and not os.path.exists(str(old / "o2.jpg"))
    assert pid_of(c, os.path.join(new, "o1.jpg")) == o1
    assert os.path.exists(watched_one)                                     # watched: left alone
    assert o1 in libmove.tagged_waiting(c, store.norm_path(str(lib)))      # ...and moves on next
    e = next(e for e in history.entries(c) if "older photo" in e["text"])
    assert history.undo(c, e["id"])[0] == 2 and os.path.exists(str(old / "o2.jpg"))
    assert w.bring_into_library(answers={"watched": True})
    w.idle()
    assert not os.path.exists(watched_one)
    assert os.path.exists(libmove.target(watched_one, str(lib)))
    assert not [p for p, _g in libmove.strays(c, w.settings, store.norm_path(str(lib))) if os.path.exists(p)]
