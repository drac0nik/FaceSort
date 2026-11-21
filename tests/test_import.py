"""Add photos (1.46): import once (the folder is forgotten) or keep watching; the folders table -
a sub-folder as a person, a folder (inside the person above it), an album, or left out; the
guesses and the choices remembered for next time."""
import os

import store
from conftest import pump, write_photo
from test_ui import window  # noqa: F401  (the window fixture)


def drop_tree(tmp_path):
    d = tmp_path / "Shoot 2024"
    write_photo(str(d / "Anna" / "a1.jpg"), 901)
    write_photo(str(d / "Trips" / "Beach" / "b1.jpg"), 902)
    write_photo(str(d / "Pets" / "p1.jpg"), 903)
    write_photo(str(d / "Junk" / "j1.jpg"), 904)
    write_photo(str(d / "top.jpg"), 905)
    return d


def pid_of(c, path):
    r = c.execute("SELECT id FROM photos WHERE path=?", (store.norm_path(str(path)),)).fetchone()
    return r[0] if r else None


def test_import_once_and_the_folders_table(window, qapp, tmp_path):
    w, c = window, window.conn
    d = drop_tree(tmp_path)
    s = str(d)
    j = os.path.join
    assert w.handle_drop([s], answers={
        "people": [], "category": None, "later": False,
        "folders": [(j(s, "Anna"), "person", "Anna"), (j(s, "Trips"), "folder", "Trips"),
                    (j(s, "Trips", "Beach"), "folder", "Beach"), (j(s, "Pets"), "album", "Pets"),
                    (j(s, "Junk"), "skip", "Junk")]})
    # imported once: not one of your library folders (not watched), but its photos are kept
    assert store.norm_path(s) in w.settings["imported"] and store.norm_path(s) not in w.settings["folders"]
    w.idle()
    a1, b1, p1 = (pid_of(c, d / "Anna" / "a1.jpg"), pid_of(c, d / "Trips" / "Beach" / "b1.jpg"),
                  pid_of(c, d / "Pets" / "p1.jpg"))
    assert a1 and b1 and p1
    assert pid_of(c, d / "Junk" / "j1.jpg") is None                       # left out: not added
    anna = c.execute("SELECT id FROM persons WHERE name='Anna'").fetchone()[0]
    assert c.execute("SELECT 1 FROM photo_people WHERE photo_id=? AND person_id=?", (a1, anna)).fetchone()
    tree = store.folder_tree(c)
    chain = []
    fid = c.execute("SELECT folder_id FROM photo_folders WHERE photo_id=?", (b1,)).fetchone()
    fid = fid[0] if fid else c.execute("SELECT category_id FROM photos WHERE id=?", (b1,)).fetchone()[0]
    while fid:
        chain.append(tree[fid][0])
        fid = tree[fid][1]
    assert chain[::-1][-2:] == ["Trips", "Beach"]                          # a folder inside a folder
    assert c.execute("""SELECT 1 FROM album_photos ap JOIN albums a ON a.id=ap.album_id
                        WHERE a.name='Pets' AND ap.photo_id=?""", (p1,)).fetchone()
    # a full check of the library later keeps them (the folder isn't scanned, the files are there)
    w.start_scan(opts={"mode": "both"})
    w.idle()
    assert pid_of(c, d / "Anna" / "a1.jpg") and pid_of(c, d / "top.jpg")
    # remembered for next time
    assert store.import_choices(c, [j(s, "Pets")])[store.norm_path(j(s, "Pets"))] == ("album", "Pets")


def test_keep_watching(window, tmp_path):
    w = window
    d = tmp_path / "Camera"
    write_photo(str(d / "x.jpg"), 911)
    write_photo(str(d / "Skip me" / "y.jpg"), 912)
    w.start_scan = lambda **k: None
    assert w.handle_drop([str(d)], watch=True,
                         answers={"folders": [(os.path.join(str(d), "Skip me"), "skip", "Skip me")]})
    assert store.norm_path(str(d)) in w.settings["folders"]
    assert store.norm_path(os.path.join(str(d), "Skip me")) in w.settings["exclude"]   # left out of later scans too


