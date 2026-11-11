"""The library: naming, grouping, duplicates (with the RAW rules), albums and vaults."""
import json
import os
import shutil

import numpy as np
import pytest

import store
from conftest import add_photo, unit


def faces_by_label(lib):
    out = {}
    for fid, pid, person, conf, cluster in lib.conn.execute(
            "SELECT f.id, f.photo_id, p.name, f.confirmed, f.cluster FROM faces f "
            "LEFT JOIN persons p ON p.id=f.person_id"):
        out.setdefault(lib.who[pid], []).append((fid, person, conf, cluster))
    return out


# ------------------------------------------------------------------ naming and grouping
def test_named_people_get_suggestions(library):
    for name in library.people:
        faces = faces_by_label(library)[name]
        assert all(person == name for _, person, _, _ in faces), faces
        assert sum(conf for _, _, conf, _ in faces) == 2          # only the tagged ones are confirmed


def test_strangers_become_one_group_each(library):
    by = faces_by_label(library)
    groups = {}
    for lab in ("stranger0", "stranger1"):
        clusters = {c for _, person, _, c in by[lab]}
        assert len(clusters) == 1 and None not in clusters, (lab, clusters)
        assert all(person is None for _, person, _, _ in by[lab])
        groups[lab] = clusters.pop()
    assert groups["stranger0"] != groups["stranger1"]


def test_regroup_is_stable(library):
    before = sorted(library.conn.execute("SELECT id, person_id, confirmed, cluster FROM faces"))
    store.regroup(library.conn, store.threshold(library.settings))
    library.conn.commit()
    after = sorted(library.conn.execute("SELECT id, person_id, confirmed, cluster FROM faces"))
    assert [r[:3] for r in before] == [r[:3] for r in after]
    # group numbers may be renumbered, but the same faces stay together
    part = lambda rows: sorted(sorted(i for i, *_r, c in rows if c == g) for g in {r[3] for r in rows if r[3]})
    assert part(before) == part(after)


def test_naming_a_group_and_rejecting(library):
    conn = library.conn
    fids = [f for f, *_ in faces_by_label(library)["stranger0"]]
    dave = store.get_or_create_person(conn, "Dave")
    store.assign(conn, fids, dave)
    assert {r[0] for r in conn.execute(f"SELECT person_id FROM faces WHERE id IN ({','.join(map(str, fids))})")} == {dave}
    store.reject(conn, fids[:1])
    store.regroup(conn, store.threshold(library.settings))
    row = conn.execute("SELECT person_id FROM faces WHERE id=?", (fids[0],)).fetchone()
    assert row[0] != dave                                # "not Dave" is remembered


def test_ignored_faces_stay_out(library):
    conn = library.conn
    fids = [f for f, *_ in faces_by_label(library)["stranger1"]]
    store.set_ignored(conn, fids, True)
    store.regroup(conn, store.threshold(library.settings))
    rows = conn.execute(f"SELECT ignored, person_id, cluster FROM faces WHERE id IN ({','.join(map(str, fids))})").fetchall()
    assert all(r == (1, None, None) for r in rows), rows


def test_rescan_keeps_names(library):
    """Re-storing an edited photo keeps the names already on its faces (matched by position)."""
    conn = library.conn
    pid, path = conn.execute("SELECT f.photo_id, ph.path FROM faces f JOIN photos ph ON ph.id=f.photo_id "
                             "WHERE f.confirmed=1 LIMIT 1").fetchone()
    name = conn.execute("SELECT p.name FROM faces f JOIN persons p ON p.id=f.person_id WHERE f.photo_id=?", (pid,)).fetchone()[0]
    emb = library.base[name]
    add_photo(conn, path, [emb], seed=999)
    conn.commit()
    row = conn.execute("SELECT p.name, f.confirmed FROM faces f JOIN photos ph ON ph.id=f.photo_id "
                       "JOIN persons p ON p.id=f.person_id WHERE ph.path=?", (path,)).fetchone()
    assert row == (name, 1)


# ------------------------------------------------------------------ duplicates
def _set_phash(conn, pid, h):
    conn.execute("UPDATE photos SET phash=? WHERE id=?", (h, pid))


def _photo(conn, path, seed, size=(320, 240)):
    pid = add_photo(conn, path, [unit(np.random.default_rng(seed).normal(size=128))], seed=seed, size=size)
    conn.commit()
    return pid


