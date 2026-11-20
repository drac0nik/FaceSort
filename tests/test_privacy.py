"""1.55: the profile password, recovery key, locking, and encrypting FaceSort's library."""
import json
import os
import zipfile

import pytest

import cryptbox
import privacy
import store
from conftest import pump


@pytest.fixture(autouse=True)
def _forget_key():
    cryptbox.lock()
    yield
    cryptbox.lock()
    store._ENC_CACHE[0] = None


def test_password_recovery_key_and_changes(data_dir):
    assert not cryptbox.has_password()
    rk = cryptbox.setup("first pw")
    assert len(cryptbox.norm_rk(rk)) == 25 and cryptbox.has_password() and cryptbox.unlocked()
    m = cryptbox.master()
    cryptbox.lock()
    with pytest.raises(cryptbox.WrongPassword):
        cryptbox.unlock("wrong")
    cryptbox.unlock("first pw")
    assert cryptbox.master() == m
    cryptbox.change_password("first pw", "second pw")
    cryptbox.lock()
    with pytest.raises(cryptbox.WrongPassword):
        cryptbox.unlock("first pw")
    cryptbox.unlock_with_recovery(rk.lower().replace("-", " "), "third pw")      # (typed loosely)
    assert cryptbox.master() == m
    cryptbox.lock()
    cryptbox.unlock("third pw")
    rk2 = cryptbox.new_recovery("third pw")
    with pytest.raises(cryptbox.WrongPassword):
        cryptbox.unlock_with_recovery(rk, "x")                                  # the old key stopped working
    cryptbox.unlock_with_recovery(rk2, "fourth pw")
    raw = open(cryptbox.lock_path(), encoding="utf-8").read()
    assert m.hex() not in raw and "fourth" not in raw                           # nothing usable in the file
    cryptbox.set_options({"ask_on_open": False, "idle_minutes": 5, "on_minimize": True})
    assert cryptbox.options() == {"ask_on_open": False, "idle_minutes": 5, "on_minimize": True}
    cryptbox.remove_password("fourth pw")
    assert not cryptbox.has_password()


def test_encrypt_and_decrypt_the_library(library):
    lib = library
    n = lib.conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    names = sorted(r[0] for r in lib.conn.execute("SELECT name FROM persons"))
    lib.conn.close()
    data = store.data_dir()
    s = store.load_settings()
    s["marker"] = "my secret folder"
    store.save_settings(s)
    import neverfaces
    neverfaces.save([{"id": 1, "label": "Logo", "added": 1, "embs": {}, "seen": [], "thumb": None}])
    os.makedirs(os.path.join(data, "thumbs", "ab"), exist_ok=True)
    open(os.path.join(data, "thumbs", "ab", "x.jpg"), "wb").write(b"jpg")
    open(os.path.join(data, "facesort.log"), "w").write("moved C:\\Photos\\Jane\\a.jpg\n")
    cryptbox.setup("pw")
    privacy.request(True)
    privacy.convert(True)
    assert cryptbox.data_encrypted() and privacy.pending() is None
    for name in ("library.db", "known_people.db"):
        assert not open(os.path.join(data, name), "rb").read(16).startswith(b"SQLite format 3")
    assert open(os.path.join(data, "settings.json"), "rb").read(4) == cryptbox.MAGIC
    assert open(os.path.join(data, "never_track.json"), "rb").read(4) == cryptbox.MAGIC
    assert not os.path.exists(os.path.join(data, "thumbs")) and not os.path.exists(os.path.join(data, "facesort.log"))
    c = store.connect()
    assert c.execute("SELECT COUNT(*) FROM photos").fetchone()[0] == n
    assert sorted(r[0] for r in c.execute("SELECT name FROM persons")) == names
    c.close()
    assert store.load_settings()["marker"] == "my secret folder"
    assert neverfaces.load()[0]["label"] == "Logo"
    import known
    assert known.counts()[0] >= 0 if hasattr(known, "counts") else True
    # locked: nothing opens, and nothing is written in the clear
    cryptbox.lock()
    with pytest.raises(store.LockedError):
        store.connect()
    assert "marker" not in store.load_settings()
    store.save_settings({"marker": "overwritten"})                               # (ignored while locked)
    cryptbox.unlock("pw")
    assert store.load_settings()["marker"] == "my secret folder"
    # logs keep paths and names out
    from ui_common import log
    log(r"moved C:\Photos\Jane Doe\beach 01.jpg to D:\Sorted")
    assert "Jane" not in open(os.path.join(data, "facesort.log"), encoding="utf-8").read()
    # and back
    privacy.request(False)
    privacy.convert(False)
    assert open(os.path.join(data, "library.db"), "rb").read(16).startswith(b"SQLite format 3")
    assert json.loads(open(os.path.join(data, "settings.json"), encoding="utf-8").read())["marker"] == "my secret folder"
    lib.conn = store.connect()


