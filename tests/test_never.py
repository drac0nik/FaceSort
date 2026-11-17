"""(1.51) Never track: a permanent list of things that are never faces."""
import os

import numpy as np

import neverfaces
import store
from conftest import add_photo, unit


def logo_face(c, name, base, seed, noise=0.02, people=None):
    rng = np.random.default_rng(seed)
    pid = add_photo(c, os.path.abspath(os.path.join(os.sep, "lib", "SG", f"{name}.jpg")),
                    [unit(base + rng.normal(size=base.shape) * noise)], seed=seed)
    return c.execute("SELECT id FROM faces WHERE photo_id=?", (pid,)).fetchone()[0]


def test_never_track_sets_aside_matches_now_and_later(library):
    c = library.conn
    model = store.get_model(c)
    dim = store.model_dim(model)
    rng = np.random.default_rng(5)
    logo = unit(rng.normal(size=dim))
    real = unit(rng.normal(size=dim))
    a, b = logo_face(c, "sg1", logo, 501), logo_face(c, "sg2", logo, 502)
    person_face = logo_face(c, "alice", real, 503)
    named = logo_face(c, "named_logo", logo, 504)
    alice = store.get_or_create_person(c, "Alice NT")
    c.execute("UPDATE faces SET person_id=?, confirmed=1 WHERE id=?", (alice, named))   # named by you: never touched
    c.commit()
    eid, n = neverfaces.add(c, [a], "SuicideGirls logo")
    assert eid is not None and n >= 1
    state = dict(c.execute("SELECT id, ignored FROM faces"))
    assert state[a] == 3 and state[b] == 3                    # the one you picked and its look-alike
    assert state[person_face] == 0 and state[named] == 0
    # a photo scanned later: set aside when suggestions are worked out
    later = logo_face(c, "sg3", logo, 505)
    store.regroup(c, store.threshold({"engine": model}, model))
    assert c.execute("SELECT ignored FROM faces WHERE id=?", (later,)).fetchone()[0] == 3
    # survives clearing the library (it lives next to the settings)
    assert os.path.exists(os.path.join(store.data_dir(), neverfaces.FILE))
    e = neverfaces.load()[0]
    assert e["label"] == "SuicideGirls logo" and e["seen"]
    # adding a very alike one again goes into the same entry
    eid2, _ = neverfaces.add(c, [b], "again")
    assert eid2 == eid and len(neverfaces.load()) == 1
    # removed, with its faces brought back
    back = neverfaces.remove(c, eid, restore=True)
    assert back >= 3 and not neverfaces.load()
    assert c.execute("SELECT ignored FROM faces WHERE id=?", (later,)).fetchone()[0] == 0


def test_carried_over_to_another_engine(library, monkeypatch):
    """After switching face engines, the same spot in the same photo (measured again) keeps the
    entry working."""
    c = library.conn
    model = store.get_model(c)
    dim = store.model_dim(model)
    rng = np.random.default_rng(6)
    logo = unit(rng.normal(size=dim))
    a = logo_face(c, "carry1", logo, 601)
    eid, _ = neverfaces.add(c, [a], "Logo")
    entries = neverfaces.load()
    entries[0]["embs"] = {"othermodel": entries[0]["embs"][model]}       # as if made with another engine
    neverfaces.save(entries)
    c.execute("UPDATE faces SET ignored=0 WHERE id=?", (a,))
    c.commit()
    got = neverfaces.matches(c)
    assert a in got and neverfaces.load()[0]["embs"].get(model)


from test_ui import window  # noqa: E402,F401


def test_never_track_from_the_window(window, qapp, monkeypatch):
    import mw_engine
    from neverfaces_ui import NeverTrackDialog
    w, c = window, window.conn
    w.view = ("unknown", None)
    w.refresh_grid()
    fid = next(r[0] for r in w.model.rows if r[0] > 0)
    monkeypatch.setattr(type(w), "sel_faces", lambda self: [fid])
    monkeypatch.setattr(mw_engine.QInputDialog, "getText", staticmethod(lambda *a, **k: ("Watermark", True)))
    w.act_never_track()
    assert c.execute("SELECT ignored FROM faces WHERE id=?", (fid,)).fetchone()[0] == 3
    d = NeverTrackDialog(w, c)
    assert d.list.count() == 1 and "Watermark" in d.list.item(0).text()
    d.list.setCurrentRow(0)
    d.do_remove()
    assert d.changed and not neverfaces.load()
    assert c.execute("SELECT ignored FROM faces WHERE id=?", (fid,)).fetchone()[0] == 0
