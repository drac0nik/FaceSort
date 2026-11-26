"""The window, offscreen: every tab and list opens without errors, Smart Groups are split by
folder, the viewer opens, duplicates update quickly, and nothing holds the write gate long."""
import os
import sys
import time

import numpy as np
import pytest

import dbgate
import store
from conftest import add_photo, build_library, pump, unit


@pytest.fixture
def window(data_dir, qapp):
    lib = build_library(data_dir)
    # a set of three duplicates (already compared, as the background check leaves them)
    ids = []
    for i, size in enumerate([(320, 240), (640, 480), (160, 120)]):
        p = os.path.join(str(data_dir.lib), "Dupes", f"copy{i}.jpg")
        ids.append(add_photo(lib.conn, p, [unit(np.random.default_rng(50).normal(size=128))], seed=500 + i, size=size))
    lib.conn.commit()
    for p in ids:
        lib.conn.execute("UPDATE photos SET phash=? WHERE id=?", (0x1357_9BDF_2468_ACE0, p))
    sz = dict(lib.conn.execute(f"SELECT id, size FROM photos WHERE id IN ({','.join(map(str, ids))})"))
    for a in ids:
        for b in ids:
            if a < b:
                lib.conn.execute("INSERT INTO dup_checked(a,b,sa,sb,same) VALUES(?,?,?,?,1)", (a, b, sz[a], sz[b]))
    lib.conn.commit()
    lib.conn.close()

    import facesort
    from PySide6.QtCore import QRect
    errors = []
    old_hook = sys.excepthook
    sys.excepthook = lambda t, e, tb: errors.append("".join(__import__("traceback").format_exception(t, e, tb)))
    w = facesort.MainWindow()
    w.setGeometry(QRect(30, 30, 1400, 860))
    w.show()

    def idle():
        pump(qapp, 0.3)
        end = time.time() + 60
        while time.time() < end and (w.busy() or (w.regroup_worker and w.regroup_worker.isRunning())
                                     or w.regroup_timer.isActive() or w.bg_running()):
            pump(qapp, 0.1)
        pump(qapp, 0.2)
    idle()
    w.idle = idle
    w.errors = errors
    w.dup_ids = ids
    w.lib = lib
    yield w
    w.close()
    pump(qapp, 0.3)
    sys.excepthook = old_hook
    assert not errors, "\n".join(errors)
    assert not dbgate.GATE.slow, dbgate.GATE.slow
    assert dbgate.GATE.ui_wait_max < 1.0


def all_keys(w, tree):
    import facesort
    return [facesort.key_of(it) for it in w.iter_items() if it.treeWidget() is tree and facesort.key_of(it)]


def test_tabs(window, qapp):
    w = window
    assert [w.side_tabs.tabText(i) for i in range(4)] == ["People", "Folders", "Albums", "Smart Groups"]
    for i in range(4):
        w.side_tabs.setCurrentIndex(i)
        pump(qapp, 0.1)
    people = {k[1] for k in all_keys(w, w.tree) if k[0] == "person"}
    assert len(people) == 3


def test_every_list_opens(window):
    w = window
    seen = 0
    for tree in (w.tree, w.treeF, w.treeA, w.tree2):
        for k in all_keys(w, tree):
            w.view = k
            w.refresh_grid()
            w.idle()
            seen += 1
    assert seen >= 6


def test_smart_groups_are_split_by_folder(window):
    w = window
    kinds = w.smart_kinds()
    checked = 0
    for k in all_keys(w, w.tree2):
        if k[0] not in kinds or k[0] in ("dupes", "errors"):
            continue
        w.view = k
        w.refresh_grid()
        rows = w.model.rows
        items = [r for r in rows if r[0]]
        if not items:
            continue
        heads = [r for r in rows if r[0] == 0]
        folders = {os.path.dirname(r[1]) for r in items}
        assert len(heads) >= len(folders), (k, [h[1] for h in heads])
        for f in folders:
            assert any(os.path.normpath(f) in h[1] for h in heads), (k, f)
        checked += 1
    assert checked >= 2                    # the two stranger groups at least


def test_viewer_opens_steps_and_closes(window, qapp):
    w = window
    pid = next(iter(w.lib.who))
    person = next(k for k in all_keys(w, w.tree) if k[0] == "person")
    w.view = person
    w.refresh_grid()
    i = next(i for i, r in enumerate(w.model.rows) if r[0])
    w.open_viewer(w.model.index(i))
    pump(qapp, 0.8)
    v = w.viewer
    assert v is not None and v.isVisible()
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    QTest.keyClick(v.canvas, Qt.Key_Right)
    pump(qapp, 0.4)
    QTest.keyClick(v.canvas, Qt.Key_Left)
    pump(qapp, 0.4)
    QTest.keyClick(v.canvas, Qt.Key_Escape)
    pump(qapp, 0.4)


