"""History (1.47): the window's changes are recorded (one entry per click), any entry can be
undone, changes made since are left alone, merges and file moves come back, old entries go."""
import os
import time

import history
import store
from test_ui import window  # noqa: F401  (the window fixture)


def latest(c):
    return history.entries(c, 1)[0]


def test_name_ignore_and_undo(window):
    w, c = window, window.conn
    fids = [r[0] for r in c.execute("SELECT id FROM faces WHERE person_id IS NULL AND ignored=0 LIMIT 3")]
    before = {f: tuple(r) for f, *r in c.execute(
        f"SELECT id, person_id, confirmed, cluster FROM faces WHERE id IN ({','.join('?' * len(fids))})", fids)}
    pid = store.get_or_create_person(c, "History Person")
    store.assign(c, fids, pid)
    w.after_change()
    e = latest(c)
    assert e["text"].startswith("Named 3 faces History Person") and not e["undone"]
    store.set_ignored(c, fids[:1], 1)
    w.after_change()
    assert latest(c)["text"] == "Ignored 1 face"
    # undo the naming (not the latest entry): the ignored face changed since - left alone
    done, skipped = history.undo(c, e["id"])
    assert skipped >= 1 and done >= 2
    now = {f: tuple(r) for f, *r in c.execute(
        f"SELECT id, person_id, confirmed, cluster FROM faces WHERE id IN ({','.join('?' * len(fids))})", fids)}
    for f in fids[1:]:
        assert now[f] == before[f]
    assert history.entries(c, 5)[1]["undone"]
    # Ctrl+Z: the latest (the ignore)
    w.undo_last()
    assert c.execute("SELECT ignored FROM faces WHERE id=?", (fids[0],)).fetchone()[0] == 0


def test_merge_and_album_undo(window):
    w, c = window, window.conn
    people = [r for r in c.execute("SELECT id, name FROM persons ORDER BY id LIMIT 2")]
    (a, an), (b, bn) = people
    faces_b = [r[0] for r in c.execute("SELECT id FROM faces WHERE person_id=?", (b,))]
    store.rename_person(c, b, an)                         # renaming B to A's name merges them
    w.after_change()
    assert not c.execute("SELECT 1 FROM persons WHERE id=?", (b,)).fetchone()
    w.undo_last()
    assert c.execute("SELECT name FROM persons WHERE id=?", (b,)).fetchone()[0] == bn
    assert {r[0] for r in c.execute("SELECT id FROM faces WHERE person_id=?", (b,))} == set(faces_b)
    ph = [r[0] for r in c.execute("SELECT id FROM photos LIMIT 2")]
    aid = store.create_album(c, "History album")
    store.add_to_album(c, aid, ph)
    w.after_change()
    w.undo_last()
    assert not c.execute("SELECT 1 FROM album_photos WHERE album_id=?", (aid,)).fetchone()


def test_background_changes_not_recorded_and_file_moves(window, tmp_path):
    w, c = window, window.conn
    n = len(history.entries(c, 10000))
    with history.paused():
        store.set_sort_later(c, [c.execute("SELECT id FROM photos LIMIT 1").fetchone()[0]], True)
    w.after_change()
    assert len(history.entries(c, 10000)) == n
    # files moved (Sort into folders) go back
    src = c.execute("SELECT path FROM photos LIMIT 1").fetchone()[0]
    dst = str(tmp_path / "moved" / os.path.basename(src))
    os.makedirs(os.path.dirname(dst))
    os.rename(src, dst)
    c.execute("UPDATE photos SET path=? WHERE path=?", (dst, src))
    c.commit()
    history.record_files("Moved 1 file", [(src, dst)])
    history.flush(c)
    done, skipped = history.undo(c, latest(c)["id"])
    assert done == 1 and os.path.exists(src) and not os.path.exists(dst)
    assert c.execute("SELECT 1 FROM photos WHERE path=?", (src,)).fetchone()


def test_kept_30_days_and_at_least_5000(window, monkeypatch):
    c = window.conn
    monkeypatch.setattr(history, "KEEP_MIN", 3)
    old = time.time() - 40 * 86400
    for i in range(6):
        with c.write():
            c.execute("INSERT INTO history(at, kind, text, data, size) VALUES(?, 'x', ?, '{}', 0)", (old, f"old {i}"))
    history.record(c, "x", "new", {"rows": [], "links": [], "files": []})
    texts = [e["text"] for e in history.entries(c, 100)]
    assert texts[0] == "new" and len([t for t in texts if t.startswith("old")]) == 2   # 3 kept in all
