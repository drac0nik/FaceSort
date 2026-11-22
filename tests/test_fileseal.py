"""1.58: encrypting the photo and video files themselves (optional, off unless turned on)."""
import hashlib
import os

import pytest

import cryptbox
import fsx
import store
from conftest import pump, write_photo
from test_ui import window  # noqa: F401  (the window fixture)


@pytest.fixture(autouse=True)
def _forget_key():
    cryptbox.lock()
    yield
    cryptbox.lock()
    store._ENC_CACHE[0] = None


def _sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def _real_files(c):
    return [(i, p) for i, p in c.execute("SELECT id, path FROM photos WHERE COALESCE(is_other,0)=0")
            if os.path.isfile(p)]


def test_off_by_default(data_dir):
    assert not fsx.enabled()
    p = str(data_dir.lib / "x.jpg")
    assert fsx.plain(p) == p and fsx.outside(p) == p


def test_encrypt_use_and_decrypt_the_files(window, data_dir, qapp, tmp_path):
    w, c = window, window.conn
    cryptbox.setup("pw")
    vault = str(tmp_path / "lib" / fsx.VAULT)
    os.makedirs(os.path.dirname(vault), exist_ok=True)
    fsx.set_enabled(True, vault=vault)
    files = _real_files(c)
    assert files
    shas = {p: _sha(p) for _i, p in files}
    assert w.seal_soon()
    w.idle()
    n, waiting = fsx.counts(c)
    assert n == len(files) and waiting == len(files)
    blob = c.execute("SELECT blob FROM vault LIMIT 1").fetchone()[0]
    raw = open(fsx.blob_path(blob), "rb").read(16)
    assert raw[:4] == cryptbox.FILE_MAGIC and b"JFIF" not in raw and b"Exif" not in raw
    assert all(os.path.exists(p) for p in shas)                         # originals kept until you confirm
    # remove the originals: only the encrypted copies are left, empty folders go
    d, kept = fsx.remove_originals(c, fsx.originals(c), roots=fsx.library_roots(w.settings))
    assert d == len(files) and not kept and not any(os.path.exists(p) for p in shas)
    assert not [x for x in os.listdir(str(data_dir.lib)) if x != fsx.VAULT and os.path.isdir(os.path.join(str(data_dir.lib), x))
                and any(f.endswith(".jpg") for _r, _d, fs in os.walk(os.path.join(str(data_dir.lib), x)) for f in fs)]
    # still readable through FaceSort
    pid, path = files[0]
    copy = fsx.plain(path)
    assert copy != path and _sha(copy) == shas[path] and copy.startswith(fsx.open_dir())
    import engine
    assert engine.load_image(path, max_side=256).bgr is not None
    out = fsx.outside(path)
    assert os.path.basename(out) == os.path.basename(path) and _sha(out) == shas[path]
    # a scan doesn't take them out of the library
    import jobs
    jobs.scan_library(c, w.settings, opts={"mode": "both"})
    assert fsx.counts(c)[0] == len(files)
    assert c.execute("SELECT COUNT(*) FROM photos WHERE id=?", (pid,)).fetchone()[0] == 1
    # moving / renaming only changes where FaceSort shows it
    import renamer
    st = renamer.apply(c, [(pid, path, os.path.join(os.path.dirname(path), "renamed.jpg"))])
    assert st["renamed"] == 1
    new = c.execute("SELECT path FROM photos WHERE id=?", (pid,)).fetchone()[0]
    assert new.endswith("renamed.jpg") and not os.path.exists(new) and _sha(fsx.plain(new)) == shas[path]
    shas[new] = shas.pop(path)
    # turning it: decrypted, turned, encrypted again
    from PIL import Image, ImageOps
    with Image.open(fsx.plain(new)) as im:
        size0 = ImageOps.exif_transpose(im).size
    old_blob = fsx.entry(new)[0]
    done, skipped = w.rotate_photos([pid], True)
    assert done == 1, skipped
    assert fsx.entry(new)[0] != old_blob and not os.path.exists(fsx.blob_path(old_blob))
    with Image.open(fsx.plain(new)) as im:
        assert ImageOps.exif_transpose(im).size == (size0[1], size0[0])
    shas[new] = _sha(fsx.plain(new))
    # names aren't written into encrypted files
    jobs.write_tags(c, w.settings)
    assert not os.path.exists(new) and not os.path.exists(os.path.splitext(new)[0] + ".xmp")
    # exporting gives a normal copy
    import exporter
    ex = exporter.export(c, [pid], {"dest": str(tmp_path / "out"), "zip": False, "strip": False})
    outs = [os.path.join(r, f) for r, _d, fs in os.walk(ex["out"]) for f in fs]
    assert len(outs) == 1 and outs[0].endswith("renamed.jpg") and _sha(outs[0]) == shas[new]
    # locking / closing wipes the readable copies
    fsx.wipe()
    assert not os.path.exists(fsx.open_dir())
    # decrypting puts every one back as a normal file at its place in the library
    w.start_unseal()
    w.idle()
    assert fsx.counts(c) == (0, 0) and not fsx.enabled()
    for p, h in shas.items():
        assert os.path.isfile(p) and _sha(p) == h, p
    assert not os.path.exists(vault) or not any(fs for _r, _d, fs in os.walk(vault))


