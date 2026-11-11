"""Same-shoot suggestions: who's wearing the same clothes at the same occasion."""
import os

import numpy as np
import pytest

import bodies
import store
from conftest import add_photo, unit

H = 3600


def photo(folder="/p/party", taken=1.7e9, gps=None):
    return {"folder": folder, "taken": taken, "gps": gps}


def test_same_occasion_by_time():
    ok, why = bodies.same_occasion(photo(), photo(taken=1.7e9 + 600), (None, 0.8))
    assert ok and why[0] == "10 min apart"
    assert not bodies.same_occasion(photo(), photo(folder="/q", taken=1.7e9 + 5 * H), (None, 0.8))[0]
    assert bodies.same_occasion(photo(), photo(taken=1.7e9 + 5 * H), (None, 0.8))[0]       # same folder: same day
    assert not bodies.same_occasion(photo(), photo(taken=1.7e9 + 30 * H), (None, 0.8))[0]  # next day: no


def test_same_occasion_by_place():
    a = photo(gps=(51.5, -0.12))
    assert bodies.same_occasion(a, photo(taken=1.7e9 + 60, gps=(51.5005, -0.1205)), (None, 0.8))[0]
    assert not bodies.same_occasion(a, photo(taken=1.7e9 + 60, gps=(48.85, 2.35)), (None, 0.8))[0]   # Paris
    assert bodies.same_occasion(a, photo(taken=1.7e9 + 60), (None, 0.8))[0]      # one without GPS: time decides


def test_same_occasion_without_dates_needs_folder_and_scene():
    a, b = photo(taken=None), photo(taken=None)
    assert bodies.same_occasion(a, b, (0.9, 0.8)) == (True, ["same folder", "same scene"])
    assert not bodies.same_occasion(a, b, (0.5, 0.8))[0]
    assert not bodies.same_occasion(a, photo(folder="/other", taken=None), (0.95, 0.8))[0]


def test_km_between():
    assert abs(bodies.km_between((51.5074, -0.1278), (48.8566, 2.3522)) - 344) < 5     # London - Paris


def test_link_faces():
    people = [(100, 50, 120, 380, 0.9), (300, 60, 110, 360, 0.9)]
    faces = [(7, 130, 60, 50, 60), (8, 330, 70, 45, 55), (9, 500, 400, 40, 40)]
    assert bodies.link_faces(people, faces) == [7, 8]
    assert bodies.link_faces(people[:1], [(9, 500, 400, 40, 40)]) == [None]


# ------------------------------------------------------------------ suggestions in a library
def clothes(seed):
    return unit(np.random.default_rng(seed).normal(size=768))


def near(v, seed, noise=0.35):
    return unit(v + unit(np.random.default_rng(seed).normal(size=768)) * noise)


@pytest.fixture
def shoot(data_dir):
    """A party: Alice (red dress) and Bob (suit) confirmed in photo 1; in photo 2, an hour later,
    two people with no readable face. And a photo a week later of someone in a red dress."""
    conn = store.connect()
    rng = np.random.default_rng(0)
    fa, fb = unit(rng.normal(size=128)), unit(rng.normal(size=128))
    party = os.path.join(str(data_dir.lib), "party")
    p1 = add_photo(conn, os.path.join(party, "1.jpg"), [fa, fb], seed=1, taken=1.7e9,
                   hints=(("Alice", 0.05, 0.25, 0.25, 0.4), ("Bob", 0.35, 0.25, 0.25, 0.4)))
    p2 = add_photo(conn, os.path.join(party, "2.jpg"), [], seed=2, taken=1.7e9 + H)
    p3 = add_photo(conn, os.path.join(str(data_dir.lib), "later", "3.jpg"), [], seed=3, taken=1.7e9 + 7 * 24 * H)
    conn.commit()
    faces = dict(conn.execute("SELECT p.name, f.id FROM faces f JOIN persons p ON p.id=f.person_id"))
    red, suit = clothes(10), clothes(11)
    rows = [(p1, red, faces["Alice"]), (p1, suit, faces["Bob"]), (p2, near(red, 1), None), (p2, near(suit, 2), None),
            (p3, near(red, 3), None)]
    for pid, d, fid in rows:
        conn.execute("INSERT INTO bodies(photo_id, x, y, w, h, score, desc, face_id) VALUES(?,0,0,0.3,0.9,0.9,?,?)",
                     (pid, d.astype(np.float16).tobytes(), fid))
    conn.execute("UPDATE photos SET body_done=1")
    conn.commit()
    ids = [r[0] for r in conn.execute("SELECT id FROM bodies ORDER BY id")]
    yield conn, dict(p1=p1, p2=p2, p3=p3, bodies=ids, faces=faces)
    conn.close()


def suggestions(conn):
    return {(b, n) for b, n in conn.execute("SELECT s.body_id, p.name FROM body_suggestions s "
                                            "JOIN persons p ON p.id=s.person_id")}


