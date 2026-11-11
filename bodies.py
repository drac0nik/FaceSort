"""Same-shoot suggestions: people whose face can't be read (turned away, blurry, hidden) are
recognised by what they're wearing - but only against photos from the same occasion.

How it works
- A person finder (YOLOX-S) marks everyone in a photo, face or no face, and an appearance
  model (Youtu person re-identification) turns each person's clothes and build into a list of
  numbers. Both come from OpenCV's model zoo (Apache 2.0 licence), are downloaded once (about
  140 MB), and run on this PC only.
- Each person is linked to the face found inside them, if any.
- Photos belong to the same occasion ("shoot") when they were taken close together in time
  (and, for photos with a GPS position, close together in place), or - for photos without a
  date - sit in the same folder and show the same kind of scene. The scene itself (same room,
  same beach) is compared from the description numbers of "Search by description" when those
  are there, otherwise from the colours of the background.
- Inside a shoot, someone whose face isn't recognised is compared with the people you've
  confirmed there. Clothes alone never name anyone across occasions: the same shirt at a
  different party is a different day, and the same room shows up in years of photos.
- Suggestions are shown in Smart Groups > Same shoot, with the reason; you confirm or reject
  each one. Confirming names the face (or, for someone with no face visible, tags the photo).
"""
import math
import os
import time

import cv2
import numpy as np

MODELS = {
    "detect": ("yolox_s.onnx", 36, "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
               "object_detection_yolox/object_detection_yolox_2022nov.onnx"),
    "appearance": ("person_reid_youtu.onnx", 107, "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/"
                   "models/person_reid_youtureid/person_reid_youtu_2021nov.onnx"),
}
SIZES = {"detect": 35858002, "appearance": 106878407}

PERSON_MIN_SCORE = 0.45      # person finder confidence
PERSON_MIN_HEIGHT = 0.12     # people smaller than this share of the photo's height are skipped
WORK_SIDE = 1024             # photos are read at this size for finding people

# matching (tuned on people filmed walking about: same person, same clothes, other poses)
SAME_CLOTHES = 0.62          # appearance similarity needed for a suggestion
MARGIN = 0.06                # ... and this much more than the next most alike person
SHOOT_HOURS = 3.0            # photos taken within this many hours can be one occasion
SHOOT_HOURS_SAME_FOLDER = 12.0
SHOOT_KM = 1.0               # photos with GPS positions further apart than this are different places
SCENE_ALIKE = 0.80           # (CLIP) scene this alike = "same place" for photos without a date
SCENE_ALIKE_COLOURS = 0.85   # the same, from background colours
FACE_UNCLEAR = 0.35          # faces less clear than this are helped by clothes even if suggested


def model_dir():
    import store
    return os.path.join(store.data_dir(), "models", "body")


def model_path(kind):
    return os.path.join(model_dir(), MODELS[kind][0])


def available():
    return all(os.path.exists(model_path(k)) for k in MODELS)


def download(progress=lambda d, t, m: None, cancelled=lambda: False):
    """The two models (about 140 MB, once). False if cancelled."""
    import urllib.request
    os.makedirs(model_dir(), exist_ok=True)
    total = sum(SIZES.values())
    done = 0
    for kind, (name, mb, url) in MODELS.items():
        dest = model_path(kind)
        if os.path.exists(dest):
            done += SIZES[kind]
            continue
        tmp = dest + ".part"
        req = urllib.request.Request(url, headers={"User-Agent": "FaceSort"})
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            while True:
                if cancelled():
                    f.close()
                    os.remove(tmp)
                    return False
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                progress(done, total, f"Downloading the person models… {done / 1e6:.0f} / {total / 1e6:.0f} MB")
        if os.path.getsize(tmp) < SIZES[kind] * 0.9:
            os.remove(tmp)
            raise RuntimeError(f"the download of {name} was incomplete")
        os.replace(tmp, dest)
    return True