def test_new_photos_encrypted_as_they_come(window, data_dir, qapp, tmp_path):
    w, c = window, window.conn
    cryptbox.setup("pw")
    fsx.set_enabled(True, vault=str(tmp_path / "vault"))
    w.seal_soon()
    w.idle()
    fsx.remove_originals(c, fsx.originals(c), roots=fsx.library_roots(w.settings))
    # a picture FaceSort makes (here: a copy of an encrypted photo) goes straight once encrypted
    pid, path = c.execute("SELECT v.photo_id, p.path FROM vault v JOIN photos p ON p.id=v.photo_id LIMIT 1").fetchone()
    shot = os.path.join(str(data_dir.lib), "made.jpg")
    write_photo(shot, 77)
    w.add_derived(path, shot, scan=False)
    assert w.seal_soon()
    w.idle()
    assert not os.path.exists(shot) and fsx.entry(shot) is not None
    # your own new photo waits for the review
    mine = os.path.join(str(data_dir.lib), "Trip", "new.jpg")
    write_photo(mine, 78)
    c.execute("INSERT INTO photos(path, mtime, size) VALUES(?,?,?)", (store.norm_path(mine), 1, os.path.getsize(mine)))
    c.commit()
    w.seal_soon()
    w.idle()
    assert os.path.exists(mine) and fsx.entry(mine) is not None
    assert store.norm_path(mine) in [p for p, _i in fsx.originals(c)]
    # an encrypted name counts as taken
    import viewer_edit
    assert viewer_edit.copy_path(path, "{name}") != path
    pump(qapp, 0.1)


def test_changed_original_is_encrypted_again_before_it_goes(window, data_dir, qapp, tmp_path):
    w, c = window, window.conn
    cryptbox.setup("pw")
    fsx.set_enabled(True, vault=str(tmp_path / "vault"))
    w.seal_soon()
    w.idle()
    path, pid = fsx.originals(c)[0]
    write_photo(path, 991)                                   # edited in another app afterwards
    h = _sha(path)
    fsx.remove_originals(c, [(path, pid)])
    assert not os.path.exists(path) and _sha(fsx.plain(path)) == h
    fsx.collect_gone(c)
    assert len(os.listdir(os.path.join(str(tmp_path / "vault")))) >= 1


def test_settings_page(window, qapp, tmp_path, monkeypatch):
    """Off by default; needs the library encrypted first; the review button shows when originals wait."""
    import privacy_ui
    w = window
    if getattr(w, "locker", None) is not None:
        monkeypatch.setattr(w.locker, "lock_now", lambda: None)    # (no locking in the middle of the test)
    page = privacy_ui.PrivacyPage(w)
    assert not page.files.isChecked() and not page.files.isEnabled()
    cryptbox.setup("pw")
    monkeypatch.setattr(cryptbox, "data_encrypted", lambda data=None: True)
    monkeypatch.setattr(store, "encrypted_now", lambda *a, **k: False)    # (the test's database stays plain)
    page.refresh()
    assert page.files.isEnabled() and not page.b_review.isVisibleTo(page)
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    page.enc.setChecked(True)                   # (the library stays "encrypted" - only the files change)
    page.files.setChecked(True)
    page.save_to(w.settings)
    page.apply_after(w)
    import time
    end = time.time() + 30                       # (it may start once the duplicate check has given way)
    while time.time() < end and not fsx.counts(w.conn)[0]:
        pump(qapp, 0.2)
    w.idle()
    assert fsx.enabled() and fsx.counts(w.conn)[0] > 0
    page.refresh()
    assert page.b_review.isVisibleTo(page) and "waiting" in page.files_state.text()


