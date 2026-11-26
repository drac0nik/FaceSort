"""1.61: speed - auto-move only looks after a change, quick review reads questions a batch at a time, the
graphics card is tried again after crash-free days, the side lists open from last time."""
import json
import os
import time

import helpers
import store
from conftest import pump
from test_ui import window  # noqa: F401


def test_gpu_crash_memory_retries_and_reset(data_dir):
    p = helpers.Pool.__new__(helpers.Pool)        # (no helper processes needed for this)
    p.log = lambda m: logs.append(m)
    logs = []
    old = time.time() - 4 * 86400                 # crashed twice, 4 days ago
    with open(os.path.join(str(data_dir.data), "gpu_crashes.json"), "w") as f:
        json.dump({"jobs": {"jobfuncs:content_job": [2, old], "jobfuncs:clip_index_job": [2, time.time()]}}, f)
    p._crashes = None
    c = p.gpu_crashes()
    assert c["jobfuncs:content_job"] == helpers.GPU_CRASHES_MAX - 1        # the card again (without FP16)
    assert c["jobfuncs:clip_index_job"] == helpers.GPU_CRASHES_MAX           # (crashed today: still the CPU)
    assert any("trying the card again" in m for m in logs)
    assert [k for k, _u in p.cpu_only_jobs()] == ["jobfuncs:clip_index_job"]
    p.reset_gpu_crashes()
    assert p.gpu_crashes() == {} and not os.path.exists(os.path.join(str(data_dir.data), "gpu_crashes.json"))


def test_device_is_logged():
    p = helpers.Pool.__new__(helpers.Pool)
    logs = []
    p.log = logs.append
    t = {"msg": ("jobfuncs:clip_index_job", (), {}, 2, "gpu", ""), "start": time.monotonic() - 60,
         "device": ["DmlExecutionProvider"], "opts": {}}
    p._log_device(t)
    t2 = dict(t, device=["CPUExecutionProvider"], opts={"moved_to_cpu": True})
    t2["msg"] = ("jobfuncs:content_job", (), {}, 2, "cpu", "")
    p._log_device(t2)
    assert "clip_index_job: ran on the graphics card (DirectML)" in logs[0]
    assert "content_job: ran on the CPU" in logs[1] and "crashed the graphics card" in logs[1]


def test_index_counts_remembered(data_dir):
    import clip_search
    conn = store.connect()
    a = clip_search.index_counts(conn)
    calls = []
    real = conn.execute

    class Spy:
        def __getattr__(self, n):
            return getattr(conn, n)

        def execute(self, sql, *a):
            calls.append(sql)
            return real(sql, *a)
    spy = Spy()
    clip_search._COUNTS[id(spy)] = clip_search._COUNTS[id(conn)]
    assert clip_search.index_counts(spy) == a
    assert not any("COUNT(*)" in s for s in calls)                 # answered from memory
    conn.close()


def test_automove_looks_only_after_a_change(window, qapp, tmp_path):
    import libmove
    w = window
    lib = tmp_path / "My Library"
    os.makedirs(libmove.unsorted_dir(str(lib)))
    w.settings["library_root"] = store.norm_path(str(lib))
    looks = []
    real = libmove.sync_follow
    libmove.sync_follow = lambda conn, lib=None: looks.append(1) or real(conn, lib)
    try:
        w._am_dirty, w._am_last = True, 0.0
        w.automove_tick()
        for _ in range(50):
            pump(qapp, 0.05)
            th = getattr(w, "_am_thread", None)
            if th is None or not th.is_alive():
                break
        assert len(looks) == 1
        w.automove_tick()                          # nothing changed: no look
        assert len(looks) == 1
        w.after_change()
        assert w._am_dirty
    finally:
        libmove.sync_follow = real
    w.idle()


def test_quick_review_reads_questions_a_batch_at_a_time(window, qapp, monkeypatch):
    from types import SimpleNamespace
    import review
    w = window
    fids = [r[0] for r in w.conn.execute("SELECT id FROM faces WHERE ignored=0 ORDER BY id LIMIT 12")]

    def unit(k, f):
        return SimpleNamespace(kind="group", key=("g", k), name="", size=2, show=[f], faces=[f], photos=set(),
                               cluster=k)

    def fake(*a, **k):
        builds.append(1)
        qs = [review.Question(unit(i, fids[i]), unit(100 + i, fids[i + 1]), 0.9, 0.0, None) for i in range(10)]
        return qs, len(qs)
    builds = []
    monkeypatch.setattr(review, "build_queue", fake)
    w.quick_review()
    r = w.review
    for _ in range(100):
        pump(qapp, 0.05)
        if r.q is not None:
            break
    assert r.q is not None and len(builds) == 1          # read in the background on opening
    first = r.q
    r.skip()
    second = r.q
    assert second is not first and len(builds) == 1     # the next one from the batch, no new read
    r.undo()
    assert r.q is first                                  # the skipped one comes back first
    w.close_review()
    pump(qapp, 0.3)
    w.idle()


def test_lists_open_from_last_time(window, qapp):
    w = window
    w.refresh_all()
    for _ in range(100):
        pump(qapp, 0.05)
        if getattr(w, "_ra_worker", None) is None and getattr(w, "_ra_ready", None) is None:
            break
    w.refresh_all()                                # (a read-ahead redraw: its answers are kept)
    for _ in range(100):
        pump(qapp, 0.05)
        if getattr(w, "_ra_worker", None) is None and getattr(w, "_ra_ready", None) is None:
            break
    w.save_list_questions(answers=True)
    assert os.path.exists(w._questions_file())
    if getattr(w, "_last_answers", None):
        w._last_answers = None
        ans = w.load_list_answers()
        assert ans
    w._tree_questions = None
    assert w.load_list_questions() and w._tree_questions
    w.idle()


def test_side_lists_are_patched_not_remade(window, qapp):
    """1.62: only the rows that changed are touched; rows, selection and open / closed stay."""
    import treespec
    from ui_common import key_of
    w = window
    w.rebuild_tree()
    before = {id(it): key_of(it) for it in w.iter_items()}
    items_before = list(w.iter_items())            # (kept alive so ids can't be reused)
    w.rebuild_tree()                               # nothing changed: nothing touched
    assert w._tree_patch == (0, 0)
    assert {id(it) for it in w.iter_items()} == set(before)
    # a group named: a few rows change, the rest are the same objects
    grp = next(k for k in before.values() if k and k[0] == "group")
    fids = [r[0] for r in w.conn.execute("SELECT id FROM faces WHERE cluster=? AND person_id IS NULL",
                                         (grp[1],))]
    store.assign(w.conn, fids, store.get_or_create_person(w.conn, "Patched Person"))
    w.conn.commit()
    w.rebuild_tree()
    touched, removed = w._tree_patch
    assert 0 < touched < len(before) // 3 and removed >= 1
    kept = sum(1 for it in w.iter_items() if id(it) in before)
    assert kept > len(before) * 0.6
    assert w.find_tree_item(grp) is None and any(k == ("person", store.get_or_create_person(w.conn, "Patched Person"))
                                                  for k in (key_of(i) for i in w.iter_items()))
    del items_before
    # a worked-out spec digest: the same twice
    ctx = w.tree_ctx()
    a, b = treespec.build(w.conn, ctx), treespec.build(w.conn, ctx)
    assert treespec.digest(a) == treespec.digest(b)
    w.idle()
