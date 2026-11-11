"""Long-running jobs (scan, tag writing, export). UI-independent so they can be tested."""
import csv
import filecmp
import hashlib
import os
import re
import shutil
import time

import numpy as np
from concurrent.futures import ThreadPoolExecutor, FIRST_COMPLETED, wait

import aidetect
import companions
import convert
import engine
import known
import store
import xmp

SKIP_DIRS = {"$recycle.bin", "system volume information", "@eadir", ".thumbnails", "__macosx"}


_last_commit = [0.0]


def _tick(conn, every=0.0):
    """Save what a long job has just written straight away, so the library is never locked while
    the job works on the next file (you can keep tagging, renaming... while a scan or a check
    runs). (It used to save at most every half second - but then a slow next file, a long
    video say, kept the library locked the whole time it took, and tagging gave "database is
    locked".) Saving is cheap: the library runs in WAL mode with synchronous=NORMAL."""
    now = time.monotonic()
    if conn.in_transaction and now - _last_commit[0] >= every:
        conn.commit()
        _last_commit[0] = now


def iter_photos(folder):
    for path, kind, _st in iter_files(folder):
        yield path


# system files are never listed as "other files" (hidden files are)
SYSTEM_NAMES = {"thumbs.db", "desktop.ini", ".ds_store", "ehthumbs.db", "ehthumbs_vista.db", "iconcache.db",
                "folder.jpg.db", "pagefile.sys", "hiberfil.sys", "swapfile.sys", ".localized", "icon\r"}
FILE_ATTRIBUTE_SYSTEM = 0x4


def media_kind(path):
    ext = os.path.splitext(path)[1].lower()
    return "photo" if ext in engine.IMAGE_EXTS else "video" if ext in engine.VIDEO_EXTS else "other"