def test_viewer_and_edits_on_encrypted_photos(window, data_dir, qapp, tmp_path, monkeypatch):
    from test_edit import _open_viewer, _wait
    import ui_common
    w, c = window, window.conn
    cryptbox.setup("pw")
    fsx.set_enabled(True, vault=str(tmp_path / "vault"))
    w.seal_soon()
    w.idle()
    fsx.remove_originals(c, fsx.originals(c), roots=fsx.library_roots(w.settings))
    v = _open_viewer(w, qapp)
    _wait(qapp, lambda: not v.image.isNull() and not getattr(v, "_loading", False))
    assert not os.path.exists(v.path) and v.image.width() > 0
    opened = []
    monkeypatch.setattr(ui_common.subprocess, "Popen", lambda args, **k: opened.append(args))
    monkeypatch.setattr(ui_common.os, "startfile", lambda p: opened.append([p]), raising=False)
    ui_common.open_file(v.path)
    assert opened and os.path.basename(opened[-1][-1]) == os.path.basename(v.path)
    assert opened[-1][-1].startswith(os.path.join(store.data_dir(), fsx.OUTSIDE_DIR))
    # an edited copy of an encrypted photo: saved, then encrypted itself (FaceSort's own copy goes)
    v.enter_edit()
    v.ed_panel.rows["exposure"].set_value(30)
    v.ed_save(False)
    _wait(qapp, lambda: v.editing is None)
    w.idle()
    made = c.execute("SELECT path FROM photos WHERE path LIKE '%(edited)%'").fetchone()
    assert made
    _wait(qapp, lambda: fsx.entry(made[0]) is not None and not os.path.exists(made[0]), 30)
    v.close_viewer()
    pump(qapp, 3)
    w.idle()


def test_other_files_encrypted_too(window, data_dir, qapp, tmp_path):
    """1.58.1: notes, links and documents next to the photos go into the vault as well."""
    w, c = window, window.conn
    folder = os.path.dirname(_real_files(c)[0][1])
    note = os.path.join(folder, "release notes.txt")
    open(note, "w").write("Anna Reyes - model release")
    h = _sha(note)
    cryptbox.setup("pw")
    fsx.set_enabled(True, vault=str(tmp_path / "vault"))
    w.seal_soon()
    w.idle()
    assert fsx.entry(note) is not None
    assert c.execute("SELECT is_other FROM photos WHERE path=?", (store.norm_path(note),)).fetchone()[0] == 1
    fsx.remove_originals(c, fsx.originals(c), roots=fsx.library_roots(w.settings))
    assert not os.path.exists(note) and _sha(fsx.plain(note)) == h
    for root, _d, files in os.walk(str(tmp_path / "vault")):
        for f in files:
            assert b"Anna" not in open(os.path.join(root, f), "rb").read()
    # an encrypted other file follows its photos out of ~Unsorted by FaceSort's record alone
    import libmove
    lib = store.norm_path(str(data_dir.lib))
    uns = os.path.join(libmove.unsorted_dir(lib), "Trip")
    pid = c.execute("SELECT id FROM photos WHERE path=?", (store.norm_path(note),)).fetchone()[0]
    moved_to = os.path.join(uns, "release notes.txt")
    c.execute("UPDATE photos SET path=? WHERE id=?", (store.norm_path(moved_to), pid))
    c.commit()
    libmove.remember_moves(c, lib, [(os.path.join(uns, "x.jpg"), os.path.join(lib, "Alice", "x.jpg"))])
    moved = libmove.follow_extras(c, lib)
    assert moved and moved[0][1].endswith(os.path.join("Alice", "~files", "release notes.txt"))
    assert _sha(fsx.plain(moved[0][1])) == h
    # decrypting brings it back as a normal file too
    w.start_unseal()
    w.idle()
    assert _sha(moved[0][1]) == h
