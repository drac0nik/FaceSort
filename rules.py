"""Rules for incoming photos (1.46): "when a new photo ... then ...".

When (every part you fill in must match):
- folder: it came from inside this folder
- people: it has these people on it (any / all of them) - a confirmed face or whole-photo tag,
  or (Settings, on by default) a suggestion at least 95% sure
- subject: what it is (No people: food, landscapes, text and screenshots, animals, other)
- tag: it has this tag
- pose: someone on it is in this pose (when pose reading is on)
Then (any of):
- folder: put it in this folder (a > b > c)
- album: add it to this album
- tag: give it this tag
- later: put it in Sort Later

A rule acts on photos added after it was made (or also on the ones already in the library, if
you ticked that when making it), and on each photo once: change something by hand afterwards
and the rule leaves it alone. Rules run after a scan and after the suggestions are updated.
Everything a rule does goes into the history (and can be undone there)."""
import json
import os
import time

import store

ACTION_KEYS = ("folder", "album", "tag", "later")


def settings_threshold(settings):
    """None = rules act on confirmed people only; else the suggestion strength they accept."""
    s = settings or {}
    if not s.get("rules_suggestions", True):
        return None
    try:
        return max(50.0, min(100.0, float(s.get("rules_suggestion_pct", 95))))
    except (TypeError, ValueError):
        return 95.0


def all_rules(conn):
    """[{"id", "name", "enabled", "ord", "cond", "action", "created", "since"}] in order."""
    out = []
    for rid, name, en, ord_, cond, action, created, existing in conn.execute(
            "SELECT id, name, enabled, ord, cond, action, created, existing FROM rules ORDER BY ord, id"):
        out.append({"id": rid, "name": name or "", "enabled": bool(en), "ord": ord_,
                    "cond": json.loads(cond or "{}"), "action": json.loads(action or "{}"),
                    "created": created, "since": existing})
    return out


def save_rule(conn, rule):
    """Add or change a rule. "existing": also the photos already in the library (else only photos
    added from now on: the newest photo id now is remembered)."""
    cond, action = json.dumps(rule.get("cond") or {}), json.dumps(rule.get("action") or {})
    if rule.get("id"):
        conn.execute("UPDATE rules SET name=?, enabled=?, cond=?, action=? WHERE id=?",
                     (rule.get("name", ""), int(rule.get("enabled", True)), cond, action, rule["id"]))
        rid = rule["id"]
    else:
        since = 0 if rule.get("existing") else (conn.execute("SELECT COALESCE(MAX(id), 0) FROM photos").fetchone()[0])
        ord_ = conn.execute("SELECT COALESCE(MAX(ord), 0) + 1 FROM rules").fetchone()[0]
        rid = conn.execute("INSERT INTO rules(name, enabled, ord, cond, action, created, existing) VALUES(?,?,?,?,?,?,?)",
                           (rule.get("name", ""), int(rule.get("enabled", True)), ord_, cond, action, time.time(),
                            since)).lastrowid
    conn.commit()
    return rid


def delete_rule(conn, rid):
    conn.execute("DELETE FROM rules WHERE id=?", (rid,))
    conn.execute("DELETE FROM rule_applied WHERE rule_id=?", (rid,))
    conn.commit()


def move_rule(conn, rid, step):
    rows = [r["id"] for r in all_rules(conn)]
    if rid not in rows:
        return
    i = rows.index(rid)
    j = max(0, min(len(rows) - 1, i + step))
    rows[i], rows[j] = rows[j], rows[i]
    conn.executemany("UPDATE rules SET ord=? WHERE id=?", [(n, r) for n, r in enumerate(rows)])
    conn.commit()


def describe(rule, conn=None):
    """A rule in words: "From Camera with Anna → folder Trips › Beach, Sort Later"."""
    c, a = rule.get("cond") or {}, rule.get("action") or {}
    when = []
    if c.get("folder"):
        when.append(f"from {os.path.basename(c['folder'].rstrip(chr(92) + '/')) or c['folder']}")
    if c.get("people"):
        names = c.get("people_names") or [str(p) for p in c["people"]]
        when.append(("with " if c.get("people_mode") != "all" or len(names) == 1 else "with all of ")
                    + ", ".join(names))
    if c.get("subject"):
        when.append(f"of {c['subject']}")
    if c.get("tag"):
        when.append(f"tagged {c['tag']}")
    if c.get("pose"):
        when.append(f"pose {c['pose']}")
    then = []
    if a.get("folder"):
        then.append("folder " + " › ".join(a["folder"]))
    if a.get("album"):
        then.append(f"album {a['album']}")
    if a.get("tag"):
        then.append(f"tag {a['tag']}")
    if a.get("later"):
        then.append("Sort Later")
    return (" ".join(when) or "Any new photo") + " → " + (", ".join(then) or "nothing")


