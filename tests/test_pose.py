"""Poses (1.46): the geometry from 17 body points; poses stored and offered as tags; rules can use them."""
import numpy as np

import pose
import rules
import store
from conftest import add_photo, unit

H = 1000


def body(**pts):
    """17 points (x, y, conf) - everything not given is unseen."""
    k = np.zeros((17, 3), np.float32)
    for name, (x, y) in pts.items():
        k[pose.KP[name]] = (x, y, 0.9)
    return k


STAND = dict(nose=(500, 100), l_eye=(490, 90), r_eye=(510, 90), l_shoulder=(460, 200), r_shoulder=(540, 200),
             l_hip=(470, 450), r_hip=(530, 450), l_knee=(470, 650), r_knee=(530, 650), l_ankle=(470, 850),
             r_ankle=(530, 850), l_wrist=(440, 420), r_wrist=(560, 420))


def test_geometry():
    assert {"Standing", "Full body"} <= pose.poses_of(body(**STAND), None, H)
    sit = dict(STAND, l_knee=(650, 470), r_knee=(700, 470), l_ankle=(660, 700), r_ankle=(710, 700))
    assert "Sitting" in pose.poses_of(body(**sit), None, H)
    kneel = dict(STAND, l_knee=(470, 650), r_knee=(530, 650), l_ankle=(300, 660), r_ankle=(360, 660))
    assert "Kneeling" in pose.poses_of(body(**kneel), None, H)
    lying = dict(nose=(100, 500), l_shoulder=(200, 480), r_shoulder=(200, 520), l_hip=(450, 480), r_hip=(450, 520),
                 l_knee=(650, 480), l_ankle=(850, 480))
    assert "Lying down" in pose.poses_of(body(**lying), None, H)
    arms = dict(STAND, l_wrist=(420, 50), r_wrist=(580, 60))
    assert "Arms raised" in pose.poses_of(body(**arms), None, H)
    back = {k: v for k, v in STAND.items() if k not in ("nose", "l_eye", "r_eye")}
    assert "Facing away" in pose.poses_of(body(**back), None, H)
    close = dict(nose=(500, 300), l_eye=(470, 260), r_eye=(530, 260), l_shoulder=(300, 700), r_shoulder=(700, 700))
    assert pose.poses_of(body(**close), None, H) == {"Close-up"}
    lunge = dict(STAND, l_knee=(650, 470), l_ankle=(660, 700))          # one leg each way: neither
    assert not {"Sitting", "Kneeling"} & pose.poses_of(body(**lunge), None, H)


def test_poses_as_tags_and_in_rules(library):
    c = library.conn
    pid = add_photo(c, "/lib/Pose/p.jpg", [unit(np.random.default_rng(7001).normal(size=128))], seed=7001)
    rid = rules.save_rule(c, {"cond": {"pose": "Sitting"}, "action": {"tag": "Seated"}, "existing": True})
    c.execute("INSERT INTO photo_pose(photo_id, pose) VALUES(?, 'Sitting')", (pid,))
    c.commit()
    assert pose.photo_poses(c, pid) == ["Sitting"]
    import content
    sc = content.TagScorer(c, ["Sitting", "Beach"])
    assert sc.suggest(pid, [], 10)[0] == "Sitting"
    store.tag_photo(c, pid, "Sitting", True)
    store.tag_photo(c, pid, "Sitting", False)                            # taken off: not offered again
    assert "Sitting" not in content.TagScorer(c, ["Sitting"]).suggest(pid, [], 10)
    rules.run(c, {})
    assert "Seated" in store.photo_tags(c, pid)
    c.execute("DELETE FROM photos WHERE id=?", (pid,))
    assert not c.execute("SELECT 1 FROM photo_pose WHERE photo_id=?", (pid,)).fetchone()