def _same(conn, a, b):
    """Mark a pair as already compared closely and found the same (as the background check does)."""
    sa = conn.execute("SELECT size FROM photos WHERE id=?", (a,)).fetchone()[0]
    sb = conn.execute("SELECT size FROM photos WHERE id=?", (b,)).fetchone()[0]
    a, b, sa, sb = (a, b, sa, sb) if a < b else (b, a, sb, sa)
    conn.execute("INSERT OR REPLACE INTO dup_checked(a, b, sa, sb, same) VALUES(?,?,?,?,1)", (a, b, sa, sb))
    conn.commit()


def test_exact_copies_are_one_set(data_dir):
    conn = store.connect()
    a = _photo(conn, os.path.join(data_dir.lib, "x", "pic.jpg"), 1)
    shutil.copy2(os.path.join(data_dir.lib, "x", "pic.jpg"), os.path.join(data_dir.lib, "x", "pic copy.jpg"))
    b = _photo(conn, os.path.join(data_dir.lib, "x", "other.jpg"), 2)
    # store the copy with the same file contents
    p = os.path.join(data_dir.lib, "x", "pic copy.jpg")
    st = os.stat(p)
    from conftest import img_info
    from PIL import Image
    c = store.store_photo(conn, p, st.st_mtime, st.st_size, img_info(Image.open(p), 1.6e9 + 1), [], settings={})
    for pid in (a, c):
        _set_phash(conn, pid, 0x1234_5678_9ABC_DEF0)
    _set_phash(conn, b, 0x0FED_CBA9_8765_4321)
    conn.commit()
    sets = store.duplicate_sets(conn)
    assert len(sets) == 1 and sorted(sets[0]["ids"]) == sorted([a, c]) and sets[0]["exact"]
    assert sets[0]["best"] == a                      # same size: the shorter name wins the tie
    # "not duplicates" is remembered
    store.not_duplicates(conn, c, sets[0]["ids"])
    assert store.duplicate_sets(conn) == []
    conn.close()


def test_discarded_photos_leave_their_set(data_dir):
    conn = store.connect()
    ids = [_photo(conn, os.path.join(data_dir.lib, f"p{i}.jpg"), 10 + i) for i in range(3)]
    for p in ids:
        _set_phash(conn, p, 0x2AAA_BBBB_CCCC_DDDD)
    _same(conn, ids[0], ids[1])
    _same(conn, ids[1], ids[2])
    assert len(store.duplicate_sets(conn)[0]["ids"]) == 3
    store.set_photos_discarded(conn, [ids[2]], True)
    conn.commit()
    sets = store.duplicate_sets(conn)
    assert sets and sorted(sets[0]["ids"]) == ids[:2]
    conn.close()


def test_largest_picture_is_best(data_dir):
    conn = store.connect()
    small = _photo(conn, os.path.join(data_dir.lib, "small.jpg"), 20, size=(160, 120))
    big = _photo(conn, os.path.join(data_dir.lib, "zz_big.jpg"), 21, size=(640, 480))
    for p in (small, big):
        _set_phash(conn, p, 0x1111_2222_3333_4444)
    _same(conn, small, big)
    sets = store.duplicate_sets(conn)
    assert sets[0]["best"] == big and not sets[0]["exact"]
    conn.close()


def _raw_setup(data_dir, conn, same_name):
    jpg = _photo(conn, os.path.join(data_dir.lib, "IMG_0001.jpg"), 30, size=(640, 480))
    raw_path = os.path.join(data_dir.lib, ("IMG_0001" if same_name else "IMG_0099") + ".cr2")
    with open(raw_path, "wb") as f:
        f.write(b"fake raw" * 64)
    st = os.stat(raw_path)
    from types import SimpleNamespace
    raw = store.store_photo(conn, raw_path, st.st_mtime, st.st_size,
                            SimpleNamespace(width=320, height=240, orientation=1, taken=1.6e9, cover_t=None), [],
                            settings={})
    conn.commit()
    for p in (jpg, raw):
        _set_phash(conn, p, 0x5555_6666_7777_8888)
    conn.commit()
    _same(conn, jpg, raw)
    return jpg, raw


def _settings(data_dir, **kw):
    p = data_dir.data / "settings.json"
    s = json.loads(p.read_text(encoding="utf-8"))
    s.update(kw)
    p.write_text(json.dumps(s), encoding="utf-8")


def test_raw_jpeg_pair_is_not_a_duplicate(data_dir):
    conn = store.connect()
    _raw_setup(data_dir, conn, same_name=True)
    assert store.duplicate_sets(conn) == []          # the camera's RAW + JPEG of one shot: kept on purpose
    _settings(data_dir, dup_raw_jpeg_pairs=False)
    assert len(store.duplicate_sets(conn)) == 1
    conn.close()