def iter_files(folder, others=False, exclude=()):
    """(path, "photo" | "video" | "other", os.stat result) for every file to scan in folder.
    others: also list other files - everything except system files and the photos' own extra
    files (.xmp tags, Takeout .json, .aae, .thm), which travel with their photo anyway.
    exclude: folder names (any depth) or full folder paths to leave out."""
    ex_names = {e.lower() for e in exclude if e and not os.path.isabs(e)}
    ex_paths = [os.path.normcase(store.norm_path(e)).rstrip("\\/") for e in exclude if e and os.path.isabs(e)]
    for root, dirs, files in os.walk(folder):
        keep = []
        for d in dirs:
            ld = d.lower()
            if d.startswith((".", "$")) or ld in SKIP_DIRS or ld in ex_names:
                continue
            if ex_paths and os.path.normcase(os.path.join(root, d)) in ex_paths:
                continue
            keep.append(d)
        dirs[:] = keep
        media_stems, media_names = set(), set()
        if others:
            for fn in files:
                if media_kind(fn) != "other":
                    media_stems.add(os.path.splitext(fn)[0].lower())
                    media_names.add(fn.lower())
        for fn in files:
            kind = media_kind(fn)
            if fn.startswith("._"):
                continue
            if kind == "other":
                if not others:
                    continue
                lf = fn.lower()
                stem, ext = os.path.splitext(lf)
                if lf in SYSTEM_NAMES or lf.endswith((".part", ".part.mp4", ".tmp.fs")):
                    continue
                if ext in (".xmp", ".aae", ".thm") and (stem in media_stems or stem.split(".")[0] in media_stems):
                    continue                                   # a photo's own tags / edits / thumbnail
                if ext == ".json" and any(lf.startswith(m) for m in media_names):
                    continue                                   # Google Takeout's details for a photo
            p = os.path.join(root, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            if kind == "other" and getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_SYSTEM:
                continue
            yield store.norm_path(p), kind, st


def scan_options(settings, opts=None):
    """The choices for one scan (the Scan window), filled in with what older scans did."""
    vm = settings.get("video_mode", "after")
    o = {"mode": "both", "folders": None, "limit": settings.get("import_limit", 0) or 0, "order": "folder",
         "videos": vm != "separate", "video_mode": "with" if vm == "with" else "after", "others": False,
         "min_kb": 0, "exclude": [], "missing": "remove", "nofaces_again": False, "dark_again": False,
         "dupes_after": False, "shoot_after": True, "light": False}
    o.update({k: v for k, v in (opts or {}).items() if k in o})
    try:
        o["limit"] = max(0, int(o["limit"] or 0))
    except (TypeError, ValueError):
        o["limit"] = 0
    try:
        o["min_kb"] = max(0, int(o["min_kb"] or 0))
    except (TypeError, ValueError):
        o["min_kb"] = 0
    return o


def _work(path, min_face, use_gpu, det_thr=engine.SCORE_THRESHOLD, sideways=True, model="sface"):
    try:
        eng = engine.thread_engine(min_face, use_gpu, det_thr, sideways, model)
        try:
            img, faces = eng.process(path)
        except Exception as e:
            if eng.gpu is None:
                raise
            # GPU failed mid-scan (driver reset, out of memory...): carry on with the CPU
            engine.disable_gpu(f"stopped working during the scan ({e})")
            img, faces = engine.thread_engine(min_face, use_gpu, det_thr, sideways, model).process(path)
        try:
            extras = (engine.dhash(img.bgr) if img.bgr is not None else None,) + aidetect.label_check(path)
        except Exception:
            extras = (None, 0, None)
        img.bgr = None  # don't keep the pixels around
        try:
            hints = xmp.read_face_hints(path, img.orientation)
        except Exception:
            hints = []
        try:
            hints = list(hints) + list(known.face_names_for(path))   # faces you confirmed before
        except Exception:
            pass
        try:
            marks = known.marks_for(path)
        except Exception:
            marks = []
        names = []
        for src in (xmp.read_photo_people, known.photo_tags_for):
            try:
                for n in src(path):
                    if n.casefold() not in {x.casefold() for x in names}:
                        names.append(n)
            except Exception:
                pass
        try:
            # Google Takeout: the .json next to the photo has the real date taken and the people
            t_taken, t_people = companions.takeout_info(path)
            for n in t_people:
                if n.casefold() not in {x.casefold() for x in names}:
                    names.append(n)
            if t_taken and not getattr(img, "taken", None):
                img.taken = t_taken
        except Exception:
            pass
        category = None
        # the known file is kept up to date continuously; photo tags only when you Write tags
        for src in (known.category_for, xmp.read_category):
            try:
                category = src(path)
            except Exception:
                category = None
            if category:
                break
        try:
            keywords = xmp.read_keywords(path)
        except Exception:
            keywords = ([], [])
        try:
            tmarks, trejects = known.tattoo_marks_for(path), known.tattoo_rejects_for(path)
        except Exception:
            tmarks, trejects = [], []
        try:
            manual = known.manual_for(path)
        except Exception:
            manual = []
        try:
            pfolders = known.person_folders_for(path)
        except Exception:
            pfolders = []
        return path, img, faces, (hints, marks, names, category, keywords, tmarks, trejects, manual, pfolders, extras), None
    except Exception as e:  # unreadable / corrupt file
        return path, None, None, None, e


def find_dark_photos(conn, progress=lambda done, total, msg: None, cancelled=lambda: False):
    """Photos scanned before the second look at dark photos existed (1.36): measure how bright each
    is (a quick small read) and mark the dark ones to be scanned again. Returns how many."""
    rows = conn.execute("""SELECT id, path, bright FROM photos WHERE COALESCE(dark_seen, 0)=0 AND is_video=0
                           AND error IS NULL""").fetchall()
    dark, light, measured = [], [], []
    for n, (pid, path, bright) in enumerate(rows, 1):
        if cancelled():
            break
        if n % 50 == 0 or n == len(rows):
            progress(n, len(rows), f"Finding dark photos… {n:,} / {len(rows):,}")
        try:
            img = engine.load_image(path, max_side=320)
            mean, p30 = engine.brightness(img.bgr)
        except Exception:
            light.append(pid)
            continue
        measured.append((round(mean, 4), pid))
        (dark if engine.is_dark(mean, p30) else light).append(pid)
        if len(measured) >= 500:
            conn.executemany("UPDATE photos SET bright=? WHERE id=?", measured)
            measured = []
    if measured:
        conn.executemany("UPDATE photos SET bright=? WHERE id=?", measured)
    for i in range(0, len(light), 900):
        part = light[i:i + 900]
        conn.execute(f"UPDATE photos SET dark_seen=1 WHERE id IN ({','.join('?' * len(part))})", part)
    for i in range(0, len(dark), 900):          # scanned again by the next Scan (names are kept)
        part = dark[i:i + 900]
        conn.execute(f"UPDATE photos SET mtime=NULL WHERE id IN ({','.join('?' * len(part))})", part)
    conn.commit()
    return len(dark)


def scan_library(conn, settings, progress=lambda done, total, msg: None, cancelled=lambda: False,
                 videos_only=False, check_only=False, only=None, opts=None):
    """Scan new/changed photos and videos.
    settings["video_mode"]: "with" (videos together with photos), "after" (photos first, then
    videos) or "separate" (videos only when asked for: videos_only=True).
    settings["import_limit"]: at most this many files per scan (0 = no limit); the rest wait
    for the next Scan. check_only: find what's new but import nothing (removed files are
    still tidied up). only: scan just these files again (right-click > Rescan), nothing else.
    opts: the Scan window's choices (scan_options): new files / check files already in the
    library / both, which folders, how many, in what order, videos, other files, small pictures
    to skip, folders to leave out, what to do with missing files."""
    o = scan_options(settings, opts)
    stats = {"scanned": 0, "faces": 0, "errors": 0, "removed": 0, "total": 0,
             "waiting": 0, "waiting_videos": 0, "videos": 0, "others": 0, "missing": [], "skipped_small": 0}
    all_folders = [store.norm_path(f) for f in settings.get("folders", [])]
    folders = list(all_folders)
    if o["folders"]:
        chosen = {os.path.normcase(store.norm_path(f)) for f in o["folders"]}
        folders = [f for f in folders if os.path.normcase(f) in chosen]
    engine.DARK_SECOND_LOOK = bool(settings.get("dark_second_look", True))

    progress(0, 0, "Looking for photos…")
    found = {}
    if only is not None:
        for p in only:
            try:
                st = os.stat(p)
            except OSError:
                continue
            found[p] = (round(st.st_mtime, 3), st.st_size)
        folders = []
    for p in ([] if only is not None else settings.get("files", [])):          # single files you dropped in
        p = store.norm_path(p)
        try:
            st = os.stat(p)
        except OSError:
            continue
        found[p] = (round(st.st_mtime, 3), st.st_size)
    kinds = {}
    for folder in folders:
        if not os.path.isdir(folder):
            continue  # drive unplugged? keep its photos in the library
        for p, kind, st in iter_files(folder, others=o["others"], exclude=o["exclude"]):
            found[p] = (round(st.st_mtime, 3), st.st_size)
            kinds[p] = kind
            if len(found) % 500 == 0:
                progress(0, 0, f"Looking for photos… {len(found):,} found")
                if cancelled():
                    return stats

    known, known_kind = {}, {}
    for p, m, sz, vid, oth in conn.execute("SELECT path, mtime, size, is_video, is_other FROM photos"):
        known[p] = (round(m or 0, 3), sz)
        known_kind[p] = "other" if oth else "video" if vid else "photo"
    # forget photos that were deleted from folders that are still reachable
    gone = []
    checking = o["mode"] in ("both", "verify") and only is None
    ex_names = {e.lower() for e in o["exclude"] if e and not os.path.isabs(e)}
    for p in (known if checking else ()):
        if p in found:
            continue
        k = known_kind[p]
        if (k == "other" and not o["others"]) or (k == "video" and not o["videos"]):
            continue                                 # not looked for this time: left as they are
        if ex_names and any(part.lower() in ex_names for part in p.split(os.sep)[:-1]):
            continue                                 # in a folder left out of this scan
        top = next((f for f in all_folders if p.startswith(f.rstrip("\\/") + os.sep)), None)
        if top is not None and top not in folders:
            continue                                 # in a library folder not chosen this time
        if top is None and p in {store.norm_path(f) for f in settings.get("files", [])}:
            top = os.path.dirname(p)                 # a dropped file: gone if its folder is still there
        if top is None or os.path.isdir(top):
            gone.append(p)
    if o["missing"] == "keep":
        stats["missing"] = sorted(gone)              # kept in the library, listed after the scan
    else:
        for p in gone:
            conn.execute("DELETE FROM photos WHERE path=?", (p,))
        stats["removed"] = len(gone)
    conn.commit()

    if only is not None:
        todo = list(found)
    else:
        todo = [p for p, sig in found.items()
                if known.get(p) != sig and (o["mode"] == "both" or (o["mode"] == "new") == (p not in known))]
    # other files: only recorded (nothing to read in them)
    other = [p for p in todo if (kinds.get(p) or media_kind(p)) == "other"]
    if other and not check_only:
        for n, p in enumerate(other, 1):
            store.store_other(conn, p, *found[p])
            if n % 500 == 0:
                progress(n, len(other), f"Recording other files… {n:,} / {len(other):,}")
                conn.commit()
        conn.commit()
        stats["others"] = len(other)
    oset = set(other)
    todo = [p for p in todo if p not in oset]
    if o["min_kb"]:                                   # tiny pictures (icons, web thumbnails): not imported
        small = {p for p in todo if p not in known and kinds.get(p) == "photo" and found[p][1] < o["min_kb"] * 1024}
        stats["skipped_small"] = len(small)
        todo = [p for p in todo if p not in small]
    if o["order"] in ("newest", "oldest"):
        todo.sort(key=lambda p: found[p][0], reverse=o["order"] == "newest")
    mode = ("separate" if not o["videos"] else o["video_mode"]) if only is None else "with"
    rules = store.drop_rules(conn)
    # what you dragged in is always imported in full and first (no limit, videos included)
    dropped = [p for p in todo if rules and store.rules_matching(rules, p)] if not check_only else []
    dset = set(dropped)
    todo = [p for p in todo if p not in dset]
    if videos_only:
        todo = [p for p in todo if engine.is_video(p)]
    elif mode == "separate":
        held = [p for p in todo if engine.is_video(p)]
        stats["waiting_videos"] = len(held)
        todo = [p for p in todo if not engine.is_video(p)]
    if mode == "after" or videos_only:
        todo.sort(key=engine.is_video)          # every photo before any video (stable: folder order kept)
        dropped.sort(key=engine.is_video)
    limit = o["limit"]
    if only is not None:
        limit_rest = []
    elif check_only:
        limit_rest, todo = todo, []
    elif limit and len(todo) > limit:
        limit_rest, todo = todo[limit:], todo[:limit]
    else:
        limit_rest = []
    todo = dropped + todo
    stats["waiting"] = len(limit_rest)
    stats["waiting_videos"] += sum(1 for p in limit_rest if engine.is_video(p))
    stats["total"] = len(todo)
    stats["videos"] = sum(1 for p in todo if engine.is_video(p))
    if not todo:
        if rules and not check_only:
            store.clear_drop_rules(conn)
        if settings.get("use_gpu", True):
            engine.get_gpu(True)
        read_existing_tags(conn, settings, progress, cancelled)
        if not check_only:
            check_extras(conn, settings, progress, cancelled)
        return stats

    min_face = int(settings.get("min_face_px", 32))
    threads = 1 if o["light"] else max(1, int(settings.get("threads", 2)))
    use_gpu = bool(settings.get("use_gpu", True))
    det_thr = float(settings.get("detect_threshold", engine.SCORE_THRESHOLD))
    sideways = bool(settings.get("look_sideways", True))
    model = settings.get("engine", "sface")
    if model in ("arcface", "adaface"):
        import engine_hq
        if not engine_hq.available(model):
            model = "sface"
    if store.get_model(conn) != model:
        store.set_model(conn, model)
    if use_gpu and model == "sface":
        progress(0, 0, "Starting the GPU…")
        engine.get_gpu(True)
    done = 0
    it = iter(todo)
    with ThreadPoolExecutor(max_workers=threads) as ex:
        pending = set()
        for _ in range(threads * 3):
            p = next(it, None)
            if p is None:
                break
            pending.add(ex.submit(_work, p, min_face, use_gpu, det_thr, sideways, model))
        while pending:
            finished, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
            for fut in finished:
                path, img, faces, hints, err = fut.result()
                mtime, size = found[path]
                if err is not None:
                    store.store_error(conn, path, mtime, size, err)
                    stats["errors"] += 1
                else:
                    hints_, marks_, names_, cat_, kw_, tmarks_, trej_, manual_, pf_, ex_ = hints
                    pid = store.store_photo(conn, path, mtime, size, img, faces, hints_, marks_, names_, cat_,
                                            keywords=kw_, tattoo_marks=tmarks_, settings=settings, model=model,
                                            manual_faces=manual_, person_folders=pf_)
                    conn.execute("UPDATE photos SET phash=?, ai=CASE WHEN ?=1 THEN 1 ELSE ai END, "
                                 "ai_info=COALESCE(?, ai_info), extras_read=1 WHERE id=?", (ex_[0], ex_[1], ex_[2], pid))
                    if rules:
                        store.apply_drop_rules(conn, pid, path, rules)
                    for name in trej_:
                        conn.execute("INSERT OR IGNORE INTO tattoo_rejects(photo_id, person_id) VALUES(?,?)",
                                     (pid, store.get_or_create_person(conn, name)))
                    stats["faces"] += len(faces)
                done += 1
                stats["scanned"] = done
                _tick(conn)
                progress(done, len(todo), ("Video · " if engine.is_video(path) else "") + path)
                if not cancelled():
                    p = next(it, None)
                    if p is not None:
                        pending.add(ex.submit(_work, p, min_face, use_gpu, det_thr, sideways, model))
    conn.commit()
    if not cancelled():
        if rules:
            store.clear_drop_rules(conn)          # everything dropped in has been imported and tagged
        read_existing_tags(conn, settings, progress, cancelled)
        check_extras(conn, settings, progress, cancelled)
        try:
            stats["copies_named"] = store.names_from_copies(conn)     # copies of group photos keep their names
        except Exception:
            pass
    return stats


def check_extras(conn, settings, progress=lambda d, t, m: None, cancelled=lambda: False):
    """Once per photo: the picture fingerprint (duplicates) and AI labels for photos scanned by
    older versions, then (if turned on) the AI detector. New photos get these while scanning."""
    rows = conn.execute("SELECT id, path, is_video FROM photos WHERE extras_read=0 AND error IS NULL").fetchall()
    for n, (pid, path, video) in enumerate(rows, 1):
        if cancelled():
            break
        if n % 25 == 0 or n == len(rows):
            progress(n, len(rows), "Checking for duplicates and AI labels…")
        ph = None
        if not video:
            try:
                ph = engine.dhash(engine.load_image(path, max_side=256).bgr)
            except Exception:
                ph = None
        try:
            ai, info = aidetect.label_check(path)
        except Exception:
            ai, info = 0, None
        conn.execute("UPDATE photos SET phash=?, ai=CASE WHEN ?=1 THEN 1 ELSE ai END, ai_info=COALESCE(?, ai_info), "
                     "extras_read=1 WHERE id=?", (ph, ai, info, pid))
        _tick(conn)
    conn.commit()
    if not cancelled():           # close look at pictures that look alike, so the Duplicates list opens at once
        try:
            store.duplicate_sets(conn, progress=lambda d, t: progress(d, t, "Checking possible duplicates closely…"),
                                 cancelled=cancelled)
        except Exception:
            pass
    if settings.get("ai_detector") and aidetect.detector_available() and not cancelled():
        rows = conn.execute("""SELECT id, path FROM photos WHERE error IS NULL AND is_video=0 AND ai<>1
                               AND ai_score IS NULL AND COALESCE(is_other, 0)=0""").fetchall()
        if rows:
            try:
                det = aidetect.Detector()
            except Exception:
                return
            for n, (pid, path) in enumerate(rows, 1):
                if cancelled():
                    break
                if n % 10 == 0 or n == len(rows):
                    progress(n, len(rows), "AI detector: looking at photos…")
                try:
                    sc = det.score(engine.load_image(path, max_side=900).bgr)
                except Exception:
                    sc = -1.0
                conn.execute("UPDATE photos SET ai_score=?, ai=CASE WHEN ?>=? THEN 2 ELSE ai END, "
                             "ai_info=CASE WHEN ?>=? THEN ? ELSE ai_info END WHERE id=?",
                             (sc, sc, aidetect.POSSIBLY_AI, sc, aidetect.POSSIBLY_AI,
                              f"AI detector: {sc:.0%} likely AI-generated", pid))
                _tick(conn)
            conn.commit()


def read_existing_tags(conn, settings, progress=lambda d, t, m: None, cancelled=lambda: False):
    """Photos scanned before tag import existed: read the people keywords already on them
    (only the file headers are read, so this is quick)."""
    dates = conn.execute("SELECT id, path FROM photos WHERE taken_read=0 AND error IS NULL").fetchall()
    for n, (pid, path) in enumerate(dates, 1):   # dates for photos scanned by older versions
        if cancelled():
            break
        if n % 100 == 0 or n == len(dates):
            progress(n, len(dates), "Reading photo dates…")
        taken = engine.read_taken(path)            # (read first: the library isn't held meanwhile)
        conn.execute("UPDATE photos SET taken=?, taken_read=1 WHERE id=?", (taken, pid))
        _tick(conn)
    conn.commit()
    rows = conn.execute("SELECT id, path FROM photos WHERE tags_read=0 AND error IS NULL").fetchall()
    if not rows:
        return 0
    names = store.known_person_names(conn)
    for n, (pid, path) in enumerate(rows, 1):
        if cancelled():
            break
        if n % 50 == 0 or n == len(rows):
            progress(n, len(rows), "Reading existing tags…")
        try:
            kw = xmp.read_keywords(path)
        except Exception:
            kw = ([], [])
        store.apply_keywords(conn, pid, kw, settings, names)
        _tick(conn)
    conn.commit()
    return len(rows)


# ---------------------------------------------------------------- tattoo search
def tattoo_search(conn, settings, person_ids=None, progress=lambda d, t, m: None, cancelled=lambda: False):
    """Look for marked tattoos in your photos and save matches as suggestions.
    Photos already checked for a tattoo are skipped, so searching again only looks at new
    photos and new tattoo marks."""
    import tattoo
    min_inl = int(settings.get("tattoo_min_inliers", tattoo.DEFAULT_MIN_INLIERS))
    cond = ""
    args = []
    if person_ids:
        cond = f"WHERE t.person_id IN ({','.join('?' * len(person_ids))})"
        args = list(person_ids)
    marks = conn.execute(f"""SELECT t.id, t.person_id, t.photo_id, ph.path, t.x, t.y, t.w, t.h
                             FROM tattoos t JOIN photos ph ON ph.id=t.photo_id {cond}""", args).fetchall()
    stats = {"tattoos": len(marks), "unusable": 0, "photos": 0, "found": 0}
    if not marks:
        return stats
    progress(0, 0, "Reading tattoo marks…")
    samples = []
    for tid, pid, photo_id, path, x, y, w, h in marks:
        try:
            smp = tattoo.make_sample(path, (x, y, w, h), tid, pid, photo_id)
        except Exception:
            smp = None
        if smp is None:
            stats["unusable"] += 1
        else:
            samples.append(smp)
    if not samples:
        return stats
    tids = [s.tattoo_id for s in samples]
    checked = {}
    for photo_id, tid in conn.execute(
            f"SELECT photo_id, tattoo_id FROM tattoo_checked WHERE tattoo_id IN ({','.join('?' * len(tids))})", tids):
        checked.setdefault(photo_id, set()).add(tid)
    # people already on a photo (named face, tag, suggestion or "not them") don't need searching there
    settled = set()
    for q in ("SELECT photo_id, person_id FROM faces WHERE confirmed=1 AND ignored=0 AND person_id IS NOT NULL",
              "SELECT photo_id, person_id FROM photo_people",
              "SELECT photo_id, person_id FROM tattoo_suggestions",
              "SELECT photo_id, person_id FROM tattoo_rejects"):
        settled.update(conn.execute(q).fetchall())
    work = []
    for photo_id, path in conn.execute("SELECT id, path FROM photos WHERE error IS NULL AND sort_later=0 AND is_video=0 "
                                       "AND COALESCE(is_other, 0)=0 ORDER BY path"):
        todo = [s for s in samples if s.tattoo_id not in checked.get(photo_id, ())
                and (photo_id, s.person_id) not in settled]
        if todo:
            work.append((photo_id, path, todo))
    stats["photos"] = len(work)

    def run(item):
        photo_id, path, todo = item
        try:
            feats = tattoo.photo_features(path)
        except Exception:
            feats = None
        best = {}
        if feats is not None:
            for smp in todo:
                m = tattoo.match(smp, feats, min_inl)
                if m and m[0] > best.get(smp.person_id, (0,))[0]:
                    best[smp.person_id] = m
        return photo_id, [s.tattoo_id for s in todo], best

    threads = max(1, int(settings.get("threads", 2)))
    done = 0
    with ThreadPoolExecutor(max_workers=threads) as ex:
        it = iter(work)
        pending = set()
        for _ in range(threads * 2):
            w = next(it, None)
            if w is None:
                break
            pending.add(ex.submit(run, w))
        while pending:
            finished, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
            for fut in finished:
                photo_id, tested, best = fut.result()
                conn.executemany("INSERT OR IGNORE INTO tattoo_checked(photo_id, tattoo_id) VALUES(?,?)",
                                 [(photo_id, t) for t in tested])
                for person_id, (inl, (x, y, w, h)) in best.items():
                    conn.execute("""INSERT OR REPLACE INTO tattoo_suggestions(photo_id, person_id, x, y, w, h, inliers)
                                    VALUES(?,?,?,?,?,?,?)""", (photo_id, person_id, x, y, w, h, inl))
                    stats["found"] += 1
                done += 1
                _tick(conn)
                progress(done, len(work), "Looking for tattoos…")
                if not cancelled():
                    w = next(it, None)
                    if w is not None:
                        pending.add(ex.submit(run, w))
    conn.commit()
    stats["cancelled"] = cancelled()
    return stats


# ---------------------------------------------------------------- tag writing
def _named_faces(conn, settings):
    cond = "" if settings.get("tag_suggestions") else "AND f.confirmed=1"
    rows = conn.execute(f"""SELECT f.photo_id, p.name, f.x, f.y, f.w, f.h FROM faces f
                            JOIN persons p ON p.id=f.person_id
                            WHERE f.ignored=0 {cond} ORDER BY f.photo_id, f.x""").fetchall()
    out = {}
    for pid, name, x, y, w, h in rows:
        out.setdefault(pid, []).append((name, x, y, w, h))
    return out


def write_tags(conn, settings, progress=lambda done, total, msg: None, cancelled=lambda: False):
    stats = {"written": 0, "sidecars": 0, "unchanged": 0, "errors": [], "total": 0}
    prefix = settings.get("keyword_prefix", "") or ""
    named = _named_faces(conn, settings)
    # folders (formerly categories) are only for saving into folders: no longer written as tags,
    # and tags written by older versions are taken out
    photo_people = {}
    for pid, name in conn.execute("""SELECT pp.photo_id, p.name FROM photo_people pp
                                     JOIN persons p ON p.id=pp.person_id ORDER BY p.name"""):
        photo_people.setdefault(pid, []).append(name)
    written = {}
    for pid, kw in conn.execute("SELECT photo_id, keyword FROM written"):
        written.setdefault(pid, set()).add(kw)
    photo_ids = sorted(set(named) | set(written) | set(photo_people))
    others = {r[0] for r in conn.execute("SELECT id FROM photos WHERE is_other=1")}
    photo_ids = [p for p in photo_ids if p not in others]       # nothing is ever written into other files
    stats["total"] = len(photo_ids)

    for n, pid in enumerate(photo_ids, 1):
        if cancelled():
            break
        row = conn.execute("SELECT path, width, height, orientation, tagsig, mtime, size, is_video FROM photos "
                           "WHERE id=?", (pid,)).fetchone()
        if not row:
            continue
        path, width, height, orientation, oldsig, mtime, size, video = row
        faces = named.get(pid, [])
        desired = []
        tagged = photo_people.get(pid, [])
        for name in [f[0] for f in faces] + tagged:
            k = prefix + name
            if k not in desired:
                desired.append(k)
        category = None
        prev = written.get(pid, set())
        # Face rectangles only for photos without an EXIF rotation (apps disagree on
        # how rotated photos' rectangles should be stored, so we don't guess).
        regions = None
        if settings.get("write_regions", True) and (orientation or 1) == 1 and not video:
            regions = [(name, x, y, w, h) for name, x, y, w, h in faces]
        private = [(name, x, y, w, h) for name, x, y, w, h in faces]  # FaceSort's own copy, for read-back
        sig = hashlib.sha1(repr((sorted(desired), regions, private, tagged, category,
                                 bool(settings.get("always_sidecar")), "v5")).encode()).hexdigest()
        progress(n, len(photo_ids), os.path.basename(path))
        try:
            st = os.stat(path)
        except OSError:
            stats["errors"].append((path, "file not found"))
            continue
        if sig == oldsig and set(desired) == prev and round(st.st_mtime, 3) == round(mtime or 0, 3) \
                and st.st_size == size:
            stats["unchanged"] += 1
            continue
        try:
            where, _final = xmp.update_file(
                path, desired, [k for k in prev if k not in desired], regions=regions,
                dims=(width, height) if width and height else None,
                always_sidecar=bool(settings.get("always_sidecar")),
                keep_mtime=bool(settings.get("keep_modified_date", True)),
                private_faces=private, private_people=tagged, category=category)
        except Exception as e:
            stats["errors"].append((path, str(e)))
            continue
        st = os.stat(path)
        conn.execute("UPDATE photos SET tagsig=?, mtime=?, size=? WHERE id=?",
                     (sig, round(st.st_mtime, 3), st.st_size, pid))
        conn.execute("DELETE FROM written WHERE photo_id=?", (pid,))
        conn.executemany("INSERT INTO written(photo_id, keyword) VALUES(?,?)", [(pid, k) for k in desired])
        conn.commit()
        stats["sidecars" if where == "sidecar" else "written"] += 1
    return stats


# ---------------------------------------------------------------- export
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(name):
    n = _BAD.sub("_", name).strip(" .")
    return n or "Unnamed"


DISCARD_FOLDER = "~To Be Discarded"
UNCATEGORIZED_FOLDER = "~Uncategorized"
SORT_LATER_FOLDER = "~Sort Later"
AI_FOLDER = "~AI generated"
NO_PEOPLE_FOLDER = "~No people"


def _same_file(a, b):
    try:
        return os.path.getsize(a) == os.path.getsize(b) and filecmp.cmp(a, b, shallow=False)
    except OSError:
        return False


def _quick_same(a, b):
    """Same file without reading it: same size and modified time (a copy made by FaceSort keeps
    both), else a full comparison."""
    try:
        sa, sb = os.stat(a), os.stat(b)
    except OSError:
        return False
    if sa.st_size != sb.st_size:
        return False
    if int(sa.st_mtime) == int(sb.st_mtime):
        return True
    return _same_file(a, b)


def _free_target(folder, src):
    """Where src should go in folder: (path, already_there)."""
    base, ext = os.path.splitext(os.path.basename(src))
    target = os.path.join(folder, base + ext)
    if os.path.normcase(os.path.abspath(folder)) == os.path.normcase(os.path.dirname(os.path.abspath(src))):
        return os.path.abspath(src), True          # already sitting in this folder: nothing to do
    i = 2
    while os.path.exists(target):
        if _quick_same(target, src):
            return target, True
        target = os.path.join(folder, f"{base} ({i}){ext}")
        i += 1
    return target, False


def _sidecar(path):
    sc = xmp.sidecar_path(path)
    return sc if os.path.exists(sc) else None


JUNK_FILES = {"thumbs.db", "desktop.ini", ".ds_store", "ehthumbs.db"}   # don't keep a folder "in use"


def webp_to_jpg(src):
    """JPEG bytes of a .webp photo (first frame; transparency on white), keeping its EXIF and
    the names saved in its .xmp sidecar (written into the JPEG itself)."""
    from io import BytesIO
    from PIL import Image
    with Image.open(src) as im:
        exif = im.info.get("exif")
        icc = im.info.get("icc_profile")
        im.seek(0)
        if im.mode in ("RGBA", "LA", "P"):
            rgba = im.convert("RGBA")
            rgb = Image.new("RGB", rgba.size, (255, 255, 255))
            rgb.paste(rgba, mask=rgba.split()[-1])
        else:
            rgb = im.convert("RGB")
    buf = BytesIO()
    kw = {"quality": 95, "subsampling": 0}
    if exif:
        kw["exif"] = exif
    if icc:
        kw["icc_profile"] = icc
    rgb.save(buf, "JPEG", **kw)
    data = buf.getvalue()
    try:
        packet = xmp.read_xmp(src, sidecar=True)
        if packet:
            data = xmp._jpeg_write(data, packet.encode("utf-8"))
    except Exception:
        pass
    return data


def _free_name(folder, name, data=None):
    """A free file name in folder ("x (2).jpg" ...): (path, an identical file is already there)."""
    base, ext = os.path.splitext(name)
    target, i = os.path.join(folder, name), 2
    while os.path.exists(target):
        if data is not None:
            try:
                if os.path.getsize(target) == len(data):
                    with open(target, "rb") as f:
                        if f.read() == data:
                            return target, True
            except OSError:
                pass
        target = os.path.join(folder, f"{base} ({i}){ext}")
        i += 1
    return target, False


def _free_dir(parent, name):
    target, i = os.path.join(parent, name), 2
    while os.path.exists(target):
        target = os.path.join(parent, f"{name} ({i})")
        i += 1
    return target


def cleanup_empty_folders(roots, dest, progress=lambda d, t, m: None):
    r"""Move empty folders (nothing in them but empty folders, or Windows' Thumbs.db /
    desktop.ini) from your photo folders into <dest>\~To Be Discarded. A folder with any other
    file in it - text, html, anything - stays where it is. Your photo folders themselves and
    the destination are never moved. Returns [(old, new)]."""
    dest_n = os.path.normcase(os.path.abspath(dest))
    discard = os.path.join(dest, DISCARD_FOLDER)
    moved = []

    def inside_dest(p):
        q = os.path.normcase(os.path.abspath(p))
        return q == dest_n or q.startswith(dest_n.rstrip("\\/") + os.sep) or dest_n.startswith(q.rstrip("\\/") + os.sep)

    for root in roots:
        if not os.path.isdir(root):
            continue
        empty = {}
        for d, dirs, files in os.walk(root, topdown=False):
            real = [f for f in files if f.lower() not in JUNK_FILES]
            empty[d] = not real and all(empty.get(os.path.join(d, x), False) for x in dirs)
        root_n = os.path.normcase(os.path.abspath(root))
        tops = []
        for d, e in empty.items():
            if not e or os.path.normcase(os.path.abspath(d)) == root_n or inside_dest(d):
                continue
            parent = os.path.dirname(d)
            if os.path.normcase(os.path.abspath(parent)) != root_n and empty.get(parent) and not inside_dest(parent):
                continue          # its parent is empty too: the parent moves (with this inside)
            tops.append(d)
        for n, d in enumerate(sorted(tops), 1):
            progress(n, len(tops), f"Cleaning up: {d}")
            try:
                os.makedirs(discard, exist_ok=True)
                target = _free_dir(discard, os.path.basename(d.rstrip("\\/")))
                shutil.move(d, target)
                moved.append((d, target))
            except OSError:
                pass
    return moved


def _is_empty_dir(d):
    for _r, _dirs, files in os.walk(d):
        if any(f.lower() not in JUNK_FILES for f in files):
            return False
    return True


def cleanup_left_behind(dirs, dest):
    """Folders inside the library that photos were just taken out of: the empty ones (nothing
    but empty folders / Thumbs.db / desktop.ini) go into ~To Be Discarded, going up as far as
    the library itself. Returns [(old, new)]."""
    dest_n = os.path.normcase(os.path.abspath(dest))
    discard = os.path.join(dest, DISCARD_FOLDER)
    disc_n = os.path.normcase(os.path.abspath(discard))
    tops = set()
    for d in dirs:
        d = os.path.abspath(d)
        top = None
        while True:
            n = os.path.normcase(d)
            if n == dest_n or not n.startswith(dest_n.rstrip("\\/") + os.sep) or n.startswith(disc_n):
                break
            if not os.path.isdir(d) or not _is_empty_dir(d):
                break
            top = d
            d = os.path.dirname(d)
        if top:
            tops.add(top)
    moved = []
    for d in sorted(tops, key=len):
        if not os.path.isdir(d):
            continue          # went with a folder above it
        try:
            os.makedirs(discard, exist_ok=True)
            target = _free_dir(discard, os.path.basename(d.rstrip("\\/")))
            shutil.move(d, target)
            moved.append((d, target))
        except OSError:
            pass
    return moved


def plan_destinations(conn, include_suggestions=False, ai_folder=False, include_dropped=False):
    """Where Sort into folders puts each photo: ({photo id: [path, [folders, primary first]]},
    discarded ids, sort-later ids). Folders are relative to the destination (or absolute, for a
    vault with its own place on disk)."""
    later = {r[0] for r in conn.execute("SELECT id FROM photos WHERE sort_later=1")}
    discarded = {r[0] for r in conn.execute("SELECT DISTINCT photo_id FROM faces WHERE ignored=2")}
    discarded |= {r[0] for r in conn.execute("SELECT id FROM photos WHERE discarded=1")}
    discarded -= later
    paths = store.category_paths(conn)
    names = dict(conn.execute("SELECT id, name FROM persons"))

    def chain(cid):
        return [safe_name(n) for n in paths.get(cid, [])]
    # a vault with its own place on disk: its photos go there instead of under the destination
    vault_paths = store.vault_root_paths(conn)
    vault_top = {}
    for vid in vault_paths:
        for k in {vid} | store.category_descendants(conn, vid):
            vault_top[k] = vid

    def base_parts(cid):
        parts = chain(cid)
        if cid in vault_top and parts:
            return [vault_paths[vault_top[cid]]] + parts[1:]       # absolute: os.path.join(dest, it) keeps it
        return parts
    plan = {}
    photo_path = dict(conn.execute("SELECT id, path FROM photos"))
    for pid, dests in store.photo_destinations(conn, include_suggestions).items():
        folders = []
        for base, person, inner in dests:
            if person:
                parts = (base_parts(base) if base else [UNCATEGORIZED_FOLDER]) + [safe_name(names.get(person, "?"))]
            else:
                parts = base_parts(base) + [NO_PEOPLE_FOLDER] if base else [NO_PEOPLE_FOLDER]
            if inner:
                parts += chain(inner)
            f = os.path.join(*parts)
            if f not in folders:
                folders.append(f)
        if folders and pid in photo_path:
            plan[pid] = [photo_path[pid], folders]
    if ai_folder:
        for (pid,) in conn.execute("SELECT id FROM photos WHERE ai=1 AND error IS NULL"):
            if pid in photo_path and pid not in discarded and pid not in later:
                plan[pid] = [photo_path[pid], [AI_FOLDER]]
    for ids, folder in ((discarded, DISCARD_FOLDER), (later, SORT_LATER_FOLDER)):
        for pid in ids:
            if pid in photo_path:
                plan[pid] = [photo_path[pid], [folder]]
    if not include_dropped:          # still in Recently dropped: waits until you've checked it
        for (pid,) in conn.execute("SELECT id FROM photos WHERE dropped IS NOT NULL"):
            plan.pop(pid, None)
    return plan, discarded, later


def misplaced(conn, dest, include_suggestions=False, ai_folder=False):
    """Photos already in the sorted library (the Sort into folders destination, or a vault's own
    place) that aren't in a folder Sort into folders would put them in - left over from an
    older FaceSort, moved by hand, or tagged differently since: [(photo id, path, where it goes)].
    Photos outside the library (not sorted yet) aren't counted; nor is anything inside
    '~To Be Discarded' or the ~links / ~archives / ~files folders."""
    if not dest:
        return []
    plan, _, _ = plan_destinations(conn, include_suggestions, ai_folder)
    skip = {DISCARD_FOLDER.lower(), "~links", "~archives", "~files"}
    roots = [os.path.normcase(os.path.abspath(dest))]
    roots += [os.path.normcase(os.path.abspath(p)) for p in store.vault_root_paths(conn).values()]
    out = []

    def in_library(path):
        here = os.path.normcase(os.path.dirname(os.path.abspath(path)))
        root = next((r for r in roots if here == r or here.startswith(r.rstrip("\\/") + os.sep)), None)
        if root is None:
            return None
        rel = os.path.relpath(here, root) if here != root else ""
        if any(part.lower() in skip for part in rel.replace("\\", "/").split("/")):
            return None
        return here
    for pid, (path, folders) in plan.items():
        here = in_library(path)
        if here is None:
            continue
        wanted = [os.path.normcase(os.path.abspath(os.path.join(dest, f))) for f in folders]
        if here not in wanted:
            out.append((pid, path, folders[0]))
    # in the sorted library but nothing says where it goes (an older FaceSort sorted it, but its
    # tags are gone): it needs sorting again, so it's listed (it isn't moved until it's tagged)
    for pid, path in conn.execute("""SELECT id, path FROM photos ph WHERE error IS NULL AND dropped IS NULL
                                     AND COALESCE(sort_later, 0)=0 AND copy_of IS NULL
                                     AND NOT EXISTS (SELECT 1 FROM faces f WHERE f.photo_id=ph.id
                                                     AND f.person_id IS NOT NULL AND f.ignored=0)"""):
        # (a photo with names suggested is waiting to be confirmed - not listed)
        if pid not in plan and in_library(path) is not None:
            out.append((pid, path, None))
    return out


def export_folders(conn, dest, include_suggestions=False, move=False,
                   progress=lambda d, t, m: None, cancelled=lambda: False, roots=(), convert_webp=True,
                   cleanup=True, ai_folder=False, include_dropped=False, only=None):
    r"""Put photos into <dest>\<folder>\<sub-folder...>\<person>\<inner folder...>\<file>.
    - a person you placed in a folder always goes there; others follow the photo's folder
    - a photo's folder that goes inside people's folders ends up inside each person's folder
    - people but no folder -> '~Uncategorized\<person>'; a folder but nobody on the photo ->
      '<folder>\~No people'; discarded (D) only '~To Be Discarded'; Sort Later only '~Sort Later'
    - WebP / HEIC / AVIF / BMP / TIFF photos are saved as .jpg, see-through pictures and
      screenshots / graphics as .png (convert.py); when moving, the original goes to
      '~To Be Discarded'

    copy (default): originals stay where they are; every person folder gets a copy.
    move: each original is moved into the folder of the person with the biggest face in
          it; other people in the same photo get copies. The library follows the move.
          Afterwards empty folders left behind in your photo folders (roots) are moved to
          '~To Be Discarded' (cleanup)."""
    plan, discarded, later = plan_destinations(conn, include_suggestions, ai_folder, include_dropped)
    if only is not None:             # just these photos (e.g. putting misplaced ones back)
        only = set(only)
        plan = {k: v for k, v in plan.items() if k in only}

    # where each original folder's photos go (for the other files that came with them)
    dir_dests = {}
    for pid, (path, fl) in plan.items():
        fl = [f for f in fl if f not in (DISCARD_FOLDER, SORT_LATER_FOLDER, AI_FOLDER)]
        if fl:
            lst = dir_dests.setdefault(os.path.normcase(os.path.dirname(path)), [])
            for f in fl:
                if f not in lst:
                    lst.append(f)
    total = sum(len(v[1]) for v in plan.values())
    stats = {"copied": 0, "moved": 0, "skipped": 0, "errors": [], "total": total,
             "discarded": len(discarded), "log": None, "webp": 0, "folders_cleaned": 0}
    log_rows = []
    done = 0
    for pid, (src, folders) in sorted(plan.items(), key=lambda kv: kv[1][0]):
        if cancelled():
            break
        if not os.path.exists(src):
            stats["errors"].append((src, "file not found"))
            done += len(folders)
            continue
        plan = None
        if convert_webp and folders not in ([DISCARD_FOLDER], [SORT_LATER_FOLDER]) and media_kind(src) != "other":
            try:
                plan = convert.plan_for(src)       # WebP/HEIC/AVIF... -> JPEG, graphics -> PNG
            except Exception:
                plan = None
        if plan:
            try:
                done = _export_converted(conn, pid, src, folders, dest, move, stats, log_rows, done, total,
                                         progress, plan[0])
            except Exception as e:
                stats["errors"].append((src, str(e)))
                done += len(folders)
            continue
        current = src
        if move:
            # already sitting in one of its own person folders (earlier export)? leave it there
            here = os.path.normcase(os.path.dirname(os.path.abspath(src)))
            for fn in folders:
                if os.path.normcase(os.path.abspath(os.path.join(dest, fn))) == here:
                    folders = [fn] + [x for x in folders if x != fn]
                    break
        for k, folder_name in enumerate(folders):
            done += 1
            progress(done, total, f"{folder_name}: {os.path.basename(src)}")
            folder = os.path.join(dest, folder_name)
            try:
                os.makedirs(folder, exist_ok=True)
                target, there = _free_target(folder, current)
                sc = _sidecar(current)
                same_place = os.path.normcase(os.path.abspath(target)) == os.path.normcase(os.path.abspath(current))
                if move and k == 0 and same_place:
                    stats["skipped"] += 1
                    continue
                comps = companions.companions(current)        # Takeout .json, .aae, .thm
                if move and k == 0:
                    if not there:
                        shutil.move(current, target)
                        if sc:
                            shutil.move(sc, xmp.sidecar_path(target))
                        _carry_companions(comps, target, current, move=True)
                    elif os.path.normcase(os.path.abspath(target)) != os.path.normcase(os.path.abspath(current)):
                        os.remove(current)  # an identical copy is already there (earlier export)
                        if sc and not os.path.exists(xmp.sidecar_path(target)):
                            shutil.move(sc, xmp.sidecar_path(target))
                    new = store.norm_path(target)
                    clash = conn.execute("SELECT id FROM photos WHERE path=? AND id<>?", (new, pid)).fetchone()
                    if clash:   # target was already in the library as its own photo
                        conn.execute("DELETE FROM photos WHERE id=?", (pid,))
                    else:
                        st = os.stat(new)
                        conn.execute("UPDATE photos SET path=?, mtime=?, size=? WHERE id=?",
                                     (new, round(st.st_mtime, 3), st.st_size, pid))
                    conn.commit()
                    known.move_marks(src, new)
                    log_rows.append((src, new))
                    current = new
                    stats["moved"] += 1
                else:
                    if there:
                        stats["skipped"] += 1
                        continue
                    shutil.copy2(current, target)
                    if sc:
                        shutil.copy2(sc, xmp.sidecar_path(target))
                    _carry_companions(comps, target, current, move=False)
                    try:
                        known.copy_marks(store.norm_path(current), store.norm_path(target))   # names come along
                    except Exception:
                        pass
                    stats["copied"] += 1
            except Exception as e:
                stats["errors"].append((current, str(e)))
                break  # don't copy from a photo whose move failed halfway
    if not cancelled() and only is None:
        _file_drop_files(conn, dest, move, include_dropped, dir_dests, stats, log_rows, progress)
    if only is not None and move and cleanup and log_rows:
        cleaned = cleanup_left_behind({os.path.dirname(a) for a, _b in log_rows}, dest)
        stats["folders_cleaned"] = len(cleaned)
        log_rows += [(a, b) for a, b in cleaned]
    elif move and cleanup and not cancelled() and (stats["moved"] or stats["webp"] or stats.get("extra_files")):
        cleaned = cleanup_empty_folders([r for r in roots if os.path.isdir(r)], dest, progress)
        stats["folders_cleaned"] = len(cleaned)
        log_rows += [(a, b) for a, b in cleaned]
    if log_rows:
        # a record of every move, in case you want to put things back
        # (kept in FaceSort's own data folder - the library doesn't need it, so it stays out of your photos)
        name = time.strftime("FaceSort moves %Y-%m-%d %H%M%S.csv")
        logs = os.path.join(store.data_dir(), "move logs")
        try:
            os.makedirs(logs, exist_ok=True)
            with open(os.path.join(logs, name), "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["original location", "moved to"])
                w.writerows(log_rows)
            stats["log"] = os.path.join(logs, name)
        except OSError:
            pass
    return stats


def drop_file_folders(path, root, dir_dests):
    """The folders (under the destination) a dropped non-photo file goes with: those of the
    photos in its own folder, else the nearest folder above it with photos, else the most
    common place the drop's photos went."""
    d = os.path.normcase(os.path.dirname(path))
    top = os.path.normcase(os.path.dirname(root.rstrip("\\/"))) if root else d
    cur = d
    while True:
        if cur in dir_dests:
            return dir_dests[cur]
        parent = os.path.dirname(cur)
        if cur == top or parent == cur or not root:
            break
        cur = parent
    if root:
        pre = os.path.normcase(root.rstrip("\\/"))
        votes = {}
        for k, fl in dir_dests.items():
            if k == pre or k.startswith(pre + os.sep):
                votes[fl[0]] = votes.get(fl[0], 0) + 1
        if votes:
            return [max(votes, key=votes.get)]
    return []


def _file_drop_files(conn, dest, move, include_dropped, dir_dests, stats, log_rows, progress):
    r"""Notes, links, documents, archives that came with a drop -> <person folder>\~links /
    ~archives / ~files, once the drop is checked (or when you ask to sort Recently dropped too)."""
    waiting = {b for (b,) in conn.execute("SELECT DISTINCT dropped FROM photos WHERE dropped IS NOT NULL")}
    rows = [r for r in store.drop_files(conn) if include_dropped or r[1] not in waiting]
    done_ids, n = [], 0
    for fid, batch, path, kind, root in rows:
        n += 1
        if not os.path.exists(path):
            done_ids.append(fid)
            continue
        fl = drop_file_folders(path, root, dir_dests)
        if not fl:
            stats.setdefault("extra_left", []).append(path)
            continue
        progress(n, len(rows), f"Other files: {os.path.basename(path)}")
        try:
            sub = companions.SUB.get(kind, "~files")
            first = None
            for k, f in enumerate(fl):
                folder = os.path.join(dest, f, sub)
                os.makedirs(folder, exist_ok=True)
                target, _ = _free_name(folder, os.path.basename(path))
                src = first or path
                if move and k == 0:
                    shutil.move(path, target)
                    first = target
                    log_rows.append((path, store.norm_path(target)))
                else:
                    shutil.copy2(src if first else path, target)
            stats["extra_files"] = stats.get("extra_files", 0) + 1
            done_ids.append(fid)
        except Exception as e:
            stats["errors"].append((path, str(e)))
    if done_ids:
        store.forget_drop_files(conn, done_ids)


def _carry_companions(comps, target, src, move):
    """A photo's own extra files (Takeout .json, .aae, .thm) go where the photo goes."""
    for p, suffix in comps:
        if not os.path.exists(p):
            continue
        to = companions.companion_target(target, suffix, src)
        if os.path.exists(to):
            continue
        try:
            (shutil.move if move else shutil.copy2)(p, to)
        except OSError:
            pass


def _discard_original(src, dest):
    """The original of a converted photo (and its sidecar) -> ~To Be Discarded."""
    discard = os.path.join(dest, DISCARD_FOLDER)
    os.makedirs(discard, exist_ok=True)
    wt, _ = _free_name(discard, os.path.basename(src))
    sc = _sidecar(src)
    shutil.move(src, wt)
    if sc:
        shutil.move(sc, xmp.sidecar_path(wt))
    return wt


def _export_converted(conn, pid, src, folders, dest, move, stats, log_rows, done, total, progress, target):
    """A photo saved as a real JPEG or PNG (see convert.py) in each of its folders; when moving,
    the original (and its sidecar) goes to ~To Be Discarded and the library follows the photo
    to its new file. The new file is checked before anything is moved."""
    data = convert.converted_bytes(src, target)
    size = conn.execute("SELECT width, height FROM photos WHERE id=?", (pid,)).fetchone()
    if size and size[0] and size[1] and not convert.check(data, target, size):
        raise ValueError(f"the {target} copy didn't come out right - the original was left as it is")
    name = convert.new_name(src, target)
    comps = companions.companions(src)
    wt = None
    if move:
        # the new file would land exactly where the original is (e.g. a WebP named photo.jpg):
        # move the original to ~To Be Discarded first, so the new one keeps the name
        here = {os.path.normcase(os.path.abspath(os.path.join(dest, f, name))) for f in folders}
        if os.path.normcase(os.path.abspath(src)) in here:
            wt = _discard_original(src, dest)
    first = None
    for folder_name in folders:
        done += 1
        progress(done, total, f"{folder_name}: {name}")
        folder = os.path.join(dest, folder_name)
        os.makedirs(folder, exist_ok=True)
        out, there = _free_name(folder, name, data)
        if not there:
            with open(out, "wb") as f:
                f.write(data)
            stats["copied"] += 1
        else:
            stats["skipped"] += 1
        if not there:
            _carry_companions(comps, out, src, move=False)
            try:
                known.copy_marks(store.norm_path(src), store.norm_path(out))
            except Exception:
                pass
        first = first or out
    stats["webp"] += 1
    stats["as_" + target.lower()] = stats.get("as_" + target.lower(), 0) + 1
    if move and first:
        for p, _ in comps:              # copied with the new file; the originals aren't needed
            try:
                os.remove(p)
            except OSError:
                pass
        if wt is None:
            wt = _discard_original(src, dest)
        new = store.norm_path(first)
        clash = conn.execute("SELECT id FROM photos WHERE path=? AND id<>?", (new, pid)).fetchone()
        if clash:
            conn.execute("DELETE FROM photos WHERE id=?", (pid,))
        else:
            st = os.stat(new)
            conn.execute("UPDATE photos SET path=?, mtime=?, size=?, tagsig=NULL WHERE id=?",
                         (new, round(st.st_mtime, 3), st.st_size, pid))
        conn.commit()
        known.move_marks(src, new)
        log_rows += [(src, new), (src, store.norm_path(wt))]
        stats["moved"] += 1
    return done


def discard_dirs(conn, roots=()):
    """Every '~To Be Discarded' folder FaceSort knows of: in your Photos library, your photo
    folders and the places you sorted into (where Sort into folders puts discarded photos,
    .webp originals and empty folders), plus any that photos in the library sit in."""
    found = {}
    for r in roots:
        if not r:
            continue
        d = os.path.join(r, DISCARD_FOLDER)
        if os.path.isdir(d):
            found[os.path.normcase(os.path.abspath(d))] = d
    pat = re.compile(r"^(.*[\\/]" + re.escape(DISCARD_FOLDER) + r")[\\/]", re.I)
    for (p,) in conn.execute("SELECT path FROM photos WHERE path LIKE ?", (f"%{DISCARD_FOLDER}%",)):
        m = pat.match(p)
        if m and os.path.isdir(m.group(1)):
            found.setdefault(os.path.normcase(os.path.abspath(m.group(1))), m.group(1))
    return sorted(found.values())


def discard_contents(dirs):
    """Everything inside those folders (files and sub-folders, hidden ones too)."""
    out = []
    for d in dirs:
        try:
            out += [os.path.join(d, n) for n in sorted(os.listdir(d))]
        except OSError:
            pass
    return out


def recycle_discarded(conn, roots=(), progress=lambda d, t, m: None, cancelled=lambda: False):
    """Send everything in ~To Be Discarded to the Recycle Bin (Trash on a Mac / Linux): the
    photos you marked with D, wherever they are now, and what's inside the '~To Be Discarded'
    folders of your photo folders. They're taken out of the library too."""
    from send2trash import send2trash
    marked = conn.execute("""SELECT id, path FROM photos WHERE sort_later=0 AND (discarded=1
                             OR id IN (SELECT photo_id FROM faces WHERE ignored=2))""").fetchall()
    items = [(pid, p) for pid, p in marked]
    dirs = discard_dirs(conn, roots)
    items += [(None, p) for p in discard_contents(dirs)]       # .webp originals, empty folders, anything
    stats = {"trashed": 0, "errors": [], "photos": 0}
    seen = set()
    by_path = {store.norm_path(p): pid for pid, p in conn.execute("SELECT id, path FROM photos")}
    for n, (pid, p) in enumerate(items, 1):
        if cancelled():
            break
        key = store.norm_path(p)
        if key in seen:
            continue
        seen.add(key)
        progress(n, len(items), f"Recycle Bin: {os.path.basename(p)}")
        if not os.path.exists(p):
            if pid:
                conn.execute("DELETE FROM photos WHERE id=?", (pid,))   # already gone
            continue
        try:
            send2trash(p)
        except Exception as e:
            stats["errors"].append((p, str(e)))
            continue
        stats["trashed"] += 1
        for side in (p + ".xmp", os.path.splitext(p)[0] + ".xmp"):        # its sidecar goes with it
            if os.path.isfile(side):
                try:
                    send2trash(side)
                except Exception:
                    pass
        # forget it (and anything the library had inside a trashed folder)
        gone = [pid] if pid else []
        if os.path.isdir(p) or not pid:
            pre = key.rstrip("\\/") + os.sep
            gone += [i for q, i in by_path.items() if q == key or q.startswith(pre)]
        for i in set(gone):
            conn.execute("DELETE FROM photos WHERE id=?", (i,))
            stats["photos"] += 1
        if n % 50 == 0:
            conn.commit()
    # anything left behind (e.g. the bin refused one by one): try the whole folder's contents again
    for p in discard_contents(dirs):
        if cancelled():
            break
        try:
            send2trash(p)
            stats["trashed"] += 1
        except Exception as e:
            if (p, str(e)) not in stats["errors"]:
                stats["errors"].append((p, str(e)))
    stats["left"] = discard_contents(dirs)
    conn.commit()
    return stats


def raw_jpeg_copies(conn, paths, progress=lambda d, t, m: None, cancelled=lambda: False):
    """A full-quality JPEG next to each RAW ("IMG_0001.jpg", or "IMG_0001 (2).jpg" if taken)."""
    out = {"made": [], "errors": []}
    for n, src in enumerate(paths, 1):
        if cancelled():
            break
        progress(n - 1, len(paths), f"JPEG copy: {os.path.basename(src)}")
        try:
            dest, _ = _free_name(os.path.dirname(src), os.path.splitext(os.path.basename(src))[0] + ".jpg")
            convert.raw_to_jpeg(src, dest)
            out["made"].append((src, dest))
        except Exception as e:
            out["errors"].append((src, str(e)))
    progress(len(paths), len(paths), "JPEG copies done")
    return out


FPS_CHOICES = ((24.0, "24"), (25.0, "25"), (30000 / 1001, "30000/1001"), (30.0, "30"), (48.0, "48"),
               (50.0, "50"), (60000 / 1001, "60000/1001"), (60.0, "60"))      # output frame rates, 24 - 60


def fps_label(v):
    return f"{v:.3f}".rstrip("0").rstrip(".") if abs(v - round(v)) > 0.01 else f"{round(v)}"


def copy_filters(opts, src_fps):
    """The FFmpeg filters for an enhanced copy: Steady (flicker and crawling grain calmed between
    frames) and a new frame rate (made by motion interpolation, blending or repeating frames)."""
    vf = []
    if opts.get("steady"):
        # brightness flicker evened out, then grain averaged over neighbouring frames only where
        # nothing moves (moving things stay sharp)
        vf += ["deflicker=size=5:mode=pm", "atadenoise=0a=0.04:0b=0.08:1a=0.04:1b=0.08:2a=0.04:2b=0.08:s=7"]
    fps = opts.get("fps")
    if fps and abs(fps - src_fps) > 0.005:
        rate = next((r for v, r in FPS_CHOICES if abs(v - fps) < 0.005), f"{fps:.6f}")
        method = opts.get("method", "motion")
        if method == "motion":
            vf.append(f"minterpolate=fps={rate}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1:scd=fdiff")
        elif method == "blend":
            vf.append(f"framerate=fps={rate}")
        else:
            vf.append(f"fps={rate}")
    return vf


def copy_name(src, opts, src_fps):
    """"<name> - low light 30fps.mp4" and the like (never over an existing file)."""
    parts = []
    if opts.get("strength"):
        parts.append("low light" + (" strong" if opts["strength"] >= 2 else ""))
    if opts.get("steady"):
        parts.append("steady")
    fps = opts.get("fps")
    if fps and abs(fps - src_fps) > 0.005:
        parts.append(f"{fps_label(fps)}fps")
    if opts.get("start") is not None or opts.get("end") is not None:
        t0, t1 = opts.get("start") or 0, opts.get("end")
        def stamp(t):
            t = int(t)
            return f"{t // 3600}h{t // 60 % 60:02d}m{t % 60:02d}s" if t >= 3600 else f"{t // 60}m{t % 60:02d}s"
        parts.append(f"{stamp(t0)}-{stamp(t1) if t1 is not None else 'end'}")
    folder = os.path.dirname(src)
    stem = os.path.splitext(os.path.basename(src))[0]
    label = " ".join(parts) or "copy"
    out = os.path.join(folder, f"{stem} - {label}.mp4")
    n = 2
    while os.path.exists(out):
        out = os.path.join(folder, f"{stem} - {label} ({n}).mp4")
        n += 1
    return out


def enhance_video(conn, src, opts=None, progress=lambda d, t, m: None, cancelled=lambda: False):
    """An enhanced copy of a video, saved next to it: low-light recovery (the same as in the viewer),
    Steady (less flicker between frames), a new frame rate (24 - 60) and / or only the section
    between the markers. H.264 video, the original sound kept (as AAC).
    opts: {"strength": 0-2, "steady": bool, "fps": float or None, "method": "motion" / "blend" /
    "repeat", "start": seconds or None, "end": seconds or None}. (A bare number = low light
    strength, as before.) Returns (new path or None, frames done, total)."""
    import subprocess
    import imageio_ffmpeg
    if not isinstance(opts, dict):
        opts = {"strength": int(opts or 1)}
    strength = int(opts.get("strength") or 0)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    vs = engine.VideoSource(src)              # OpenCV, or FFmpeg for files OpenCV can't read
    fps = vs.fps or 25.0
    if not 1 <= fps <= 240:
        fps = 25.0
    f0, f1 = engine.frame_span(vs.n, fps, opts.get("start"), opts.get("end")) if vs.n > 0 else (0, None)
    total = (f1 - f0) if f1 is not None else vs.n
    W = vs.W // 2 * 2                         # H.264 wants even sizes
    H = vs.H // 2 * 2
    out = copy_name(src, opts, fps)
    tmp = out + ".part.mp4"
    vf = copy_filters(opts, fps)
    # motion interpolation runs on one processor core in FFmpeg: for anything but a short clip the
    # frames are made first, then the video is cut into pieces worked on side by side
    two_step = bool(vf) and vf[-1].startswith("minterpolate") and total > fps * 8 and (os.cpu_count() or 1) > 1
    stage1 = out + ".step1.mkv"
    if two_step:
        interp = vf.pop()
    audio_in = []
    if f0:
        audio_in += ["-ss", f"{f0 / fps:.3f}"]
    if f1 is not None and (f0 or f1 < vs.n):
        audio_in += ["-t", f"{(f1 - f0) / fps:.3f}"]
    raw_in = [ffmpeg, "-y", "-loglevel", "error",
              "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", f"{fps:.6f}", "-i", "-"]
    filt = ["-vf", ",".join(vf)] if vf else []
    if two_step:          # step 1: a near-lossless file of the enhanced frames (no sound yet)
        cmd = raw_in + filt + ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "10",
                               "-g", str(max(1, round(fps))), "-pix_fmt", "yuv420p", stage1]
    else:
        cmd = raw_in + audio_in + ["-i", src, "-map", "0:v:0", "-map", "1:a?"] + filt + [
              "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
              "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", tmp]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    import tempfile
    errf = tempfile.TemporaryFile()              # (a pipe could fill up and stall FFmpeg)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errf, creationflags=flags)
    level, done = None, 0
    fast = W * H > 1280 * 720 or total > fps * 120      # big or long: the quicker smoothing
    what = "Enhanced copy" if (vf or not strength) else "Low light copy"
    try:
        for bgr in vs.frames(f0, f1):
            if cancelled():
                break
            rgb = bgr[:H, :W, ::-1]
            if strength:
                lv = engine.frame_level(rgb)
                level = lv if level is None else (level[0] * 0.85 + lv[0] * 0.15, level[1] * 0.85 + lv[1] * 0.15)
                frame = engine.low_light(np.ascontiguousarray(rgb), strength, level, fast=fast)
            else:
                frame = rgb
            proc.stdin.write(np.ascontiguousarray(frame).tobytes())
            done += 1
            if done % 5 == 0:
                progress(done, total, (f"Step 1 of 2 - " if two_step else "") +
                         f"{what}: {os.path.basename(src)}  ({done:,} / {total:,} frames)")
    except BrokenPipeError:
        pass
    finally:
        vs.release()
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.wait()
        errf.seek(0)
        err = errf.read().decode("utf-8", "replace")
        errf.close()
    if two_step and not cancelled() and proc.returncode == 0 and os.path.exists(stage1):
        try:
            err = _interpolate_parts(ffmpeg, stage1, src, audio_in, interp, done / fps, tmp, progress, cancelled,
                                     os.path.basename(src))
        finally:
            for f in (stage1,):
                try:
                    os.remove(f)
                except OSError:
                    pass
    elif two_step:
        try:
            os.remove(stage1)
        except OSError:
            pass
    if cancelled() or proc.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
        try:
            os.remove(tmp)
        except OSError:
            pass
        if cancelled():
            return None, done, total
        raise RuntimeError("the video couldn't be written" + (f": {err.strip().splitlines()[-1]}" if err.strip() else ""))
    os.replace(tmp, out)
    return out, done, total


def _interpolate_parts(ffmpeg, stage1, src, audio_in, interp, duration, out, progress, cancelled, label):
    """Step 2 of a motion-interpolated copy: the step-1 file cut into pieces, each piece given its
    new in-between frames by its own FFmpeg (one per processor core), then joined, with the sound."""
    import subprocess
    import tempfile
    import time as _t
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    k = max(2, min(12, (os.cpu_count() or 2) - 1))
    k = min(k, max(1, int(duration // 4)))          # pieces of at least 4 seconds
    work = tempfile.mkdtemp(prefix="facesort_interp_", dir=os.path.dirname(out) or None)
    rate = interp.split("fps=", 1)[1].split(":", 1)[0]
    num, _, den = rate.partition("/")
    out_fps = float(num) / float(den or 1)
    total_out = int(duration * out_fps)
    parts, procs = [], []
    try:
        for i in range(k):
            a, b = duration * i / k, duration * (i + 1) / k
            part = os.path.join(work, f"part{i:02d}.mkv")
            prog = os.path.join(work, f"part{i:02d}.txt")
            cmd = [ffmpeg, "-y", "-loglevel", "error", "-ss", f"{a:.3f}", "-i", stage1, "-t", f"{b - a:.3f}",
                   "-vf", interp, "-r", rate, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                   "-pix_fmt", "yuv420p", "-an", "-progress", prog, part]
            procs.append(subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL, creationflags=flags))
            parts.append((part, prog))
        while any(p.poll() is None for p in procs):
            if cancelled():
                for p in procs:
                    p.kill()
                return ""
            made = 0
            for _part, prog in parts:
                try:
                    with open(prog, "r", encoding="ascii", errors="ignore") as fh:
                        txt = fh.read()
                    j = txt.rfind("frame=")
                    if j >= 0:
                        made += int(txt[j + 6:].split("\n", 1)[0].strip() or 0)
                except (OSError, ValueError):
                    pass
            progress(min(made, total_out), total_out,
                     f"Step 2 of 2 - new in-between frames: {label}  ({made:,} / {total_out:,} frames, "
                     f"{len(parts)} at once)")
            _t.sleep(0.5)
        if any(p.returncode != 0 for p in procs):
            return "a piece of the video couldn't be interpolated"
        lst = os.path.join(work, "parts.txt")
        with open(lst, "w", encoding="utf-8") as fh:
            for part, _p in parts:
                fh.write("file '" + part.replace("\\", "/").replace("'", "'\\''") + "'\n")
        cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst] + audio_in + [
               "-i", src, "-map", "0:v:0", "-map", "1:a?", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
               "-shortest", "-movflags", "+faststart", out]
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           creationflags=flags)
        return r.stderr.decode("utf-8", "replace")
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
        import shutil
        shutil.rmtree(work, ignore_errors=True)


def best_lowlight_frames(conn, src, count=12, strength=1, progress=lambda d, t, m: None, cancelled=lambda: False,
                         t0=None, t1=None):
    """The best moments of a dark video, brightened (low-light recovery) and saved as JPEGs next
    to it ("<name> - low light 1m23_40s.jpg"). Frames all through the video are looked at
    brightened; the sharpest ones with the most in them win, kept apart in time so they're not
    all the same moment. Returns [(path, seconds)]."""
    import cv2
    vs = engine.VideoSource(src)
    fps = vs.fps or 25.0
    if not 1 <= fps <= 240:
        fps = 25.0
    n = vs.n
    if n <= 0:
        raise ValueError("this video doesn't say how long it is")
    f0, f1 = engine.frame_span(n, fps, t0, t1)                         # only the marked section, if any
    span = f1 - f0
    samples = min(span, max(count * 12, min(400, int(span / fps * 2))))  # about 2 a second, at most 400
    cands = []
    level = None
    try:
        for k in range(samples):
            if cancelled():
                return []
            f = f0 + int((k + 0.5) * span / samples)
            bgr = vs.read_at(f)
            if bgr is None:
                continue
            small = cv2.resize(bgr, (480, max(2, 480 * bgr.shape[0] // max(1, bgr.shape[1]))),
                               interpolation=cv2.INTER_AREA)[:, :, ::-1]
            small = np.ascontiguousarray(small)
            lv = engine.frame_level(small)
            level = lv if level is None else (level[0] * 0.7 + lv[0] * 0.3, level[1] * 0.7 + lv[1] * 0.3)
            enh = engine.low_light(small, strength, lv, fast=True)
            g = cv2.cvtColor(enh, cv2.COLOR_RGB2GRAY)
            sharp = float(cv2.Laplacian(g, cv2.CV_64F).var())
            spread = float(g.std())
            lit = float(g.mean()) / 255.0
            if spread < 6 or lit < 0.04:
                continue                                 # nothing to see even brightened
            # sharp, with plenty of detail, and not blown out / still mostly black
            score = np.sqrt(sharp) * spread * (1.0 - abs(lit - 0.42))
            cands.append((score, f))
            progress(k + 1, samples + count, f"Finding the best moments… ({k + 1}/{samples})")
        if not cands:
            raise ValueError("no frames with anything in them were found, even brightened")
        gap = max(1, span // (count * 2))                # never two from the same moment
        chosen = []
        for score, f in sorted(cands, reverse=True):
            if all(abs(f - c) >= gap for c in chosen):
                chosen.append(f)
            if len(chosen) >= count:
                break
        folder = os.path.dirname(src)
        stem = os.path.splitext(os.path.basename(src))[0]
        out = []
        for j, f in enumerate(sorted(chosen)):
            if cancelled():
                break
            bgr = vs.read_at(f)
            if bgr is None:
                continue
            rgb = np.ascontiguousarray(bgr[:, :, ::-1])
            enh = engine.low_light(rgb, strength, engine.frame_level(rgb))       # full quality
            t = f / fps
            stamp = (f"{int(t // 3600)}h{int(t // 60 % 60):02d}m{t % 60:05.2f}s" if t >= 3600
                     else f"{int(t // 60)}m{t % 60:05.2f}s").replace(".", "_")
            path = os.path.join(folder, f"{stem} - low light {stamp}.jpg")
            k2 = 2
            while os.path.exists(path):
                path = os.path.join(folder, f"{stem} - low light {stamp} ({k2}).jpg")
                k2 += 1
            ok, buf = cv2.imencode(".jpg", enh[:, :, ::-1], [cv2.IMWRITE_JPEG_QUALITY, 95])
            if ok:
                with open(path, "wb") as fh:
                    fh.write(buf.tobytes())
                out.append((path, t))
            progress(samples + j + 1, samples + count, f"Saving the best moments… ({j + 1}/{len(chosen)})")
    finally:
        vs.release()
    return out
