"""One writer at a time: every connection to the library writes through a single in-process
write gate, so "database is locked" can't happen inside FaceSort.

How it works
- Reading never waits (the library runs in WAL mode: readers and the writer don't block
  each other).
- Writing needs the gate. Only one connection holds it at a time.
- The window (the main thread) goes first: while it's waiting, background jobs don't take
  the gate - they wait for it to finish. But a background job that has waited FAIR seconds
  gets the next turn, so a window writing in a quick loop can't starve it (Windows hands the
  lock straight back to the thread that just let go of it).
- Background connections (scans, checks, regrouping, indexing…) run in autocommit mode: each
  write is its own short transaction, and the gate is held only while that one statement
  runs - never while the job loads the next file or runs a model. (That was the cause of
  every "database is locked" so far: a job wrote something, then kept the transaction open
  while it did slow work.) Big batches (executemany) are split into chunks of a couple of
  thousand rows, so the window can get in between them.
- Background code that needs several statements to happen together uses
  `with conn.write(): ...` (database work only inside).
- The window keeps Python's usual transactions (its changes are saved with commit()); an
  idle timer in the window saves anything left open, so it never holds the gate for long.
- A watchdog logs any write that held the gate for more than WARN_SECONDS, with the thread
  and the line of code that took it - so a slow writer shows up in the log (and in the tests)
  long before anyone sees an error.
"""
import os
import re
from dbshim import sqlite3
import sys
import threading
import time
from contextlib import contextmanager

WARN_SECONDS = 2.0
UI_TIMEOUT = 30.0              # the window gives up (with an error) after this long - never expected
BG_TIMEOUT = 900.0             # a background job waits this long at most
CHUNK = 2000                   # executemany rows per transaction for background jobs
FAIR = 0.05                    # a background job that has waited this long gets the next turn, even
                               # over the window (else a window writing in a quick loop starves it)

_WRITE_RE = re.compile(r"^\s*(?:--[^\n]*\n\s*|/\*.*?\*/\s*)*(\w+)", re.S)
_WRITES = {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER", "ANALYZE", "VACUUM",
           "REINDEX", "BEGIN", "SAVEPOINT"}
_CTE_WRITE = re.compile(r"\b(INSERT|UPDATE|DELETE|REPLACE)\b", re.I)


def is_write(sql):
    m = _WRITE_RE.match(sql)
    if not m:
        return False
    word = m.group(1).upper()
    if word in _WRITES:
        return True
    if word == "WITH":
        return bool(_CTE_WRITE.search(sql))
    if word == "PRAGMA":
        return "=" in sql and "busy_timeout" not in sql.lower()
    return False


def _log(msg):
    """Into facesort.log (the same file the window writes to)."""
    try:
        import store
        with open(os.path.join(store.data_dir(), "facesort.log"), "a", encoding="utf-8") as f:
            import cryptbox
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + cryptbox.scrub(msg) + "\n")
    except Exception:
        print(msg, file=sys.stderr)


def _caller():
    """The first line of FaceSort's own code that led here (for the watchdog). Walks the frames
    directly - no source files are read (this runs on every write)."""
    here = os.path.basename(__file__)
    f = sys._getframe(2)
    for _ in range(12):
        if f is None:
            break
        fn = f.f_code.co_filename
        base = os.path.basename(fn)
        # ((1.61) by name: in the packaged app the frames' file names are bare, so this file never matched
        # and every slow write was blamed on dbgate.py itself)
        if base != here and "sqlite3" not in fn and base != "contextlib.py":
            return f"{os.path.basename(fn)}:{f.f_lineno} {f.f_code.co_name}"
        f = f.f_back
    return "?"


# ---- across processes (1.45): FaceSort's helper processes write to the same library. One lock
# shared by every process keeps it to one writer at a time across all of them; the window still
# goes first (XUI counts the window's waiters - helpers hold back while it's waiting).
XLOCK = None          # multiprocessing.Lock shared with the helpers (None: no helpers)
XUI = None            # multiprocessing.Value('i'): the window is waiting to write
XOWNER = None         # multiprocessing.Value('i'): the process id holding XLOCK (0: nobody)