def test_keep_one_copy_updates_sets_like_a_full_check(window):
    w = window
    sets = w.dup_sets(wait=True)
    d = next(d for d in sets if set(w.dup_ids) <= set(d["ids"]))
    assert d["best"] == w.dup_ids[1]                       # the largest picture
    w.not_duplicate(w.dup_ids[2])                          # one leaves the set
    quick = sorted(sorted(d["ids"]) for d in w._dups)
    full = sorted(sorted(d["ids"]) for d in store.duplicate_sets(w.conn, check_new=False))
    assert quick == full
    w.keep_this_copy(w.dup_ids[0])                         # keep the smaller one: the best goes
    w.idle()
    assert not any(w.dup_ids[0] in d["ids"] for d in w._dups)
    assert w.conn.execute("SELECT discarded FROM photos WHERE id=?", (w.dup_ids[1],)).fetchone()[0] == 1


def test_not_ai(window):
    w = window
    pids = list(w.lib.who)[:2]
    w.conn.executemany("UPDATE photos SET ai=1 WHERE id=?", [(p,) for p in pids])
    w.conn.commit()
    w.act_not_ai(pids)
    w.idle()
    assert {r[0] for r in w.conn.execute(f"SELECT ai FROM photos WHERE id IN ({pids[0]},{pids[1]})")} == {0}


