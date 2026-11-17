"""Reading the side lists' numbers ahead, on a background thread (1.50).

Redrawing the left-hand lists after a change asks the library about 40 questions (each person's
faces and best face, the unnamed groups, the Smart Groups counts…). On a big library that's most of
the half second - or second - the window froze for after every change.

Now, after a change, those same questions are asked on a background thread first (the window keeps
responding meanwhile), and the redraw then takes its answers from there instead of asking again.
The questions are the ones the last redraw asked (they hardly change from one redraw to the next);
anything new is simply asked as before. If you change something else while the answers are being
read, they're thrown away and read again - the lists never show older numbers than before.
"""
import sqlite3
import time

SKIP = ("temp.", "scope_ids")      # (temp tables belong to the window's own connection)
MAX_QUERIES = 400
MAX_SECONDS = 20.0


def _key(sql, params):
    if isinstance(params, dict):
        return sql, tuple(sorted(params.items()))
    try:
        return sql, tuple(params)
    except TypeError:
        return sql, (params,)


class Rows:
    """A finished query's rows, read like a cursor."""

    def __init__(self, rows):
        self._rows = rows
        self._i = 0
        self.rowcount = -1
        self.lastrowid = None

    def fetchone(self):
        if self._i < len(self._rows):
            self._i += 1
            return self._rows[self._i - 1]
        return None

    def fetchall(self):
        out = self._rows[self._i:]
        self._i = len(self._rows)
        return list(out)

    def fetchmany(self, n=1):
        out = self._rows[self._i:self._i + n]
        self._i += len(out)
        return list(out)

    def __iter__(self):
        rest = self._rows[self._i:] if self._i else self._rows
        self._i = len(self._rows)
        return iter(rest)


class Recording:
    """The window's connection during a redraw: answers read ahead are used, and every read is
    noted (the questions to read ahead next time). Everything else goes to the real connection."""

    def __init__(self, real, answers=None):
        self._real = real
        self._answers = answers or {}
        self.asked = {}
        self.hits = 0
        self.misses = 0
        self.missed = []                  # (questions not read ahead, when there were answers)
        self.part = "tree"                # which part of the redraw is asking (tree / grid / stats)
        self.parts = {}                   # part -> its questions

    def execute(self, sql, params=()):
        head = sql.lstrip()[:10].upper()
        if not (head.startswith("SELECT") or head.startswith("WITH")):
            return self._real.execute(sql, params)
        key = _key(sql, params)
        if key not in self.asked and len(self.asked) < MAX_QUERIES:
            self.asked[key] = None
        self.parts.setdefault(self.part, {})[key] = None
        got = self._answers.get(key)
        if got is not None:
            self.hits += 1
            return Rows(got)
        self.misses += 1
        if self._answers:
            self.missed.append(key)
        return self._real.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._real, name)


def read_ahead(conn, questions, in_scope=None, progress=None, cancelled=lambda: False):
    """On a background connection: {question: rows} for the questions (a failed one is left out).
    in_scope: only some library folders shown - the photos in them are worked out here too, so the
    questions about just those photos can be read ahead as well."""
    out = {}
    end = time.monotonic() + MAX_SECONDS
    scoped = False
    if in_scope is not None:
        try:
            ids = [pid for pid, path in conn.execute("SELECT id, path FROM photos") if in_scope(path)]
            raw = sqlite3.Connection            # (a temp table is this connection's own: no need for the write gate)
            raw.execute(conn, "CREATE TEMP TABLE IF NOT EXISTS scope_ids(id INTEGER PRIMARY KEY)")
            raw.execute(conn, "DELETE FROM temp.scope_ids")
            raw.executemany(conn, "INSERT INTO temp.scope_ids(id) VALUES(?)", ((i,) for i in ids))
            scoped = True
        except Exception:
            scoped = False
    for sql, params in questions:
        if cancelled() or time.monotonic() > end:
            break
        if not scoped and any(s in sql for s in SKIP):
            continue
        try:
            out[(sql, params)] = conn.execute(sql, params).fetchall()
        except Exception:
            pass
    return out
