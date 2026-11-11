"""The single-writer gate: one writer at a time, the window first, no "database is locked"."""
import sqlite3
import threading
import time

import pytest

import dbgate
import store
from dbgate import GATE, is_write


@pytest.mark.parametrize("sql, expected", [
    ("SELECT * FROM photos", False),
    ("  select 1", False),
    ("INSERT INTO t VALUES(1)", True),
    ("update photos set x=1", True),
    ("DELETE FROM faces", True),
    ("REPLACE INTO meta VALUES(1,2)", True),
    ("CREATE INDEX IF NOT EXISTS i ON t(a)", True),
    ("ANALYZE", True),
    ("BEGIN IMMEDIATE", True),
    ("-- a comment\nUPDATE t SET a=1", True),
    ("/* c */ SELECT 1", False),
    ("WITH x AS (SELECT 1) SELECT * FROM x", False),
    ("WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x", True),
    ("PRAGMA table_info(faces)", False),
    ("PRAGMA journal_mode=WAL", True),
    ("PRAGMA busy_timeout=5000", False),
])
def test_is_write(sql, expected):
    assert is_write(sql) is expected


def _table(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS t(a INTEGER, who TEXT)")
    conn.commit()


def in_thread(fn, *a):
    out = {}

    def run():
        try:
            out["value"] = fn(*a)
        except BaseException as e:     # noqa: BLE001 - reported to the test
            out["error"] = e
    th = threading.Thread(target=run, name="test-bg")
    th.start()
    return th, out


def test_background_connections_autocommit(data_dir):
    ui = store.connect()
    _table(ui)

    def bg():
        c = store.connect()
        assert c.isolation_level is None and not c.ui
        c.execute("INSERT INTO t VALUES(1,'bg')")
        held = GATE.owner is c
        c.close()
        return held
    th, out = in_thread(bg)
    th.join(10)
    assert "error" not in out, out.get("error")
    assert out["value"] is False                    # not held after the statement
    assert ui.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
    ui.close()


def test_ui_connection_keeps_transactions(data_dir):
    ui = store.connect()
    assert ui.ui
    _table(ui)
    ui.execute("INSERT INTO t VALUES(1,'ui')")
    assert ui.in_transaction and GATE.owner is ui     # held until saved
    ui.save_if_idle()
    assert not ui.in_transaction and GATE.owner is None
    ui.close()


def test_background_executemany_is_chunked(data_dir, monkeypatch):
    monkeypatch.setattr(dbgate, "CHUNK", 100)
    ui = store.connect()
    _table(ui)
    takes = []
    real = GATE.acquire

    def counting(conn, is_ui):
        takes.append(is_ui)
        return real(conn, is_ui)
    monkeypatch.setattr(GATE, "acquire", counting)

    def bg():
        c = store.connect()
        c.executemany("INSERT INTO t VALUES(?, 'bg')", [(i,) for i in range(1050)])
        c.close()
    th, out = in_thread(bg)
    th.join(20)
    assert "error" not in out, out.get("error")
    assert ui.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1050
    assert takes.count(False) >= 11                 # 11 chunks, each a quick transaction
    ui.close()


def test_write_block_is_atomic(data_dir):
    ui = store.connect()
    _table(ui)

    def bg():
        c = store.connect()
        with c.write():
            c.execute("INSERT INTO t VALUES(1,'a')")
            c.execute("INSERT INTO t VALUES(2,'b')")
        try:
            with c.write():
                c.execute("INSERT INTO t VALUES(3,'c')")
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert GATE.owner is None
        c.close()
    th, out = in_thread(bg)
    th.join(10)
    assert "error" not in out, out.get("error")
    assert sorted(r[0] for r in ui.execute("SELECT a FROM t")) == [1, 2]
    ui.close()


def test_background_waits_for_open_ui_transaction(data_dir):
    ui = store.connect()
    _table(ui)
    ui.execute("INSERT INTO t VALUES(1,'ui')")              # the window holds the gate (unsaved)
    done = threading.Event()

    def bg():
        c = store.connect()
        c.execute("INSERT INTO t VALUES(2,'bg')")
        done.set()
        c.close()
    th, out = in_thread(bg)
    time.sleep(0.4)
    assert not done.is_set()                                # waits - no "database is locked"
    ui.commit()
    th.join(10)
    assert done.is_set() and "error" not in out
    assert ui.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 2
    ui.close()


def test_window_goes_first(data_dir):
    """A busy background writer (many short holds) never keeps the window waiting long."""
    ui = store.connect()
    _table(ui)
    stop = threading.Event()

    def bg():
        c = store.connect()
        n = 0
        while not stop.is_set():
            with c.write():
                c.execute("INSERT INTO t VALUES(?, 'bg')", (n,))
                time.sleep(0.03)                            # a slowish write
            n += 1
        c.close()
        return n
    th, out = in_thread(bg)
    time.sleep(0.2)
    waits = []
    for i in range(20):
        t0 = time.monotonic()
        ui.execute("INSERT INTO t VALUES(?, 'ui')", (i,))
        ui.commit()
        waits.append(time.monotonic() - t0)
        time.sleep(0.01)
    stop.set()
    th.join(10)
    assert "error" not in out, out.get("error")
    assert out["value"] > 5
    assert max(waits) < 0.25, waits                         # at most one background hold
    ui.close()


def test_stress_no_locked_errors(data_dir):
    ui = store.connect()
    _table(ui)
    stop = time.monotonic() + 2.0

    def worker(k):
        c = store.connect()
        n = 0
        while time.monotonic() < stop:
            if n % 3 == 0:
                c.executemany("INSERT INTO t VALUES(?, ?)", [(n, f"w{k}")] * 50)
            elif n % 3 == 1:
                with c.write():
                    c.execute("INSERT INTO t VALUES(?, ?)", (n, f"w{k}"))
                    c.execute("UPDATE t SET a=a WHERE who=?", (f"w{k}",))
            else:
                c.execute("INSERT INTO t VALUES(?, ?)", (n, f"w{k}"))
            c.execute("SELECT COUNT(*) FROM t").fetchone()
            n += 1
        c.close()
        return n
    threads = [in_thread(worker, k) for k in range(4)]
    ui_rows = 0
    while time.monotonic() < stop:
        ui.execute("INSERT INTO t VALUES(0, 'ui')")
        ui_rows += 1
        if ui_rows % 5 == 0:
            ui.commit()
    ui.commit()
    for th, _ in threads:
        th.join(30)
    for _, out in threads:
        assert "error" not in out, out.get("error")
    assert ui.execute("SELECT COUNT(*) FROM t WHERE who='ui'").fetchone()[0] == ui_rows
    assert not GATE.slow
    ui.close()


def test_watchdog_logs_slow_writes(data_dir, monkeypatch):
    monkeypatch.setattr(dbgate, "WARN_SECONDS", 0.05)
    ui = store.connect()
    _table(ui)
    with ui.write():
        ui.execute("INSERT INTO t VALUES(1,'ui')")
        time.sleep(0.12)
    assert GATE.slow and GATE.slow[-1][0] >= 0.1
    log = (data_dir.data / "facesort.log").read_text(encoding="utf-8")
    assert "slow database write" in log and "test_dbgate.py" in log
    ui.close()


def test_ui_times_out_with_a_clear_error(data_dir, monkeypatch):
    monkeypatch.setattr(dbgate, "UI_TIMEOUT", 0.3)
    ui = store.connect()
    _table(ui)
    release = threading.Event()

    def bg():
        c = store.connect()
        with c.write():
            c.execute("INSERT INTO t VALUES(1,'bg')")
            release.wait(5)
        c.close()
    th, _ = in_thread(bg)
    time.sleep(0.1)
    with pytest.raises(sqlite3.OperationalError, match="busy"):
        ui.execute("INSERT INTO t VALUES(2,'ui')")
    release.set()
    th.join(5)
    ui.close()


def test_window_writing_in_a_loop_cant_starve_a_job(data_dir):
    """The window saving over and over (no pause between) still lets a waiting job in."""
    ui = store.connect()
    _table(ui)
    done = threading.Event()

    def bg():
        c = store.connect()                  # (connecting writes too: the schema check)
        for i in range(30):
            c.execute("INSERT INTO t VALUES(?, 'bg')", (i,))
        c.close()
        done.set()
    th, out = in_thread(bg)
    end = time.monotonic() + 15
    t0 = time.monotonic()
    while not done.is_set() and time.monotonic() < end:
        ui.execute("INSERT INTO t VALUES(0, 'ui')")
        ui.commit()
    took = time.monotonic() - t0
    th.join(5)
    assert done.is_set() and "error" not in out, out.get("error")
    assert took < 10, f"the job waited {took:.1f} s"
    ui.close()
