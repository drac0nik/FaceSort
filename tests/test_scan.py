"""The Scan window's choices, and other files (documents, music, archives...)."""
import ctypes
import os
import shutil
import time

import numpy as np
import pytest

import engine
import jobs
import store
from conftest import write_photo

pytestmark = pytest.mark.skipif(not os.path.exists(engine.DET_MODEL), reason="face models not present")


def touch(path, data=b"x" * 100, age=0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    if age:
        t = time.time() - age
        os.utime(path, (t, t))
    return path


@pytest.fixture
def lib(data_dir):
    L = str(data_dir.lib)
    write_photo(os.path.join(L, "Trip", "a.jpg"), 1)
    write_photo(os.path.join(L, "Trip", "b.jpg"), 2)
    touch(os.path.join(L, "Trip", "a.xmp"))                       # a's own tags: not an other file
    touch(os.path.join(L, "Trip", "b.jpg.json"))                  # Takeout details of b: not either
    touch(os.path.join(L, "Trip", "itinerary.pdf"))
    touch(os.path.join(L, "Trip", "song.mp3"))
    touch(os.path.join(L, "Trip", "desktop.ini"))                 # system file
    touch(os.path.join(L, "Trip", "Thumbs.db"))
    touch(os.path.join(L, "Trip", ".hidden-notes.txt"))           # hidden files are included
    touch(os.path.join(L, "Trip", "backup.zip"))                  # listed, never opened
    touch(os.path.join(L, "Old backups", "c.pdf"))
    write_photo(os.path.join(L, "Old backups", "c.jpg"), 3)
    conn = store.connect()
    yield conn, L
    conn.close()


def names(conn, where="1=1"):
    return sorted(os.path.basename(p) for (p,) in conn.execute(f"SELECT path FROM photos WHERE {where}"))


def test_which_files_count_as_other(lib):
    _conn, L = lib
    got = {os.path.basename(p): k for p, k, _ in jobs.iter_files(L, others=True)}
    assert got == {"a.jpg": "photo", "b.jpg": "photo", "itinerary.pdf": "other", "song.mp3": "other",
                   ".hidden-notes.txt": "other", "backup.zip": "other", "c.pdf": "other", "c.jpg": "photo"}
    no_others = {os.path.basename(p) for p, _k, _ in jobs.iter_files(L)}
    assert no_others == {"a.jpg", "b.jpg", "c.jpg"}
    left_out = {os.path.basename(p) for p, _k, _ in jobs.iter_files(L, others=True, exclude=["old backups"])}
    assert "c.pdf" not in left_out and "c.jpg" not in left_out


def test_other_files_are_recorded_not_read(lib):
    conn, L = lib
    st = jobs.scan_library(conn, store.load_settings(), opts={"others": True})
    assert st["others"] == 5 and st["scanned"] == 3
    rows = conn.execute("SELECT is_other, extras_read, taken_read, tags_read, dark_seen, body_done, dup_skip, nfaces "
                        "FROM photos WHERE path LIKE '%itinerary.pdf'").fetchone()
    assert rows == (1, 1, 1, 1, 1, 3, 1, 0)
    from ui_common import NOFACES_WHERE, UNASSOC_WHERE
    unassoc = sorted(os.path.basename(p) for (p,) in conn.execute(f"SELECT ph.path FROM photos ph WHERE {UNASSOC_WHERE}"))
    assert unassoc == [".hidden-notes.txt", "backup.zip", "c.pdf", "itinerary.pdf", "song.mp3"]
    nof = {os.path.basename(p) for (p,) in conn.execute(f"SELECT ph.path FROM photos ph WHERE {NOFACES_WHERE}")}
    assert not nof & set(unassoc)                                   # not "No faces found"


def test_tagged_file_leaves_unassociated_and_moves_with_the_photos(lib, tmp_path):
    conn, L = lib
    jobs.scan_library(conn, store.load_settings(), opts={"others": True})
    pdf = conn.execute("SELECT id FROM photos WHERE path LIKE '%itinerary.pdf'").fetchone()[0]
    alice = store.get_or_create_person(conn, "Alice")
    store.tag_photos(conn, [pdf], alice)
    from ui_common import UNASSOC_WHERE
    assert pdf not in {r[0] for r in conn.execute(f"SELECT ph.id FROM photos ph WHERE {UNASSOC_WHERE}")}
    dest = str(tmp_path / "Sorted")
    stats = jobs.export_folders(conn, dest, move=True, cleanup=False)
    moved = os.path.join(dest, "~Uncategorized", "Alice", "itinerary.pdf")
    assert os.path.exists(moved), (stats, list(os.walk(dest)))
    assert not os.path.exists(os.path.join(L, "Trip", "itinerary.pdf"))
    assert conn.execute("SELECT path FROM photos WHERE id=?", (pdf,)).fetchone()[0] == store.norm_path(moved)
    # nothing is ever written into it
    jobs.write_tags(conn, store.load_settings()) if hasattr(jobs, "write_tags") else None
    assert open(moved, "rb").read() == b"x" * 100 and not os.path.exists(os.path.splitext(moved)[0] + ".xmp")


def test_new_only_and_verify_only(lib):
    conn, L = lib
    jobs.scan_library(conn, store.load_settings())
    os.remove(os.path.join(L, "Trip", "b.jpg"))                     # deleted outside FaceSort
    write_photo(os.path.join(L, "Trip", "d.jpg"), 4)                # and a new one
    jobs.scan_library(conn, store.load_settings(), opts={"mode": "new"})
    assert names(conn) == ["a.jpg", "b.jpg", "c.jpg", "d.jpg"]      # new imported, the gone one not checked
    write_photo(os.path.join(L, "Trip", "e.jpg"), 5)
    st = jobs.scan_library(conn, store.load_settings(), opts={"mode": "verify"})
    assert names(conn) == ["a.jpg", "c.jpg", "d.jpg"] and st["removed"] == 1   # checked; e not imported
    jobs.scan_library(conn, store.load_settings(), opts={"mode": "both"})
    assert names(conn) == ["a.jpg", "c.jpg", "d.jpg", "e.jpg"]


def test_missing_files_kept_and_listed(lib):
    conn, L = lib
    jobs.scan_library(conn, store.load_settings())
    os.remove(os.path.join(L, "Trip", "a.jpg"))
    st = jobs.scan_library(conn, store.load_settings(), opts={"missing": "keep"})
    assert [os.path.basename(p) for p in st["missing"]] == ["a.jpg"] and st["removed"] == 0
    assert "a.jpg" in names(conn)


def test_what_isnt_looked_for_is_left_alone(lib):
    conn, L = lib
    jobs.scan_library(conn, store.load_settings(), opts={"others": True})
    before = names(conn)
    # an ordinary scan (no other files), a scan of one folder only, a scan leaving a folder out:
    # nothing that wasn't looked for is taken out of the library
    jobs.scan_library(conn, store.load_settings())
    assert names(conn) == before
    jobs.scan_library(conn, store.load_settings(), opts={"folders": [os.path.join(L, "Trip")], "others": True})
    assert names(conn) == before
    jobs.scan_library(conn, store.load_settings(), opts={"exclude": ["Old backups"], "others": True})
    assert names(conn) == before


def test_only_chosen_folders_are_scanned(data_dir):
    import json
    a, b = data_dir.root / "A", data_dir.root / "B"
    write_photo(str(a / "1.jpg"), 1)
    write_photo(str(b / "2.jpg"), 2)
    p = data_dir.data / "settings.json"
    s = json.loads(p.read_text())
    s["folders"] = [str(a), str(b)]
    p.write_text(json.dumps(s))
    conn = store.connect()
    jobs.scan_library(conn, store.load_settings(), opts={"folders": [str(a)]})
    assert names(conn) == ["1.jpg"]
    conn.close()


def test_limit_order_and_small_pictures(lib):
    conn, L = lib
    for i, age in enumerate((300, 100, 200)):
        write_photo(os.path.join(L, "New", f"n{i}.jpg"), 10 + i)
        t = time.time() - age * 86400
        os.utime(os.path.join(L, "New", f"n{i}.jpg"), (t, t))
    tiny = os.path.join(L, "New", "icon.jpg")
    write_photo(tiny, 20, size=(16, 16))
    st = jobs.scan_library(conn, store.load_settings(), opts={"limit": 2, "order": "newest", "min_kb": 1,
                                                              "exclude": ["Trip", "Old backups"]})
    assert st["skipped_small"] == 1
    assert names(conn) == ["icon.jpg"][:0] + sorted(["n1.jpg", "n2.jpg"])     # the two newest
    st = jobs.scan_library(conn, store.load_settings(), opts={"order": "oldest", "exclude": ["Trip", "Old backups"]})
    assert "icon.jpg" in names(conn) and "n0.jpg" in names(conn)              # without the size limit


def test_videos_left_out_stay(lib):
    conn, L = lib
    conn.execute("INSERT INTO photos(path, mtime, size, is_video) VALUES(?, 1, 1, 1)",
                 (store.norm_path(os.path.join(L, "Trip", "gone.mp4")),))
    conn.commit()
    jobs.scan_library(conn, store.load_settings(), opts={"videos": False})
    assert "gone.mp4" in names(conn)
    jobs.scan_library(conn, store.load_settings(), opts={"videos": True})
    assert "gone.mp4" not in names(conn)


def test_scan_options_defaults():
    o = jobs.scan_options({"video_mode": "separate", "import_limit": "250"})
    assert o["videos"] is False and o["limit"] == 250 and o["mode"] == "both" and o["others"] is False
    o = jobs.scan_options({}, {"limit": "x", "min_kb": -5, "bogus": 1})
    assert o["limit"] == 0 and o["min_kb"] == 0 and "bogus" not in o
