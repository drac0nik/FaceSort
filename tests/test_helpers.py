"""Helper processes (1.45): jobs run in separate processes, the write lock is shared with them
(one writer at a time across every process, the window first), jobs split across several CPU
helpers, cancelling, a helper that dies while writing, and the window using them for real."""
import os
import time

import pytest

import helpers
import jobfuncs
import store
from conftest import pump


@pytest.fixture
def helpers_on(monkeypatch):
    monkeypatch.delenv("FACESORT_NO_HELPERS", raising=False)
    yield
    # (the pool stays for the next test - starting helpers takes a moment)


def run(w, qapp, timeout=60):
    got = {}
    w.ok.connect(lambda r: got.setdefault("ok", r))
    w.failed.connect(lambda tb: got.setdefault("failed", tb))
    w.start()
    end = time.time() + timeout
    while not got and time.time() < end:
        pump(qapp, 0.02)
    return got


def test_job_runs_in_a_helper_process(library, qapp, helpers_on):
    from ui_common import ProcessWorker, bg_job
    w = bg_job({}, jobfuncs.ping_job)
    assert isinstance(w, ProcessWorker)
    got = run(w, qapp)
    assert "failed" not in got, got.get("failed")
    pid, role, n = got["ok"]
    assert pid != os.getpid() and role == "cpu"
    assert n == library.conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]     # the same library
    g = run(bg_job({}, jobfuncs.ping_job, role="gpu"), qapp)
    assert g["ok"][1] == "gpu" and g["ok"][0] not in (pid, os.getpid())


def test_helpers_off_uses_a_thread(library, qapp, helpers_on):
    from ui_common import Worker, bg_job
    assert isinstance(bg_job({"helpers": "off"}, jobfuncs.ping_job), Worker)
    assert isinstance(bg_job({}, lambda conn, progress, cancelled: 1), Worker)     # (not a jobfuncs job)


def test_one_writer_across_processes(library, qapp, helpers_on):
    """A helper writing in a loop (holding the lock a moment each time) and the window writing at
    the same time: never "database is locked", and the window never waits long."""
    import dbgate
    from ui_common import bg_job
    w = bg_job({}, jobfuncs.ping_job, write=40, hold=0.01)
    got = {}
    w.ok.connect(lambda r: got.setdefault("ok", r))
    w.failed.connect(lambda tb: got.setdefault("failed", tb))
    w.start()
    c = library.conn
    worst = 0.0
    end = time.time() + 60
    i = 0
    while not got and time.time() < end:
        t = time.monotonic()
        c.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('window', ?)", (str(i),))
        c.commit()
        worst = max(worst, time.monotonic() - t)
        i += 1
        pump(qapp, 0.005)
    assert "failed" not in got, got.get("failed")
    assert c.execute("SELECT v FROM meta WHERE k='ping'").fetchone()[0] == "39"
    assert worst < 1.0, f"the window waited {worst:.2f} s to write"
    assert dbgate.XOWNER.value == 0


def test_job_split_across_cpu_helpers(library, qapp, helpers_on):
    from ui_common import bg_job
    c = library.conn
    c.execute("UPDATE faces SET clarity=NULL")
    c.commit()
    n = c.execute("SELECT COUNT(*) FROM faces WHERE manual<2").fetchone()[0]
    w = bg_job({"cpu_helpers": 2}, jobfuncs.clarity_job, shards=2, shard_fn=jobfuncs.clarity_shard_job)
    got = run(w, qapp)
    assert "failed" not in got, got.get("failed")
    assert got["ok"] == n                                     # the parts added up
    assert not c.execute("SELECT 1 FROM faces WHERE clarity IS NULL AND manual<2").fetchone()


def test_cancel(library, qapp, helpers_on):
    from ui_common import bg_job
    w = bg_job({}, jobfuncs.sleep_job, 30)
    got = {}
    w.ok.connect(lambda r: got.setdefault("ok", r))
    w.start()
    pump(qapp, 0.5)
    t = time.time()
    w.stop()
    assert w.wait(10000)
    assert time.time() - t < 5
    pump(qapp, 0.1)
    assert got.get("ok") is False                             # it stopped early


def test_helper_that_dies_while_writing_frees_the_lock(library, qapp, helpers_on):
    from ui_common import bg_job
    p = helpers.pool()
    w = bg_job({}, jobfuncs.ping_job, write=1, hold=30)       # holds the write lock for 30 s
    got = {}
    w.failed.connect(lambda tb: got.setdefault("failed", tb))
    w.start()
    end = time.time() + 30
    import dbgate
    while not dbgate.XOWNER.value and time.time() < end:
        pump(qapp, 0.02)
    holder = [h for h in p.helpers if h.pid == dbgate.XOWNER.value]
    assert holder
    holder[0].proc.kill()
    end = time.time() + 15
    while "failed" not in got and time.time() < end:
        pump(qapp, 0.05)
    assert "stopped unexpectedly" in got.get("failed", "")
    c = library.conn
    t = time.monotonic()
    c.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('after', '1')")
    c.commit()
    assert time.monotonic() - t < 2


def test_window_with_helpers(data_dir, qapp, helpers_on):
    """The whole window with helpers on: the background jobs go to them and the lists still work."""
    from conftest import build_library
    lib = build_library(data_dir)
    lib.conn.close()
    import facesort
    w = facesort.MainWindow()
    w.show()
    try:
        pump(qapp, 0.5)
        w.start_stats()
        w.conn.execute("UPDATE faces SET clarity=NULL")
        w.conn.commit()
        w.start_clarity()
        from ui_common import ProcessWorker
        assert isinstance(w.clarity_worker, ProcessWorker)
        end = time.time() + 60
        while (w.clarity_worker.isRunning() or w.stats_worker.isRunning()) and time.time() < end:
            pump(qapp, 0.05)
        assert not w.conn.execute("SELECT 1 FROM faces WHERE clarity IS NULL AND manual<2").fetchone()
        w.after_change()
        pump(qapp, 0.3)
    finally:
        w.close()
        pump(qapp, 0.2)
