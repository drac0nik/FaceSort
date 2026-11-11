"""Face clarity: blurry / tiny / dark faces need a closer match, count less in groups, aren't
used as someone's examples, and sit last in the galleries."""
import os

import numpy as np
import pytest

import engine
import store
from conftest import add_photo, unit
from test_grouping import data, naive, same_partition


def test_clarity_scale():
    assert engine.clarity_from_norm(5) == 0.0
    assert engine.clarity_from_norm(30) == 1.0
    mid = (engine.CLARITY_LO + engine.CLARITY_HI) / 2
    assert abs(engine.clarity_from_norm(mid) - 0.5) < 1e-6


def test_thumbnail_without_a_face_reads_as_unclear():
    from conftest import jpeg_bytes
    from PIL import Image
    assert engine.clarity_of_thumb(jpeg_bytes(Image.new("RGB", (192, 192), (90, 90, 90)))) == 0.1
    assert engine.clarity_of_thumb(b"not a picture") is None


def naive_weighted(E, order, threshold, w):
    cents, sums, of = [], [], np.full(len(E), -1)
    for i in order:
        e = E[i]
        if cents:
            sims = np.array(cents) @ e
            j = int(np.argmax(sims))
            if sims[j] >= threshold:
                sums[j] = sums[j] + e * w[i]
                cents[j] = sums[j] / np.linalg.norm(sums[j])
                of[i] = j
                continue
        cents.append(e.copy())
        sums.append(e * w[i])
        of[i] = len(cents) - 1
    return of


@pytest.mark.parametrize("n_people, per", [(40, 5), (2600, 2)])
def test_weighted_groups_match_one_by_one(n_people, per):
    E, order = data(n_people, per, noise=0.8, seed=5)
    w = np.random.default_rng(2).uniform(0.25, 1.0, len(E)).astype(np.float32)
    got, _, _ = store.leader_cluster(E, order, 0.5, weights=w)
    assert same_partition(got, naive_weighted(E, order, 0.5, w), order)


def _named_library(dd, sim, clar):
    """Alice with 5 confirmed photos; one more face at a given similarity to her and clarity."""
    conn = store.connect()
    rng = np.random.default_rng(3)
    base = unit(rng.normal(size=128))
    for k in range(5):
        e = unit(base + unit(rng.normal(size=128)) * 0.2)
        add_photo(conn, os.path.join(str(dd.lib), f"alice{k}.jpg"), [e], seed=k, hints=(("Alice", 0.05, 0.25, 0.25, 0.4),))
    # a face whose similarity to Alice's typical photo is about `sim`
    other = unit(rng.normal(size=128))
    other -= (other @ base) * base
    other = unit(other)
    probe = unit(sim * base + np.sqrt(max(0.0, 1 - sim * sim)) * other)
    pid = add_photo(conn, os.path.join(str(dd.lib), "probe.jpg"), [probe], seed=99, clarity=clar)
    conn.commit()
    store.regroup(conn, store.threshold(store.load_settings()))
    fid = conn.execute("SELECT id FROM faces WHERE photo_id=?", (pid,)).fetchone()[0]
    return conn, fid


def _suggested(conn, fid):
    r = conn.execute("SELECT p.name FROM faces f LEFT JOIN persons p ON p.id=f.person_id WHERE f.id=?", (fid,)).fetchone()
    return r[0]


def test_unclear_face_needs_a_closer_match(data_dir):
    # similar enough for a clear face...
    conn, fid = _named_library(data_dir, 0.50, 1.0)
    assert _suggested(conn, fid) == "Alice"
    conn.close()


def test_unclear_face_same_match_not_suggested(tmp_path, monkeypatch, data_dir):
    # ...but the same match on a very unclear face isn't suggested
    conn, fid = _named_library(data_dir, 0.50, 0.0)
    assert _suggested(conn, fid) is None
    conn.close()


def test_unclear_examples_are_left_out(data_dir):
    """Someone with enough clear photos is compared through those, not through their blurry ones."""
    conn = store.connect()
    rng = np.random.default_rng(4)
    base = unit(rng.normal(size=128))
    for k in range(5):
        add_photo(conn, os.path.join(str(data_dir.lib), f"b{k}.jpg"), [unit(base + unit(rng.normal(size=128)) * 0.2)],
                  seed=k, hints=(("Bob", 0.05, 0.25, 0.25, 0.4),))
    junk = unit(rng.normal(size=128))          # a blurry "Bob" photo that looks like nobody in particular
    for k in range(3):
        add_photo(conn, os.path.join(str(data_dir.lib), f"bj{k}.jpg"), [unit(junk + unit(rng.normal(size=128)) * 0.05)],
                  seed=50 + k, hints=(("Bob", 0.05, 0.25, 0.25, 0.4),), clarity=0.05)
    # a stranger who happens to look like the blurry photos
    pid = add_photo(conn, os.path.join(str(data_dir.lib), "stranger.jpg"), [unit(junk + unit(rng.normal(size=128)) * 0.1)],
                    seed=77)
    conn.commit()
    store.regroup(conn, store.threshold(store.load_settings()))
    fid = conn.execute("SELECT id FROM faces WHERE photo_id=?", (pid,)).fetchone()[0]
    assert _suggested(conn, fid) is None
    conn.close()


def test_clearest_first_in_galleries(data_dir):
    conn = store.connect()
    e = unit(np.random.default_rng(6).normal(size=128))
    ids = []
    for k, c in enumerate([0.2, 0.95, 0.6]):
        ids.append(add_photo(conn, os.path.join(str(data_dir.lib), f"c{k}.jpg"), [e], seed=k, clarity=c))
    conn.commit()
    order = [r[0] for r in conn.execute(f"SELECT f.photo_id FROM faces f ORDER BY {store.clear_order()} DESC")]
    assert order == [ids[1], ids[2], ids[0]]
    conn.close()


def test_missing_clarity_is_read_from_thumbnails(data_dir):
    conn = store.connect()
    for k in range(3):
        add_photo(conn, os.path.join(str(data_dir.lib), f"d{k}.jpg"), [unit(np.ones(128))], seed=k, clarity=None)
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM faces WHERE clarity IS NULL").fetchone()[0] == 3
    assert store.read_missing_clarity(conn) == 3
    assert {r[0] for r in conn.execute("SELECT clarity FROM faces")} == {0.1}      # plain thumbnails: no face in them
    assert store.read_missing_clarity(conn) == 0                                   # done once
    conn.close()