def share(lock, ui_flag, owner):
    global XLOCK, XUI, XOWNER
    XLOCK, XUI, XOWNER = lock, ui_flag, owner


def _x_acquire(ui, deadline):
    """Take the shared lock (after this process's own gate)."""
    if XLOCK is None:
        return
    if ui:
        with XUI.get_lock():
            XUI.value += 1
        try:
            while not XLOCK.acquire(timeout=0.05):
                if time.monotonic() > deadline:
                    raise sqlite3.OperationalError("database is busy - a helper process has been writing too long")
        finally:
            with XUI.get_lock():
                XUI.value -= 1
    else:
        held_back = time.monotonic()
        while True:
            # the window first - unless this has waited FAIR already (so it can't be starved)
            if XUI.value > 0 and time.monotonic() - held_back < FAIR:
                time.sleep(0.002)
                continue
            if XLOCK.acquire(timeout=0.05):
                break
            if time.monotonic() > deadline:
                raise sqlite3.OperationalError("database is busy - another process has been writing too long")
    XOWNER.value = os.getpid()


def _x_release():
    if XLOCK is None:
        return
    XOWNER.value = 0
    try:
        XLOCK.release()
    except ValueError:            # (wasn't held - released for a helper that stopped, say)
        pass


SLOW_READ = 0.3                 # a read on the window's thread slower than this is written to the log
_slow_seen = {}


def _slow_read(conn, sql, params, dt):
    """(1.45.3) A slow read on the window's thread: log it once per query, with how SQLite ran it
    and how big the write-ahead log is - so a slow library can be diagnosed from facesort.log."""
    key = " ".join(sql.split())[:300]
    n = _slow_seen.get(key, 0)
    _slow_seen[key] = n + 1
    if n not in (0, 10, 100):
        return
    try:
        plan = "; ".join(r[3] for r in sqlite3.Connection.execute(conn, "EXPLAIN QUERY PLAN " + sql, params))
    except Exception as e:
        plan = f"(no plan: {e})"
    wal = ""
    try:
        db = sqlite3.Connection.execute(conn, "PRAGMA database_list").fetchone()[2]
        wal = f", wal {os.path.getsize(db + '-wal') / 1e6:.0f} MB" if os.path.exists(db + "-wal") else ""
    except Exception:
        pass
    _log(f"slow read {dt:.2f} s (x{n + 1}{wal}): {key[:200]} | plan: {plan[:400]}")


class WriteGate:
    def __init__(self):
        self.cond = threading.Condition()
        self.owner = None
        self.ui_waiting = 0
        self.bg_waits = {}                   # background waiters -> when they started waiting
        self.since = 0.0
        self.where = ""
        # statistics (the tests read these)
        self.longest = 0.0
        self.longest_where = ""
        self.ui_wait_max = 0.0
        self.slow = []                       # (seconds, where) of holds over WARN_SECONDS

    def acquire(self, conn, ui):
        with self.cond:
            if self.owner is conn:
                return
            t0 = time.monotonic()
            end = t0 + (UI_TIMEOUT if ui else BG_TIMEOUT)
            me = object()
            if ui:
                self.ui_waiting += 1
            else:
                self.bg_waits[me] = t0
            try:
                while True:
                    now = time.monotonic()
                    if self.owner is None:
                        starving = [t for t in self.bg_waits.values() if now - t >= FAIR]
                        if ui:
                            if not starving:
                                break                    # the window goes first...
                        elif not self.ui_waiting or now - t0 >= FAIR:
                            # ...unless a background job has waited long enough: then the one
                            # that has waited longest goes
                            if not starving or t0 <= min(starving):
                                break
                    left = end - now
                    if left <= 0:
                        raise sqlite3.OperationalError(
                            f"database is busy - {self.where} has been writing for "
                            f"{time.monotonic() - self.since:.0f} s")
                    self.cond.wait(min(left, 0.05))
                self.owner = conn
                self.since = time.monotonic()
                self.where = f"{threading.current_thread().name}: {_caller()}"
                if ui:
                    self.ui_wait_max = max(self.ui_wait_max, self.since - t0)
            finally:
                if ui:
                    self.ui_waiting -= 1
                else:
                    self.bg_waits.pop(me, None)
                self.cond.notify_all()
        if XLOCK is not None:                       # then the lock shared with the helper processes
            try:
                _x_acquire(ui, end)
            except BaseException:
                with self.cond:
                    self.owner = None
                    self.cond.notify_all()
                raise
            with self.cond:
                self.since = time.monotonic()

    def release(self, conn):
        with self.cond:
            if self.owner is not conn:
                return
            held = time.monotonic() - self.since
            if held > self.longest:
                self.longest, self.longest_where = held, self.where
            if held > WARN_SECONDS:
                self.slow.append((round(held, 2), self.where))
                del self.slow[:-50]
                _log(f"slow database write: {held:.1f} s by {self.where}")
            _x_release()
            self.owner = None
            self.cond.notify_all()

    def reset_stats(self):
        with self.cond:
            self.longest, self.longest_where, self.ui_wait_max, self.slow = 0.0, "", 0.0, []


