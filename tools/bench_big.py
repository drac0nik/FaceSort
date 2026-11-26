"""Speed check on a big made-up library (1.61): times the paths the speed logs showed as slow.

    python tools/bench_big.py build <folder> [photos]     make the library once (kept for reuse)
    python tools/bench_big.py run <folder> [--ui]         time it (copy of the library each time)
"""
import io
import json
import os
import shutil
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _jpeg(seed):
    from PIL import Image
    rnd = np.random.default_rng(seed)
    b = io.BytesIO()
    Image.fromarray(rnd.integers(0, 255, (8, 8, 3), dtype=np.uint8)).resize((96, 96)).save(b, "JPEG", quality=70)
    return b.getvalue()


def build(folder, n_photos=60000):
    os.makedirs(folder, exist_ok=True)
    data = os.path.join(folder, "data")
    lib = os.path.join(folder, "My Library")
    shutil.rmtree(data, ignore_errors=True)
    os.makedirs(data)
    os.environ["FACESORT_DATA"] = data
    settings = {"folders": [lib], "library_root": lib, "scan_on_start": False, "engine": "sface",
                "settings_version": 99, "first_run_done": True, "engine_asked": True, "watch_folders": False,
                "auto_move": True, "detector_version": 99}
    json.dump(settings, open(os.path.join(data, "settings.json"), "w"))
    import store
    conn = store.connect()
    rnd = np.random.default_rng(1)
    n_people = 2000
    conn.executemany("INSERT INTO persons(id, name) VALUES(?,?)", [(i, f"Person {i:04d}") for i in range(1, n_people + 1)])
    thumbs = [_jpeg(s) for s in range(40)]
    centers = rnd.normal(size=(n_people + 600, 128)).astype(np.float32)
    centers /= np.linalg.norm(centers, axis=1, keepdims=True)
    photos, faces = [], []
    uns = os.path.join(lib, "~Unsorted")
    fid = 0
    for pid in range(1, n_photos + 1):
        if pid % 12 == 0:                       # waiting in ~Unsorted
            d = os.path.join(uns, "C", "Downloads", f"set{pid % 400:03d}")
        else:
            d = os.path.join(lib, "Models", f"Person {pid % n_people + 1:04d}", f"Gallery {pid % 7}")
        path = os.path.join(d, f"img_{pid:06d}.jpg")
        photos.append((pid, path, 1.0, 1000, 1200, 1600, 1, T0 + pid * 30, pid % 12 == 0 and T0 or None))
        for k in range(1 + (pid % 5 == 0)):
            fid += 1
            who = pid % n_people + 1
            known = pid % 12 != 0 and rnd.random() < 0.7
            c = centers[who - 1] if known or rnd.random() < 0.5 else centers[n_people + rnd.integers(0, 600)]
            e = c + rnd.normal(scale=0.35, size=128).astype(np.float32)
            e /= np.linalg.norm(e)
            faces.append((fid, pid, 0.3, 0.2, 0.2, 0.25, 0.9, 120, e.astype(np.float32).tobytes(),
                          thumbs[fid % 40], who if known else None, 1 if known and rnd.random() < 0.6 else 0,
                          None if known else int(rnd.integers(0, 3000)), None if fid % 3 else 0.5))
    conn.executemany("INSERT INTO photos(id, path, mtime, size, width, height, orientation, taken, dropped) "
                     "VALUES(?,?,?,?,?,?,?,?,?)", photos)
    conn.executemany("INSERT INTO faces(id, photo_id, x, y, w, h, score, px, emb, thumb, person_id, confirmed, "
                     "cluster, clarity) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", faces)
    # other files (links, archives…) that came with drops, in ~Unsorted
    oid = n_photos
    others = []
    for k in range(3000):
        oid += 1
        others.append((oid, os.path.join(uns, "C", "Downloads", f"set{k % 400:03d}", f"note{k}.url"), 1.0, 10, 1, T0))
    conn.executemany("INSERT INTO photos(id, path, mtime, size, is_other, dropped) VALUES(?,?,?,?,?,?)", others)
    # description index
    if store.table_exists(conn, "clip_emb") if hasattr(store, "table_exists") else True:
        try:
            conn.executemany("INSERT INTO clip_emb(photo_id, vec) VALUES(?,?)",
                             [(p, rnd.normal(size=512).astype(np.float16).tobytes()) for p in range(1, n_photos + 1, 2)])
        except Exception as e:
            print("clip_emb:", e)
    conn.commit()
    conn.execute("UPDATE photos SET nfaces = (SELECT COUNT(*) FROM faces f WHERE f.photo_id = photos.id)")
    conn.execute("UPDATE faces SET male = 0 WHERE id % 100 <> 0")         # (most faces already looked at)
    conn.execute("UPDATE faces SET clarity = 0.6 WHERE id % 100 <> 0")
    os.makedirs(os.path.join(uns, "C", "Downloads"), exist_ok=True)
    conn.commit()
    store.refresh_stats(conn, force=True)            # (the app does this as the library grows)
    conn.close()
    print(f"built {n_photos:,} photos, {fid:,} faces, {len(others):,} other files in {folder}")


T0 = 1.7e9


def _fresh(folder):
    src = os.path.join(folder, "data")
    work = os.path.join(folder, "work")
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(src, work)
    os.environ["FACESORT_DATA"] = work
    return work


def _t(label, fn, n=3, out=None):
    best = 1e9
    for _ in range(n):
        t = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t)
    print(f"{label:<44} {best * 1000:8.1f} ms")
    if out is not None:
        out[label] = best * 1000
    return best


