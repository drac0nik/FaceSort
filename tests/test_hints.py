"""1.57: folders suggested for a person from where their photos sit on disk."""
import os

import store


def test_folder_hints_rules(library):
    c = library.conn
    alice = c.execute("SELECT id FROM persons WHERE name='Alice'").fetchone()[0]
    j = os.path.join
    rows = [((-1, j("D:", os.sep, "Imp", "Holiday 2019", "a.jpg"), "", 1), 1),
            ((-2, j("D:", os.sep, "Imp", "Holiday 2019", "b.jpg"), "", 1), 2),
            ((-3, j("D:", os.sep, "Imp", "DCIM", "c.jpg"), "", 1), 3),          # generic names are kept
            ((-4, j("D:", os.sep, "Lib", "Alice", "d.jpg"), "", 1), 4),         # her own name: not a suggestion
            ((-5, j("D:", os.sep, "Lib", "~Sort Later", "e.jpg"), "", 1), 5),   # FaceSort's own folders: no
            ((-6, j("D:", os.sep, "Imp", "Solo", "f.jpg"), "", 1), 6)]          # one photo is enough
    h = store.folder_hints(c, alice, rows)
    names = sorted(v[0] for v in h.values())
    assert names == ["DCIM", "Holiday 2019", "Solo"]
    hol = next(v for v in h.values() if v[0] == "Holiday 2019")
    assert len(hol[2]) == 2
    key = next(k for k, v in h.items() if v[0] == "DCIM")
    c.execute("INSERT INTO folder_hint_no(person_id, dir) VALUES(?,?)", (alice, key))
    c.execute("INSERT INTO folder_hint_out(person_id, photo_id) VALUES(?,?)", (alice, 6))
    names = sorted(v[0] for v in store.folder_hints(c, alice, rows).values())
    assert names == ["Holiday 2019"]