# ------------------------------------------------------------------ the models
class Models:
    def __init__(self):
        import photo_tools
        self.det = photo_tools._session(model_path("detect"))
        self.app = photo_tools._session(model_path("appearance"))
        self.det_in = self.det.get_inputs()[0].name
        self.app_in = self.app.get_inputs()[0].name
        s = 640
        grids, strides = [], []
        for st in (8, 16, 32):
            n = s // st
            yv, xv = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
            grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
            strides.append(np.full((n * n, 1), st))
        self.grids = np.concatenate(grids).astype(np.float32)
        self.strides = np.concatenate(strides).astype(np.float32)

    def people(self, bgr):
        """[(x, y, w, h, score)] in pixels of bgr: everyone the person finder sees."""
        h, w = bgr.shape[:2]
        k = min(640 / w, 640 / h)
        nw, nh = int(round(w * k)), int(round(h * k))
        pad = np.full((640, 640, 3), 114, np.uint8)
        pad[:nh, :nw] = cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_LINEAR)
        blob = pad.transpose(2, 0, 1)[None].astype(np.float32)
        out = self.det.run(None, {self.det_in: blob})[0][0]
        out[:, :2] = (out[:, :2] + self.grids) * self.strides
        out[:, 2:4] = np.exp(out[:, 2:4]) * self.strides
        score = out[:, 4] * out[:, 5]                       # objectness x "person"
        keep = score >= PERSON_MIN_SCORE
        if not keep.any():
            return []
        b, sc = out[keep, :4], score[keep]
        boxes = np.stack([b[:, 0] - b[:, 2] / 2, b[:, 1] - b[:, 3] / 2, b[:, 2], b[:, 3]], 1) / k
        idx = cv2.dnn.NMSBoxes(boxes.tolist(), sc.tolist(), PERSON_MIN_SCORE, 0.45)
        res = []
        for i in np.array(idx).flatten():
            x, y, bw, bh = boxes[i]
            x0, y0 = max(0.0, x), max(0.0, y)
            x1, y1 = min(float(w), x + bw), min(float(h), y + bh)
            if y1 - y0 >= PERSON_MIN_HEIGHT * h and x1 - x0 >= 8:
                res.append((x0, y0, x1 - x0, y1 - y0, float(sc[i])))
        return res

    def appearance(self, bgr, boxes):
        """(n, 768) appearance numbers (unit length) of the people in these boxes."""
        if not boxes:
            return np.zeros((0, 768), np.float32)
        mean = np.array((0.485, 0.456, 0.406), np.float32)
        std = np.array((0.229, 0.224, 0.225), np.float32)
        crops = []
        for x, y, bw, bh, *_ in boxes:
            c = bgr[int(y):int(y + bh), int(x):int(x + bw)]
            c = cv2.resize(c, (128, 256), interpolation=cv2.INTER_AREA if c.shape[0] > 256 else cv2.INTER_LINEAR)
            crops.append((c[:, :, ::-1].astype(np.float32) / 255.0 - mean) / std)
        blob = np.stack(crops).transpose(0, 3, 1, 2).astype(np.float32)
        out = []
        for i in range(0, len(blob), 16):
            out.append(self.app.run(None, {self.app_in: blob[i:i + 16]})[0].reshape(-1, 768))
        f = np.concatenate(out).astype(np.float32)
        return f / (np.linalg.norm(f, axis=1, keepdims=True) + 1e-9)


_MODELS = [None]


def models():
    if _MODELS[0] is None:
        _MODELS[0] = Models()
    return _MODELS[0]