def run(folder, ui=False):
    _fresh(folder)
    import libmove
    import store
    out = {}
    conn = store.connect()
    s = store.load_settings()
    lib = libmove.library(s)
    _t("sync_follow (auto-move tick)", lambda: libmove.sync_follow(conn, lib), out=out)
    _t("tagged_waiting (auto-move tick)", lambda: libmove.tagged_waiting(conn, lib), out=out)
    _t("faces still to check (male)", lambda: conn.execute(
        "SELECT 1 FROM faces WHERE male IS NULL AND thumb IS NOT NULL LIMIT 1").fetchall(), out=out)
    _t("count faces still to check (male)", lambda: conn.execute(
        "SELECT COUNT(*) FROM faces WHERE male IS NULL AND thumb IS NOT NULL").fetchall(), out=out)
    _t("clarity to do (one of six)", lambda: conn.execute(
        "SELECT id, thumb FROM faces WHERE clarity IS NULL AND manual<2 AND id % 6 = 1 LIMIT 200").fetchall(), out=out)
    _t("description index count", lambda: conn.execute(
        "SELECT COUNT(*) FROM clip_emb e JOIN photos ph ON ph.id=e.photo_id WHERE ph.error IS NULL").fetchall(), out=out)
    try:
        import clip_search
        _t("clip_search.index_counts", lambda: clip_search.index_counts(conn), out=out)
    except Exception as e:
        print("index_counts:", e)
    conn.close()
    if ui:
        _ui(out)
    json.dump(out, open(os.path.join(folder, f"bench-{int(time.time())}.json"), "w"), indent=1)
    return out


def _ui(out):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["FACESORT_NO_PROMPT"] = "1"
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication([])
    for name in ("information", "warning", "question", "critical"):       # (no window may wait for a click)
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))

    def pump(sec, until=None):
        """Runs the window for sec seconds (or until until() is true); -> longest freeze (ms)."""
        end = time.time() + sec
        last = time.perf_counter()
        worst = 0.0
        while time.time() < end:
            app.processEvents()
            now = time.perf_counter()
            worst = max(worst, now - last)
            last = now
            if until is not None and until():
                break
            time.sleep(0.002)
        return worst * 1000

    def rec(label, ms):
        out[label] = ms
        print(f"{label:<44} {ms:8.1f} ms")
    import facesort
    import libmove
    import stallwatch
    stallwatch.MIN_STALL = 0.15              # (speed.log: freezes over ~50 ms past its 100 ms tick)
    # a first start (FaceSort keeps what the lists ask between sessions), then the start that's timed
    w = facesort.MainWindow()
    w.show()
    pump(8)
    w._closing = True
    w.close()
    pump(1)
    t = time.perf_counter()
    w = facesort.MainWindow()
    w.resize(1400, 860)
    w.show()
    app.processEvents()
    rec("window opens (to first paint)", (time.perf_counter() - t) * 1000)
    t = time.perf_counter()

    def shown():
        return w.tree.topLevelItemCount() > 0 and any(w.tree.topLevelItem(i).childCount()
                                                      for i in range(w.tree.topLevelItemCount()))
    worst = pump(20, shown)
    rec("opening: lists shown", (time.perf_counter() - t) * 1000 + out["window opens (to first paint)"])
    worst = max(worst, pump(20, lambda: getattr(w, "_ra_worker", None) is None
                            and getattr(w, "_ra_ready", None) is None and shown()))
    rec("opening: lists up to date", (time.perf_counter() - t) * 1000 + out["window opens (to first paint)"])
    rec("longest freeze, first 20 s after opening", max(worst, pump(20)))
    end = time.time() + 120
    while time.time() < end and w.busy():        # (the jobs a window starts with)
        pump(0.2)
    os.makedirs(libmove.unsorted_dir(libmove.library(w.settings)), exist_ok=True)
    w._last_input = 0
    w._am_dirty = True
    _t("automove_tick, after a change", lambda: (setattr(w, "_am_dirty", True), w.automove_tick()), out=out)
    _t("automove_tick, nothing changed", lambda: w.automove_tick(), out=out)

    def lists_done():
        return getattr(w, "_ra_worker", None) is None and getattr(w, "_ra_ready", None) is None
    t = time.perf_counter()
    w.refresh_all()
    worst = pump(30, lists_done)
    rec("refresh: lists ready", (time.perf_counter() - t) * 1000)
    rec("refresh: longest freeze", worst)
    # naming a few faces: until the lists show it, and the longest freeze meanwhile
    fids = [r[0] for r in w.conn.execute("SELECT id FROM faces WHERE person_id IS NULL AND cluster IS NOT NULL "
                                         "LIMIT 3")]
    w._last_input = 0
    t = time.perf_counter()
    w.assign_ids(fids, "Person 0007")
    after = (time.perf_counter() - t) * 1000
    worst = pump(6)
    rec("naming 3 faces: the action itself", after)
    rec("naming 3 faces: longest freeze after", worst)
    t = time.perf_counter()
    w.quick_review()
    worst = pump(60, lambda: w.review is not None and w.review.q is not None)
    rec("quick review: opening, until a question", (time.perf_counter() - t) * 1000)
    rec("quick review: longest freeze opening", worst)
    r = w.review
    if r.q is not None:
        r.sess.answered = r.REGROUP_EVERY - 1
        _t("quick review answer (5th: regroup)", lambda: r.answer(False), n=1, out=out)
        _t("quick review answer (plain)", lambda: r.answer(False), n=1, out=out)
    rec("longest freeze, 10 s after answering", pump(10))
    w.close_review()
    rec("longest freeze, closing quick review", pump(5))
    rec("longest freeze while idle 25 s", pump(25))
    w._closing = True
    w.close()
    pump(1)


if __name__ == "__main__":
    cmd, folder = sys.argv[1], sys.argv[2]
    if cmd == "build":
        build(folder, int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 60000)
    else:
        run(folder, "--ui" in sys.argv)
