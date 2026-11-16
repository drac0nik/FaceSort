"""What's in a photo, beyond faces (1.44): how explicit it is, recommended tags, and whether it
has any people at all. Everything runs on this PC.

Two small models read each photo once, in the background:
 - NudeNet 3.4 (AGPL-3.0, notAI-tech; 12 MB): finds body areas, exposed or covered, with boxes.
   Downloaded from its package on PyPI and checked against the package's published checksum.
 - GantMan's NSFW model (MIT; MobileNet v2, 17 MB): the whole photo as drawing / hentai /
   neutral / porn / sexy. Converted unchanged to ONNX (.github/workflows/models.yml) and
   downloaded from FaceSort's GitHub page with its checksum.
The photo-search model (CLIP, clip_search.py), when its index is ready, adds a third opinion:
scene tags (beach, studio, food...) and what a photo without people is of.

Levels: 0 Safe, 1 Suggestive, 2 Explicit, 3 Unsure. What you say (right-click) always wins, and
FaceSort learns from your corrections (a small model on the CLIP descriptions)."""
import hashlib
import io
import json
import os
import threading
import time
import urllib.request
import zipfile

import cv2
import numpy as np

import engine

NUDENET_WHEEL = ("https://files.pythonhosted.org/packages/1c/ee/1aa02d44ba958cc77e16ff1e41a0aac5e721037db7bf62b9c9d124917f87/"
                 "nudenet-3.4.2-py3-none-any.whl")
NUDENET_WHEEL_SHA = "5937dbd84e5d8e5de038f08ffea5a1bb50a08475776bf2b4795914ce0eaf0331"
NUDENET_SHA = "c15d8273adad2d0a92f014cc69ab2d6c311a06777a55545f2c4eb46f51911f0f"
GANTMAN_URL = "https://github.com/drac0nik/FaceSort/releases/download/models-nsfw/gantman_nsfw.onnx"
DOWNLOAD_MB = 29
UA = {"User-Agent": "FaceSort"}

NUDE_LABELS = ["FEMALE_GENITALIA_COVERED", "FACE_FEMALE", "BUTTOCKS_EXPOSED", "FEMALE_BREAST_EXPOSED",
               "FEMALE_GENITALIA_EXPOSED", "MALE_BREAST_EXPOSED", "ANUS_EXPOSED", "FEET_EXPOSED", "BELLY_COVERED",
               "FEET_COVERED", "ARMPITS_COVERED", "ARMPITS_EXPOSED", "FACE_MALE", "BELLY_EXPOSED",
               "MALE_GENITALIA_EXPOSED", "ANUS_COVERED", "FEMALE_BREAST_COVERED", "BUTTOCKS_COVERED"]
EXPLICIT_PARTS = {"FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED", "ANUS_EXPOSED", "FEMALE_BREAST_EXPOSED"}
SUGGESTIVE_PARTS = {"BUTTOCKS_EXPOSED", "FEMALE_GENITALIA_COVERED", "FEMALE_BREAST_COVERED", "BUTTOCKS_COVERED",
                    "BELLY_EXPOSED", "ANUS_COVERED"}
BLUR_PARTS = EXPLICIT_PARTS | {"BUTTOCKS_EXPOSED"}
GANT = ["drawings", "hentai", "neutral", "porn", "sexy"]
LEVELS = {0: "Safe", 1: "Suggestive", 2: "Explicit", 3: "Unsure"}
PART_MIN = 0.45                     # a body area counts from this sure

# tags FaceSort starts with (Settings > Tagging > Tags): content tags come from the two models,
# scene tags from the photo descriptions; your own tags (and album names) learn from what you click
CONTENT_TAGS = ["Explicit", "Suggestive", "Nude", "Topless", "Lingerie", "Swimwear"]
SCENE_TAGS = ["Beach", "Pool", "Studio", "Outdoors", "Bedroom", "Bathroom", "Car", "Party", "Concert", "Selfie",
              "Night", "Snow", "City", "Nature", "Food", "Pets"]
