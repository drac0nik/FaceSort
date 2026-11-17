"""Rules for incoming photos (1.46)."""
import os

import rules
import store
from conftest import add_photo, unit
import numpy as np


def new_photo(c, name, seed, person=None, confirmed=1, conf=None):
    rng = np.random.default_rng(seed)
    pid = add_photo(c, os.path.abspath(os.path.join(os.sep, "lib", "In", f"{name}.jpg")),
                    [unit(rng.normal(size=128))], seed=seed)
    if person:
        c.execute("UPDATE faces SET person_id=?, confirmed=?, sugg_conf=? WHERE photo_id=?",
                  (person, confirmed, conf, pid))
    c.commit()
    return pid


def test_rules(library):
    c = library.conn
    anna = store.get_or_create_person(c, "Anna Rules")
    old = new_photo(c, "old", 5001, anna)
    rid = rules.save_rule(c, {"name": "Anna to trips", "cond": {"people": [anna], "people_names": ["Anna Rules"]},
                              "action": {"folder": ["Trips", "Anna"], "album": "Anna album", "later": False}})
    a = new_photo(c, "a", 5002, anna)                              # confirmed
    s95 = new_photo(c, "s95", 5003, anna, confirmed=0, conf=97)    # a suggestion, 97% sure
    s80 = new_photo(c, "s80", 5004, anna, confirmed=0, conf=80)    # not sure enough
    other = new_photo(c, "other", 5005)
    out = rules.run(c, {})
    assert out and out[0][1] == 2
    done = {p for (p,) in c.execute("SELECT photo_id FROM rule_applied WHERE rule_id=?", (rid,))}
    assert done == {a, s95}                                        # not the older photo, not 80%, not the other
    tree = store.folder_tree(c)
    cat = c.execute("SELECT category_id FROM photos WHERE id=?", (a,)).fetchone()[0]
    assert tree[cat][0] == "Anna" and tree[tree[cat][1]][0] == "Trips"
    aid = c.execute("SELECT id FROM albums WHERE name='Anna album'").fetchone()[0]
    assert {p for (p,) in c.execute("SELECT photo_id FROM album_photos WHERE album_id=?", (aid,))} == {a, s95}
    # once per photo: moved by hand afterwards, the rule leaves it alone
    c.execute("UPDATE photos SET category_id=NULL WHERE id=?", (a,))
    rules.run(c, {})
    assert c.execute("SELECT category_id FROM photos WHERE id=?", (a,)).fetchone()[0] is None
    # suggestions off: only confirmed
    rules.delete_rule(c, rid)
    rid2 = rules.save_rule(c, {"cond": {"people": [anna]}, "action": {"later": True}, "existing": True})
    rules.run(c, {"rules_suggestions": False})
    done = {p for (p,) in c.execute("SELECT photo_id FROM rule_applied WHERE rule_id=?", (rid2,))}
    assert old in done and a in done and s95 not in done and other not in done
    assert c.execute("SELECT sort_later FROM photos WHERE id=?", (old,)).fetchone()[0] == 1
    assert "Sort Later" in rules.describe(rules.all_rules(c)[0])
    # a lower threshold in Settings lets the 80% one in
    rules.run(c, {"rules_suggestion_pct": 75})
    assert c.execute("SELECT 1 FROM rule_applied WHERE rule_id=? AND photo_id=?", (rid2, s80)).fetchone()


def test_rule_folder_condition_and_order(library):
    c = library.conn
    rid = rules.save_rule(c, {"cond": {"folder": os.path.abspath(os.path.join(os.sep, "lib", "In"))},
                              "action": {"tag": "Incoming"}})
    rid2 = rules.save_rule(c, {"cond": {}, "action": {"tag": "Any"}})
    p = new_photo(c, "f", 5011)
    rules.run(c, {})
    assert set(store.photo_tags(c, p)) == {"Incoming", "Any"}
    rules.move_rule(c, rid2, -1)
    assert [r["id"] for r in rules.all_rules(c)] == [rid2, rid]


def test_rule_window(library, qapp):
    from rules_ui import RuleDialog, RulesEditor
    c = library.conn
    d = RuleDialog(None, c, people=["Anna W"], folders=["Trips"], tags=["Beach"])
    assert not d.ok.isEnabled()                                    # nothing to do yet
    d.people.setText("Anna W")
    d.to_folder.setCurrentText("Trips › Anna")
    d.later.setChecked(True)
    assert d.ok.isEnabled() and "Sort Later" in d.words.text()
    v = d.value()
    assert v["action"] == {"folder": ["Trips", "Anna"], "later": True} and v["cond"]["people_names"] == ["Anna W"]
    rid = rules.save_rule(c, v)
    ed = RulesEditor(c, {"rules_suggestion_pct": 90})
    assert ed.list.count() == 1 and ed.pct.value() == 90
    s = {}
    ed.save_to(s)
    assert s == {"rules_suggestions": True, "rules_suggestion_pct": 90}
    rules.delete_rule(c, rid)