def test_raw_preferred_even_when_smaller(data_dir):
    conn = store.connect()
    jpg, raw = _raw_setup(data_dir, conn, same_name=False)
    assert store.duplicate_sets(conn)[0]["best"] == raw
    _settings(data_dir, dup_prefer_raw=False)
    assert store.duplicate_sets(conn)[0]["best"] == jpg   # then the larger picture wins
    conn.close()


# ------------------------------------------------------------------ albums
def test_albums(library):
    conn = library.conn
    pids = sorted(library.who)[:5]
    a = store.create_album(conn, "  Summer   trip ")
    assert store.create_album(conn, "Summer trip") == a          # same name: the same album
    assert store.add_to_album(conn, a, pids) == 5
    assert store.add_to_album(conn, a, pids) == 0                # no double entries
    b = store.create_album(conn, "Beach")
    store.add_to_album(conn, b, pids[3:] + [sorted(library.who)[6]])
    assert store.albums_of(conn, pids)[a] == 5
    left = store.rename_album(conn, b, "Summer trip")            # renaming onto another = merge
    assert left == a
    assert {r[1]: r[2] for r in store.albums(conn)} == {"Summer trip": 6}
    store.remove_from_album(conn, a, pids[:2])
    assert store.albums(conn)[0][2] == 4
    store.delete_album(conn, a)
    assert store.albums(conn) == []


# ------------------------------------------------------------------ vaults
def test_vaults(library):
    conn = library.conn
    store.ensure_vault(conn)
    names = [v[1] for v in store.vaults(conn)]
    assert names == ["Vault"]
    v2 = store.create_vault(conn, "vault", password="s3cret")
    assert [v[1] for v in store.vaults(conn) if v[0] == v2] == ["vault (2)"]
    assert store.vault_password_ok(conn, v2, "s3cret")
    assert not store.vault_password_ok(conn, v2, "wrong")
    store.lock_vault(conn, v2, True)
    assert dict((v[0], v[2]) for v in store.vaults(conn))[v2] is True
    store.lock_vault(conn, v2, False)
    assert dict((v[0], v[2]) for v in store.vaults(conn))[v2] is False


def test_regroup_in_background_while_the_window_writes(library):
    """A real background job (regrouping, on its own connection) and the window writing at the
    same time: no errors, and the window never waits long."""
    import threading
    import time
    import dbgate
    dbgate.GATE.reset_stats()
    err = []

    def job():
        try:
            c = store.connect()
            for _ in range(3):
                store.regroup(c, store.threshold(library.settings))
            c.close()
        except Exception as e:          # noqa: BLE001
            err.append(e)
    th = threading.Thread(target=job, name="regroup-test")
    th.start()
    pids = sorted(library.who)
    a = store.create_album(library.conn, "During regroup")
    waits = []
    while th.is_alive():
        t0 = time.monotonic()
        store.add_to_album(library.conn, a, pids[:3])
        store.remove_from_album(library.conn, a, pids[:3])
        waits.append(time.monotonic() - t0)
    th.join()
    assert not err, err
    assert max(waits) < 1.0 and not dbgate.GATE.slow


# ------------------------------------------------------------------ moving photos between folders
def _two_people_photo(data_dir, conn, n_people=2):
    rng = np.random.default_rng(0)
    names = ["Alice", "Bob", "Carol"][:n_people]
    pid = add_photo(conn, os.path.join(str(data_dir.lib), "group.jpg"), [unit(rng.normal(size=128)) for _ in names],
                    seed=1, hints=tuple((n, 0.05 + 0.3 * i, 0.25, 0.25, 0.4) for i, n in enumerate(names)))
    conn.commit()
    return pid, [store.get_or_create_person(conn, n) for n in names]


def _placements(conn, pid):
    tree, paths = store.folder_tree(conn), store.category_paths(conn)
    return sorted((tree[f][2], "/".join(paths[f])) for (f,) in
                  conn.execute("SELECT folder_id FROM photo_folders WHERE photo_id=?", (pid,)))


@pytest.mark.parametrize("n_people", [1, 2, 3])
def test_folders_are_only_for_the_person_whose_gallery_youre_in(data_dir, n_people):
    """Putting a group photo in Alice's "Beach" makes no "Beach" for anyone else on it, and moving
    it to her "Party" doesn't leave it in "Beach"."""
    conn = store.connect()
    pid, people = _two_people_photo(data_dir, conn, n_people)
    alice = people[0]
    beach = store.create_category(conn, "Beach", None, alice)
    party = store.create_category(conn, "Party", None, alice)
    folders_before = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    store.set_photo_folder(conn, [pid], beach)
    conn.commit()
    assert _placements(conn, pid) == [(alice, "Beach")]
    store.set_photo_folder(conn, [pid], party)                                  # oops - meant Party
    conn.commit()
    assert _placements(conn, pid) == [(alice, "Party")]
    assert conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == folders_before   # no copies made
    dests = store.photo_destinations(conn)[pid]
    assert (None, alice, party) in dests and len(dests) == n_people             # the others: their own place
    assert all(inner is None for _b, person, inner in dests if person != alice)
    conn.close()