GATE = WriteGate()


class GatedConnection(sqlite3.Connection):
    """A library connection that writes through GATE. Made by store.connect()."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.ui = threading.current_thread() is threading.main_thread()
        self._block = 0
        if not self.ui:
            self.isolation_level = None          # background: every statement saved at once

    # -- the gate
    def _enter(self):
        GATE.acquire(self, self.ui)

    def _leave_if_done(self):
        if self._block == 0 and not self.in_transaction:
            GATE.release(self)

    # -- statements
    def execute(self, sql, params=()):
        if not is_write(sql):
            if not self.ui:
                return super().execute(sql, params)
            t0 = time.monotonic()
            cur = super().execute(sql, params)
            dt = time.monotonic() - t0
            if dt > SLOW_READ:
                _slow_read(self, sql, params, dt)
            return cur
        self._enter()
        try:
            return super().execute(sql, params)
        finally:
            self._leave_if_done()

    def executemany(self, sql, seq):
        if not is_write(sql):
            return super().executemany(sql, seq)
        if self.ui or self._block:
            self._enter()
            try:
                return super().executemany(sql, seq)
            finally:
                self._leave_if_done()
        # background: chunks of CHUNK rows, each one quick transaction (the window can get in
        # between chunks)
        rows = seq if isinstance(seq, (list, tuple)) else list(seq)
        cur = None
        for i in range(0, max(1, len(rows)), CHUNK):
            part = rows[i:i + CHUNK]
            if not part and i:
                break
            self._enter()
            try:
                super().execute("BEGIN IMMEDIATE")
                try:
                    cur = super().executemany(sql, part)
                    super().execute("COMMIT")
                except BaseException:
                    super().execute("ROLLBACK")
                    raise
            finally:
                self._leave_if_done()
        return cur

    def executescript(self, script):
        self._enter()
        try:
            return super().executescript(script)
        finally:
            self._leave_if_done()

    def commit(self):
        try:
            super().commit()
        finally:
            if self._block == 0:
                GATE.release(self)

    def rollback(self):
        try:
            super().rollback()
        finally:
            if self._block == 0:
                GATE.release(self)

    def close(self):
        try:
            if self.in_transaction:
                super().commit()
        except sqlite3.Error:
            pass
        GATE.release(self)
        super().close()

    @contextmanager
    def write(self):
        """Several statements as one transaction, holding the gate throughout - database work
        only inside (no file reading, no models)."""
        self._enter()
        self._block += 1
        began = False
        try:
            if not self.in_transaction:
                super().execute("BEGIN IMMEDIATE")
                began = True
            yield self
            if began or not self.ui:
                super().commit()
        except BaseException:
            try:
                super().rollback()
            except sqlite3.Error:
                pass
            raise
        finally:
            self._block -= 1
            if self._block == 0 and not self.in_transaction:
                GATE.release(self)

    def save_if_idle(self):
        """The window: save anything left open (called from an idle timer)."""
        if self.in_transaction and self._block == 0:
            self.commit()
