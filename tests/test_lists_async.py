"""(1.50) The side lists' numbers are read ahead on a background thread, then the redraw uses them."""
import time

import listcache
import store
from conftest import pump
from test_ui import all_keys, window  # noqa: F401  (the window fixture)


def texts(w):
    return [(it.treeWidget().objectName(), it.text(0)) for it in w.iter_items()]


def wait_lists(w, qapp, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        pump(qapp, 0.05)
        r = getattr(w, "_ra_worker", None)
        if r is None or not r.isRunning():
            pump(qapp, 0.1)
            if getattr(w, "_ra_worker", None) is None:
                return


def test_read_ahead_matches_a_live_redraw(window, qapp):
    w = window
    w.rebuild_tree()
    live = texts(w)
    answers = listcache.read_ahead(store.connect(), w._tree_questions)
    assert answers
    w.apply_lists(answers)
    assert w._lists_hits[0] > 10 and texts(w) == live
    # a stale answer is never used once the redraw is over (a button made during it keeps working)
    assert w.conn.__class__.__name__ != "Recording"


def test_changes_redrawn_after_reading_ahead(window, qapp, monkeypatch):
    w, c = window, window.conn
    monkeypatch.setenv("FACESORT_SYNC_LISTS", "0")
    w.rebuild_tree()
    assert w.lists_async()
    g = next(k for k in all_keys(w, w.tree2) if k[0] == "group")
    faces = [r[0] for r in c.execute("SELECT id FROM faces WHERE cluster=? AND person_id IS NULL", (g[1],))]
    pid = store.get_or_create_person(c, "Read Ahead")
    store.assign(c, faces, pid)
    c.commit()
    w.after_change(regroup=False)
    assert ("person", pid) not in all_keys(w, w.tree)          # not yet: being read
    wait_lists(w, qapp)
    assert ("person", pid) in all_keys(w, w.tree)
    assert w._lists_hits[0] > 10
    # changed again while reading: read again, and the newest change shows
    w.after_change(regroup=False)
    store.rename_person(c, pid, "Read Ahead Two")
    c.commit()
    w.after_change(regroup=False)
    wait_lists(w, qapp)
    assert any("Read Ahead Two" in it.text(0) for it in w.iter_items())


def test_scope_questions_read_ahead(window, qapp, monkeypatch):
    w = window
    folders = list(w.settings["folders"])
    monkeypatch.setattr(type(w), "scope_on", lambda self: True)
    monkeypatch.setattr(type(w), "in_scope", lambda self, path: True)
    w.apply_lists(None)
    live = texts(w)
    assert any("scope_ids" in q[0] for q in w._tree_questions)
    w.conn.commit()                       # (as update_lists does before reading ahead)
    answers = listcache.read_ahead(store.connect(), w._tree_questions, w.in_scope)
    assert any("scope_ids" in k[0] for k in answers)
    w.apply_lists(answers)
    assert texts(w) == live and w._lists_hits[1] < 5, w._lists_missed
    w.settings["folders"] = folders


def test_swapped_in_at_a_pause_and_skipped_when_unchanged(window, qapp, monkeypatch):
    """(1.50.3) While you're clicking, new lists wait for a short pause; the same numbers again
    redraw nothing."""
    w, c = window, window.conn
    monkeypatch.setenv("FACESORT_SYNC_LISTS", "0")
    w.apply_lists(None)
    pid = store.get_or_create_person(c, "Pause Person")
    g = next(k for k in all_keys(w, w.tree2) if k[0] == "group")
    store.assign(c, [r[0] for r in c.execute("SELECT id FROM faces WHERE cluster=? AND person_id IS NULL",
                                             (g[1],))], pid)
    c.commit()
    end = time.time() + 1.2
    w.after_change(regroup=False)
    while time.time() < end:                      # "typing": input keeps coming
        w._last_input = time.monotonic()
        pump(qapp, 0.05)
    assert ("person", pid) not in all_keys(w, w.tree)
    wait_lists(w, qapp)
    pump(qapp, 0.6)
    assert ("person", pid) in all_keys(w, w.tree)
    # nothing changed: the lists aren't rebuilt (their rows stay the very same objects)
    it = w.find_tree_item(("person", pid))
    w.after_change(regroup=False)
    wait_lists(w, qapp)
    pump(qapp, 0.6)
    assert w.find_tree_item(("person", pid)) is it