def test_dialog_guesses(window, tmp_path):
    from importer import FOLDER, NOTHING, PERSON, THEIR_FOLDER, ImportDialog
    w = window
    known = w.people_by_recent()[0]
    d = tmp_path / "Party"
    write_photo(str(d / known / "a.jpg"), 921)
    write_photo(str(d / "New folder" / "b.jpg"), 922)
    write_photo(str(d / "Cake" / "c.jpg"), 923)
    dlg = ImportDialog(w, [store.norm_path(str(d))], [], 0)
    assert not dlg.watch.isChecked()                                        # a drop: import once
    s = store.norm_path(str(d))
    assert dlg.use_of(s) == THEIR_FOLDER
    assert dlg.use_of(os.path.join(s, known)) == PERSON
    assert dlg.use_of(os.path.join(s, "New folder")) == NOTHING
    assert dlg.use_of(os.path.join(s, "Cake")) == FOLDER
    dlg.people.setText("Bea")
    dlg._done("tag")
    ch = dlg.choices()
    assert ch["people"] == ["Bea"] and (os.path.join(s, known), PERSON, known) in ch["folders"]
    dlg2 = ImportDialog(w, [s], [], 0, watch=True)                           # Add folder…: watching
    assert dlg2.watch.isChecked()
    dlg2._done("plain")
    assert dlg2.choices()["folders"] == [] and dlg2.choices()["watch"]


def test_emptied_imported_folders_are_cleaned_up(tmp_path):
    """(1.50.2) A folder dropped in and imported once: when Sort into folders has moved everything
    out of it, it goes to ~To Be Discarded too (the whole folder, not just the empty ones inside)."""
    import jobs
    lib = tmp_path / "Photos"
    (lib / "Kept" / "Empty inside").mkdir(parents=True)
    (lib / "Kept" / "note.txt").write_text("x")
    dropped = tmp_path / "Downloads" / "Shoot 12"
    (dropped / "Day 1").mkdir(parents=True)
    (dropped / "Day 2").mkdir(parents=True)
    (dropped / "Day 2" / "Thumbs.db").write_text("junk")
    busy = tmp_path / "Downloads" / "Shoot 13"
    busy.mkdir(parents=True)
    (busy / "readme.txt").write_text("still here")
    dest = tmp_path / "Sorted"
    dest.mkdir()
    moved = jobs.cleanup_empty_folders([str(lib), str(dropped), str(busy)], str(dest), whole=[str(dropped), str(busy)])
    olds = {os.path.basename(a) for a, _b in moved}
    assert "Shoot 12" in olds and not dropped.exists()                # the whole dropped folder
    assert busy.exists() and "Shoot 13" not in olds                   # something else is in it: stays
    assert "Empty inside" in olds and lib.exists()                    # library folder itself never moves
    assert (dest / jobs.DISCARD_FOLDER / "Shoot 12" / "Day 2").exists()


def test_gallery_imports_show_the_window_filled_in(window, qapp, tmp_path, monkeypatch):
    """1.57.6: every import brings up Add photos first - a watched gallery's with its tagging filled in;
    a second one waits until the first window is closed."""
    import importer
    from PySide6.QtWidgets import QDialog
    w = window
    w.start_scan = lambda **k: None
    w.settings["import_into_library"] = False     # (no library folder here: it would ask for one)
    seen = []

    def fake_exec(dlg):
        seen.append((dlg.people.text(), dlg.category.currentText(), dlg.later.isChecked(),
                     [lb.text() for lb in dlg.findChildren(importer.QLabel)]))
        if len(seen) == 1:                         # while the first is open, another gallery finishes
            w.import_gallery_files([str(tmp_path / "g2" / "b.jpg")], {"people": ["Bo"]}, "Second")
            assert len(w._import_queue) == 1
        dlg._done("tag")
        return QDialog.Accepted
    monkeypatch.setattr(importer.ImportDialog, "exec", fake_exec)
    write_photo(str(tmp_path / "g1" / "a.jpg"), 981)
    write_photo(str(tmp_path / "g2" / "b.jpg"), 982)
    assert w.import_gallery_files([str(tmp_path / "g1" / "a.jpg")],
                                  {"people": ["Ann Lee"], "category": "Shoots", "later": True}, "Ann's page")
    assert seen[0][:3] == ("Ann Lee", "Shoots", True)
    assert any("Ann's page" in t for t in seen[0][3])
    pump(qapp, 0.8)
    assert len(seen) == 2 and seen[1][0] == "Bo" and not w._import_queue
    ann = w.conn.execute("SELECT id FROM persons WHERE name='Ann Lee'").fetchone()
    assert ann is not None or store.drop_rules(w.conn)            # the window's answers were used
