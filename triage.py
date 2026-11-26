"""Biggest jobs first (1.59): what would sort the most photos, so a big import gets done in the fewest
steps.

- impact of an unnamed group = photos not sorted yet that naming it would sort;
- impact of a person's suggestions = photos not sorted yet that confirming them would sort;
- one-off faces: faces seen once or twice, all in one folder, that nobody is suggested for - on a big
  import, mostly passers-by. They can be set aside as strangers in one go (and brought back from the
  Strangers list)."""
import os

ONE_OFF_MAX = 2          # a look-alike group this small, all from one folder, counts as a one-off


def group_impact(conn, sorted_sql):
    """{group: photos not sorted yet with a face from it}."""
    return dict(conn.execute(f"""SELECT f.cluster, COUNT(DISTINCT f.photo_id) FROM faces f JOIN photos ph
                                 ON ph.id = f.photo_id WHERE f.cluster IS NOT NULL AND f.person_id IS NULL
                                 AND f.ignored = 0 AND ph.sort_later = 0 AND NOT {sorted_sql}
                                 GROUP BY f.cluster"""))


def person_impact(conn, sorted_sql):
    """{person: photos not sorted yet with a suggestion for them}."""
    return dict(conn.execute(f"""SELECT f.person_id, COUNT(DISTINCT f.photo_id) FROM faces f JOIN photos ph
                                 ON ph.id = f.photo_id WHERE f.person_id IS NOT NULL AND f.confirmed = 0
                                 AND f.ignored = 0 AND ph.sort_later = 0 AND NOT {sorted_sql}
                                 GROUP BY f.person_id"""))


def tasks(conn, sorted_sql):
    """[(photos it would sort, key, what to do)] biggest first."""
    out = []
    names = dict(conn.execute("SELECT id, name FROM persons"))
    for pid, n in person_impact(conn, sorted_sql).items():
        if n and pid in names:
            out.append((n, ("person", pid), f"confirm {names[pid]}'s suggestions"))
    for cl, n in group_impact(conn, sorted_sql).items():
        if n:
            out.append((n, ("group", cl), "name this group"))
    out.sort(key=lambda t: -t[0])
    return out


def one_offs(conn):
    """Face ids seen only once or twice, all in one folder, with no suggestion - not drawn by you."""
    rows = conn.execute("""SELECT f.id, f.cluster, ph.path FROM faces f JOIN photos ph ON ph.id = f.photo_id
                           WHERE f.person_id IS NULL AND f.confirmed = 0 AND f.ignored = 0
                             AND COALESCE(f.manual, 0) = 0 AND ph.sort_later = 0""").fetchall()
    groups = {}
    out = []
    for fid, cl, path in rows:
        if cl is None:
            out.append(fid)                    # (not like any other face: seen once)
        else:
            groups.setdefault(cl, []).append((fid, os.path.dirname(path)))
    for members in groups.values():
        if len(members) <= ONE_OFF_MAX and len({d for _f, d in members}) == 1:
            out.extend(f for f, _d in members)
    return out
