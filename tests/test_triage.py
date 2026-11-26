"""1.59: confirm the sure ones, biggest jobs first, one-off faces, folder names as hints."""
import os

import numpy as np

import store
from conftest import add_photo, unit
from test_ui import window  # noqa: F401


def _vec(i, n=128):
    v = np.zeros(n, np.float32)
    v[i] = 1
    return v


def test_folder_named_after_someone_leans_to_them(data_dir):
    conn = store.connect()
    s = store.load_settings()
    a, b, c = _vec(0), _vec(1), _vec(2)
    lib = str(data_dir.lib)
    for k in range(3):
        add_photo(conn, os.path.join(lib, "Refs", f"anna{k}.jpg"), [a], 10 + k, settings=s, clarity=1.0)
        add_photo(conn, os.path.join(lib, "Refs", f"bea{k}.jpg"), [b], 20 + k, settings=s, clarity=1.0)
    anna, bea = store.get_or_create_person(conn, "Anna Reyes"), store.get_or_create_person(conn, "Bea")
    for name, pid in (("anna", anna), ("bea", bea)):
        fids = [r[0] for r in conn.execute("SELECT f.id FROM faces f JOIN photos p ON p.id=f.photo_id WHERE p.path LIKE ?",
                                           (f"%{name}%",))]
        store.assign(conn, fids, pid)
    thr = store.threshold(s) + 0.03                       # the match line
    near = unit(a * (thr - 0.01) + c * np.sqrt(1 - (thr - 0.01) ** 2))   # just short of Anna
    add_photo(conn, os.path.join(lib, "2019 Anna Reyes shoot", "x.jpg"), [near], 31, settings=s, clarity=1.0)
    add_photo(conn, os.path.join(lib, "Misc", "y.jpg"), [near], 32, settings=s, clarity=1.0)
    add_photo(conn, os.path.join(lib, "Annual party", "z.jpg"), [near], 33, settings=s, clarity=1.0)
    store.regroup(conn, store.threshold(s))
    who = lambda part: conn.execute("SELECT f.person_id FROM faces f JOIN photos p ON p.id=f.photo_id "
                                    "WHERE p.path LIKE ?", (f"%{part}%",)).fetchone()[0]
    assert who("Anna Reyes shoot") == anna                # the folder's name tipped it
    assert who("Misc") != anna and who("Annual") != anna  # (not "Ann…" in "Annual")
    conn.close()


def test_biggest_jobs_and_one_offs(window, qapp):
    import triage
    from conftest import pump
    w, c = window, window.conn
    t = triage.tasks(c, w.sorted_photo_sql())
    assert t == sorted(t, key=lambda x: -x[0])
    if t:
        n, key = w.next_task()
        pump(qapp, 0.5)
        assert key == t[0][1] and tuple(w.view) == tuple(key)
    ids = triage.one_offs(c)
    if ids:
        assert w.set_aside_one_offs(ask=False) == len(ids)
        assert c.execute(f"SELECT COUNT(*) FROM faces WHERE ignored=1 AND id IN ({','.join(map(str, ids))})").fetchone()[0] == len(ids)
        assert not triage.one_offs(c)
    pump(qapp, 0.5)
    w.idle()


def test_confirm_the_sure_ones(window):
    w, c = window, window.conn
    row = c.execute("""SELECT person_id, COUNT(*) FROM faces WHERE confirmed=0 AND ignored=0 AND person_id IS NOT NULL
                       GROUP BY person_id ORDER BY COUNT(*) DESC LIMIT 1""").fetchone()
    assert row, "the test library has suggestions"
    pid, n = row
    fids = [r[0] for r in c.execute("SELECT id FROM faces WHERE person_id=? AND confirmed=0 AND ignored=0 ORDER BY id",
                                    (pid,))]
    for i, f in enumerate(fids):                           # strengths spread 55..95
        c.execute("UPDATE faces SET sugg_conf=? WHERE id=?", (55 + 40 * i / max(1, len(fids) - 1), f))
    c.commit()
    import sure_ui
    items = sure_ui.suggestions(c, pid)
    line = 75
    above = [f for f, s in items if s >= line]
    skip = above[-1:]                                      # the weakest above the line: unticked
    got = w.confirm_sure(pid, answer=(line, skip))
    assert got == len(above) - len(skip)
    conf = {r[0] for r in c.execute("SELECT id FROM faces WHERE confirmed=1 AND person_id=?", (pid,))}
    assert set(above) - set(skip) <= conf and not set(skip) & conf
    assert not ({f for f, s in items if s < line} & conf)  # below the line: still suggestions
    dlg = sure_ui.SureDialog(w, pid, "x", sure_ui.suggestions(c, pid))
    assert dlg.strip.count() >= 0 and dlg.go.text().startswith("Confirm")


def test_big_scan_groups_as_it_goes(data_dir, monkeypatch):
    import jobfuncs
    import jobs
    from conftest import write_photo
    monkeypatch.setattr(jobs, "MIDWAY_MIN_PHOTOS", 4)
    monkeypatch.setattr(jobs, "MIDWAY_MIN_LEFT", 1)
    monkeypatch.setattr(jobs, "MIDWAY_SECS", 0)
    calls = []
    real = store.regroup
    monkeypatch.setattr(store, "regroup", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    order = []
    monkeypatch.setattr(jobs, "check_extras", lambda *a, **k: order.append("extras"))
    for i in range(6):
        write_photo(os.path.join(str(data_dir.lib), f"p{i}.jpg"), 900 + i)
    conn = store.connect()
    s = store.load_settings()
    msgs = []
    jobfuncs.scan_and_group(conn, s, lambda d, t, m="": (msgs.append(m), order.append(m))[0], lambda: False)
    grouped = [m for m in msgs if m.startswith(jobs.GROUPED_MSG)]
    assert len(calls) >= 2 and len(grouped) >= 2                       # during the scan, and after it
    assert grouped[-1] == jobs.GROUPED_MSG + "all"
    assert order.index("extras") > order.index(jobs.GROUPED_MSG + "all")  # duplicates / AI check after
    conn.close()


def test_drop_progress(window, tmp_path):
    import libmove
    from conftest import write_photo
    from test_import import pid_of
    w, c = window, window.conn
    lib = tmp_path / "My Library"
    lib.mkdir()
    w.settings["library_root"] = store.norm_path(str(lib))
    d = tmp_path / "Big drop"
    for i in range(4):
        write_photo(str(d / f"p{i}.jpg"), 1600 + i)
    open(str(d / "notes.txt"), "w").write("n")
    assert w.handle_drop([str(d)], answers={})
    w.idle()
    new = libmove.target(str(d), str(lib))
    batch = c.execute("SELECT dropped FROM photos WHERE path LIKE '%p0.jpg'").fetchone()[0]
    p = w.drop_progress(batch)
    assert p["total"] == 5 and p["sorted"] == 0 and p["others"] == 1 and p["nofaces"] == 4
    who = store.get_or_create_person(c, "Ann")
    store.tag_photos(c, [pid_of(c, os.path.join(new, "p0.jpg"))], who)
    p = w.drop_progress(batch)
    assert p["sorted"] == 1 and p["nofaces"] == 3
    txt = w.drop_progress_text(batch)
    assert txt.startswith("Sorted 1 of 5 (20%)") and "3 photos with no faces" in txt and "1 other file" in txt
    w.rebuild_tree()
    it = w.find_tree_item(("dropped", batch))
    assert it is not None and "20% sorted" in it.text(0)