def test_encrypted_backups(library, tmp_path):
    import backup
    lib = library
    s = dict(lib.settings, backup_folder=str(tmp_path / "bk"))
    lib.conn.close()
    cryptbox.setup("pw")
    privacy.convert(True)
    p = backup.make(s)                                       # unlocked: a snapshot, still encrypted
    info = backup.verify(p)
    with zipfile.ZipFile(p) as z:
        assert not z.read("library.db")[:16].startswith(b"SQLite format 3")
        assert "profile_lock.json" in z.namelist()
    assert info["photos"]
    cryptbox.lock()                                          # the backup on close: no key there
    s["backup_keep"] = 5
    p2 = backup.make(s)
    with zipfile.ZipFile(p2) as z:
        assert not z.read("library.db")[:16].startswith(b"SQLite format 3")
    backup.verify(p2)
    cryptbox.unlock("pw")
    lib.conn = store.connect()


def test_helpers_get_the_key(library, qapp, monkeypatch):
    import time
    import jobfuncs
    from ui_common import ProcessWorker, bg_job
    monkeypatch.delenv("FACESORT_NO_HELPERS", raising=False)
    n = library.conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    library.conn.close()
    cryptbox.setup("pw")
    privacy.convert(True)
    w = bg_job({}, jobfuncs.ping_job)
    assert isinstance(w, ProcessWorker)
    got = {}
    w.ok.connect(lambda r: got.setdefault("ok", r))
    w.failed.connect(lambda tb: got.setdefault("failed", tb))
    w.start()
    end = time.time() + 60
    while not got and time.time() < end:
        pump(qapp, 0.02)
    assert "failed" not in got, got.get("failed")
    assert got["ok"][2] == n
    library.conn = store.connect()


def test_lock_screen_and_settings_page(library, qapp, monkeypatch):
    import facesort
    import privacy_ui
    from PySide6.QtWidgets import QDialog, QMessageBox
    library.conn.close()
    w = facesort.MainWindow()
    w.show()
    pump(qapp, 0.5)
    page = privacy_ui.PrivacyPage(w)
    assert page.b_set.isVisibleTo(page) and not page.idle.isEnabled()
    monkeypatch.setattr(privacy_ui.NewPasswordDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(privacy_ui.NewPasswordDialog, "password", lambda self: "pw")
    shown = []
    monkeypatch.setattr(privacy_ui.RecoveryKeyDialog, "exec", lambda self: shown.append(self.rk) or QDialog.Accepted)
    page.set_password()
    assert cryptbox.has_password() and shown and page.idle.isEnabled()
    page.idle.setValue(10)
    page.on_min.setChecked(True)
    page.enc.setChecked(True)
    page.save_to({})
    assert cryptbox.options()["idle_minutes"] == 10 and page.want_encrypt is True
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    page.apply_after(w)                                      # "restart later": asked for at the next start
    assert privacy.pending() == {"encrypt_data": True}
    privacy.cancel_request()
    # lock now: every window hidden until the password is typed
    seen = []

    def fake_exec(self):
        seen.append(w.isVisible())
        return QDialog.Accepted
    monkeypatch.setattr(privacy_ui.UnlockDialog, "exec", fake_exec)
    w.locker.lock_now()
    assert seen == [False] and w.isVisible()
    # idle: locks by itself after the minutes set
    w.locker.last -= 11 * 60
    w.locker.check()
    assert len(seen) == 2
    w.close()
    pump(qapp, 0.3)
    library.conn = store.connect()


def test_unlock_dialog(data_dir, qapp):
    import privacy_ui
    rk = cryptbox.setup("right")
    cryptbox.lock()
    d = privacy_ui.UnlockDialog()
    d.pw.setText("nope")
    d.try_unlock()
    assert not cryptbox.unlocked() and "not the password" in d.err.text()
    d.pw.setText("right")
    d.try_unlock()
    assert cryptbox.unlocked() and d.result() == QDialog_Accepted()
    assert rk


def QDialog_Accepted():
    from PySide6.QtWidgets import QDialog
    return QDialog.Accepted
