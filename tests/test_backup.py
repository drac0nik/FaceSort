"""1.52: library backups - make, verify, keep the newest N, restore (with the old library kept), move folders."""
import json
import os
import time
import zipfile

import pytest


def _settings(library, tmp):
    s = dict(library.settings)
    s["backup_folder"] = str(tmp / "backups")
    return s


def test_make_and_verify(library, tmp_path):
    import backup
    import store
    library.conn.commit()
    s = _settings(library, tmp_path)
    steps = []
    path = backup.make(s, progress=lambda d, t, m: steps.append(m))
    assert os.path.basename(path).startswith(backup.PREFIX) and path.endswith(".zip")
    info = backup.verify(path)
    assert "library.db" in info["files"] and "settings.json" in info["files"]
    n = library.conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    assert info["photos"] == n
    assert steps and steps[-1] == "Backup made"
    assert backup.listing(s["backup_folder"])[0][0] == path
    assert not [f for f in os.listdir(s["backup_folder"]) if f.endswith(".part") or f.startswith(".")]
    assert backup.state(store.data_dir())["last"] == path


def test_damaged_backup_refused(library, tmp_path):
    import backup
    s = _settings(library, tmp_path)
    path = backup.make(s)
    bad = str(tmp_path / "bad.zip")
    with zipfile.ZipFile(path) as z, zipfile.ZipFile(bad, "w") as out:
        for it in z.infolist():
            data = z.read(it.filename)
            if it.filename == "library.db":
                data = data[:-10] + b"x" * 10
            out.writestr(it, data)
    with pytest.raises(ValueError):
        backup.verify(bad)
    other = str(tmp_path / "other.zip")
    with zipfile.ZipFile(other, "w") as z:
        z.writestr("hello.txt", "hi")
    with pytest.raises(Exception):
        backup.ask_restore(other)
    assert backup.pending() is None


def test_keeps_newest(library, tmp_path):
    import backup
    s = _settings(library, tmp_path)
    s["backup_keep"] = 2
    dest = s["backup_folder"]
    os.makedirs(dest)
    for i in range(3):                                 # older backups already there
        p = os.path.join(dest, f"{backup.PREFIX}2020-01-0{i + 1} 000000.zip")
        open(p, "wb").close()
        os.utime(p, (1.6e9 + i, 1.6e9 + i))
    open(os.path.join(dest, "my notes.zip"), "wb").close()    # not ours: never touched
    newest = backup.make(s)
    names = sorted(os.listdir(dest))
    assert os.path.basename(newest) in names
    assert len([n for n in names if n.startswith(backup.PREFIX)]) == 2
    assert f"{backup.PREFIX}2020-01-03 000000.zip" in names and "my notes.zip" in names


def test_due(library, tmp_path):
    import backup
    import store
    s = _settings(library, tmp_path)
    for f in ("close", "daily", "weekly"):
        s["backup_frequency"] = f
        assert backup.due(s)
    s["backup_frequency"] = "manual"
    assert not backup.due(s)
    s["backup_frequency"] = "daily"
    backup.make(s)
    assert not backup.due(s)                            # just made (and nothing changed)
    s["backup_frequency"] = "close"
    assert not backup.due(s)                            # nothing changed since
    time.sleep(1.1)
    store.save_settings(dict(s, thumb_size=123))      # something changed
    assert backup.due(s)
    s["backup_frequency"] = "daily"
    assert not backup.due(s)                            # changed, but not a day yet


def test_restore_round_trip(library, tmp_path):
    import backup
    import store
    s = _settings(library, tmp_path)
    before = library.conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    path = backup.make(s)
    library.conn.execute("DELETE FROM faces")
    library.conn.execute("DELETE FROM photos")
    library.conn.commit()
    library.conn.close()
    data = store.data_dir()
    os.makedirs(os.path.join(data, "thumbs"), exist_ok=True)
    backup.ask_restore(path)
    assert backup.pending() == path
    info, safety = backup.restore(path)
    assert backup.pending() is None
    assert os.path.isdir(safety) and os.path.exists(os.path.join(safety, "library.db"))
    assert not os.path.exists(os.path.join(data, "thumbs"))
    c = store.connect()
    try:
        assert c.execute("SELECT COUNT(*) FROM photos").fetchone()[0] == before
    finally:
        c.close()
    library.conn = store.connect()                      # (the fixture closes it)


def test_remap_moved_folders(library, tmp_path):
    import backup
    import store
    s = dict(library.settings)
    old = s["folders"][0]
    assert backup.missing_roots(s) == []
    new = str(tmp_path / "moved")
    os.rename(old, new)
    assert backup.missing_roots(s) == [old]
    n = backup.remap(library.conn, s, old, new)
    assert n == library.conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0] > 0
    paths = [r[0] for r in library.conn.execute("SELECT path FROM photos")]
    assert all(os.path.exists(p) for p in paths)
    assert s["folders"][0] == store.norm_path(new)
    assert backup.missing_roots(s) == []


def test_backup_now_command(library, tmp_path, monkeypatch):
    import backup
    import store
    s = _settings(library, tmp_path)
    store.save_settings(s)
    library.conn.commit()
    monkeypatch.setattr(backup.time, "sleep", lambda t: None)
    assert backup.backup_now_main() == 0
    assert len(backup.listing(s["backup_folder"])) == 1


def test_restore_at_start_asks_for_moved_folder(library, tmp_path, monkeypatch, qapp):
    """The restart after Restore…: the backup goes in place before the window opens, and a photo
    folder that isn't there any more is pointed to its new place."""
    import backup
    import facesort
    import store
    s = _settings(library, tmp_path)
    store.save_settings(s)
    path = backup.make(s)
    library.conn.close()
    old = s["folders"][0]
    new = str(tmp_path / "elsewhere")
    os.rename(old, new)
    backup.ask_restore(path)
    asked = []
    monkeypatch.setattr(facesort.QMessageBox, "question", lambda *a, **k: (asked.append(a[2]), facesort.QMessageBox.Yes)[1])
    monkeypatch.setattr(facesort.QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(facesort.QFileDialog, "getExistingDirectory", lambda *a, **k: new)
    facesort.finish_restore()
    assert backup.pending() is None and asked and old in asked[0]
    assert store.load_settings()["folders"][0] == store.norm_path(new)
    library.conn = store.connect()
    assert all(os.path.exists(r[0]) for r in library.conn.execute("SELECT path FROM photos"))


def test_left_over_lock_and_big_progress(library, tmp_path, qapp):
    """1.55.3: a backup cut off in the middle left its lock (every backup for an hour refused); byte
    counts over 2 GB overflowed the progress signal."""
    import backup
    s = _settings(library, tmp_path)
    os.makedirs(s["backup_folder"])
    with open(os.path.join(s["backup_folder"], backup.LOCK), "w") as f:
        f.write("999999")                                # a process that's gone
    assert backup.make(s)
    from ui_common import Worker
    got = []
    w = Worker(lambda conn, progress=None, cancelled=None: progress(3_000_000_000, 6_000_000_000, "big"))
    w.progress.connect(lambda d, t, m: got.append((d, t)))
    w.start()
    w.wait(10000)
    from conftest import pump
    pump(qapp, 0.3)
    assert got == [(500, 1000)] and not w.error