PROMPTS = {"Swimwear": "a person wearing a swimsuit or bikini", "Lingerie": "a person wearing lingerie or underwear",
           "Beach": "a photo taken at the beach", "Pool": "a photo at a swimming pool", "Studio":
           "a studio photo shoot with a plain backdrop", "Outdoors": "a photo taken outdoors", "Bedroom":
           "a photo taken in a bedroom", "Bathroom": "a photo taken in a bathroom", "Car": "a photo taken in a car",
           "Party": "people at a party", "Concert": "a concert or live music show", "Selfie": "a selfie",
           "Night": "a photo taken at night", "Snow": "a snowy scene", "City": "a city street", "Nature":
           "a nature landscape", "Food": "a photo of food", "Pets": "a photo of a pet"}
# what a photo with nobody in it is of (Smart Groups > No people)
SUBJECTS = {"food": ("Food", ["a photo of food", "a meal on a plate", "a drink or dessert"]),
            "landscape": ("Landscapes and nature", ["a landscape", "nature scenery", "a city skyline",
                                                    "a building or architecture", "the sky and clouds"]),
            "text": ("Text and screenshots", ["a screenshot of a phone or computer screen", "a document with text",
                                              "a receipt", "a page of text", "a meme with text"]),
            "animals": ("Animals", ["a photo of a dog", "a photo of a cat", "a photo of an animal", "a bird"]),
            "other": ("Other", ["a photo of an object", "a car", "a room interior", "a drawing or artwork"])}
_LOCK = threading.Lock()


def model_dir():
    return os.path.join(engine.store_data_dir(), "models", "content")


def model_paths():
    return os.path.join(model_dir(), "320n.onnx"), os.path.join(model_dir(), "gantman_nsfw.onnx")


def ready():
    return all(os.path.exists(p) for p in model_paths())


def _get(url, timeout=60):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def download(progress=lambda d, t, m: None, cancelled=lambda: False):
    """Both models, once (about 29 MB), each checked. True when ready, False if cancelled."""
    os.makedirs(model_dir(), exist_ok=True)
    nude, gant = model_paths()
    if not os.path.exists(nude):
        progress(0, DOWNLOAD_MB, "Getting the content models… (NudeNet)")
        data = _get(NUDENET_WHEEL)
        if hashlib.sha256(data).hexdigest() != NUDENET_WHEEL_SHA:
            raise RuntimeError("NudeNet's package doesn't match its published checksum - not used")
        onnx = zipfile.ZipFile(io.BytesIO(data)).read("nudenet/320n.onnx")
        if hashlib.sha256(onnx).hexdigest() != NUDENET_SHA:
            raise RuntimeError("NudeNet's model doesn't match its checksum - not used")
        with open(nude + ".part", "wb") as f:
            f.write(onnx)
        os.replace(nude + ".part", nude)
    if cancelled():
        return False
    if not os.path.exists(gant):
        progress(12, DOWNLOAD_MB, "Getting the content models… (NSFW classifier)")
        import re
        want = re.search(r"\b([0-9a-f]{64})\b", _get(GANTMAN_URL + ".sha256", 30).decode("utf-8", "replace"))
        data = _get(GANTMAN_URL)
        if not want or hashlib.sha256(data).hexdigest() != want.group(1).lower():
            raise RuntimeError("the NSFW classifier doesn't match its published checksum - not used")
        with open(gant + ".part", "wb") as f:
            f.write(data)
        os.replace(gant + ".part", gant)
    progress(DOWNLOAD_MB, DOWNLOAD_MB, "Content models ready")
    return True


def _run(path, feed):
    import photo_tools
    return photo_tools._run(path, feed)