def scene_colours(bgr, boxes):
    """The background's colours (people left out): a small histogram, for "same place" when there's
    no description index."""
    h, w = bgr.shape[:2]
    small = cv2.resize(bgr, (192, max(1, 192 * h // max(1, w))), interpolation=cv2.INTER_AREA)
    k = small.shape[1] / w
    mask = np.full(small.shape[:2], 255, np.uint8)
    for x, y, bw, bh, *_ in boxes:
        cv2.rectangle(mask, (int(x * k), int(y * k)), (int((x + bw) * k), int((y + bh) * k)), 0, -1)
    if mask.mean() < 90:                     # people fill most of the picture: use all of it
        mask[:] = 255
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1, 2], mask, [12, 4, 4], [0, 180, 0, 256, 0, 256]).flatten()
    hist = np.sqrt(hist / max(1.0, hist.sum()))
    return (hist / (np.linalg.norm(hist) or 1)).astype(np.float32)


def body_thumb(bgr, box, height=192):
    x, y, bw, bh = (int(v) for v in box[:4])
    c = bgr[max(0, y):y + bh, max(0, x):x + bw]
    if c.size == 0:
        return b""
    k = height / c.shape[0]
    c = cv2.resize(c, (max(1, int(c.shape[1] * k)), height), interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC)
    ok, buf = cv2.imencode(".jpg", c, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return buf.tobytes() if ok else b""


def link_faces(boxes, faces):
    """Which face (if any) belongs to each person: a face near the top of the person's box, about
    the right size. boxes in pixels; faces = [(id, x, y, w, h)] in pixels. Returns face id per box."""
    out = [None] * len(boxes)
    taken = set()
    pairs = []
    for i, (x, y, bw, bh, *_r) in enumerate(boxes):
        for fid, fx, fy, fw, fh in faces:
            cx, cy = fx + fw / 2, fy + fh / 2
            if not (x <= cx <= x + bw and y - fh * 0.3 <= cy <= y + bh * 0.6):
                continue
            if fw > bw * 1.1 or fw < bw * 0.08:
                continue
            # the closer to the top middle of the person, the better
            d = abs(cx - (x + bw / 2)) / bw + abs(cy - (y + min(bh, bw * 1.6) * 0.12)) / bh
            pairs.append((d, i, fid))
    for d, i, fid in sorted(pairs):
        if out[i] is None and fid not in taken:
            out[i] = fid
            taken.add(fid)
    return out


# ------------------------------------------------------------------ reading the photos
def analyze_photos(conn, progress=lambda d, t, m: None, cancelled=lambda: False):
    """Find the people in every photo that hasn't been looked at yet (carries on where it left off).
    Returns how many photos were read."""
    import engine
    todo = conn.execute("""SELECT id, path, width, height FROM photos WHERE COALESCE(body_done, 0)=0
                           AND is_video=0 AND error IS NULL AND COALESCE(sort_later, 0)<>2 ORDER BY id""").fetchall()
    if not todo:
        return 0
    m = models()
    done = 0
    for pid, path, W, H in todo:
        if cancelled():
            break
        try:
            li = engine.load_image(path, max_side=WORK_SIDE)
            bgr = li.bgr
            h, w = bgr.shape[:2]
            boxes = m.people(bgr)
            descs = m.appearance(bgr, boxes)
            scene = scene_colours(bgr, boxes)
            ok = True
        except Exception:
            ok = False
        faces = conn.execute("SELECT id, x, y, w, h FROM faces WHERE photo_id=? AND frame_t IS NULL", (pid,)).fetchall()
        rows = []
        if ok:
            fpx = [(fid, fx * w, fy * h, fw * w, fh * h) for fid, fx, fy, fw, fh in faces]
            link = link_faces(boxes, fpx)
            for b, d, fid in zip(boxes, descs, link):
                rows.append((pid, float(b[0]) / w, float(b[1]) / h, float(b[2]) / w, float(b[3]) / h, float(b[4]),
                             d.astype(np.float16).tobytes(), fid, body_thumb(bgr, b)))
        with conn.write():
            conn.execute("DELETE FROM bodies WHERE photo_id=?", (pid,))
            if rows:
                conn.executemany("INSERT INTO bodies(photo_id, x, y, w, h, score, desc, face_id, thumb) "
                                 "VALUES(?,?,?,?,?,?,?,?,?)", rows)
            if ok:
                conn.execute("INSERT OR REPLACE INTO photo_scene(photo_id, colours) VALUES(?,?)",
                             (pid, scene.astype(np.float16).tobytes()))
            conn.execute("UPDATE photos SET body_done=? WHERE id=?", (1 if ok else 2, pid))
        done += 1
        if done % 5 == 0 or done == len(todo):
            progress(done, len(todo), f"Finding people for same-shoot suggestions… {done:,} / {len(todo):,}")
        time.sleep(0)
    return done


# ------------------------------------------------------------------ shoots and suggestions
def km_between(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 12742 * math.asin(min(1.0, math.sqrt(h)))


def same_occasion(a, b, scene_sim):
    """(same occasion?, reasons) for two photos: dicts with folder, taken, gps.
    scene_sim: how alike the two scenes are (0-1, or None if unknown) and the line for it."""
    reasons = []
    sim, alike_line = scene_sim
    same_place_scene = sim is not None and sim >= alike_line
    if a["gps"] and b["gps"]:
        km = km_between(a["gps"], b["gps"])
        if km > SHOOT_KM:
            return False, []
        reasons.append("same place" if km < 0.2 else f"{km * 1000:.0f} m apart" if km < 1 else f"{km:.1f} km apart")
    same_folder = a["folder"] == b["folder"]
    if a["taken"] and b["taken"]:
        hours = abs(a["taken"] - b["taken"]) / 3600
        if hours > (SHOOT_HOURS_SAME_FOLDER if same_folder else SHOOT_HOURS):
            return False, []
        mins = hours * 60
        reasons.insert(0, "taken together" if mins < 2 else f"{mins:.0f} min apart" if mins < 90 else
                       f"{hours:.1f} h apart")
    else:
        # no date on one of them: only the same folder and the same kind of scene will do
        if not (same_folder and same_place_scene):
            return False, []
        reasons.append("same folder")
    if same_place_scene:
        reasons.append("same scene")
    return True, reasons


def suggest(conn, progress=lambda d, t: None, cancelled=lambda: False):
    """Work out the same-shoot suggestions again (quick: only database work). Returns how many."""
    bodies = conn.execute("""SELECT b.id, b.photo_id, b.desc, b.face_id, b.person_id, f.person_id, f.confirmed,
                                    f.clarity, f.ignored
                             FROM bodies b LEFT JOIN faces f ON f.id=b.face_id""").fetchall()
    if not bodies:
        with conn.write():
            conn.execute("DELETE FROM body_suggestions")
        return 0
    photos = {}
    for pid, path, taken, lat, lon, ph_later in conn.execute(
            "SELECT id, path, taken, lat, lon, sort_later FROM photos WHERE COALESCE(body_done, 0)=1"):
        photos[pid] = {"folder": os.path.normcase(os.path.dirname(path)), "taken": taken,
                       "gps": (lat, lon) if lat is not None and lon is not None else None, "later": ph_later}
    hidden = set()
    try:
        import store
        hidden = {r[0] for r in conn.execute("SELECT id FROM persons")} & set(store.locked_vault_contents(conn)[1])
    except Exception:
        pass
    labelled, targets = [], []
    present = {}                       # photo -> people already named on it
    for bid, pid, desc, fid, bperson, fperson, conf, clar, ign in bodies:
        if pid not in photos or photos[pid]["later"]:
            continue
        who = bperson or (fperson if conf else None)
        if who:
            labelled.append((bid, pid, desc, who))
            present.setdefault(pid, set()).add(who)
        elif fid is None or (ign in (0, 4, None) and (fperson is None or (clar is not None and clar < FACE_UNCLEAR))):
            targets.append((bid, pid, desc, fid))
    for pid, person in conn.execute("SELECT photo_id, person_id FROM faces WHERE confirmed=1 AND person_id IS NOT NULL"):
        present.setdefault(pid, set()).add(person)
    for pid, person in conn.execute("SELECT photo_id, person_id FROM photo_people"):
        present.setdefault(pid, set()).add(person)
    rejected = {}
    for bid, person in conn.execute("SELECT body_id, person_id FROM body_rejects"):
        rejected.setdefault(bid, set()).add(person)
    out = []
    if labelled and targets:
        # scenes: CLIP description numbers if there, else background colours
        scene, line = {}, SCENE_ALIKE
        try:
            for p, v in conn.execute("SELECT photo_id, vec FROM clip_emb WHERE vec IS NOT NULL"):
                if p in photos:
                    scene[p] = np.frombuffer(v, np.float16).astype(np.float32)
        except Exception:
            scene = {}
        if len(scene) < len(photos) // 2:
            scene, line = {}, SCENE_ALIKE_COLOURS
            for p, v in conn.execute("SELECT photo_id, colours FROM photo_scene"):
                scene[p] = np.frombuffer(v, np.float16).astype(np.float32)

        def scene_sim(a, b):
            sa, sb = scene.get(a), scene.get(b)
            if sa is None or sb is None:
                return None
            return float(sa @ sb / ((np.linalg.norm(sa) * np.linalg.norm(sb)) or 1))

        def occasion(a, b):
            return same_occasion(photos[a], photos[b], (scene_sim(a, b), line))
        # the confirmed people, dated ones in time order: a time window is one slice
        INF = float("inf")
        labelled.sort(key=lambda r: photos[r[1]]["taken"] or INF)
        L = np.stack([np.frombuffer(d, np.float16).astype(np.float32) for _, _, d, _ in labelled])
        lab_photo = np.array([p for _, p, _, _ in labelled], dtype=np.int64)
        lab_person = np.array([w for _, _, _, w in labelled], dtype=np.int64)
        lab_t = np.array([photos[p]["taken"] or np.nan for _, p, _, _ in labelled], dtype=np.float64)
        n_dated = int(np.isfinite(lab_t).sum())
        folder_code = {}
        lab_folder = np.array([folder_code.setdefault(photos[p]["folder"], len(folder_code)) for _, p, _, _ in labelled])
        lab_lat = np.array([(photos[p]["gps"] or (np.nan, np.nan))[0] for _, p, _, _ in labelled], dtype=np.float64)
        lab_lon = np.array([(photos[p]["gps"] or (np.nan, np.nan))[1] for _, p, _, _ in labelled], dtype=np.float64)
        rows_in_folder = {}
        for j, f in enumerate(lab_folder):
            rows_in_folder.setdefault(int(f), []).append(j)
        by_target_photo = {}
        for bid, pid, desc, fid in targets:
            by_target_photo.setdefault(pid, []).append((bid, desc, fid))
        alike_cache = {}

        def alike_scene(a, b):
            key = (a, b) if a < b else (b, a)
            if key not in alike_cache:
                sim = scene_sim(a, b)
                alike_cache[key] = sim is not None and sim >= line
            return alike_cache[key]
        for n, (pid, items) in enumerate(by_target_photo.items()):
            if cancelled():
                return None
            if n % 100 == 0:
                progress(n, len(by_target_photo))
                time.sleep(0)
            ph = photos[pid]
            fcode = folder_code.get(ph["folder"], -1)
            parts = []                                   # (row indices into L, mask of same occasion)
            if ph["taken"]:
                t = ph["taken"]
                lo = int(np.searchsorted(lab_t[:n_dated], t - SHOOT_HOURS_SAME_FOLDER * 3600, "left"))
                hi = int(np.searchsorted(lab_t[:n_dated], t + SHOOT_HOURS_SAME_FOLDER * 3600, "right"))
                if hi > lo:
                    dt = np.abs(lab_t[lo:hi] - t)
                    ok = (dt <= SHOOT_HOURS * 3600) | ((lab_folder[lo:hi] == fcode) & (dt <= SHOOT_HOURS_SAME_FOLDER * 3600))
                    ok &= lab_photo[lo:hi] != pid
                    if ph["gps"]:
                        la, lo_ = lab_lat[lo:hi], lab_lon[lo:hi]
                        has = np.isfinite(la)
                        if has.any():
                            r = np.radians
                            hv = np.sin((r(la) - r(ph["gps"][0])) / 2) ** 2 + np.cos(r(la)) * math.cos(r(ph["gps"][0])) \
                                * np.sin((r(lo_) - r(ph["gps"][1])) / 2) ** 2
                            km = 12742 * np.arcsin(np.sqrt(np.clip(hv, 0, 1)))
                            ok &= ~has | (km <= SHOOT_KM)
                    parts.append((np.arange(lo, hi)[ok], None))
            # photos without a date (this one, or the others in its folder): same folder + same scene
            extra = [j for j in rows_in_folder.get(fcode, ())
                     if lab_photo[j] != pid and (not ph["taken"] or j >= n_dated)
                     and alike_scene(pid, int(lab_photo[j]))]
            if extra:
                parts.append((np.array(extra, dtype=np.int64), None))
            idx = np.unique(np.concatenate([p for p, _ in parts])) if parts else np.zeros(0, np.int64)
            if not len(idx):
                continue
            T = np.stack([np.frombuffer(d, np.float16).astype(np.float32) for _, d, _ in items])
            if len(idx) == idx[-1] - idx[0] + 1:
                S = L[idx[0]:idx[-1] + 1] @ T.T                 # a plain slice: no copying
            else:
                S = L[idx] @ T.T
            for k, (bid, _d, fid) in enumerate(items):
                sims = S[:, k]
                hit = sims >= SAME_CLOTHES - MARGIN
                best = {}              # person -> [similarity, labelled photo, photos alike]
                for j, sv in zip(idx[hit], sims[hit]):
                    who = int(lab_person[j])
                    if who in present.get(pid, ()) or who in rejected.get(bid, ()) or who in hidden:
                        continue
                    lp = int(lab_photo[j])
                    b = best.setdefault(who, [-1.0, lp, set()])
                    b[2].add(lp)
                    if sv > b[0]:
                        b[0], b[1] = float(sv), lp
                if not best:
                    continue
                ranked = sorted(best.items(), key=lambda kv: -kv[1][0])
                who, (sv, lp, alike) = ranked[0]
                second = ranked[1][1][0] if len(ranked) > 1 else -1.0
                if sv < SAME_CLOTHES or sv - second < MARGIN:
                    continue
                why = [f"same clothes as in {len(alike)} photo{'s' if len(alike) != 1 else ''}"] + occasion(pid, lp)[1]
                out.append((bid, who, round(sv, 3), " · ".join(why), fid))
    with conn.write():
        conn.execute("DELETE FROM body_suggestions")
        if out:
            conn.executemany("INSERT INTO body_suggestions(body_id, person_id, score, reason, face_id) VALUES(?,?,?,?,?)",
                             out)
    return len(out)


def confirm(conn, body_ids):
    """Yes: the face gets the name (or, with no face to see, the photo is tagged with the person)."""
    import store
    rows = conn.execute(f"""SELECT s.body_id, s.person_id, b.face_id, b.photo_id FROM body_suggestions s
                            JOIN bodies b ON b.id=s.body_id WHERE s.body_id IN ({','.join('?' * len(body_ids))})""",
                        list(body_ids)).fetchall()
    for bid, person, fid, photo in rows:
        if fid is not None:
            store.assign(conn, [fid], person)
        else:
            store.tag_photos(conn, [photo], person)
        conn.execute("UPDATE bodies SET person_id=? WHERE id=?", (person, bid))
        conn.execute("DELETE FROM body_suggestions WHERE body_id=?", (bid,))
    conn.commit()
    return len(rows)


def reject(conn, body_ids):
    rows = conn.execute(f"SELECT body_id, person_id FROM body_suggestions WHERE body_id IN "
                        f"({','.join('?' * len(body_ids))})", list(body_ids)).fetchall()
    conn.executemany("INSERT OR IGNORE INTO body_rejects(body_id, person_id) VALUES(?,?)", rows)
    conn.executemany("DELETE FROM body_suggestions WHERE body_id=?", [(b,) for b, _ in rows])
    conn.commit()
    return len(rows)
