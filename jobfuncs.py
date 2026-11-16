"""The background jobs (1.45: moved here from ui_common, with no Qt, so the helper processes in
helpers.py can run them as well as the window's background threads). Every job takes
(conn, *args, progress=..., cancelled=...) and returns something that can be pickled."""
import os
import time
import traceback

import bodies
import clip_search
import engine
import jobs
import known
import store

__all__ = ['dup_sets_job', 'misplaced_job', 'clip_index_job', 'clip_download_job', 'copies_job', 'bodies_job', 'bodies_download_job', 'find_dark_job', 'clarity_job', 'stats_job', 'collage_job', 'dup_check_job', 'scan_and_group', 'regroup_job', 'remember_job', 'attrs_job', 'content_job', 'subjects_job', 'content_learn_job', 'sex_learn_job', 'destinations_job', 'clarity_shard_job', 'ping_job', 'sleep_job', 'SUGGESTION_SIG', 'SHOOT_SIG']

def log(msg):
    try:
        with open(os.path.join(store.data_dir(), "facesort.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + str(msg) + "\n")
    except Exception:
        pass


def dup_sets_job(conn, progress, cancelled):
    sets = store.duplicate_sets(conn, check_new=False)
    return sets, store.DUP_UNCHECKED[0]


def misplaced_job(conn, dest, out, inc, ai, progress=None, cancelled=None):
    """(photos not where they belong, how many photos the next Sort into folders would move)"""
    return jobs.misplaced(conn, dest, inc, ai) if dest else [], jobs.pending_moves(conn, out, inc, ai)


def clip_index_job(conn, progress=lambda d, t, m: None, cancelled=lambda: False):
    return clip_search.index_photos(conn, progress, cancelled)


def clip_download_job(conn, progress=lambda d, t, m: None, cancelled=lambda: False):
    return clip_search.download(progress, cancelled)


def copies_job(conn, progress=None, cancelled=None):
    return store.names_from_copies(conn)


def bodies_job(conn, progress=lambda d, t, m: None, cancelled=lambda: False):
    """Find the people in photos not looked at yet, then work out the same-shoot suggestions."""
    n = bodies.analyze_photos(conn, progress, cancelled)
    if not cancelled():
        progress(0, 0, "Working out same-shoot suggestions…")
        bodies.suggest(conn, cancelled=cancelled)
    return n


def bodies_download_job(conn, progress=lambda d, t, m: None, cancelled=lambda: False):
    return bodies.download(progress, cancelled)


def find_dark_job(conn, progress=lambda d, t, m: None, cancelled=lambda: False):
    return jobs.find_dark_photos(conn, progress, cancelled)


def clarity_job(conn, progress=lambda d, t, m: None, cancelled=lambda: False):
    return store.read_missing_clarity(conn, lambda d, t: progress(d, t, f"Reading face clarity… {d:,} / {t:,}"),
                                      cancelled)


def stats_job(conn, progress=None, cancelled=lambda: False):
    return store.refresh_stats(conn)


def collage_job(conn, path, strength=0, t0=None, t1=None, progress=None, cancelled=lambda: False):
    return engine.video_collage(path, cancelled=cancelled, strength=strength, t0=t0, t1=t1)


def dup_check_job(conn, progress, cancelled):
    progress(0, 0, "Checking possible duplicates closely…")
    try:
        store.duplicate_sets(conn, progress=lambda d, t: progress(d, t, "Checking possible duplicates closely…"),
                             cancelled=cancelled)
    except Exception:
        log(traceback.format_exc())


def scan_and_group(conn, settings, progress, cancelled, videos_only=False, check_only=False, only=None, opts=None):
    o = opts or {}
    if o.get("mode", "both") != "new" and only is None and not check_only:
        # "look again" choices of the Scan window: those photos count as changed for this scan
        if o.get("nofaces_again"):
            conn.execute("UPDATE photos SET mtime=NULL WHERE error IS NULL AND nfaces=0 AND COALESCE(is_other, 0)=0")
            conn.commit()
        if o.get("dark_again"):
            jobs.find_dark_photos(conn, progress, cancelled)
    stats = jobs.scan_library(conn, settings, progress, cancelled, videos_only=videos_only, check_only=check_only,
                              only=only, opts=opts)
    if not cancelled() and (stats.get("scanned") or stats.get("removed") or not check_only):
        progress(0, 0, "Grouping faces…")
        store.regroup(conn, store.threshold(settings), progress=lambda d, t: progress(d, t, "Grouping faces…"),
                      cancelled=cancelled)
    stats["cancelled"] = cancelled()
    return stats


SUGGESTION_SIG = """SELECT COUNT(*), TOTAL(person_id), TOTAL(cluster), TOTAL(later_cluster)
                    FROM faces WHERE ignored IN (0, 5) AND confirmed=0"""


SHOOT_SIG = "SELECT COUNT(*), TOTAL(body_id), TOTAL(person_id) FROM body_suggestions"


def regroup_job(conn, threshold, progress, cancelled, same_shoot=True):
    """Returns True when the suggestions / groups came out different."""
    before = conn.execute(SUGGESTION_SIG).fetchone()
    conn.commit()
    store.regroup(conn, threshold, progress=lambda d, t: progress(d, t, "Updating suggestions…"), cancelled=cancelled,
                  sync=False)
    shoot_before = conn.execute(SHOOT_SIG).fetchone()
    if same_shoot and not cancelled() and conn.execute("SELECT 1 FROM bodies LIMIT 1").fetchone():
        bodies.suggest(conn, cancelled=cancelled)          # who's confirmed decides the same-shoot suggestions
    return (conn.execute(SUGGESTION_SIG).fetchone() != before or
            conn.execute(SHOOT_SIG).fetchone() != shoot_before)


def remember_job(conn, progress, cancelled):
    """Save who's who to the known-people file (and the Photos library's reference file)."""
    known.sync_from_library(conn)


# ---- 1.45: the jobs that used to be written inline in the window (lambdas can't go to a helper)
def attrs_job(conn, progress=lambda d, t, m: None, cancelled=lambda: False):
    import face_attrs
    return face_attrs.read_missing(conn, lambda d, t: progress(d, t, f"Reading face details… {d:,} / {t:,}"),
                                   cancelled)


def content_job(conn, progress=lambda d, t, m: None, cancelled=lambda: False):
    import content
    return content.read_missing(conn, lambda d, t: progress(d, t, f"Reading what's in photos… {d:,} / {t:,}"),
                                cancelled)


def subjects_job(conn, progress=None, cancelled=lambda: False):
    import content
    return content.update_subjects(conn)


def content_learn_job(conn, progress=None, cancelled=lambda: False):
    import content
    return content.learn_levels(conn, cancelled)


def sex_learn_job(conn, model, threshold, progress=None, cancelled=lambda: False):
    import face_attrs
    return face_attrs.learn(conn, model, threshold, lambda d, t: None, cancelled)


def destinations_job(conn, progress=None, cancelled=lambda: False):
    return store.photo_destinations(conn, "both")


def clarity_shard_job(conn, shard, progress=lambda d, t, m: None, cancelled=lambda: False):
    """One helper's share of the face clarity reading (faces with id % n == i)."""
    return store.read_missing_clarity(conn, lambda d, t: progress(d, t, f"Reading face clarity… {d:,} / {t:,}"),
                                      cancelled, shard=shard)


def ping_job(conn, write=0, hold=0.0, progress=lambda d, t, m: None, cancelled=lambda: False):
    """Checks a helper: (its process id, its role, photos in the library). write: also write that
    many times (holding the write lock hold seconds each) - the tests use it."""
    import helpers
    for i in range(int(write)):
        if cancelled():
            break
        with conn.write():
            conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('ping', ?)", (str(i),))
            if hold:
                time.sleep(hold)
        progress(i + 1, int(write), "")
    return os.getpid(), helpers.role(), conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]


def sleep_job(conn, seconds, progress=lambda d, t, m: None, cancelled=lambda: False):
    """Waits (stopping early when asked) - the tests use it."""
    end = time.monotonic() + seconds
    while time.monotonic() < end and not cancelled():
        time.sleep(0.02)
    return not cancelled()