def test_each_persons_folders_are_their_own(data_dir):
    conn = store.connect()
    pid, (alice, bob) = _two_people_photo(data_dir, conn)
    store.set_photo_folder(conn, [pid], store.create_category(conn, "Beach", None, alice))
    store.set_photo_folder(conn, [pid], store.create_category(conn, "Bob's best", None, bob))
    conn.commit()
    assert _placements(conn, pid) == [(alice, "Beach"), (bob, "Bob's best")]
    store.set_photo_folder(conn, [pid], store.create_category(conn, "Party", None, alice))
    conn.commit()
    assert _placements(conn, pid) == [(alice, "Party"), (bob, "Bob's best")]
    store.take_out_of_person_folders(conn, [pid], alice)
    conn.commit()
    assert _placements(conn, pid) == [(bob, "Bob's best")]
    conn.close()


def test_cleanup_of_folders_copied_by_older_versions(data_dir):
    conn = store.connect()
    pid, (alice, bob) = _two_people_photo(data_dir, conn)
    beach = store.create_category(conn, "Beach", None, alice)
    store.set_photo_folder(conn, [pid], beach)
    copy = store.create_category(conn, "Beach", None, bob)                  # what older versions made
    conn.execute("INSERT INTO photo_folders(photo_id, folder_id) VALUES(?,?)", (pid, copy))
    own = store.create_category(conn, "Hiking", None, bob)                  # Bob's own folder: kept
    pid2 = add_photo(conn, os.path.join(str(data_dir.lib), "solo.jpg"), [unit(np.ones(128))], seed=5)
    store.set_photo_folder(conn, [pid2], own)
    conn.commit()
    assert store.copied_folder_placements(conn) == [(pid, copy)]
    assert store.remove_copied_folders(conn) == (1, 1)
    assert _placements(conn, pid) == [(alice, "Beach")]
    assert conn.execute("SELECT COUNT(*) FROM categories WHERE id=?", (copy,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM categories WHERE id IN (?,?)", (beach, own)).fetchone()[0] == 2
    assert store.copied_folder_placements(conn) == []
    conn.close()


def _media_row(conn, path, data, video, phash=None):
    with open(path, "wb") as f:
        f.write(data)
    return conn.execute("INSERT INTO photos(path, mtime, size, is_video, width, height, phash) VALUES(?,?,?,?,?,?,?)",
                        (path, os.path.getmtime(path), len(data), int(video), 640, 480, phash)).lastrowid


def test_exact_copies_of_videos_and_gifs(data_dir):
    conn = store.connect()
    L = str(data_dir.lib)
    v1 = _media_row(conn, os.path.join(L, "clip.mp4"), b"video-A" * 1000, True)
    v2 = _media_row(conn, os.path.join(L, "clip (copy).mp4"), b"video-A" * 1000, True)
    v3 = _media_row(conn, os.path.join(L, "other clip.mp4"), b"video-B" * 1000, True)    # same size, other clip
    g1 = _media_row(conn, os.path.join(L, "fun.gif"), b"GIF89a" + b"x" * 500, False)       # no picture hash
    g2 = _media_row(conn, os.path.join(L, "fun copy.gif"), b"GIF89a" + b"x" * 500, False)
    b1 = _media_row(conn, os.path.join(L, "black.jpg"), b"\xff\xd8black" * 50, False, phash=0)
    b2 = _media_row(conn, os.path.join(L, "black copy.jpg"), b"\xff\xd8black" * 50, False, phash=0)
    conn.commit()
    sets = {tuple(d["ids"]): d["exact"] for d in store.duplicate_sets(conn)}
    assert sets == {(v1, v2): True, (g1, g2): True, (b1, b2): True}
    assert not any(v3 in ids for ids in sets)
    conn.close()


def test_videos_with_the_same_first_frame_arent_duplicates(data_dir):
    conn = store.connect()
    L = str(data_dir.lib)
    a = _media_row(conn, os.path.join(L, "take1.mp4"), b"take-1" * 900, True, phash=12345)
    b = _media_row(conn, os.path.join(L, "take2.mp4"), b"take-2" * 1000, True, phash=12345)
    conn.commit()
    assert store.duplicate_sets(conn) == []
    conn.close()