def matching(conn, rule, threshold, photo_ids=None):
    """Photo ids the rule's "when" matches, among the photos it may act on (newer than the rule,
    or all if it was made for existing photos too; not done by it already)."""
    c = rule.get("cond") or {}
    where = ["ph.error IS NULL", "ph.discarded=0", "COALESCE(ph.sort_later, 0) != 2", "ph.id > ?",
             "ph.id NOT IN (SELECT photo_id FROM rule_applied WHERE rule_id=?)"]
    args = [int(rule.get("since") or 0), rule["id"]]
    if photo_ids is not None:
        ids = list(photo_ids)
        if not ids:
            return []
        where.append(f"ph.id IN ({','.join('?' * len(ids))})")
        args += ids
    people = [int(p) for p in c.get("people") or []]
    sugg = "" if threshold is None else f" OR (f.confirmed=0 AND COALESCE(f.sugg_conf, 0) >= {float(threshold)})"
    has = ("(EXISTS (SELECT 1 FROM faces f WHERE f.photo_id=ph.id AND f.person_id=? AND f.ignored=0 "
           f"AND (f.confirmed=1{sugg})) OR EXISTS (SELECT 1 FROM photo_people pp WHERE pp.photo_id=ph.id "
           "AND pp.person_id=?))")
    if people:
        parts = [has] * len(people)
        where.append("(" + (" AND " if c.get("people_mode") == "all" else " OR ").join(parts) + ")")
        for p in people:
            args += [p, p]
    if c.get("subject"):
        where.append("EXISTS (SELECT 1 FROM photo_content pc WHERE pc.photo_id=ph.id AND pc.subject=?)")
        args.append(c["subject"])
    if c.get("tag"):
        where.append("""EXISTS (SELECT 1 FROM album_photos ap JOIN albums al ON al.id=ap.album_id
                        WHERE ap.photo_id=ph.id AND al.name=?)""")
        args.append(c["tag"])
    if c.get("pose"):
        where.append("EXISTS (SELECT 1 FROM photo_pose po WHERE po.photo_id=ph.id AND po.pose=?)")
        args.append(c["pose"])
    try:
        rows = conn.execute(f"SELECT ph.id, ph.path FROM photos ph WHERE {' AND '.join(where)}", args).fetchall()
    except Exception:
        return []                       # (a table a condition needs isn't there yet: nothing matches)
    if c.get("folder"):
        top = os.path.normcase(store.norm_path(c["folder"])).rstrip("\\/") + os.sep
        rows = [r for r in rows if os.path.normcase(r[1]).startswith(top)]
    return [r[0] for r in rows]


def act(conn, rule, photo_ids):
    """Do the rule's "then" to these photos. Returns what was done (for the history)."""
    a = rule.get("action") or {}
    if not photo_ids:
        return {}
    done = {"rule": rule["id"], "photos": list(photo_ids)}
    if a.get("folder"):
        fid = store.folder_chain(conn, [x for x in a["folder"] if x])
        if fid:
            before = dict(conn.execute(f"SELECT id, category_id FROM photos WHERE id IN "
                                       f"({','.join('?' * len(photo_ids))})", list(photo_ids)))
            store.set_photo_folder(conn, list(photo_ids), fid)
            done["folder"] = {"to": fid, "before": before}
    if a.get("album"):
        aid = store.create_album(conn, a["album"])
        now = time.time()
        added = [p for p in photo_ids if not conn.execute(
            "SELECT 1 FROM album_photos WHERE album_id=? AND photo_id=?", (aid, p)).fetchone()]
        conn.executemany("INSERT OR IGNORE INTO album_photos(album_id, photo_id, added) VALUES(?,?,?)",
                         [(aid, p, now) for p in added])
        done["album"] = {"id": aid, "added": added}
    if a.get("tag"):
        for p in photo_ids:
            store.tag_photo(conn, p, a["tag"], True)
        done["tag"] = a["tag"]
    if a.get("later"):
        were = [p for p in photo_ids if not conn.execute("SELECT sort_later FROM photos WHERE id=?", (p,)).fetchone()[0]]
        if were:
            store._hide_for_later(conn, were)
        done["later"] = were
    now = time.time()
    conn.executemany("INSERT OR IGNORE INTO rule_applied(rule_id, photo_id, at) VALUES(?,?,?)",
                     [(rule["id"], p, now) for p in photo_ids])
    conn.commit()
    return done


def run(conn, settings, progress=lambda d, t, m: None, cancelled=lambda: False, photo_ids=None):
    """Every rule that's on, in order. Returns [(rule name or words, photos it acted on)]."""
    thr = settings_threshold(settings)
    out = []
    rules = [r for r in all_rules(conn) if r["enabled"]]
    for n, rule in enumerate(rules, 1):
        if cancelled():
            break
        progress(n, len(rules), f"Rules… {n} / {len(rules)}")
        ids = matching(conn, rule, thr, photo_ids)
        if ids:
            import history
            label = f"Rule: {rule['name'] or describe(rule)} ({len(ids)} photo{'s' if len(ids) != 1 else ''})"
            faces = [r[0] for r in conn.execute(f"SELECT id FROM faces WHERE photo_id IN ({','.join('?' * len(ids))})",
                                                ids)]
            snap = history.Snapshot(conn, label, faces=faces, photos=ids)
            with history.paused():
                act(conn, rule, ids)
            try:
                data = snap.finish()
                if data:
                    history.record(conn, "rule", label, data)
            except Exception:
                pass
            out.append((rule["name"] or describe(rule), len(ids)))
    return out