def test_suggests_within_the_shoot_only(shoot):
    conn, ids = shoot
    assert bodies.suggest(conn) == 2
    b = ids["bodies"]
    assert suggestions(conn) == {(b[2], "Alice"), (b[3], "Bob")}      # not the red dress a week later
    reason = conn.execute("SELECT reason FROM body_suggestions WHERE body_id=?", (b[2],)).fetchone()[0]
    assert "same clothes" in reason and "60 min apart" in reason


def test_someone_already_on_the_photo_isnt_suggested_again(shoot):
    conn, ids = shoot
    store.tag_photos(conn, [ids["p2"]], store.get_or_create_person(conn, "Alice"))
    bodies.suggest(conn)
    assert {n for _b, n in suggestions(conn)} == {"Bob"}


def test_reject_is_remembered(shoot):
    conn, ids = shoot
    bodies.suggest(conn)
    bodies.reject(conn, [ids["bodies"][2]])
    bodies.suggest(conn)
    assert {n for _b, n in suggestions(conn)} == {"Bob"}


def test_confirm_tags_the_photo_or_names_the_face(shoot):
    conn, ids = shoot
    bodies.suggest(conn)
    bodies.confirm(conn, [ids["bodies"][2]])
    tagged = {r[0] for r in conn.execute("SELECT p.name FROM photo_people pp JOIN persons p ON p.id=pp.person_id "
                                         "WHERE pp.photo_id=?", (ids["p2"],))}
    assert tagged == {"Alice"}
    # an unclear face in the shoot gets the name itself
    fid = conn.execute("INSERT INTO faces(photo_id, x, y, w, h, score, px, emb, model, clarity) "
                       "VALUES(?, 0.6, 0.2, 0.1, 0.1, 0.8, 40, ?, 'sface', 0.1)",
                       (ids["p2"], unit(np.ones(128)).astype(np.float32).tobytes())).lastrowid
    conn.execute("UPDATE bodies SET face_id=? WHERE id=?", (fid, ids["bodies"][3]))
    conn.commit()
    bodies.suggest(conn)
    assert (ids["bodies"][3], "Bob") in suggestions(conn)
    bodies.confirm(conn, [ids["bodies"][3]])
    assert conn.execute("SELECT p.name, f.confirmed FROM faces f JOIN persons p ON p.id=f.person_id WHERE f.id=?",
                        (fid,)).fetchone() == ("Bob", 1)


def test_too_alike_two_people_no_suggestion(shoot):
    conn, ids = shoot
    # Bob changes into the same red dress as Alice: can't tell them apart
    b = ids["bodies"]
    conn.execute("UPDATE bodies SET desc=(SELECT desc FROM bodies WHERE id=?) WHERE id=?", (b[0], b[1]))
    conn.commit()
    bodies.suggest(conn)
    assert b[2] not in {x for x, _n in suggestions(conn)}


def test_photo_removed_takes_its_people_along(shoot):
    conn, ids = shoot
    bodies.suggest(conn)
    conn.execute("DELETE FROM photos WHERE id=?", (ids["p2"],))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM bodies WHERE photo_id=?", (ids["p2"],)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM body_suggestions").fetchone()[0] == 0


def test_rescan_reads_the_people_again(shoot):
    conn, ids = shoot
    import types
    path = conn.execute("SELECT path FROM photos WHERE id=?", (ids["p2"],)).fetchone()[0]
    store.store_photo(conn, path, 2.0, 10, types.SimpleNamespace(width=320, height=240, orientation=1,
                                                                 taken=1.7e9 + H, cover_t=None), [], settings={})
    assert conn.execute("SELECT body_done FROM photos WHERE id=?", (ids["p2"],)).fetchone()[0] == 0


# ------------------------------------------------------------------ the real models (if downloaded)
REAL = os.environ.get("FACESORT_BODY_MODELS")


@pytest.mark.skipif(not REAL, reason="set FACESORT_BODY_MODELS to a folder with the two person models")
def test_real_models_find_and_describe(monkeypatch):
    import cv2
    monkeypatch.setattr(bodies, "model_path", lambda k: os.path.join(REAL, bodies.MODELS[k][0]))
    bodies._MODELS[0] = None
    m = bodies.models()
    img = np.full((480, 640, 3), 200, np.uint8)
    cv2.rectangle(img, (100, 80), (220, 470), (40, 40, 160), -1)        # not a person: nothing found
    assert m.people(img) == [] or all(b[4] < 0.6 for b in m.people(img))
    d = m.appearance(img, [(100, 80, 120, 390, 1.0), (300, 80, 120, 390, 1.0)])
    assert d.shape == (2, 768) and np.allclose(np.linalg.norm(d, axis=1), 1, atol=1e-3)