def test_settings_dialog_opens_and_closes(window, qapp, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from PySide6.QtCore import QTimer
    w = window

    def close_it():
        for top in qapp.topLevelWidgets():
            if isinstance(top, QDialog) and top.isVisible():
                top.reject()
    QTimer.singleShot(600, close_it)
    w.open_settings()
    pump(qapp, 0.3)


def test_backups_tab(window, qapp, tmp_path, monkeypatch):
    """1.52: Settings has a Backups tab; Back up now makes one in the background; its choices are saved."""
    from dialogs import SettingsDialog
    w = window
    w.settings["backup_folder"] = str(tmp_path / "bk")
    d = SettingsDialog(w, w.settings)
    assert d.tabs.tabText(d.tabs.count() - 1) == "Backups"
    page = d.backups_page
    page.freq.setCurrentIndex(page.freq.findData("weekly"))
    page.keep.setValue(4)
    page.back_up_now()
    end = time.time() + 30
    while page.worker.isRunning() or not page.b_now.isEnabled():
        pump(qapp, 0.05)
        assert time.time() < end
    assert page.list.count() == 1 and page.list.item(0).data(0x0100)
    page._progress(3_000_000_000, 6_000_000_000, "big")         # (1.55.2: over 2 GB overflowed the bar)
    assert page.progress.value() == 500
    s = d.result_settings()
    assert s["backup_frequency"] == "weekly" and s["backup_keep"] == 4 and s["backup_folder"] == str(tmp_path / "bk")
    d.reject()


def test_file_tools(window, qapp, tmp_path, monkeypatch):
    """1.53: Rename… / Export… / Contact sheet… are on the right-click menus and their windows work."""
    import contactsheet_ui
    import export_ui
    import rename_ui
    from PySide6.QtWidgets import QDialog
    w = window
    alice = w.conn.execute("SELECT id FROM persons WHERE name='Alice'").fetchone()[0]
    it = w.find_tree_item(("person", alice))
    m = w.build_tree_menu(it)
    texts = [a.text() for a in m.actions()]
    assert "Rename…" in texts and "Export…" in texts and "Contact sheet…" in texts
    pids = w.person_file_ids(alice)
    assert pids
    d = rename_ui.RenameDialog(w, pids, "Alice")
    d.pattern.box.setEditText("{person} {n}")
    d.refresh()
    assert d.rows and all(os.path.basename(n).startswith("Alice ") for _p, _o, n in d.rows)
    d.reject()
    monkeypatch.setattr(rename_ui.RenameDialog, "exec", lambda self: (self.refresh(), QDialog.Accepted)[1])
    w.settings["rename_pattern"] = "{person} {n}"
    rename_ui.run_rename(w, pids, "Alice")
    w.idle()
    paths = [w.conn.execute("SELECT path FROM photos WHERE id=?", (p,)).fetchone()[0] for p in pids]
    assert all(os.path.basename(p).startswith("Alice ") and os.path.exists(p) for p in paths)
    assert "Renamed" in w.conn.execute("SELECT text FROM history ORDER BY id DESC LIMIT 1").fetchone()[0]
    e = export_ui.ExportFilesDialog(w, pids, "Alice")
    assert e.strip.isChecked()                             # on by default
    e.strip.setChecked(False)
    e.dest.setText(str(tmp_path))
    e.accept()
    assert w.settings["files_export_strip"] is False       # the last choice is remembered
    assert not export_ui.ExportFilesDialog(w, pids, "Alice").strip.isChecked()
    c = contactsheet_ui.ContactSheetDialog(w, len(pids), "Alice")
    assert c.title.text() == "Alice" and c.options()["fmt"] in ("pdf", "jpg")   # (offscreen CI: no font list)
    c.reject()


def test_same_shoot_list(window, qapp):
    """Suggestions show under Smart Groups > Same shoot; C confirms (the photo gets tagged)."""
    import bodies
    w = window
    w.settings["same_shoot"] = True
    c = w.conn
    alice = c.execute("SELECT id FROM persons WHERE name='Alice'").fetchone()[0]
    pid = c.execute("SELECT id FROM photos WHERE path LIKE '%stranger0_0.jpg'").fetchone()[0]
    bid = c.execute("INSERT INTO bodies(photo_id, x, y, w, h, score, desc, thumb) VALUES(?,0.1,0.1,0.3,0.8,0.9,?,?)",
                    (pid, np.zeros(768, np.float16).tobytes(), b"")).lastrowid
    c.execute("INSERT INTO body_suggestions(body_id, person_id, score, reason) VALUES(?,?,0.8,'same clothes as in 2 photos')",
              (bid, alice))
    c.execute("DELETE FROM faces WHERE photo_id=?", (pid,))            # nobody's face shows on it
    c.commit()
    w.rebuild_tree()
    w.idle()
    it = w.find_tree_item(("shoot", None))
    assert it is not None and it.text(0).startswith("Same shoot")
    w.view = ("shoot", None)
    w.refresh_grid()
    rows = [r for r in w.model.rows if r[0]]
    assert rows == [(-pid, rows[0][1], "Alice", 0)]
    i = w.model.rows.index(rows[0])
    assert "same clothes" in w.model.data(w.model.index(i), __import__("PySide6.QtCore").QtCore.Qt.ToolTipRole)
    w.grid.setCurrentIndex(w.model.index(i))
    w.act_confirm()
    w.idle()
    assert c.execute("SELECT 1 FROM photo_people WHERE photo_id=? AND person_id=?", (pid, alice)).fetchone()
    assert c.execute("SELECT person_id FROM bodies WHERE id=?", (bid,)).fetchone()[0] == alice


def test_scan_window_and_unassociated_files(window, qapp, data_dir):
    """The Scan window's choices reach the scan; other files show in Smart Groups with an icon and
    their name, and Enter tags them."""
    from dialogs import ScanDialog
    w = window
    with open(os.path.join(str(data_dir.lib), "Holiday 2019", "tickets.pdf"), "wb") as f:
        f.write(b"%PDF-1.4 test")
    dlg = ScanDialog(w, w.settings, nofaces=0, dark_unchecked=0)
    dlg.cb_others.setChecked(True)
    dlg.rb_new.setChecked(True)
    assert not dlg.missing.isEnabled() and dlg.limit.isEnabled()
    dlg.accept()
    assert dlg.opts["others"] and dlg.opts["mode"] == "new" and dlg.opts["folders"] is None
    w.start_scan(opts=dlg.opts)
    w.idle()
    it = w.find_tree_item(("unassoc", None))
    assert it is not None and it.text(0).startswith("Unassociated files")
    w.view = ("unassoc", None)
    w.refresh_grid()
    rows = [r for r in w.model.rows if r[0]]
    assert len(rows) == 1 and rows[0][1].endswith("tickets.pdf")
    heads = [r for r in w.model.rows if not r[0]]
    assert heads and "Holiday 2019" in heads[0][1]                  # split by folder like the other lists
    i = w.model.rows.index(rows[0])
    import gallery
    assert w.model.data(w.model.index(i), gallery.ROLE_LABEL) == "tickets.pdf"
    pm = w.model.data(w.model.index(i), __import__("PySide6.QtCore").QtCore.Qt.DecorationRole)
    assert not pm.isNull() and pm.width() >= 64
    w.grid.setCurrentIndex(w.model.index(i))
    w.ask_name = lambda *a, **k: "Alice"
    w.act_name()                                                    # Enter
    w.idle()
    end = time.time() + 15                                          # (the list redraws when it's free)
    while time.time() < end and w.find_tree_item(("unassoc", None)) is not None:
        pump(qapp, 0.2)
    assert w.find_tree_item(("unassoc", None)) is None
    pid = -rows[0][0]
    assert w.conn.execute("SELECT p.name FROM photo_people pp JOIN persons p ON p.id=pp.person_id "
                          "WHERE pp.photo_id=?", (pid,)).fetchone() == ("Alice",)


def test_ctrl_shift_n_makes_a_folder_where_you_are(window, qapp, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    import store as st
    w = window
    names = iter(["Beach", "Day 2", "Top level"])
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: (next(names), True))
    alice = w.conn.execute("SELECT id FROM persons WHERE name='Alice'").fetchone()[0]
    w.view = ("person", alice)
    QTest.keyClick(w, Qt.Key_N, Qt.ControlModifier | Qt.ShiftModifier)
    w.idle()
    beach = w.conn.execute("SELECT id, owner_id, parent_id FROM categories WHERE name='Beach'").fetchone()
    assert beach and beach[1] == alice                       # inside Alice's folder
    w.view = ("category", beach[0])
    w.new_folder_here()
    assert w.conn.execute("SELECT parent_id FROM categories WHERE name='Day 2'").fetchone()[0] == beach[0]
    w.view = ("unknown", None)
    w.new_folder_here()
    row = w.conn.execute("SELECT parent_id, owner_id FROM categories WHERE name='Top level'").fetchone()
    assert row == (None, None)


def test_exact_copies_show_once(window, qapp, data_dir):
    """A group photo saved in each person's folder: one tile in each gallery, standing for all copies."""
    import shutil
    import store as st
    from conftest import add_photo
    w = window
    rng = np.random.default_rng(9)
    embs = [unit(rng.normal(size=128)) for _ in range(3)]
    hints = tuple((n, 0.05 + 0.3 * i, 0.25, 0.25, 0.4) for i, n in enumerate(("Alice", "Bob", "Carol")))
    first = os.path.join(str(data_dir.lib), "Sorted", "Alice", "group.jpg")
    add_photo(w.conn, first, embs, seed=77, hints=hints)
    for who in ("Bob", "Carol"):
        p = os.path.join(str(data_dir.lib), "Sorted", who, "group.jpg")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        shutil.copy2(first, p)
        from conftest import face_dict, img_info
        from PIL import Image
        stt = os.stat(p)
        st.store_photo(w.conn, p, stt.st_mtime, stt.st_size, img_info(Image.open(p), 1.6e9 + 77),
                       [face_dict(e, box=(0.05 + 0.3 * i, 0.25, 0.25, 0.4)) for i, e in enumerate(embs)],
                       hints=hints, settings={})
    w.conn.commit()
    carol = w.conn.execute("SELECT id FROM persons WHERE name='Carol'").fetchone()[0]
    w.view = ("person", carol)
    w.refresh_grid()
    rows = [r for r in w.model.rows if r[0] and r[1].endswith("group.jpg")]
    assert len(rows) == 1
    i = w.model.rows.index(rows[0])
    tip = w.model.data(w.model.index(i), __import__("PySide6.QtCore").QtCore.Qt.ToolTipRole)
    assert "2 exact copies" in tip
    w.grid.setCurrentIndex(w.model.index(i))
    assert len(w.sel_faces()) == 3 and len(w.sel_photos()) == 3       # actions cover every copy
    w.settings["hide_exact_copies"] = False
    w.refresh_grid()
    assert len([r for r in w.model.rows if r[0] and r[1].endswith("group.jpg")]) == 3
    w.settings["hide_exact_copies"] = True


def test_people_panel_lists_people_without_their_folders(window, qapp):
    import store as st
    w = window
    alice = w.conn.execute("SELECT id FROM persons WHERE name='Alice'").fetchone()[0]
    st.create_category(w.conn, "Beach", None, alice)
    w.rebuild_tree()
    it = next(i for i in w.iter_items() if i.treeWidget() is w.tree and
              __import__("facesort").key_of(i) == ("person", alice))
    kids = [__import__("facesort").key_of(it.child(k)) for k in range(it.childCount())]
    assert not any(k and k[0] == "category" for k in kids)
    w.view = ("person", alice)
    w.refresh_grid()
    assert any(r[0] == 0 and r[1].startswith("Beach") for r in w.model.rows)     # still a section of her gallery


def test_update_button_appears_when_a_newer_version_is_out(window, qapp, monkeypatch):
    import time as _t
    import updater
    w = window
    assert w.a_update.isVisible() and w.update_btn.text() == "Check for updates"     # (FaceSort's own project)
    info = {"version": "9.9.9", "notes": "Better", "page": "https://example.invalid", "installer": "x",
            "size": 1, "sha256": "ab" * 32, "name": "FaceSort-Setup-9.9.9.exe"}
    monkeypatch.setattr(updater, "check_job", lambda conn, settings, progress=None, cancelled=None: info)
    w.settings["update_repo"] = "someone/FaceSort"
    w.start_update_check(force=True)
    end = _t.time() + 10
    while _t.time() < end and w.update_btn.text() != "Update to 9.9.9":
        pump(qapp, 0.1)
    assert w.a_update.isVisible() and w.update_btn.text() == "Update to 9.9.9"
    assert w.settings["update_checked"] > 0
    # nothing newer: back to "Check for updates", and a click asks again
    monkeypatch.setattr(updater, "check_job", lambda conn, settings, progress=None, cancelled=None: None)
    w._update_info = None
    w.update_clicked()
    assert w.update_btn.text() == "Checking…" and not w.update_btn.isEnabled()
    end = _t.time() + 10
    while _t.time() < end and not w.update_btn.isEnabled():
        pump(qapp, 0.1)
    assert w.update_btn.text() == "Check for updates" and "newest version" in w.statusBar().currentMessage()
    w.settings["update_repo"] = "not a project"
    monkeypatch.setattr(updater, "UPDATE_REPO", "")
    w.show_update_state(None)
    assert not w.a_update.isVisible()                    # no project to ask: no button


# ------------------------------------------------------------------ generic names
def menu_texts(menu):
    out = []
    for a in menu.actions():
        if a.menu():
            out.append((a.text(), menu_texts(a.menu())))
        elif a.text():
            out.append(a.text())
    return out


def flat(texts):
    for t in texts:
        if isinstance(t, tuple):
            yield t[0]
            yield from flat(t[1])
        else:
            yield t


def test_generic_names_list_and_numbering(window, qapp):
    from ui_common import clean_generic, generic_names
    w = window
    assert [g["name"] for g in generic_names({})] == ["Amateur", "Celebrity", "Model", "Pornstar", "Streamer"]
    assert generic_names({"generic_names": [{"name": "Model 3", "menu": False}, "model", "  ", {"name": "Dancer"}]}) == \
        [{"name": "Model", "menu": False}, {"name": "Dancer", "menu": True}]
    assert clean_generic("  Street   dancer 12 ") == "Street dancer"
    # numbers: the lowest free one, so renaming someone frees theirs
    for n in ("Model 01", "Model 02", "Model 04"):
        store.get_or_create_person(w.conn, n)
    w.conn.commit()
    assert w.next_placeholder_name("Model") == "Model 03"
    pid = w.conn.execute("SELECT id FROM persons WHERE name='Model 01'").fetchone()[0]
    w.conn.execute("UPDATE persons SET name='Jane' WHERE id=?", (pid,))
    w.conn.commit()
    assert w.next_placeholder_name("Model") == "Model 01"
    assert w.next_placeholder_name("Dancer") == "Dancer 01"


def test_generic_names_in_menus_and_folders(window, qapp, monkeypatch):
    from PySide6.QtWidgets import QMenu
    w = window
    w.settings["generic_names"] = [{"name": "Dancer", "menu": True}, {"name": "Model", "menu": False}]
    m = QMenu()
    picked = []
    w.generic_menu(m, lambda new, pre: picked.append((new, pre)))
    texts = menu_texts(m)
    assert texts[0] == "Tag new “Dancer 01”"
    assert ("More generic names", ["Tag new “Model 01”", "Edit generic names…"]) in texts
    m.actions()[0].trigger()
    assert picked == [("Dancer 01", "Dancer")]
    # a custom generic name goes in its own “Unknown …” folder
    fid = w.conn.execute("SELECT id FROM faces WHERE person_id IS NULL AND ignored=0 LIMIT 1").fetchone()[0]
    w.assign_placeholder([fid], "Dancer 01", "Dancer")
    folder = w.conn.execute("SELECT id FROM categories WHERE name='Unknown Dancer' AND parent_id IS NULL").fetchone()
    assert folder, "the folder “Unknown Dancer” was made"
    assert w.next_placeholder_name("Dancer") == "Dancer 02"
    w.idle()


def test_viewer_menu_for_marked_faces(window, qapp, monkeypatch):
    from PySide6.QtWidgets import QMenu
    w = window
    import viewer
    shown = []

    class Menu(QMenu):
        def exec(self, *a):
            shown.append(self)
    monkeypatch.setattr(viewer, "QMenu", Menu)
    person = next(k for k in all_keys(w, w.tree) if k[0] == "person")
    w.view = person
    w.refresh_grid()
    i = next(i for i, r in enumerate(w.model.rows) if r[0] > 0)
    w.open_viewer(w.model.index(i))
    pump(qapp, 0.6)
    v = w.viewer
    f = v.faces[0]
    fid = f[0]
    # named: also offers a generic name instead
    v.face_menu(f, None)
    assert "Change to a generic name" in list(flat(menu_texts(shown[-1])))
    for state, undo in ((1, "Undo “ignore”"), (3, "Undo “not a face”")):
        (store.set_ignored if state == 1 else store.set_notface)(w.conn, [fid], *(() if state == 3 else (True,)))
        v.reload_faces()
        f = next(x for x in v.faces if x[0] == fid)
        v.face_menu(f, None)
        texts = list(flat(menu_texts(shown[-1])))
        assert any(t.startswith(undo) for t in texts), texts
        assert "Name this face…" in texts and any(t.startswith("Tag new “") for t in texts)
        act = next(a for a in shown[-1].actions() if a.text().startswith(undo))
        act.trigger()
        assert w.conn.execute("SELECT ignored FROM faces WHERE id=?", (fid,)).fetchone()[0] == 0
    # a face marked "not a face" can be given a generic name straight away
    store.set_notface(w.conn, [fid], 3)
    v.reload_faces()
    f = next(x for x in v.faces if x[0] == fid)
    v.face_menu(f, None)
    act = next(a for a in shown[-1].actions() if a.text().startswith("Tag new “"))
    act.trigger()
    ign, ok, name = w.conn.execute("SELECT f.ignored, f.face_ok, p.name FROM faces f JOIN persons p ON p.id=f.person_id "
                                   "WHERE f.id=?", (fid,)).fetchone()
    assert ign == 0 and ok == 1 and name.endswith(" 01")
    v.close()
    w.idle()


def test_background_work_waits_while_you_tag(window, qapp):
    import cpulimit
    w = window
    w._last_input = time.monotonic()
    assert w.regroup_wait() > 2
    w.settings["background_cpu"] = "full"
    assert w.regroup_wait() == 0
    w.settings["background_cpu"] = "gentle"
    w._last_input = 0.0
    assert w.regroup_wait() == 0
    assert cpulimit.threads_for("gentle", 8) == 4 and cpulimit.threads_for("full", 8) == 8
    assert cpulimit.threads_for("gentle", 1) == 1


def test_speed_report_notes_a_stall(tmp_path, qapp, monkeypatch):
    import stallwatch
    monkeypatch.setattr(stallwatch, "OURS", os.path.dirname(os.path.abspath(__file__)))   # (count this file as ours)
    from PySide6.QtWidgets import QWidget
    host = QWidget()
    sw = stallwatch.StallWatch(str(tmp_path / "speed.log"), lambda: "view=test")
    sw.start(host)
    pump(qapp, 0.3)

    def busy_for_a_while():
        end = time.time() + 0.6
        while time.time() < end:
            sum(range(1000))
    busy_for_a_while()
    pump(qapp, 0.4)
    sw.stop()
    text = (tmp_path / "speed.log").read_text()
    assert "stall" in text and "view=test" in text and "busy_for_a_while" in text


def test_side_list_icons_made_together(window, qapp):
    w = window
    w.rep_cache.clear()
    w.rep_cache_checked = -1
    w.rebuild_tree()
    assert w.rep_cache, "face icons made"
    n = len(w.rep_cache)
    w.pix_cache.clear()                       # (e.g. after a scan): icons are checked, not remade
    before = {k: v[1] for k, v in w.rep_cache.items()}
    w.rebuild_tree()
    assert len(w.rep_cache) == n and all(w.rep_cache[k][1] is before[k] for k in before)


def test_actions_update_the_gallery_now_and_lists_later(window, qapp, monkeypatch):
    """Ignoring faces takes their tiles out at once; the side lists wait until you pause."""
    w = window
    person = next(k for k in all_keys(w, w.tree) if k[0] == "person")
    w.view = person
    w.refresh_grid()
    ids = [r[0] for r in w.model.rows if r[0] > 0][:1]
    w.grid.setCurrentIndex(w.model.index(next(i for i, r in enumerate(w.model.rows) if r[0] == ids[0])))
    trees = []
    monkeypatch.setattr(w, "rebuild_tree", lambda *a, **k: trees.append(1))
    w.act_ignore()
    assert not trees, "the side lists weren't redrawn under your click"
    assert ids[0] not in [r[0] for r in w.model.rows]
    assert w.refresh_timer.isActive()
    monkeypatch.undo()
    w.idle()


def test_folder_counts_worked_out_in_the_background(window, qapp, monkeypatch):
    w = window
    w.destinations()                                   # a current copy
    calls = []
    real = store.photo_destinations
    monkeypatch.setattr(store, "photo_destinations", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    w.conn.execute("UPDATE photos SET category_id=category_id")
    w.conn.commit()
    key = (w.conn.total_changes, w._data_gen, id(w.conn))
    w.places(stale_ok=True)                            # out of date: the last copy now...
    assert not calls                                   # (not worked out under your click)
    end = time.time() + 15                             # ... and a fresh one in the background (thread or helper)
    while w._dest_cache[0] != key and time.time() < end:
        pump(qapp, 0.05)
    assert w._dest_cache[0] == key
    n = len(calls)
    w.places(stale_ok=True)
    w.destinations()
    assert len(calls) == n                             # now up to date: nothing worked out again
    w.idle()


def test_known_names_kept_until_the_file_changes(data_dir):
    import known
    known._NAMES.clear()
    known.names()                                      # (makes the file)
    first = known.names()
    reads = []
    real = known._db
    known._db = lambda: (reads.append(1), real())[1]
    try:
        assert known.names() == first and not reads
        with real() as k:
            k.execute("INSERT INTO people(name) VALUES('Somebody New')")
        time.sleep(0.02)
        assert "Somebody New" not in known.names() and not reads   # (1.45.3: a list under 30 s old is used)
        known._NAMES["at"] = 0                                       # half a minute later:
        assert "Somebody New" not in known.names()                   # (1.51.1) never read on the spot -
        end = time.time() + 5                                        # read again in the background
        while known._NAMES.get("loading") and time.time() < end:
            time.sleep(0.02)
        assert "Somebody New" in known.names() and reads
    finally:
        known._db = real


def test_thumbnail_cache_counted(tmp_path):
    import gallery
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.jpg").write_bytes(b"1" * 100)
    (tmp_path / "y.jpg").write_bytes(b"2" * 50)
    assert gallery.thumb_cache_size(str(tmp_path)) == (2, 150)
    assert gallery.thumb_cache_size(str(tmp_path / "missing")) == (0, 0)


def test_drop_scans_only_the_dropped_paths(window, qapp, tmp_path, monkeypatch):
    from conftest import write_photo
    w = window
    d = tmp_path / "Holiday"
    write_photo(str(d / "x.jpg"), 801)
    got = []
    monkeypatch.setattr(w, "start_scan", lambda **k: got.append(k))
    monkeypatch.setattr(w, "busy", lambda: False)
    assert w.handle_drop([str(d)], answers=((), None, False))
    assert got and got[0]["opts"]["roots"] == [str(d)]
    assert got[0]["opts"]["limit"] == 0                # (1.56.1) a drop isn't held back by the Import limit
    e = tmp_path / "Second"
    write_photo(str(e / "y.jpg"), 802)
    monkeypatch.setattr(w, "busy", lambda: True)           # two drops while busy: both get scanned
    w._scan_after = False
    w.handle_drop([str(d)], answers=((), None, False))
    w.handle_drop([str(e)], answers=((), None, False))
    assert w._scan_after["roots"] == [str(d), str(e)]
    monkeypatch.setattr(w, "busy", lambda: False)
    w._scan_after = {"roots": [str(d)]}             # dropped while busy: the same drop-only scan afterwards
    w._after_job("other")
    pump(qapp, 0.4)
    assert got[-1]["opts"] == {"roots": [str(d)]}


def test_viewer_decodes_in_background_and_reads_ahead(window, qapp):
    """1.44.2: the picture is decoded off the window; the photos either side are read ahead."""
    w = window
    person = next(k for k in all_keys(w, w.tree) if k[0] == "person")
    w.view = person
    w.refresh_grid()
    i = next(i for i, r in enumerate(w.model.rows) if r[0])
    w.open_viewer(w.model.index(i))
    v = w.viewer
    v.ensure_picture()
    assert v.image is not None and not v.image.isNull()
    if len(v.items) > 1 and v.pos + 1 < len(v.items):
        nxt = v.items[v.pos + 1][1]
        end = time.time() + 8                         # the read-ahead lands in the cache (slow CI machines)
        while not any(k[0] == nxt for k in v._img_cache) and time.time() < end:
            pump(qapp, 0.1)
        assert any(k[0] == nxt for k in v._img_cache)
        v.go(1)
        assert v.image is not None and not getattr(v, "_loading", False)     # instant: already decoded


def test_no_folder_bar_for_duplicate_sets_or_thousands_of_sections(window, monkeypatch):
    """1.45.4: 5,400 duplicate sets made 5,400 folder bar buttons (a minute and a half frozen)."""
    import mw_actions
    w = window
    w.view = ("dupes", None)
    w.model.set_rows([(0, f"Set {i}", ("dup", i), 1) for i in range(5)])
    w.build_folder_bar()
    assert not w._bar_heads and not w.folder_bar_area.isVisible()
    monkeypatch.setattr(mw_actions, "MAX_BAR_BUTTONS", 3)
    w.view = ("category", None)
    w.model.set_rows([(0, f"Folder {i}", None, 1) for i in range(5)])
    w.build_folder_bar()
    assert not w._bar_heads


def test_selected_group_stays_or_moves_to_the_next(window):
    """1.45.5: after an update the gallery stays on the group you picked; if that group is gone it
    goes to the very next one (not back to the top)."""
    w = window
    smart = [k for k in all_keys(w, w.tree2) if k[0] in ("unknown", "partial", "dupes", "nofaces", "group")]
    assert smart
    w.view = ("dupes", None)
    w.rebuild_tree()
    assert w.view == ("dupes", None)                     # (still there: stays)
    # a group that has gone: the one listed after it is picked
    it = w.find_tree_item(("dupes", None))
    par = it.parent()
    sib = [par.child(j) for j in range(par.childCount())]
    i = sib.index(it)
    from ui_common import key_of
    want = [key_of(x) for x in sib[i + 1:]] + [key_of(x) for x in reversed(sib[:i])]
    w.select_item(it, switch=False)
    import treespec
    real_build = treespec.build

    def without_dupes(c, ctx):             # (1.62: the row really goes - the lists are patched, not remade)
        tops = real_build(c, ctx)
        for rows in tops.values():
            for top in rows:
                top.kids = [k for k in top.kids if k.dat.get(treespec.ROLE_USER) != ("dupes", None)]
        return tops
    treespec.build = without_dupes
    try:
        w.rebuild_tree()
    finally:
        treespec.build = real_build
    assert w.find_tree_item(("dupes", None)) is None
    assert w.view == next(k for k in want if k)


def test_folder_scope_list_clicks(window, qapp, tmp_path):
    """1.45.5: a click anywhere on a folder ticks / unticks it; "All" turns them all off or on;
    library folders with no photos aren't listed and can be taken off the list."""
    from PySide6.QtCore import Qt
    w = window
    empty = tmp_path / "Empty folder"
    empty.mkdir()
    w.settings["folders"] = list(w.settings["folders"]) + [str(empty)]
    w.fill_scope_list()
    lst = w.scope_list
    labels = [lst.item(i).text() for i in range(lst.count())]
    assert not any("Empty folder" in t for t in labels[1:-1]) and "empty library folder" in labels[-1]
    rows = w._scope_rows()
    if len(rows) < 2:                                    # (the test library has one folder: add another with photos)
        return
    w.scope_clicked(rows[0])                             # a click on the row (not just the box)
    assert rows[0].checkState() == Qt.Unchecked and w.settings["scope_folders"] == [rows[1].data(Qt.UserRole)]
    w.scope_clicked(lst.item(0))                         # All: (not all on) -> all on
    assert all(r.checkState() == Qt.Checked for r in rows) and w.settings["scope_folders"] == []
    w.scope_clicked(lst.item(0))                         # All again: all off (nothing changes until you tick)
    assert all(r.checkState() == Qt.Unchecked for r in rows)


def test_remove_empty_library_folders(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    w = window
    empty = tmp_path / "Done with"
    (empty / "sub").mkdir(parents=True)
    w.settings["folders"] = list(w.settings["folders"]) + [str(empty)]
    w.settings["library_root"] = str(tmp_path / "Sorted")
    w.fill_scope_list()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    w.remove_empty_library_folders()
    assert str(empty) not in w.settings["folders"] and not empty.exists()
    assert (tmp_path / "Sorted" / "~To Be Discarded" / "Done with").is_dir()


def test_suggested_folders_in_the_gallery(window, qapp, tmp_path):
    from gallery import HintButton
    from ui_common import ROLE_HINT
    w, c = window, window.conn
    alice = c.execute("SELECT id FROM persons WHERE name='Alice'").fetchone()[0]
    w.view = ("person", alice)
    w.refresh_grid()
    heads = [(i, r) for i, r in enumerate(w.model.rows) if r[0] == 0 and isinstance(r[2], tuple) and r[2][0] == "sugg"]
    assert heads, [r[1] for r in w.model.rows if r[0] == 0]
    i, r = heads[0]
    name = r[2][3]
    assert r[1].endswith("?") and w.model.data(w.model.index(i), ROLE_HINT)
    assert w.model.data(w.model.index(i + 1), ROLE_HINT)                # its photos are marked too
    assert "suggested" in w.subtitle.text()
    w.build_folder_bar()
    btns = [b for _i, b in w._bar_heads if isinstance(b, HintButton)]
    assert btns
    n = len(w.section_photos(i))
    btns[0].yes.click()                                                 # ✓
    import store
    cid = store.find_child(c, name, None, alice)
    assert cid and n
    w.refresh_grid()
    assert not any(r[0] == 0 and isinstance(r[2], tuple) and r[2][0] == "sugg" and r[2][3] == name
                   and r[2][1] == heads[0][1][2][1] for r in w.model.rows)
    assert any(r[0] == 0 and isinstance(r[2], tuple) and r[2][0] == "pf" and r[2][1] == cid for r in w.model.rows)
