"""Possibly the same person (1.49)."""
import os

import numpy as np

import history
import samepeople
import store
from conftest import add_photo, unit
from test_ui import all_keys, window  # noqa: F401  (the window fixture)


def person_with_faces(c, name, base, n, seed, noise=0.02):
    pid = store.get_or_create_person(c, name)
    rng = np.random.default_rng(seed)
    for i in range(n):
        ph = add_photo(c, os.path.abspath(os.path.join(os.sep, "lib", "Same", f"{name}_{i}.jpg")),
                       [unit(base + rng.normal(size=base.shape) * noise)], seed=seed * 10 + i)
        c.execute("UPDATE faces SET person_id=?, confirmed=1 WHERE photo_id=?", (pid, ph))
    c.commit()
    return pid


def test_tidy_and_generic():
    assert samepeople.tidy("  Zoë  O'Neil. ") == "zoe o neil"
    assert samepeople.is_generic("Model 04", ["Model"]) and not samepeople.is_generic("Anna 2", ["Model"])
    assert not samepeople.is_generic("Anna", ["Model"])


def test_pairs_found_merged_and_remembered(library):
    c = library.conn
    model = store.get_model(c)
    dim = store.model_dim(model)
    rng = np.random.default_rng(9)
    face1, face2 = unit(rng.normal(size=dim)), unit(rng.normal(size=dim))
    jen = person_with_faces(c, "Jen Smithers", face1, 5, 91)
    jen2 = person_with_faces(c, "Jennifer Q", face1, 4, 92)          # same face, different name
    typo = store.get_or_create_person(c, "Jen Smithers.")             # the same name written differently
    other = person_with_faces(c, "Zed Other", face2, 4, 93)
    ph = add_photo(c, os.path.abspath(os.path.join(os.sep, "lib", "Same", "tagged.jpg")), [], seed=999)
    store.tag_photos(c, [ph], typo)
    settings = {"engine": model}
    n = samepeople.compute(c, settings, generics=["Model"])
    got = {(a, b): why for a, b, _s, why in samepeople.pairs(c)}
    assert n == len(got)
    assert tuple(sorted((jen, jen2))) in got and "faces" in got[tuple(sorted((jen, jen2)))]
    assert tuple(sorted((jen, typo))) in got and "name" in got[tuple(sorted((jen, typo)))]
    assert not any(other in k for k in got)
    # not the same: remembered, not suggested again
    samepeople.mark_not_same(c, jen2, jen)
    samepeople.compute(c, settings, generics=["Model"])
    assert tuple(sorted((jen, jen2))) not in {(a, b) for a, b, *_ in samepeople.pairs(c)}
    # merge: name and folder chosen; everything moves; undo brings both back
    fid = store.folder_chain(c, ["Models"])
    c.execute("UPDATE persons SET category_id=? WHERE id=?", (fid, typo))
    own = store.create_category(c, "Shoots", None, typo)             # typo's own folder survives the merge
    c.ui = True
    history.install(store)
    try:
        left = samepeople.merge(c, jen, typo, "Jen Smithers", fid)
        history.flush(c)
    finally:
        c.ui = False
    assert left == jen
    assert not c.execute("SELECT 1 FROM persons WHERE id=?", (typo,)).fetchone()
    assert c.execute("SELECT category_id FROM persons WHERE id=?", (jen,)).fetchone()[0] == fid
    assert c.execute("SELECT person_id FROM photo_people WHERE photo_id=?", (ph,)).fetchone()[0] == jen
    assert c.execute("SELECT owner_id FROM categories WHERE id=?", (own,)).fetchone()[0] == jen
    entry = history.entries(c)[0]
    assert entry["text"].startswith("Merged")
    history.undo(c, entry["id"])
    assert c.execute("SELECT name FROM persons WHERE id=?", (typo,)).fetchone()[0] == "Jen Smithers."
    assert c.execute("SELECT person_id FROM photo_people WHERE photo_id=?", (ph,)).fetchone()[0] == typo


def test_same_view_in_window(window, qapp, monkeypatch):
    w, c = window, window.conn
    alice = c.execute("SELECT id FROM persons WHERE name='Alice'").fetchone()[0]
    bob = c.execute("SELECT id FROM persons WHERE name='Bob'").fetchone()[0]
    c.execute("INSERT INTO same_people(a, b, score, why) VALUES(?,?,?,?)", (min(alice, bob), max(alice, bob), 0.9,
                                                                            "faces look alike"))
    c.commit()
    w.rebuild_tree()
    assert ("same", None) in all_keys(w, w.tree2)
    w.view = ("same", None)
    w.refresh_grid()
    rows = w.model.rows
    assert rows[0][0] == 0 and "⟷" in rows[0][1]
    assert any(r[0] > 0 for r in rows[1:])
    pair = w.selected_pair()
    assert set(pair[:2]) == {alice, bob}
    w.not_same_selected_pair(pair)
    w.idle()
    assert not samepeople.pairs(c)
    assert ("same", None) not in all_keys(w, w.tree2)
    # merging from the window
    c.execute("DELETE FROM not_same_people")
    c.execute("INSERT INTO same_people(a, b, score, why) VALUES(?,?,?,?)", (min(alice, bob), max(alice, bob), 0.9, "x"))
    c.commit()
    import samepeople_ui
    monkeypatch.setattr(samepeople_ui.MergeDialog, "exec", lambda self: 1)
    w.merge_selected_pair((alice, bob, "x"))
    w.idle()
    assert c.execute("SELECT COUNT(*) FROM persons WHERE id IN (?,?)", (alice, bob)).fetchone()[0] == 1