def read_picture(bgr):
    """{"gant": [5 probabilities], "parts": [[label, score, x, y, w, h], ...] (0-1 of the picture)}"""
    nude, gant = model_paths()
    h, w = bgr.shape[:2]
    side = max(h, w)
    pad = cv2.copyMakeBorder(bgr, 0, side - h, 0, side - w, cv2.BORDER_CONSTANT)
    blob = cv2.dnn.blobFromImage(pad, 1 / 255.0, (320, 320), (0, 0, 0), swapRB=True, crop=False)
    x = cv2.cvtColor(cv2.resize(bgr, (224, 224), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
    with _LOCK:
        out = _run(nude, {"images": blob})[0]
        g = _run(gant, {"input": (x.astype(np.float32) / 255)[None]})[0][0]
    rows = np.transpose(np.squeeze(out))
    boxes, scores, ids = [], [], []
    for r in rows:
        cs = r[4:]
        k = int(np.argmax(cs))
        if cs[k] < 0.2:
            continue
        cx, cy, bw, bh = r[:4] * side / 320
        boxes.append([float(cx - bw / 2), float(cy - bh / 2), float(bw), float(bh)])
        scores.append(float(cs[k]))
        ids.append(k)
    parts = []
    for i in (cv2.dnn.NMSBoxes(boxes, scores, 0.25, 0.45) if boxes else []):
        i = int(i)
        bx, by, bw, bh = boxes[i]
        bx, by = max(0.0, bx), max(0.0, by)
        parts.append([NUDE_LABELS[ids[i]], round(scores[i], 3), round(bx / w, 4), round(by / h, 4),
                      round(min(bw, w - bx) / w, 4), round(min(bh, h - by) / h, 4)])
    return {"gant": [round(float(v), 4) for v in g], "parts": parts}


def level_of(info, said=None, learned=None):
    """0 Safe, 1 Suggestive, 2 Explicit, 3 Unsure (None: not read yet)."""
    if said is not None:
        return int(said)
    if not info:
        return None
    g = dict(zip(GANT, info["gant"]))
    seen = {p[0] for p in info["parts"] if p[1] >= PART_MIN}
    hard = g["porn"] + g["hentai"]
    if hard >= 0.6 or (seen & EXPLICIT_PARTS and hard + g["sexy"] >= 0.4):
        lv = 2
    elif g["sexy"] >= 0.5 or hard >= 0.3 or (seen & SUGGESTIVE_PARTS and g["neutral"] < 0.8) or seen & EXPLICIT_PARTS:
        lv = 1
    elif g["neutral"] + g["drawings"] >= 0.75:
        lv = 0
    else:
        lv = 3
    if learned is not None:                 # FaceSort's own model, trained on your corrections
        nsfw, explicit = learned
        mine = 2 if explicit >= 0.6 else 1 if nsfw >= 0.6 else 0 if nsfw <= 0.25 else 3
        if mine != lv and 3 not in (mine, lv):
            return 3 if abs(mine - lv) > 1 or min(nsfw, 1 - nsfw) > 0.1 else mine
    return lv


def has_people(info):
    """Anyone in the picture, as far as NudeNet can tell (faces or bodies)."""
    return bool(info) and any(p[1] >= 0.35 for p in info["parts"] if not p[0].startswith("FEET"))


def blur_boxes(info):
    """Areas to blur in Safe view: [(x, y, w, h)] 0-1, or [] (then the whole picture is blurred)."""
    if not info:
        return []
    return [tuple(p[2:6]) for p in info["parts"] if p[0] in BLUR_PARTS and p[1] >= PART_MIN]


def read_missing(conn, progress=lambda d, t: None, cancelled=lambda: False):
    """(Background) Photos not read yet get read (pictures; a video's cover frame when it has one)."""
    if not ready():
        return 0
    rows = conn.execute("""SELECT ph.id, ph.path FROM photos ph LEFT JOIN photo_content pc ON pc.photo_id=ph.id
                           WHERE pc.photo_id IS NULL AND ph.error IS NULL AND ph.is_video=0
                           AND COALESCE(ph.is_other, 0)=0""").fetchall()
    import helpers
    done = 0
    # (1.45) photos are decoded on several threads ahead of the models (decoding is most of the
    # time - the graphics card would otherwise sit waiting for the next picture)
    pictures = helpers.decoded(rows, lambda r: engine.load_image(r[1], max_side=960).bgr)
    for n, ((pid, path), bgr) in enumerate(pictures, 1):
        if cancelled():
            break
        try:
            info = None if isinstance(bgr, Exception) else read_picture(bgr)
        except Exception:
            info = None
        conn.execute("INSERT OR REPLACE INTO photo_content(photo_id, info, level) VALUES(?, ?, ?)",
                     (pid, json.dumps(info) if info else None, level_of(info) if info else 3))
        done += 1
        if n % 20 == 0:
            conn.commit()
            progress(n, len(rows))
            time.sleep(0.01)
    conn.commit()
    if done:
        update_subjects(conn)
    return done


# ------------------------------------------------------------------ the photo descriptions (CLIP)
_TEXT = {}


def text_vec(prompt):
    """The CLIP description of a few words (kept for the session)."""
    if prompt not in _TEXT:
        import clip_search
        _TEXT[prompt] = clip_search.model().words(prompt).astype(np.float32)
    return _TEXT[prompt]


def clip_ok():
    try:
        import clip_search
        return clip_search.available()
    except Exception:
        return False


def clip_vecs(conn, ids=None):
    """{photo id: CLIP description} for these photos (or all that have one)."""
    q = "SELECT photo_id, vec FROM clip_emb WHERE vec IS NOT NULL"
    rows = []
    if ids is None:
        rows = conn.execute(q).fetchall()
    else:
        ids = list(ids)
        for i in range(0, len(ids), 900):
            part = ids[i:i + 900]
            rows += conn.execute(q + f" AND photo_id IN ({','.join('?' * len(part))})", part).fetchall()
    return {pid: np.frombuffer(v, np.float16).astype(np.float32) for pid, v in rows}


def no_people_ids(conn):
    """Photos with nobody in them: no face boxes, no bodies (Same shoot's person finder), and
    NudeNet found no one."""
    rows = conn.execute("""SELECT ph.id, pc.info FROM photos ph JOIN photo_content pc ON pc.photo_id=ph.id
                           WHERE ph.error IS NULL AND COALESCE(ph.is_other, 0)=0 AND ph.is_video=0
                           AND NOT EXISTS (SELECT 1 FROM faces f WHERE f.photo_id=ph.id AND f.ignored NOT IN (3, 4))
                           AND NOT EXISTS (SELECT 1 FROM bodies b WHERE b.photo_id=ph.id)
                           AND NOT EXISTS (SELECT 1 FROM photo_people pp WHERE pp.photo_id=ph.id)""").fetchall()
    return [pid for pid, info in rows if not has_people(json.loads(info) if info else None)]


def update_subjects(conn):
    """What each photo with nobody in it is of (food, landscape, text, animals, other), from its
    CLIP description; photos without one yet are "other" until the search index has them."""
    ids = no_people_ids(conn)
    conn.execute("UPDATE photo_content SET subject=NULL WHERE subject IS NOT NULL")
    if not ids:
        conn.commit()
        return 0
    vecs = clip_vecs(conn, ids) if clip_ok() else {}
    out = []
    if vecs:
        keys = list(SUBJECTS)
        T = np.stack([np.mean([text_vec(p) for p in SUBJECTS[k][1]], axis=0) for k in keys])
        T /= np.linalg.norm(T, axis=1, keepdims=True)
    for pid in ids:
        v = vecs.get(pid)
        out.append(("other" if v is None else keys[int(np.argmax(T @ (v / (np.linalg.norm(v) or 1))))], pid))
    conn.executemany("UPDATE photo_content SET subject=? WHERE photo_id=?", out)
    conn.commit()
    return len(out)


# ------------------------------------------------------------------ learning from you
def _train(X, y, steps=300):
    import face_attrs
    return face_attrs._train(X, y, steps=steps)


def learn_levels(conn, cancelled=lambda: False):
    """Your Safe / Suggestive / Explicit corrections teach FaceSort's own model (on the CLIP
    descriptions); every photo's level is then worked out again. Returns photos changed."""
    said = dict(conn.execute("SELECT photo_id, level_set FROM photo_content WHERE level_set IS NOT NULL"))
    learned = {}
    if said and clip_ok():
        vecs = clip_vecs(conn, said)
        lab = [(vecs[p], s) for p, s in said.items() if p in vecs]
        y_nsfw = np.array([1.0 if s in (1, 2) else 0.0 for _, s in lab], np.float32)
        y_exp = np.array([1.0 if s == 2 else 0.0 for _, s in lab], np.float32)
        if len(lab) >= 20 and 8 <= y_nsfw.sum() <= len(lab) - 8:
            X = np.stack([v for v, _ in lab])
            X /= np.linalg.norm(X, axis=1, keepdims=True) + 1e-9
            w1, b1 = _train(X, y_nsfw)
            w2, b2 = _train(X, y_exp) if 5 <= y_exp.sum() <= len(lab) - 5 else (None, None)
            allv = clip_vecs(conn)
            for pid, v in allv.items():
                if cancelled():
                    return 0
                v = v / (np.linalg.norm(v) or 1)
                p1 = float(1 / (1 + np.exp(-(v @ w1 + b1))))
                p2 = float(1 / (1 + np.exp(-(v @ w2 + b2)))) if w2 is not None else 0.0
                learned[pid] = (p1, p2)
    changed = []
    for pid, info, set_, old in conn.execute("SELECT photo_id, info, level_set, level FROM photo_content").fetchall():
        new = level_of(json.loads(info) if info else None, set_, learned.get(pid))
        if new is None:
            new = 3
        if new != old:
            changed.append((new, pid))
    conn.executemany("UPDATE photo_content SET level=? WHERE photo_id=?", changed)
    conn.commit()
    return len(changed)


def set_level(conn, photo_ids, value):
    """Your correction: 0 Safe, 1 Suggestive, 2 Explicit, None = let FaceSort judge again."""
    for pid in photo_ids:
        conn.execute("INSERT OR IGNORE INTO photo_content(photo_id, info, level) VALUES(?, NULL, 3)", (pid,))
        conn.execute("UPDATE photo_content SET level_set=? WHERE photo_id=?", (value, pid))
        if value is not None:
            conn.execute("UPDATE photo_content SET level=? WHERE photo_id=?", (value, pid))
        else:
            info = conn.execute("SELECT info FROM photo_content WHERE photo_id=?", (pid,)).fetchone()[0]
            lv = level_of(json.loads(info) if info else None)
            conn.execute("UPDATE photo_content SET level=? WHERE photo_id=?", (3 if lv is None else lv, pid))
    conn.commit()


# ------------------------------------------------------------------ recommended tags
class TagScorer:
    """Scores tags for a photo: content tags from the two models, scene tags from the CLIP
    description, and your own tags / albums from the photos you've put in them (a small model per
    album, retrained when the album changes). Built once and reused while nothing changes."""

    def __init__(self, conn, tag_list):
        self.conn = conn
        self.tags = list(dict.fromkeys(tag_list))
        self.albums = {n: i for i, n in conn.execute("SELECT id, name FROM albums")}
        self.clip = clip_ok()
        self._models = {}
        self._base = None

    def _album_model(self, name):
        aid = self.albums.get(name)
        if aid is None:
            return None
        sig = self.conn.execute("SELECT COUNT(*), TOTAL(photo_id) FROM album_photos WHERE album_id=?", (aid,)).fetchone()
        sig += self.conn.execute("SELECT COUNT(*) FROM album_rejects WHERE album_id=?", (aid,)).fetchone()
        got = self._models.get(name)
        if got and got[0] == sig:
            return got[1]
        model = None
        pos = [r[0] for r in self.conn.execute("SELECT photo_id FROM album_photos WHERE album_id=?", (aid,))]
        if self.clip and len(pos) >= 5:
            rej = [r[0] for r in self.conn.execute("SELECT photo_id FROM album_rejects WHERE album_id=?", (aid,))]
            other = [r[0] for r in self.conn.execute(
                "SELECT photo_id FROM clip_emb WHERE photo_id NOT IN (SELECT photo_id FROM album_photos WHERE album_id=?) "
                "ORDER BY (photo_id * 2654435761) % 1000003 LIMIT ?", (aid, max(200, 4 * len(pos))))]
            vecs = clip_vecs(self.conn, pos + rej + other)
            P = [vecs[p] for p in pos if p in vecs]
            N = [vecs[p] for p in rej + other if p in vecs]
            if len(P) >= 5 and len(N) >= 20:
                X = np.stack(P + N)
                X /= np.linalg.norm(X, axis=1, keepdims=True) + 1e-9
                y = np.array([1.0] * len(P) + [0.0] * len(N), np.float32)
                model = _train(X, y, steps=200)
        self._models[name] = (sig, model)
        return model

    def _baseline(self):
        """How well each tag's words fit photos in general (so a tag is suggested when a photo fits
        it clearly better than usual - CLIP scores aren't comparable between tags otherwise)."""
        if self._base is None:
            sample = list(clip_vecs(self.conn, [r[0] for r in self.conn.execute(
                "SELECT photo_id FROM clip_emb ORDER BY (photo_id * 2654435761) % 1000003 LIMIT 1500")]).values())
            self._base = {}
            if sample:
                S = np.stack(sample)
                S /= np.linalg.norm(S, axis=1, keepdims=True) + 1e-9
                for t in self.tags:
                    s = S @ text_vec(PROMPTS.get(t, f"a photo of {t}"))
                    self._base[t] = (float(s.mean()), float(s.std()) + 1e-6)
        return self._base

    def scores(self, photo_id):
        """{tag: 0-1} for one photo (only tags with some evidence)."""
        out = {}
        row = self.conn.execute("SELECT info, level FROM photo_content WHERE photo_id=?", (photo_id,)).fetchone()
        info = json.loads(row[0]) if row and row[0] else None
        level = row[1] if row else None
        seen = {p[0]: p[1] for p in (info or {}).get("parts", []) if p[1] >= PART_MIN}
        if level == 2:
            out["Explicit"] = 0.95
        elif level == 1:
            out["Suggestive"] = 0.9
        if {"FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED"} & set(seen) or \
                ("FEMALE_BREAST_EXPOSED" in seen and "BUTTOCKS_EXPOSED" in seen):
            out["Nude"] = 0.9
        if "FEMALE_BREAST_EXPOSED" in seen:
            out["Topless"] = max(seen["FEMALE_BREAST_EXPOSED"], 0.6)
        covered = {"FEMALE_BREAST_COVERED", "FEMALE_GENITALIA_COVERED"} & set(seen)
        v = clip_vecs(self.conn, [photo_id]).get(photo_id) if self.clip else None
        if v is not None:
            v = v / (np.linalg.norm(v) or 1)
            base = self._baseline()
            for t in self.tags + [a for a in self.albums if a not in self.tags]:
                if t in ("Explicit", "Suggestive", "Nude", "Topless"):
                    continue
                m = self._album_model(t)
                if m is not None:                         # your own album: learned from its photos
                    p = float(1 / (1 + np.exp(-(v @ m[0] + m[1]))))
                    if p >= 0.5:
                        out[t] = max(out.get(t, 0), p)
                    continue
                if t in base:
                    z = (float(v @ text_vec(PROMPTS.get(t, f"a photo of {t}"))) - base[t][0]) / base[t][1]
                    if t in ("Lingerie", "Swimwear") and covered:
                        z += 1.0
                    if z >= 1.6:
                        out[t] = max(out.get(t, 0), min(0.95, 0.5 + z / 8))
        elif covered:
            out["Lingerie"] = 0.55
        return out

    def suggest(self, photo_id, have=(), n=10):
        """Up to n tags: the ones the photo has first, then the best suggestions."""
        have = list(dict.fromkeys(have))
        sc = self.scores(photo_id)
        rest = [t for t, _ in sorted(sc.items(), key=lambda kv: -kv[1]) if t not in have and t in self.tags + list(self.albums)]
        out = have[:n] + rest[:max(0, n - len(have))]
        if len(out) < n:                 # then the tags you've used lately (never a content tag it has no sign of)
            for (t,) in self.conn.execute("""SELECT a.name FROM albums a JOIN album_photos ap ON ap.album_id=a.id
                                             WHERE a.tag=1 GROUP BY a.id ORDER BY MAX(ap.added) DESC LIMIT 40"""):
                if t not in out and t not in CONTENT_TAGS and t in self.tags + list(self.albums):
                    out.append(t)
                    if len(out) >= n:
                        break
        return out

    def album_suggestions(self, album_name, limit=60):
        """Photos FaceSort thinks belong in this album (from the photos you put in it)."""
        m = self._album_model(album_name)
        if m is None:
            return []
        aid = self.albums[album_name]
        skip = {r[0] for r in self.conn.execute("SELECT photo_id FROM album_photos WHERE album_id=? UNION "
                                                "SELECT photo_id FROM album_rejects WHERE album_id=?", (aid, aid))}
        vecs = clip_vecs(self.conn)
        ids = [p for p in vecs if p not in skip]
        if not ids:
            return []
        X = np.stack([vecs[p] for p in ids])
        X /= np.linalg.norm(X, axis=1, keepdims=True) + 1e-9
        p = 1 / (1 + np.exp(-(X @ m[0] + m[1])))
        order = np.argsort(-p)
        return [(ids[i], float(p[i])) for i in order[:limit] if p[i] >= 0.7]
