"""SQLite library, settings, and face grouping."""
import hashlib
import json
import os
import re
import time
import sqlite3
import sys

import numpy as np

APP_NAME = "FaceSort"


def data_dir():
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    d = os.environ.get("FACESORT_DATA") or os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


# ----------------------------------------------------------------- settings
DEFAULT_SETTINGS = {
    "folders": [],
    "files": [],
    "scan_on_start": False,     # look for new photos every time FaceSort opens                # single photos/videos dropped in from outside your folders
    "threshold": 0.42,          # standard engine: face similarity needed to group / match (0.30 loose .. 0.55 strict)
    "threshold_arcface": 0.32,  # high-accuracy engine: same, on its own scale (0.22 loose .. 0.46 strict)
    "threshold_adaface": 0.32,  # best-accuracy (AdaFace) engine: about the same scale as ArcFace (tested)
    "engine": "sface",          # "arcface" = high accuracy, "sface" = standard (fast); asked once on first start
    "engine_chosen": False,
    "min_face_px": 20,          # ignore faces smaller than this (in the 1600px working image)
    "detect_threshold": 0.75,   # face detection confidence (0.55 finds more .. 0.90 fewer mistakes)
    "look_sideways": True,      # also try photos turned 90° when no confident face is found
    "tattoo_min_inliers": 14,   # tattoo matching strictness (10 looser .. 30 stricter)
    "keywords_all_people": False,  # treat every plain keyword on imported photos as a person's name
    "threads": max(1, min(4, (os.cpu_count() or 2) - 1)),
    "use_gpu": True,            # face detection/recognition on the GPU when available
    "tag_suggestions": False,   # also write tags for unconfirmed (suggested) matches
    "write_regions": True,      # write MWG face rectangles with names (digiKam, Lightroom...)
    "always_sidecar": False,    # never modify photo files; write .xmp sidecars instead
    "keep_modified_date": True,
    "keyword_prefix": "",
    "show_singles": True,
    "background_cpu": "gentle",  # "gentle": background updates at low priority, half the CPU, after you pause
    "folder_hints": True,       # faces in the same photo folder group together a little more easily
    "dup_prefer_raw": True,     # duplicates: a camera RAW file is the copy to keep, whatever the sizes
    "dup_raw_jpeg_pairs": True,  # duplicates: RAW + JPEG with the same name in the same folder are on purpose
}


SETTINGS_VERSION = 2

THRESHOLD_RANGE = {"sface": (0.30, 0.56), "arcface": (0.22, 0.46), "adaface": (0.22, 0.46)}
HQ_MODELS = ("arcface", "adaface")   # engines on the SCRFD detector, 512-number fingerprints


def threshold_key(model):
    return {"arcface": "threshold_arcface", "adaface": "threshold_adaface"}.get(model, "threshold")


def model_dim(model):
    return 512 if model in HQ_MODELS else 128
# "Probably not a face" (measured on 61 junk detections vs 181 real faces incl. partly hidden ones):
WEAK_READING = 13.5        # high accuracy: weaker than this -> set aside even without marks (57% junk, 0 real)
NOTFACE_SIM = {"arcface": 0.45, "adaface": 0.45, "sface": 0.55}   # looks like something you marked with X ...
NOTFACE_WEAK = 16.0        # ... and (high accuracy) both read weakly -> set aside (74% junk, 0 real)
NOTFACE_STRONG_SIM = 0.55  # high accuracy: very alike a strongly-read mark (patterns) -> set aside (86% of a
                           # repeating pattern from ONE mark; real faces never came closer than 0.30)


# face clarity (see engine.clarity_from_norm)
REF_MIN_CLARITY = 0.3      # someone's photos clearer than this are what other faces are compared with
MMAP_BYTES = 1 << 30      # up to 1 GB of the library file read through memory-mapping
SIDE_ON_EXTRA = 0.05      # a face turned fully side-on needs a match this much closer (head angle, 1.42)
LOW_CLARITY_EXTRA = 0.06   # a completely unclear face needs a match this much closer to be suggested
DEFAULT_CLARITY = 0.7      # faces not read yet
# gallery order "clearest first": clarity, then size x confidence
CLEAR_ORDER = "(COALESCE({a}clarity, 0.7) * 2000 + MIN({a}px, 1000) * {a}score)"


def clear_order(alias="f."):
    return CLEAR_ORDER.format(a=alias)


def threshold(settings, model=None):
    """Face-matching threshold for the engine in use (each engine has its own scale)."""
    model = model or settings.get("engine", "sface")
    return float(settings.get(threshold_key(model), 0.32 if model in HQ_MODELS else 0.42))


def load_settings():
    s = dict(DEFAULT_SETTINGS)
    try:
        with open(os.path.join(data_dir(), "settings.json"), "r", encoding="utf-8") as f:
            saved = json.load(f)
        if saved.get("settings_version", 1) < 2 and saved.get("min_face_px") == 32:
            saved["min_face_px"] = 20  # old default skipped small faces in group photos
        s.update(saved)
    except Exception:
        pass
    s["settings_version"] = SETTINGS_VERSION
    return s


def save_settings(s):
    path = os.path.join(data_dir(), "settings.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)
    os.replace(tmp, path)


# ----------------------------------------------------------------- database
SCHEMA = """
CREATE TABLE IF NOT EXISTS photos(
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    mtime REAL, size INTEGER,
    width INTEGER, height INTEGER, orientation INTEGER DEFAULT 1,
    nfaces INTEGER DEFAULT 0,
    error TEXT,
    tagsig TEXT,                      -- what FaceSort last wrote into the file
    discarded INTEGER DEFAULT 0,      -- whole photo marked to discard (photos without faces)
    tags_read INTEGER DEFAULT 0,      -- keywords already on the file have been read
    sort_later INTEGER DEFAULT 0,     -- in "Sort Later": hidden from every other list until taken out
    taken REAL,                       -- when the photo was taken (EXIF), unix time
    taken_read INTEGER DEFAULT 0,
    category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    is_video INTEGER DEFAULT 0,       -- a video (faces come from frames spread through it)
    created REAL,                     -- when the file was created (for "Date created")
    phash INTEGER,                    -- picture fingerprint (duplicates)
    ai INTEGER DEFAULT 0,             -- 1 = the file says it's AI-made/edited, 2 = the detector thinks so
    ai_info TEXT,                     -- why
    ai_score REAL,                    -- detector score 0..1 (when the detector has looked)
    extras_read INTEGER DEFAULT 0,    -- fingerprint + AI labels done
    cover_t REAL                      -- videos: the frame shown as its picture (seconds)
);
CREATE TABLE IF NOT EXISTS persons(
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL COLLATE NOCASE,
    last_used REAL DEFAULT 0,
    category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL   -- placed in a category (one place)
);
CREATE TABLE IF NOT EXISTS categories(          -- folders: your save layout ("Models", "Jane Doe › Beach")
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE,          -- unique only among its siblings
    last_used REAL DEFAULT 0,
    uses INTEGER DEFAULT 0,
    parent_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,  -- nesting (folders inside folders)
    inside_person INTEGER DEFAULT 0,  -- (old versions) shared folder inside each person's folder
    owner_id INTEGER REFERENCES persons(id) ON DELETE CASCADE   -- top folder of a person's own folders
);
CREATE TABLE IF NOT EXISTS placed_paths(      -- files Sort into folders wrote into the sorted library (1.42)
    path TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS photo_folders(      -- person folders a photo is in (one per person)
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    folder_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    PRIMARY KEY(photo_id, folder_id)
);
CREATE TABLE IF NOT EXISTS faces(
    id INTEGER PRIMARY KEY,
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    x REAL, y REAL, w REAL, h REAL,
    score REAL, px INTEGER,
    emb BLOB NOT NULL,
    thumb BLOB,
    person_id INTEGER REFERENCES persons(id) ON DELETE SET NULL,
    confirmed INTEGER DEFAULT 0,     -- 1 = set by you, 0 = suggestion
    cluster INTEGER,                  -- unnamed group id
    ignored INTEGER DEFAULT 0,       -- 1 = ignored (stranger), 2 = discarded, 3 = not a face, 4 = probably not a face
    fhash TEXT,                       -- fingerprint hash (links to the known-people file)
    model TEXT DEFAULT 'sface',       -- which face engine measured it (fingerprints never mix)
    face_ok INTEGER DEFAULT 0,        -- you said "it is a face" (never set aside automatically)
    quality REAL,                     -- how strongly the high-accuracy engine reads it as a face
    later_prev INTEGER,               -- (ignored = 5 means its photo is in Sort Later) state to restore
    frame_t REAL,                     -- videos: the frame (seconds into the video) the face is in
    manual INTEGER DEFAULT 0,         -- drawn by you (Tag Person): 1 = measured, 2 = no fingerprint
    later_cluster INTEGER             -- look-alike group inside Sort Later
);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX IF NOT EXISTS faces_photo ON faces(photo_id);
CREATE INDEX IF NOT EXISTS faces_person ON faces(person_id);
CREATE INDEX IF NOT EXISTS faces_cluster ON faces(cluster);
CREATE TABLE IF NOT EXISTS rejects(           -- "this face is NOT that person"
    face_id INTEGER REFERENCES faces(id) ON DELETE CASCADE,
    person_id INTEGER REFERENCES persons(id) ON DELETE CASCADE,
    PRIMARY KEY(face_id, person_id)
);
CREATE TABLE IF NOT EXISTS photo_people(       -- people tagged on a photo without a face box
    photo_id INTEGER REFERENCES photos(id) ON DELETE CASCADE,
    person_id INTEGER REFERENCES persons(id) ON DELETE CASCADE,
    src TEXT DEFAULT 'user',                     -- 'user', 'file' (read from the photo's tags), 'tattoo'
    PRIMARY KEY(photo_id, person_id)
);
CREATE TABLE IF NOT EXISTS tattoos(            -- tattoos you marked (or confirmed matches): references
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    x REAL, y REAL, w REAL, h REAL,
    src TEXT DEFAULT 'mark'                      -- 'mark' = drawn by you, 'match' = learned from a confirmed match
);
CREATE TABLE IF NOT EXISTS tattoo_suggestions(
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    x REAL, y REAL, w REAL, h REAL, inliers INTEGER,
    PRIMARY KEY(photo_id, person_id)
);
CREATE TABLE IF NOT EXISTS tattoo_rejects(     -- "that's not their tattoo"
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    PRIMARY KEY(photo_id, person_id)
);
CREATE TABLE IF NOT EXISTS tattoo_checked(     -- photo already searched for this tattoo
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    tattoo_id INTEGER NOT NULL REFERENCES tattoos(id) ON DELETE CASCADE,
    PRIMARY KEY(photo_id, tattoo_id)
);
CREATE TABLE IF NOT EXISTS face_links(         -- Quick review: "same person" (kept in one group)
    a INTEGER REFERENCES faces(id) ON DELETE CASCADE,
    b INTEGER REFERENCES faces(id) ON DELETE CASCADE,
    PRIMARY KEY(a, b)
);
CREATE TABLE IF NOT EXISTS face_nolinks(       -- Quick review: "not the same person"
    a INTEGER REFERENCES faces(id) ON DELETE CASCADE,
    b INTEGER REFERENCES faces(id) ON DELETE CASCADE,
    PRIMARY KEY(a, b)
);
CREATE TABLE IF NOT EXISTS drop_rules(         -- tags chosen when files/folders were dropped in, applied as they're scanned
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,                          -- a file, or a folder (everything inside it)
    is_dir INTEGER DEFAULT 0,
    people TEXT,                                 -- JSON list of names tagged on every photo
    category TEXT,
    sort_later INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS albums(             -- 1.26: a photo can be in any number of albums (files never move)
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created REAL
);
CREATE TABLE IF NOT EXISTS album_photos(
    album_id INTEGER REFERENCES albums(id) ON DELETE CASCADE,
    photo_id INTEGER REFERENCES photos(id) ON DELETE CASCADE,
    added REAL,
    PRIMARY KEY(album_id, photo_id)
);
CREATE TABLE IF NOT EXISTS clip_emb(           -- 1.26: search by description (a picture's CLIP embedding)
    photo_id INTEGER PRIMARY KEY REFERENCES photos(id) ON DELETE CASCADE,
    vec BLOB
);
CREATE TABLE IF NOT EXISTS written(            -- keywords FaceSort put in each file
    photo_id INTEGER REFERENCES photos(id) ON DELETE CASCADE,
    keyword TEXT,
    PRIMARY KEY(photo_id, keyword)
);
"""


def connect(path=None):
    """A connection to the library. All of them write through one write gate (dbgate): one
    writer at a time, the window first, background writes saved one short statement at a time."""
    import dbgate
    conn = sqlite3.connect(path or os.path.join(data_dir(), "library.db"), timeout=30,
                           factory=dbgate.GatedConnection)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    # read the library through memory-mapping, with a bigger cache: after a background job writes
    # (updating suggestions, reading face details) the lists re-read their indexes - from memory
    # this way, instead of copying them from disk again (seconds on a big library)
    conn.execute(f"PRAGMA mmap_size={MMAP_BYTES}")
    conn.execute("PRAGMA cache_size=-32768")          # 32 MB per connection (default: 2 MB)
    conn.executescript(SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(faces)")}
    if "fhash" not in cols:  # library from version 1.0.x
        conn.execute("ALTER TABLE faces ADD COLUMN fhash TEXT")
        rows = conn.execute("SELECT id, emb FROM faces").fetchall()
        conn.executemany("UPDATE faces SET fhash=? WHERE id=?", [(hashlib.sha1(b).hexdigest(), i) for i, b in rows])
        conn.commit()
    conn.execute("CREATE INDEX IF NOT EXISTS faces_fhash ON faces(fhash)")
    fcols = {r[1] for r in conn.execute("PRAGMA table_info(faces)")}
    if "model" not in fcols:   # library from version 1.7 or older
        conn.execute("ALTER TABLE faces ADD COLUMN model TEXT DEFAULT 'sface'")
        conn.execute("ALTER TABLE faces ADD COLUMN face_ok INTEGER DEFAULT 0")
        conn.execute("ALTER TABLE faces ADD COLUMN quality REAL")
        conn.commit()
    conn.execute("CREATE INDEX IF NOT EXISTS faces_model ON faces(model)")
    pcols = {r[1] for r in conn.execute("PRAGMA table_info(photos)")}
    if "discarded" not in pcols:  # library from version 1.3 or older
        conn.execute("ALTER TABLE photos ADD COLUMN discarded INTEGER DEFAULT 0")
        conn.commit()
    if "taken" not in pcols:   # library from version 1.6 or older: dates get read on next scan
        conn.execute("ALTER TABLE photos ADD COLUMN taken REAL")
        conn.execute("ALTER TABLE photos ADD COLUMN taken_read INTEGER DEFAULT 0")
        conn.commit()
    if "tags_read" not in pcols:   # keywords on photos scanned by older versions get read on next scan
        conn.execute("ALTER TABLE photos ADD COLUMN tags_read INTEGER DEFAULT 0")
        conn.commit()
    if "src" not in {r[1] for r in conn.execute("PRAGMA table_info(photo_people)")}:
        conn.execute("ALTER TABLE photo_people ADD COLUMN src TEXT DEFAULT 'user'")
        conn.commit()
    if "category_id" not in pcols:  # library from version 1.4 or older
        conn.execute("ALTER TABLE photos ADD COLUMN category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL")
        conn.commit()
    pecols = {r[1] for r in conn.execute("PRAGMA table_info(persons)")}
    if "last_used" not in pecols:
        conn.execute("ALTER TABLE persons ADD COLUMN last_used REAL DEFAULT 0")
        conn.commit()
    if "category_id" not in pecols:   # 1.9: a person can be placed in a category (all their photos go there)
        conn.execute("ALTER TABLE persons ADD COLUMN category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL")
        conn.commit()
    if "parent_id" not in {r[1] for r in conn.execute("PRAGMA table_info(categories)")}:
        conn.execute("ALTER TABLE categories ADD COLUMN parent_id INTEGER REFERENCES categories(id) ON DELETE SET NULL")
        conn.commit()
    if "sort_later" not in pcols:
        conn.execute("ALTER TABLE photos ADD COLUMN sort_later INTEGER DEFAULT 0")
        conn.commit()
    if "later_prev" not in {r[1] for r in conn.execute("PRAGMA table_info(faces)")}:
        conn.execute("ALTER TABLE faces ADD COLUMN later_prev INTEGER")
        conn.commit()
    if "inside_person" not in {r[1] for r in conn.execute("PRAGMA table_info(categories)")}:   # 1.12
        conn.execute("ALTER TABLE categories ADD COLUMN inside_person INTEGER DEFAULT 0")
        conn.commit()
    if "batch" not in {r[1] for r in conn.execute("PRAGMA table_info(drop_rules)")}:      # 1.17: Recently dropped
        conn.execute("ALTER TABLE drop_rules ADD COLUMN batch REAL")
        conn.commit()
    if "dropped" not in {r[1] for r in conn.execute("PRAGMA table_info(photos)")}:
        conn.execute("ALTER TABLE photos ADD COLUMN dropped REAL")      # in Recently dropped (which drop)
        conn.commit()
    conn.execute("CREATE TABLE IF NOT EXISTS drop_batches(batch REAL PRIMARY KEY, name TEXT)")
    # 1.20: other files that came with a drop (notes, links, documents, archives) - filed with the photos
    conn.execute("""CREATE TABLE IF NOT EXISTS drop_files(id INTEGER PRIMARY KEY, batch REAL, path TEXT UNIQUE,
                    kind TEXT, root TEXT)""")
    _vault_columns(conn)
    if "vault_prev" not in {r[1] for r in conn.execute("PRAGMA table_info(photos)")}:
        conn.execute("ALTER TABLE photos ADD COLUMN vault_prev INTEGER")     # its Sort Later state before a vault hid it
    if "vault_prev" not in {r[1] for r in conn.execute("PRAGMA table_info(faces)")}:
        conn.execute("ALTER TABLE faces ADD COLUMN vault_prev INTEGER")      # its state before a vault hid it
    conn.commit()
    if "copy_of" not in {r[1] for r in conn.execute("PRAGMA table_info(photos)")}:   # 1.19: JPEG copies of RAWs
        conn.execute("ALTER TABLE photos ADD COLUMN copy_of INTEGER")    # made from this photo (skipped by Duplicates)
        conn.commit()
    if "drop_folder" not in {r[1] for r in conn.execute("PRAGMA table_info(photos)")}:
        conn.execute("ALTER TABLE photos ADD COLUMN drop_folder TEXT")   # 1.26: folder names kept for people tagged later
    if "structure" not in {r[1] for r in conn.execute("PRAGMA table_info(drop_rules)")}:
        conn.execute("ALTER TABLE drop_rules ADD COLUMN structure INTEGER")   # NULL = folder names not used
        conn.commit()
    pc = {r[1] for r in conn.execute("PRAGMA table_info(photos)")}
    for col, decl in (("phash", "INTEGER"), ("ai", "INTEGER DEFAULT 0"), ("ai_info", "TEXT"), ("ai_score", "REAL"),
                      ("extras_read", "INTEGER DEFAULT 0")):     # 1.16: duplicates + AI images
        if col not in pc:
            conn.execute(f"ALTER TABLE photos ADD COLUMN {col} {decl}")
    conn.execute("""CREATE TABLE IF NOT EXISTS dup_ignore(a INTEGER, b INTEGER, PRIMARY KEY(a, b))""")
    # 1.16.1: a small grey copy of pictures that look alike, for the close duplicate check
    conn.execute("""CREATE TABLE IF NOT EXISTS dup_fine(photo_id INTEGER PRIMARY KEY, size INTEGER,
                    aspect REAL, px BLOB)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS dup_checked(a INTEGER, b INTEGER, sa INTEGER, sb INTEGER,
                    same INTEGER, PRIMARY KEY(a, b))""")      # close checks already done
    conn.commit()
    if "created" not in {r[1] for r in conn.execute("PRAGMA table_info(photos)")}:   # 1.15: sort by date created
        conn.execute("ALTER TABLE photos ADD COLUMN created REAL")
        conn.commit()
    if "is_video" not in {r[1] for r in conn.execute("PRAGMA table_info(photos)")}:   # 1.10: videos
        conn.execute("ALTER TABLE photos ADD COLUMN is_video INTEGER DEFAULT 0")
        conn.execute("ALTER TABLE photos ADD COLUMN cover_t REAL")
        conn.commit()
    if "frame_t" not in {r[1] for r in conn.execute("PRAGMA table_info(faces)")}:
        conn.execute("ALTER TABLE faces ADD COLUMN frame_t REAL")
        conn.commit()
    if "manual" not in {r[1] for r in conn.execute("PRAGMA table_info(faces)")}:   # 1.13: Tag Person
        conn.execute("ALTER TABLE faces ADD COLUMN manual INTEGER DEFAULT 0")
        conn.commit()
    if "later_cluster" not in {r[1] for r in conn.execute("PRAGMA table_info(faces)")}:   # 1.14: groups in Sort Later
        conn.execute("ALTER TABLE faces ADD COLUMN later_cluster INTEGER")
        conn.commit()
    pc2 = {r[1] for r in conn.execute("PRAGMA table_info(photos)")}
    for col, decl in (("bright", "REAL"), ("dark_seen", "INTEGER DEFAULT 0"), ("lat", "REAL"), ("lon", "REAL")):
        if col not in pc2:     # 1.36: brightness (second look at dark photos), GPS position (same shoot)
            conn.execute(f"ALTER TABLE photos ADD COLUMN {col} {decl}")
    if "is_other" not in pc2:           # 1.37: other files (documents, audio, archives...) - recorded, never opened
        conn.execute("ALTER TABLE photos ADD COLUMN is_other INTEGER DEFAULT 0")
    if "body_done" not in pc2:          # 1.36: same-shoot suggestions (people found by their whole figure)
        conn.execute("ALTER TABLE photos ADD COLUMN body_done INTEGER DEFAULT 0")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS bodies(id INTEGER PRIMARY KEY, photo_id INTEGER, x REAL, y REAL, w REAL, h REAL,
            score REAL, desc BLOB, face_id INTEGER, person_id INTEGER, thumb BLOB);
        CREATE INDEX IF NOT EXISTS bodies_photo ON bodies(photo_id);
        CREATE INDEX IF NOT EXISTS bodies_face ON bodies(face_id);
        CREATE TABLE IF NOT EXISTS photo_scene(photo_id INTEGER PRIMARY KEY, colours BLOB);
        CREATE TABLE IF NOT EXISTS body_suggestions(body_id INTEGER PRIMARY KEY, person_id INTEGER, score REAL,
            reason TEXT, face_id INTEGER);
        CREATE TABLE IF NOT EXISTS body_rejects(body_id INTEGER, person_id INTEGER, PRIMARY KEY(body_id, person_id));
        CREATE TRIGGER IF NOT EXISTS photos_gone_bodies AFTER DELETE ON photos BEGIN
            DELETE FROM body_suggestions WHERE body_id IN (SELECT id FROM bodies WHERE photo_id=OLD.id);
            DELETE FROM bodies WHERE photo_id=OLD.id;
            DELETE FROM photo_scene WHERE photo_id=OLD.id;
        END;
        CREATE TRIGGER IF NOT EXISTS faces_gone_bodies AFTER DELETE ON faces BEGIN
            UPDATE bodies SET face_id=NULL WHERE face_id=OLD.id;
        END;
        CREATE TRIGGER IF NOT EXISTS persons_gone_bodies AFTER DELETE ON persons BEGIN
            DELETE FROM body_suggestions WHERE person_id=OLD.id;
            UPDATE bodies SET person_id=NULL WHERE person_id=OLD.id;
        END;""")
    fc = {r[1] for r in conn.execute("PRAGMA table_info(faces)")}
    if "clarity" not in fc:            # 1.36: how clearly each face can be recognised (0-1)
        conn.execute("ALTER TABLE faces ADD COLUMN clarity REAL")
        conn.commit()
    if "outside" not in {r[1] for r in conn.execute("PRAGMA table_info(photos)")}:
        # 1.42: 1 = the scan found this file somewhere new - added or moved outside FaceSort, not
        # put there by Sort into folders (photos already in the library count as known)
        conn.execute("ALTER TABLE photos ADD COLUMN outside INTEGER DEFAULT 0")
        conn.commit()
    if "male" not in fc:               # 1.42: looks male (0-1) and head angle in degrees (face_attrs.py)
        for col in ("male", "yaw", "pitch", "roll"):
            conn.execute(f"ALTER TABLE faces ADD COLUMN {col} REAL")
        conn.commit()
    conn.execute("CREATE INDEX IF NOT EXISTS photos_category ON photos(category_id)")
    # covering index: lists and counts read this instead of the face rows (which hold fingerprints
    # and thumbnails), so big libraries stay quick
    new_index = not conn.execute("SELECT 1 FROM sqlite_master WHERE name='faces_cover'").fetchone()
    if not new_index and "yaw" not in {r[2] for r in conn.execute("PRAGMA index_info(faces_cover)")}:
        conn.execute("DROP INDEX faces_cover")          # 1.42: the index also holds the face details
        new_index = True
    conn.execute("CREATE INDEX IF NOT EXISTS faces_cover ON faces(ignored, person_id, confirmed, cluster, photo_id, "
                 "w, h, px, score, manual, clarity, male, yaw, pitch)")
    if new_index or not conn.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_stat1'").fetchone():
        conn.execute("ANALYZE")      # lets SQLite pick the right index for each query
        conn.commit()
    conn.execute("CREATE INDEX IF NOT EXISTS photo_people_person ON photo_people(person_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS photo_folders_folder ON photo_folders(folder_id)")
    # 1.29: look-ups that scanned a whole table on big libraries (and on deleting a photo / person)
    for name, sql in (("album_photos_photo", "album_photos(photo_id)"),
                      ("tattoo_sugg_person", "tattoo_suggestions(person_id)"),
                      ("rejects_person", "rejects(person_id)"),
                      ("face_links_b", "face_links(b)"),
                      ("face_nolinks_b", "face_nolinks(b)"),
                      ("photos_later", "photos(sort_later)")):
        try:
            conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {sql}")
        except sqlite3.OperationalError:
            pass
    migrate_folders(conn)
    return conn


def _video_exts():
    try:
        from engine import VIDEO_EXTS as v      # one list for the whole app
        return set(v)
    except Exception:
        return {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".wmv", ".webm", ".3gp", ".mts", ".m2ts", ".mpg", ".mpeg",
                ".flv", ".f4v", ".asf", ".rm", ".rmvb", ".vob", ".divx", ".xvid", ".ogv", ".ogm", ".dv", ".m1v",
                ".m2v", ".mpe", ".mod", ".tod", ".3g2", ".qt", ".ts", ".amv"}


VIDEO_EXTS = _video_exts()


def path_is_video(path):
    return os.path.splitext(path)[1].lower() in VIDEO_EXTS


def emb_from_blob(b):
    return np.frombuffer(b, dtype=np.float32)


def norm_path(p):
    return os.path.normpath(os.path.abspath(p))


# ----------------------------------------------------------------- library ops
def mark_placed(conn, path):
    """Sort into folders wrote this file: when a scan finds it, it isn't "added outside FaceSort"."""
    conn.execute("INSERT OR IGNORE INTO placed_paths(path) VALUES(?)", (norm_path(path),))


def was_placed(conn, path):
    return conn.execute("SELECT 1 FROM placed_paths WHERE path=?", (norm_path(path),)).fetchone() is not None


def store_photo(conn, path, mtime, size, img, faces, hints=(), marks=(), photo_names=(), category=None,
                keywords=None, tattoo_marks=(), settings=None, model="sface", manual_faces=(), person_folders=()):
    """Insert/replace one scanned photo. Keeps names on faces that were already known
    (matched by position), so re-scanning an edited file doesn't lose your work.
    `hints` are named faces read from the photo's own tags: (name, x, y, w, h).
    `marks` are remembered ignored/discarded faces: (x, y, w, h, state); a mark with x < 0
    means the whole photo was discarded.
    `photo_names` are people tagged on the photo itself (no face box).
    `category` is the photo's remembered category name (from its tags or the known file).
    `keywords` = (people, plain) keywords already on the file (see apply_keywords).
    `tattoo_marks` = remembered tattoo marks: (name, x, y, w, h, src)."""
    old, old_t = {}, {}
    video = path_is_video(path)
    old_ok, old_rejects, old_links, old_nolinks = set(), {}, [], []
    row = conn.execute("SELECT id FROM photos WHERE path=?", (path,)).fetchone()
    if row:
        pid = row[0]
        for fid, x, y, w, h, person, conf, ign, ok, prev, ft, vprev in conn.execute(
                "SELECT id,x,y,w,h,person_id,confirmed,ignored,face_ok,later_prev,frame_t,vault_prev FROM faces "
                "WHERE photo_id=?", (pid,)):
            if ign == 6:                 # hidden by a locked vault: carry the state it had before
                ign = vprev or 0
            if ign == 5:                 # photo is in Sort Later: carry the state it had before
                ign = prev or 0
            old[fid] = (x, y, w, h, person, conf, ign if ign != 4 else 0)   # "probably not" is worked out again
            old_t[fid] = ft
            if ok:
                old_ok.add(fid)
        if old:
            # your "not this person" / Quick review answers follow the faces through a rescan
            q = ",".join("?" * len(old))
            for fid, person in conn.execute(f"SELECT face_id, person_id FROM rejects WHERE face_id IN ({q})", list(old)):
                old_rejects.setdefault(fid, []).append(person)
            old_links = conn.execute(f"SELECT a, b FROM face_links WHERE a IN ({q}) OR b IN ({q})", list(old) * 2).fetchall()
            old_nolinks = conn.execute(f"SELECT a, b FROM face_nolinks WHERE a IN ({q}) OR b IN ({q})", list(old) * 2).fetchall()
        conn.execute("UPDATE photos SET mtime=?,size=?,width=?,height=?,orientation=?,nfaces=?,error=NULL,"
                     "taken=?,taken_read=1,is_video=?,cover_t=? WHERE id=?",
                     (mtime, size, img.width, img.height, img.orientation, len(faces), getattr(img, "taken", None),
                      int(video), getattr(img, "cover_t", None), pid))
        # faces you drew by hand (Tag Person) are kept through a rescan
        manual_keep = [dict(zip(MANUAL_COLS, r)) for r in conn.execute(
            f"SELECT {','.join(MANUAL_COLS)} FROM faces WHERE photo_id=? AND manual>0", (pid,))]
        conn.execute("DELETE FROM faces WHERE photo_id=?", (pid,))
        is_new = False
        sl = conn.execute("SELECT sort_later FROM photos WHERE id=?", (pid,)).fetchone()[0] or 0
        restore_later = sl == 1
        restore_vault = sl == 2
    else:
        is_new = True
        restore_vault = False
        manual_keep = []
        for m in manual_faces:     # remembered in the known-people file (survives Clear library)
            m = dict(m)
            m["person_id"] = get_or_create_person(conn, m.pop("name"))
            m.setdefault("confirmed", 1)
            m.setdefault("ignored", 0)
            manual_keep.append(m)
        cur = conn.execute("INSERT INTO photos(path,mtime,size,width,height,orientation,nfaces,taken,taken_read,"
                           "is_video,cover_t) VALUES(?,?,?,?,?,?,?,?,1,?,?)",
                           (path, mtime, size, img.width, img.height, img.orientation, len(faces),
                            getattr(img, "taken", None), int(video), getattr(img, "cover_t", None)))
        pid = cur.lastrowid
        conn.execute("UPDATE photos SET created=?, outside=? WHERE id=?",
                     (file_created(path), 0 if was_placed(conn, path) else 1, pid))
    if is_new:
        if any(m[0] == -1 for m in marks):
            conn.execute("UPDATE photos SET discarded=1 WHERE id=?", (pid,))
        restore_later = any(m[0] == -2 for m in marks)
        if category:
            cid = get_or_create_category(conn, category)
            if cid and not is_inside(conn, cid):
                conn.execute("UPDATE photos SET category_id=? WHERE id=?", (cid, pid))
        marks = [m for m in marks if m[0] >= 0]
        for name, x, y, w, h, src in tattoo_marks:
            conn.execute("INSERT INTO tattoos(person_id, photo_id, x, y, w, h, src) VALUES(?,?,?,?,?,?,?)",
                         (get_or_create_person(conn, name), pid, x, y, w, h, src))
    gps = getattr(img, "gps", None) or (None, None)
    conn.execute("UPDATE photos SET bright=?, dark_seen=?, lat=COALESCE(?, lat), lon=COALESCE(?, lon), body_done=0 "
                 "WHERE id=?",
                 (getattr(img, "bright", None), int(getattr(img, "dark_seen", 0) or 0), gps[0], gps[1], pid))
    used_hints = set()
    inserted = []
    new_of = {}   # old face id -> new face id
    for f in faces:
        box = (f["x"], f["y"], f["w"], f["h"])
        person, conf, ign, src_old = None, 0, 0, None
        best = 0.5
        for ofid, (x, y, w, h, op, oc, oi) in old.items():
            if video and abs((old_t.get(ofid) or 0) - (f.get("t") or 0)) > 0.6:
                continue          # a different moment of the video
            iou = _iou(box, (x, y, w, h))
            if iou > best and ofid not in new_of:
                best, person, conf, ign, src_old = iou, op, oc, oi, ofid
        if not old and not ign and marks:
            for (x, y, w, h, st) in marks:
                if _iou(box, (x, y, w, h)) > 0.5:
                    ign, person, conf = st, None, 0
                    break
        if not (person and conf) and not ign and hints:
            # name saved in the photo's tags (by FaceSort or another app)
            bi, bv = None, 0.3
            for i, (name, x, y, w, h) in enumerate(hints):
                if i in used_hints:
                    continue
                iou = _iou(box, (x, y, w, h))
                if iou > bv:
                    bi, bv = i, iou
            if bi is not None:
                used_hints.add(bi)
                person, conf = get_or_create_person(conn, hints[bi][0]), 1
        blob = f["emb"].astype(np.float32).tobytes()
        cur = conn.execute(
            "INSERT INTO faces(photo_id,x,y,w,h,score,px,emb,thumb,person_id,confirmed,ignored,fhash,model,face_ok,"
            "quality,frame_t,clarity) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, f["x"], f["y"], f["w"], f["h"], f["score"], f["px"], blob,
             f["thumb"], person if conf else None, conf if person else 0, ign, hashlib.sha1(blob).hexdigest(),
             model, 1 if src_old in old_ok else 0, f.get("quality"), f.get("t"), f.get("clarity")))
        if src_old is not None:
            new_of[src_old] = cur.lastrowid
        inserted.append((cur.lastrowid, box, f.get("t"), person if conf else None))
    for m in manual_keep:
        box = (m["x"], m["y"], m["w"], m["h"])
        hit = next((i for i in inserted if _iou(box, i[1]) > 0.4
                    and abs((i[2] or 0) - (m.get("frame_t") or 0)) < 0.6), None)
        if hit:
            # the scan now finds that face itself: give it the name you gave the drawn box
            if hit[3] is None and m.get("person_id"):
                conn.execute("UPDATE faces SET person_id=?, confirmed=1, ignored=0, cluster=NULL WHERE id=?",
                             (m["person_id"], hit[0]))
            continue
        insert_face(conn, pid, m)
    if new_of:
        conn.executemany("INSERT OR IGNORE INTO rejects(face_id, person_id) VALUES(?,?)",
                         [(new_of[o], p) for o, ps in old_rejects.items() if o in new_of for p in ps])
        for table, pairs in (("face_links", old_links), ("face_nolinks", old_nolinks)):
            conn.executemany(f"INSERT OR IGNORE INTO {table}(a, b) VALUES(?,?)",
                             [(new_of.get(a, a), new_of.get(b, b)) for a, b in pairs
                              if (a in new_of or a not in old) and (b in new_of or b not in old)])
    if keywords is not None:
        apply_keywords(conn, pid, keywords, settings or {})   # first, so tags from the file keep src='file'
    if is_new:
        for name in photo_names:
            person = get_or_create_person(conn, name)
            if not conn.execute("SELECT 1 FROM faces WHERE photo_id=? AND person_id=? AND confirmed=1 AND ignored=0",
                                (pid, person)).fetchone():
                conn.execute("INSERT OR IGNORE INTO photo_people(photo_id, person_id) VALUES(?,?)", (pid, person))
    if is_new:
        for name, chain in person_folders:      # remembered person folders (survive Clear library)
            f = folder_chain(conn, chain.split(PATH_SEP), owner=get_or_create_person(conn, name))
            if f:
                conn.execute("INSERT OR IGNORE INTO photo_folders(photo_id, folder_id) VALUES(?,?)", (pid, f))
    if restore_later:
        _hide_for_later(conn, [pid])
    if restore_vault:
        _hide_faces_for_vault(conn, [pid])
    return pid


MANUAL_COLS = ("x", "y", "w", "h", "score", "px", "emb", "thumb", "person_id", "confirmed", "ignored", "fhash",
               "model", "quality", "frame_t", "manual", "clarity")


def insert_face(conn, photo_id, m):
    cols = [c for c in MANUAL_COLS if c in m]
    return conn.execute(f"INSERT INTO faces(photo_id,{','.join(cols)}) VALUES(?{',?' * len(cols)})",
                        [photo_id] + [m[c] for c in cols]).lastrowid


def delete_face(conn, face_id):
    """Remove a face box (used for boxes you drew yourself)."""
    r = conn.execute("SELECT photo_id FROM faces WHERE id=?", (face_id,)).fetchone()
    conn.execute("DELETE FROM faces WHERE id=?", (face_id,))
    if r:
        conn.execute("UPDATE photos SET nfaces=MAX(0, nfaces-1) WHERE id=?", (r[0],))
    conn.commit()


def add_manual_face(conn, photo_id, face, person_id, model):
    """Tag Person: a face box you drew, named. face = dict from engine.face_from_box."""
    measured = face.get("emb") is not None
    emb = (face["emb"] if measured else np.zeros(model_dim(model))).astype(np.float32)
    blob = emb.tobytes()
    fid = insert_face(conn, photo_id, {
        "x": face["x"], "y": face["y"], "w": face["w"], "h": face["h"], "score": face.get("score") or 0.0,
        "px": face.get("px") or 0, "emb": blob, "thumb": face.get("thumb"), "person_id": person_id,
        "confirmed": 1, "ignored": 0, "fhash": hashlib.sha1(blob + str(face["x"]).encode()).hexdigest(),
        "model": model, "quality": face.get("quality"), "frame_t": face.get("t"), "manual": 1 if measured else 2,
        "clarity": face.get("clarity")})
    conn.execute("UPDATE photos SET nfaces=nfaces+1 WHERE id=?", (photo_id,))
    conn.execute("DELETE FROM photo_people WHERE photo_id=? AND person_id=?", (photo_id, person_id))
    touch_person(conn, person_id)
    conn.commit()
    return fid


# ----------------------------------------------------------------- Sort Later
def _hide_for_later(conn, photo_ids):
    q = ",".join("?" * len(photo_ids))
    conn.execute(f"UPDATE faces SET later_prev=ignored, ignored=5, cluster=NULL WHERE photo_id IN ({q}) AND ignored<>5",
                 photo_ids)
    conn.execute(f"UPDATE photos SET sort_later=1 WHERE id IN ({q})", photo_ids)


def set_sort_later(conn, photo_ids, on=True):
    """Sort Later: the photo leaves every other list (people, groups, categories, no faces...) until
    you take it out again; everything about it is kept and comes back exactly as it was."""
    if not photo_ids:
        return
    q = ",".join("?" * len(photo_ids))
    if on:
        _hide_for_later(conn, photo_ids)
    else:
        conn.execute(f"UPDATE faces SET ignored=COALESCE(later_prev, 0), later_prev=NULL, later_cluster=NULL "
                     f"WHERE photo_id IN ({q}) AND ignored=5", photo_ids)
        conn.execute(f"UPDATE photos SET sort_later=0 WHERE id IN ({q})", photo_ids)
    conn.commit()


def rehide_later(conn):
    """After naming / sorting faces inside Sort Later: the photo stays in Sort Later (the face
    keeps what you did to it, ready for when the photo is taken out)."""
    conn.execute("""UPDATE faces SET later_prev=ignored, ignored=5, cluster=NULL
                    WHERE ignored<>5 AND photo_id IN (SELECT id FROM photos WHERE sort_later=1)""")


def refresh_stats(conn, force=False):
    """Tell SQLite how the library's data is spread (ANALYZE), so it picks the quick way to
    answer each question. Done once, then again when the library has grown a lot. Returns True
    if it ran."""
    try:
        n = conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0]
        row = conn.execute("SELECT v FROM meta WHERE k='stats_faces'").fetchone()
        last = int(row[0]) if row else -1
        if not force and last >= 0 and n < last * 1.5 + 2000:
            return False
        conn.execute("PRAGMA analysis_limit=1000")       # (a quick, sampled ANALYZE - never long)
        conn.execute("ANALYZE")
        conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('stats_faces', ?)", (str(n),))
        conn.commit()
        return True
    except sqlite3.Error:
        return False


def leader_cluster(E, order, threshold, folder_of=None, bonus=0.0, cancelled=lambda: False, progress=None,
                   block=256, weights=None):
    """Look-alike grouping, best faces first: each face joins the group whose centre it's most
    similar to (if close enough), else starts a new group; a group's centre is the average of its
    faces. Same result as comparing every face with every group one by one, but done as an
    exact index: a block of faces is compared with all the groups in one matrix multiplication,
    and only the few groups that changed inside the block are compared again.
    folder_of / bonus: a small bonus for groups that already hold a face from the same folder.
    weights: how much each face counts in its group's centre (clear faces more than blurry ones).
    Returns (group per row (-1 = none), group centres, groups per folder)."""
    n, d = E.shape
    cap = 1024
    cents = np.zeros((cap, d), dtype=np.float32)
    sums = np.zeros((cap, d), dtype=np.float32)
    cluster_of = np.full(n, -1, dtype=np.int64)
    in_folder = {}
    k = 0
    order = np.asarray(order, dtype=np.int64)
    for b0 in range(0, len(order), block):
        if cancelled():
            return None, None, None
        if progress:
            progress(b0, len(order))
        part = order[b0:b0 + block]
        k0 = k if k >= 2048 else 0                            # few groups yet: one by one is quicker
        S = E[part] @ cents[:k0].T if k0 else None          # (block, groups so far)
        changed = []                                          # groups changed or made in this block
        changed_set = set()
        for r, i in enumerate(part):
            e = E[i]
            fo = int(folder_of[i]) if folder_of is not None else -1
            if k:
                sims = np.empty(k, dtype=np.float32)
                if k0:
                    sims[:k0] = S[r]
                if not k0:
                    sims = cents[:k] @ e
                elif changed:
                    ch = np.fromiter(changed, dtype=np.int64, count=len(changed))
                    sims[ch] = cents[ch] @ e
                if bonus and fo >= 0 and in_folder.get(fo):
                    sims = sims.copy() if not k0 else sims
                    sims[list(in_folder[fo])] += bonus
                j = int(np.argmax(sims))
                if sims[j] >= threshold:
                    if fo >= 0:
                        in_folder.setdefault(fo, set()).add(j)
                    sums[j] += e if weights is None else e * weights[i]
                    c = sums[j]
                    cents[j] = c / (np.linalg.norm(c) or 1)
                    cluster_of[i] = j
                    if j not in changed_set:
                        changed_set.add(j)
                        changed.append(j)
                    continue
            if k == cap:
                cap *= 2
                cents = np.resize(cents, (cap, d))
                sums = np.resize(sums, (cap, d))
                cents[k:] = 0
                sums[k:] = 0
            cents[k] = e
            sums[k] = e if weights is None else e * weights[i]
            cluster_of[i] = k
            if fo >= 0:
                in_folder.setdefault(fo, set()).add(k)
            changed_set.add(k)
            changed.append(k)
            k += 1
        time.sleep(0)                                         # let the window breathe
    return cluster_of, cents[:k], in_folder


def _leader_groups(E, order, threshold):
    """Look-alike groups: each face joins the most similar group if close enough, else starts
    one; then every face moves to its nearest final group. Returns a group index per row (-1 =
    none) for the rows in order."""
    cluster_of, C, _ = leader_cluster(E, order, threshold)
    of = {int(i): int(cluster_of[i]) for i in order}
    if C is not None and len(C):
        idx = np.asarray(order, dtype=np.int64)
        for s0 in range(0, len(idx), 2048):
            part = idx[s0:s0 + 2048]
            sims = E[part] @ C.T
            best = sims.argmax(axis=1)
            ok = sims[np.arange(len(part)), best] >= threshold
            for i, j, o in zip(part, best, ok):
                if o:
                    of[int(i)] = int(j)
    return of


def read_missing_clarity(conn, progress=lambda d, t: None, cancelled=lambda: False, batch=400):
    """Faces scanned before 1.36 get their clarity, read from their stored thumbnails (no photo
    is opened). Carries on where it left off. Returns how many faces were read."""
    import engine
    total = conn.execute("SELECT COUNT(*) FROM faces WHERE clarity IS NULL AND manual<2").fetchone()[0]
    done = 0
    while not cancelled():
        rows = conn.execute("SELECT id, thumb FROM faces WHERE clarity IS NULL AND manual<2 LIMIT ?",
                            (batch,)).fetchall()
        if not rows:
            break
        out = []
        for fid, thumb in rows:
            c = engine.clarity_of_thumb(thumb) if thumb else None
            out.append((round(c, 3) if c is not None else 0.5, fid))
        conn.executemany("UPDATE faces SET clarity=? WHERE id=?", out)
        conn.commit()
        done += len(out)
        progress(done, total)
        time.sleep(0)
    return done


def cluster_later(conn, threshold, model):
    """Group the unnamed faces in Sort Later by look-alike, separately from everything else."""
    rows = conn.execute("""SELECT id, emb, px*score FROM faces WHERE ignored=5 AND COALESCE(later_prev, 0)=0
                           AND person_id IS NULL AND model=? AND COALESCE(manual, 0)<2""", (model,)).fetchall()
    if len(rows) < 2:
        conn.execute("UPDATE faces SET later_cluster=NULL WHERE later_cluster IS NOT NULL")
        conn.commit()
        return
    E = np.stack([emb_from_blob(b) for _, b, _ in rows]).astype(np.float32)
    E /= np.linalg.norm(E, axis=1, keepdims=True) + 1e-9
    order = sorted(range(len(rows)), key=lambda i: -(rows[i][2] or 0))
    of = _leader_groups(E, order, threshold)
    sizes = {}
    for g in of.values():
        sizes[g] = sizes.get(g, 0) + 1
    conn.execute("UPDATE faces SET later_cluster=NULL WHERE later_cluster IS NOT NULL")
    conn.executemany("UPDATE faces SET later_cluster=? WHERE id=?",
                     [(g + 1, rows[i][0]) for i, g in of.items() if sizes[g] >= 2])
    conn.commit()


def known_person_names(conn):
    names = {r[0].casefold(): r[0] for r in conn.execute("SELECT name FROM persons")}
    try:
        import known
        for n in known.names():
            names.setdefault(n.casefold(), n)
    except Exception:
        pass
    return names


def apply_keywords(conn, photo_id, keywords, settings, known_names=None):
    """People names among the keywords already on a photo -> tag them on the photo
    (src='file'). Hierarchical people tags always count; plain keywords count if they
    carry your FaceSort tag prefix, match someone FaceSort knows, or (setting) always."""
    people, plain = keywords
    prefix = (settings.get("keyword_prefix") or "").strip()
    every = bool(settings.get("keywords_all_people"))
    known_names = known_person_names(conn) if known_names is None else known_names
    names = list(people)
    for k in plain:
        if prefix and k.casefold().startswith(prefix.casefold()) and k[len(prefix):].strip():
            names.append(k[len(prefix):].strip())
        elif k.casefold() in known_names or every:
            names.append(known_names.get(k.casefold(), k))
    for name in names:
        pid = get_or_create_person(conn, name)
        if conn.execute("SELECT 1 FROM faces WHERE photo_id=? AND person_id=? AND confirmed=1 AND ignored=0",
                        (photo_id, pid)).fetchone():
            continue  # already on a face in this photo
        conn.execute("INSERT OR IGNORE INTO photo_people(photo_id, person_id, src) VALUES(?,?,'file')",
                     (photo_id, pid))
    conn.execute("UPDATE photos SET tags_read=1 WHERE id=?", (photo_id,))


def settle_photo_tags(conn):
    """Use names that came with the photo files to name its faces:
    - a face suggested as someone who is tagged on that photo -> confirmed
    - one unnamed face + one tagged name not on a face yet   -> that face is them (only
      suggested when the name came from dragging the photo in)
    Then drop photo tags that a named face in the same photo now covers."""
    conn.execute("""UPDATE faces SET confirmed=1, cluster=NULL
                    WHERE confirmed=0 AND ignored=0 AND person_id IS NOT NULL
                    AND EXISTS (SELECT 1 FROM photo_people pp WHERE pp.photo_id=faces.photo_id
                                AND pp.person_id=faces.person_id)
                    AND NOT EXISTS (SELECT 1 FROM rejects r WHERE r.face_id=faces.id AND r.person_id=faces.person_id)""")
    rows = conn.execute("""
        SELECT pp.photo_id, MIN(pp.person_id), COUNT(*), MAX(pp.src='drop') FROM photo_people pp
        WHERE pp.src IN ('file', 'drop')
        AND NOT EXISTS (SELECT 1 FROM faces f WHERE f.photo_id=pp.photo_id AND f.person_id=pp.person_id
                        AND f.confirmed=1 AND f.ignored=0)
        GROUP BY pp.photo_id HAVING COUNT(*)=1""").fetchall()
    for photo_id, person_id, _, dropped in rows:
        faces = conn.execute("""SELECT id FROM faces WHERE photo_id=? AND ignored=0
                                AND NOT (person_id IS NOT NULL AND confirmed=1)""", (photo_id,)).fetchall()
        named = conn.execute("SELECT COUNT(*) FROM faces WHERE photo_id=? AND ignored=0 AND confirmed=1",
                             (photo_id,)).fetchone()[0]
        if len(faces) == 1 and named == 0 and not conn.execute(
                "SELECT 1 FROM rejects WHERE face_id=? AND person_id=?", (faces[0][0], person_id)).fetchone():
            # from a file's own tags: that face is them. Dragged in with a name: only suggested -
            # you confirm it (the one face found may be someone else), so a wrong guess never
            # teaches the face matching
            conn.execute("UPDATE faces SET person_id=?, confirmed=?, cluster=NULL WHERE id=?",
                         (person_id, 0 if dropped else 1, faces[0][0]))
    conn.execute("""DELETE FROM photo_people WHERE EXISTS (
                        SELECT 1 FROM faces f WHERE f.photo_id=photo_people.photo_id
                        AND f.person_id=photo_people.person_id AND f.confirmed=1 AND f.ignored=0)""")
    conn.commit()


# ----------------------------------------------------------------- drag & drop tags
def add_drop_rule(conn, path, is_dir, people, category, sort_later, structure=None, batch=None):
    """structure (folders only): None = folder names not used; -1 = keep the sub-folders (inside
    each person named, or as folders); 0 = keep the folder structure as
    folders; k >= 1 = the folder k levels down (1 = the dropped folder itself) is the person:
    folders above it hold the person, folders below it go inside the person's folder."""
    conn.execute("""INSERT INTO drop_rules(path, is_dir, people, category, sort_later, structure, batch)
                    VALUES(?,?,?,?,?,?,?)""",
                 (norm_path(path), int(bool(is_dir)), json.dumps(list(people or [])), category or None,
                  int(bool(sort_later)), structure, batch))
    conn.commit()


def drop_rules(conn):
    return [(path, bool(d), json.loads(p or "[]"), c, bool(sl), st, b) for path, d, p, c, sl, st, b in conn.execute(
        "SELECT path, is_dir, people, category, sort_later, structure, batch FROM drop_rules")]


def rules_matching(rules, path):
    """The drop rules that cover this file (most specific last, so it wins)."""
    out = []
    for r in rules:
        rp = r[0]
        if path == rp or (r[1] and path.startswith(rp.rstrip("\\/") + os.sep)):
            out.append(r)
    return sorted(out, key=lambda r: len(r[0]))


def structure_of(rule_path, path, level):
    """(folders above the person, person name or None, folders below) from the folder names
    of a dropped folder tree. level: 0 = folders only, k = person at depth k."""
    top = os.path.dirname(rule_path.rstrip("\\/"))
    rel = os.path.relpath(os.path.dirname(path), top)
    chain = [x for x in rel.split(os.sep) if x and x != "."]
    if not level or len(chain) < level:
        return chain, None, []
    return chain[:level - 1], chain[level - 1], chain[level:]


def apply_drop_rules(conn, photo_id, path, rules):
    """Tag a photo the way you asked when you dropped it (or its folder) in."""
    hit = rules_matching(rules, path)
    if not hit:
        return False
    folder_id, later, names, person_folders, batch = None, False, [], [], None
    for rpath, is_dir, people, cat, sl, structure, b in hit:
        batch = b or batch
        names += people
        if cat:
            folder_id = get_or_create_category(conn, cat, [get_or_create_person(conn, n) for n in people])
        if is_dir and structure == -2:
            # remember the folder names: whoever is tagged on the photo - now or later - gets
            # them as their own folders (Jane\Beach 2024\...)
            _, top, below = structure_of(rpath, path, 1)
            chain = [x for x in ([top] + below) if x]
            if chain:
                conn.execute("UPDATE photos SET drop_folder=? WHERE id=?", (json.dumps(chain), photo_id))
        elif is_dir and structure == -1:
            # keep the sub-folders: inside each person named when it was dropped (or as plain
            # folders, under the dropped folder's name, when nobody was named)
            _, top, below = structure_of(rpath, path, 1)
            if people:
                if below:
                    for n in people:
                        person_folders.append(folder_chain(conn, below, owner=get_or_create_person(conn, n)))
            elif top:
                folder_id = folder_chain(conn, [top] + below)
        elif is_dir and structure is not None:
            above, person, below = structure_of(rpath, path, structure)
            if person:
                names.append(person)
                pid = get_or_create_person(conn, person)
                if above and not conn.execute("SELECT category_id FROM persons WHERE id=?", (pid,)).fetchone()[0]:
                    place = folder_chain(conn, above)
                    if place:
                        conn.execute("UPDATE persons SET category_id=? WHERE id=?", (place, pid))
                if below:
                    person_folders.append(folder_chain(conn, below, owner=pid))
            elif above:
                folder_id = folder_chain(conn, above)
        later = later or sl
    for name in dict.fromkeys(names):
        person = get_or_create_person(conn, name)
        touch_person(conn, person)
        if not conn.execute("SELECT 1 FROM faces WHERE photo_id=? AND person_id=? AND confirmed=1 AND ignored=0",
                            (photo_id, person)).fetchone():
            conn.execute("INSERT OR IGNORE INTO photo_people(photo_id, person_id, src) VALUES(?,?,'drop')",
                         (photo_id, person))
    if folder_id:
        set_photo_folder(conn, [photo_id], folder_id)
    for f in person_folders:
        set_photo_folder(conn, [photo_id], f)
    if later:
        _hide_for_later(conn, [photo_id])
    if batch:
        conn.execute("UPDATE photos SET dropped=? WHERE id=?", (batch, photo_id))   # shown in Recently dropped
    apply_drop_folders(conn, [photo_id])
    return True


def apply_drop_folders(conn, photo_ids=None):
    """Photos dropped in with "remember the folder name": everyone tagged on them (a confirmed
    face or a whole-photo tag) gets that folder (and the ones inside it) as their own folder,
    and the photo is filed there. Run again whenever people are tagged. Returns photos filed."""
    # quick pre-check: only photos with more people tagged than folders given (one per person)
    q = """SELECT ph.id, ph.drop_folder FROM photos ph WHERE ph.drop_folder IS NOT NULL
           AND (SELECT COUNT(*) FROM (SELECT person_id FROM faces WHERE photo_id=ph.id AND confirmed=1
                                      AND ignored=0 AND person_id IS NOT NULL
                                      UNION SELECT person_id FROM photo_people WHERE photo_id=ph.id))
               > (SELECT COUNT(*) FROM photo_folders WHERE photo_id=ph.id)"""
    args = []
    if photo_ids is not None:
        ids = list(photo_ids)
        if not ids:
            return 0
        q += f" AND ph.id IN ({','.join('?' * len(ids))})"
        args = ids
    rows = conn.execute(q, args).fetchall()
    if not rows:
        return 0
    tree = None
    done = 0
    for pid, chain_json in rows:
        try:
            chain = json.loads(chain_json)
        except ValueError:
            continue
        people = {r[0] for r in conn.execute("""SELECT person_id FROM faces WHERE photo_id=? AND confirmed=1
                                                AND ignored=0 AND person_id IS NOT NULL
                                                UNION SELECT person_id FROM photo_people WHERE photo_id=?""",
                                             (pid, pid))}
        if not people:
            continue
        tree = tree or folder_tree(conn)
        have = {tree[f][2] for (f,) in conn.execute("SELECT folder_id FROM photo_folders WHERE photo_id=?", (pid,))
                if f in tree}
        for person in people:
            if person in have:
                continue                  # already in one of this person's folders: left as you set it
            fid = folder_chain(conn, chain, owner=person)
            conn.execute("INSERT OR IGNORE INTO photo_folders(photo_id, folder_id) VALUES(?,?)", (pid, fid))
            tree = None
            done += 1
    if done:
        conn.commit()
    return done


def new_drop_batch(conn, name):
    b = time.time()
    conn.execute("INSERT OR REPLACE INTO drop_batches(batch, name) VALUES(?,?)", (b, name))
    conn.commit()
    return b


def drop_batches(conn):
    """[(batch, name, photo count)], newest first - what's still in Recently dropped."""
    names = dict(conn.execute("SELECT batch, name FROM drop_batches"))
    out = {b: [b, names.get(b) or "Dropped", n] for b, n in conn.execute(
        "SELECT dropped, COUNT(*) FROM photos WHERE dropped IS NOT NULL AND error IS NULL "
        "AND COALESCE(sort_later, 0) != 2 GROUP BY dropped")}
    return sorted((tuple(v) for v in out.values()), key=lambda r: -r[0])


def done_checking(conn, batch=None):
    """Take photos out of Recently dropped (they stay tagged, in the library)."""
    if batch is None:
        conn.execute("UPDATE photos SET dropped=NULL WHERE dropped IS NOT NULL")
    else:
        conn.execute("UPDATE photos SET dropped=NULL WHERE dropped=?", (batch,))
    conn.execute("DELETE FROM drop_batches WHERE batch NOT IN (SELECT DISTINCT dropped FROM photos WHERE dropped IS NOT NULL)")
    conn.commit()


# ----------------------------------------------------------------- albums
def albums(conn):
    """[(id, name, photo count)] by name (photos hidden in a locked vault aren't counted)."""
    return conn.execute("""SELECT a.id, a.name, (SELECT COUNT(*) FROM album_photos ap JOIN photos ph ON ph.id=ap.photo_id
                                                 WHERE ap.album_id=a.id AND COALESCE(ph.sort_later, 0) != 2)
                           FROM albums a ORDER BY a.name COLLATE NOCASE""").fetchall()


def create_album(conn, name):
    name = " ".join(name.split())
    row = conn.execute("SELECT id FROM albums WHERE name=?", (name,)).fetchone()
    if row:
        return row[0]
    cur = conn.execute("INSERT INTO albums(name, created) VALUES(?,?)", (name, time.time()))
    conn.commit()
    return cur.lastrowid


def rename_album(conn, aid, name):
    """Rename; to the name of another album = merge into it. Returns the album id that's left."""
    name = " ".join(name.split())
    other = conn.execute("SELECT id FROM albums WHERE name=? AND id<>?", (name, aid)).fetchone()
    if other:
        conn.execute("INSERT OR IGNORE INTO album_photos(album_id, photo_id, added) "
                     "SELECT ?, photo_id, added FROM album_photos WHERE album_id=?", (other[0], aid))
        conn.execute("DELETE FROM albums WHERE id=?", (aid,))
        conn.commit()
        return other[0]
    conn.execute("UPDATE albums SET name=? WHERE id=?", (name, aid))
    conn.commit()
    return aid


def delete_album(conn, aid):
    conn.execute("DELETE FROM album_photos WHERE album_id=?", (aid,))
    conn.execute("DELETE FROM albums WHERE id=?", (aid,))
    conn.commit()


def add_to_album(conn, aid, photo_ids):
    now = time.time()
    before = conn.total_changes
    conn.executemany("INSERT OR IGNORE INTO album_photos(album_id, photo_id, added) VALUES(?,?,?)",
                     [(aid, p, now) for p in photo_ids])
    conn.commit()
    return conn.total_changes - before


def remove_from_album(conn, aid, photo_ids):
    conn.executemany("DELETE FROM album_photos WHERE album_id=? AND photo_id=?", [(aid, p) for p in photo_ids])
    conn.commit()


def albums_of(conn, photo_ids):
    """{album id: how many of these photos are in it}"""
    out = {}
    ids = list(photo_ids)
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        for aid, n in conn.execute(f"SELECT album_id, COUNT(*) FROM album_photos WHERE photo_id IN "
                                   f"({','.join('?' * len(chunk))}) GROUP BY album_id", chunk):
            out[aid] = out.get(aid, 0) + n
    return out


def clear_drop_rules(conn):
    conn.execute("DELETE FROM drop_rules")
    conn.commit()


# ----------------------------------------------------------------- tattoos
def add_tattoo(conn, person_id, photo_id, box, src="mark"):
    """Mark a tattoo. The person is also tagged on that photo (marking it says they're in it)."""
    x, y, w, h = box
    tid = conn.execute("INSERT INTO tattoos(person_id, photo_id, x, y, w, h, src) VALUES(?,?,?,?,?,?,?)",
                       (person_id, photo_id, x, y, w, h, src)).lastrowid
    if not conn.execute("SELECT 1 FROM faces WHERE photo_id=? AND person_id=? AND confirmed=1 AND ignored=0",
                        (photo_id, person_id)).fetchone():
        conn.execute("INSERT OR IGNORE INTO photo_people(photo_id, person_id, src) VALUES(?,?,'user')",
                     (photo_id, person_id))
    conn.execute("DELETE FROM tattoo_suggestions WHERE photo_id=? AND person_id=?", (photo_id, person_id))
    touch_person(conn, person_id)
    conn.commit()
    return tid


def remove_tattoo(conn, tattoo_id):
    conn.execute("DELETE FROM tattoos WHERE id=?", (tattoo_id,))
    conn.commit()


MAX_LEARNED_TATTOOS = 12   # confirmed matches kept as extra references, per person


def confirm_tattoo_matches(conn, photo_ids, person_id=None):
    """Accept tattoo suggestions: tag the person on the photo, and keep the matched area as
    another reference for that person's tattoo (so later searches find more)."""
    q = ",".join("?" * len(photo_ids))
    cond, args = (" AND person_id=?", [person_id]) if person_id else ("", [])
    rows = conn.execute(f"SELECT photo_id, person_id, x, y, w, h FROM tattoo_suggestions "
                        f"WHERE photo_id IN ({q}){cond}", [*photo_ids, *args]).fetchall()
    for photo_id, pid, x, y, w, h in rows:
        if not conn.execute("SELECT 1 FROM faces WHERE photo_id=? AND person_id=? AND confirmed=1 AND ignored=0",
                            (photo_id, pid)).fetchone():
            conn.execute("INSERT OR IGNORE INTO photo_people(photo_id, person_id, src) VALUES(?,?,'tattoo')",
                         (photo_id, pid))
        learned = conn.execute("SELECT COUNT(*) FROM tattoos WHERE person_id=? AND src='match'", (pid,)).fetchone()[0]
        if learned < MAX_LEARNED_TATTOOS:
            conn.execute("INSERT INTO tattoos(person_id, photo_id, x, y, w, h, src) VALUES(?,?,?,?,?,?,'match')",
                         (pid, photo_id, x, y, w, h))
        conn.execute("DELETE FROM tattoo_suggestions WHERE photo_id=? AND person_id=?", (photo_id, pid))
        touch_person(conn, pid)
    conn.commit()
    return len(rows)


def reject_tattoo_matches(conn, photo_ids, person_id=None):
    q = ",".join("?" * len(photo_ids))
    cond, args = (" AND person_id=?", [person_id]) if person_id else ("", [])
    rows = conn.execute(f"SELECT photo_id, person_id FROM tattoo_suggestions WHERE photo_id IN ({q}){cond}",
                        [*photo_ids, *args]).fetchall()
    conn.executemany("INSERT OR IGNORE INTO tattoo_rejects(photo_id, person_id) VALUES(?,?)", rows)
    conn.executemany("DELETE FROM tattoo_suggestions WHERE photo_id=? AND person_id=?", rows)
    conn.commit()
    return len(rows)


def friendly_error(err, path=""):
    """Turn a raw error into a short explanation (and drop the long file path from it)."""
    t = str(err)
    low = t.lower()
    if isinstance(err, PermissionError) or "permission denied" in low:
        return "No permission to read this file"
    if isinstance(err, FileNotFoundError) or "no such file" in low:
        return "File not found"
    if "cannot identify image file" in low or "unidentifiedimageerror" in low:
        return "Damaged, or not really an image"
    if "truncated" in low:
        return "Image is incomplete (file was cut off)"
    if "decompression bomb" in low or "exceeds limit" in low:
        return "Image is too large to open"
    if "heif" in low or "heic" in low:
        return "This HEIC/HEIF photo couldn't be decoded"
    if path:
        for v in (path, os.path.basename(path)):
            t = t.replace(repr(v), "").replace(v, "")
    t = " ".join(t.split()).strip(" :'\"")
    return (t[:120] + "…") if len(t) > 120 else (t or "Couldn't read this file")


def store_other(conn, path, mtime, size):
    """A file that isn't a photo or video (document, audio, archive...): only recorded - where it
    is, its size and date - never opened. It can be tagged with people and put in folders like a
    photo, and moves with them. Everything that reads pictures skips it (the flags below)."""
    row = conn.execute("SELECT id FROM photos WHERE path=?", (path,)).fetchone()
    if row:
        conn.execute("UPDATE photos SET mtime=?, size=?, is_other=1, error=NULL WHERE id=?", (mtime, size, row[0]))
        return row[0]
    cur = conn.execute(
        "INSERT INTO photos(path, mtime, size, nfaces, is_video, is_other, extras_read, taken_read, tags_read, "
        "dark_seen, body_done, dup_skip) VALUES(?,?,?,0,0,1,1,1,1,1,3,1)", (path, mtime, size))
    return cur.lastrowid


def store_error(conn, path, mtime, size, err):
    conn.execute("INSERT INTO photos(path,mtime,size,error) VALUES(?,?,?,?) "
                 "ON CONFLICT(path) DO UPDATE SET mtime=excluded.mtime,size=excluded.size,error=excluded.error",
                 (path, mtime, size, friendly_error(err, path)))


def _iou(a, b):
    ax0, ay0, ax1, ay1 = a[0], a[1], a[0] + a[2], a[1] + a[3]
    bx0, by0, bx1, by1 = b[0], b[1], b[0] + b[2], b[1] + b[3]
    iw, ih = max(0, min(ax1, bx1) - max(ax0, bx0)), max(0, min(ay1, by1) - max(ay0, by0))
    inter = iw * ih
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0


def touch_person(conn, person_id):
    conn.execute("UPDATE persons SET last_used=? WHERE id=?", (time.time(), person_id))


# ----------------------------------------------------------------- categories
def top_categories(conn, n=5):
    """Most used normal folders as labels ('Models › Studio') (ties: most recently used)."""
    tree = folder_tree(conn)
    rows = conn.execute("""SELECT c.id FROM categories c
                           WHERE EXISTS (SELECT 1 FROM photos p WHERE p.category_id=c.id)
                           ORDER BY c.uses DESC, c.last_used DESC""").fetchall()
    return [folder_label(conn, cid, tree) for (cid,) in rows if cid in tree and tree[cid][2] is None][:n]


def folder_labels_by_use(conn, people=()):
    """Folder bar suggestions: normal folders, plus the folders of these people."""
    tree = folder_tree(conn)
    names = dict(conn.execute("SELECT id, name FROM persons"))
    out = []
    for (cid,) in conn.execute("SELECT id FROM categories ORDER BY uses DESC, last_used DESC, name COLLATE NOCASE"):
        owner = tree.get(cid, ("", None, None))[2]
        if owner is None or owner in people:
            out.append(folder_label(conn, cid, tree, names))
    return out


def get_model(conn):
    r = conn.execute("SELECT v FROM meta WHERE k='model'").fetchone()
    return r[0] if r else "sface"


def set_model(conn, model):
    conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('model', ?)", (model,))
    conn.commit()


def set_notface(conn, face_ids, state=3):
    """3 = you said it's not a face (FaceSort learns from these); 0 = it is a face after all."""
    q = ",".join("?" * len(face_ids))
    if state:
        conn.execute(f"UPDATE faces SET ignored=?, person_id=NULL, confirmed=0, cluster=NULL, face_ok=0 "
                     f"WHERE id IN ({q})", [state, *face_ids])
    else:
        conn.execute(f"UPDATE faces SET ignored=0, face_ok=1 WHERE id IN ({q})", face_ids)
    conn.commit()


def get_or_create_person(conn, name):
    name = name.strip()
    row = conn.execute("SELECT id FROM persons WHERE name=?", (name,)).fetchone()
    if row:
        return row[0]
    return conn.execute("INSERT INTO persons(name) VALUES(?)", (name,)).lastrowid


def assign(conn, face_ids, person_id):
    touch_person(conn, person_id)
    q = ",".join("?" * len(face_ids))
    conn.execute(f"DELETE FROM rejects WHERE person_id=? AND face_id IN ({q})", [person_id, *face_ids])
    conn.execute(f"UPDATE faces SET person_id=?, confirmed=1, ignored=0, cluster=NULL WHERE id IN ({q})",
                 [person_id, *face_ids])
    conn.commit()


def confirm(conn, face_ids):
    q = ",".join("?" * len(face_ids))
    conn.execute(f"UPDATE faces SET confirmed=1 WHERE person_id IS NOT NULL AND id IN ({q})", face_ids)
    conn.commit()


def reject(conn, face_ids):
    """'Not this person': remember it and move the face back to unknown."""
    for fid in face_ids:
        r = conn.execute("SELECT person_id FROM faces WHERE id=?", (fid,)).fetchone()
        if r and r[0]:
            conn.execute("INSERT OR IGNORE INTO rejects(face_id,person_id) VALUES(?,?)", (fid, r[0]))
    q = ",".join("?" * len(face_ids))
    conn.execute(f"UPDATE faces SET person_id=NULL, confirmed=0, cluster=NULL WHERE id IN ({q})", face_ids)
    conn.commit()


def ungroup(conn, face_ids):
    q = ",".join("?" * len(face_ids))
    conn.execute(f"UPDATE faces SET cluster=NULL WHERE id IN ({q})", face_ids)
    conn.commit()


def set_ignored(conn, face_ids, value):
    q = ",".join("?" * len(face_ids))
    if value:
        conn.execute(f"UPDATE faces SET ignored=1, person_id=NULL, confirmed=0, cluster=NULL WHERE id IN ({q})", face_ids)
    else:
        conn.execute(f"UPDATE faces SET ignored=0 WHERE id IN ({q})", face_ids)
    conn.commit()


def tag_photos(conn, photo_ids, person_id):
    touch_person(conn, person_id)
    conn.executemany("INSERT OR IGNORE INTO photo_people(photo_id, person_id) VALUES(?,?)",
                     [(p, person_id) for p in photo_ids])
    conn.commit()


def untag_photos(conn, photo_ids, person_id):
    conn.executemany("DELETE FROM photo_people WHERE photo_id=? AND person_id=?", [(p, person_id) for p in photo_ids])
    conn.commit()


def set_photos_discarded(conn, photo_ids, value=True):
    if value:
        set_sort_later(conn, photo_ids, False)   # discarding takes a photo out of Sort Later
    conn.executemany("UPDATE photos SET discarded=? WHERE id=?", [(1 if value else 0, p) for p in photo_ids])
    conn.commit()


def set_discarded(conn, face_ids, value=True):
    """D = discard: the photo goes to '~To Be Discarded' and this face is never matched again."""
    q = ",".join("?" * len(face_ids))
    if value:
        set_sort_later(conn, [r[0] for r in conn.execute(
            f"SELECT DISTINCT photo_id FROM faces WHERE id IN ({q})", face_ids)], False)
        conn.execute(f"UPDATE faces SET ignored=2, person_id=NULL, confirmed=0, cluster=NULL WHERE id IN ({q})", face_ids)
    else:
        conn.execute(f"UPDATE faces SET ignored=0 WHERE ignored=2 AND id IN ({q})", face_ids)
    conn.commit()


def rename_person(conn, person_id, new_name):
    """Rename; if the name already exists the two people are merged."""
    import known
    new_name = new_name.strip()
    old = conn.execute("SELECT name FROM persons WHERE id=?", (person_id,)).fetchone()
    if old:
        known.rename(old[0], new_name)
    row = conn.execute("SELECT id FROM persons WHERE name=?", (new_name,)).fetchone()
    if row and row[0] != person_id:
        target = row[0]
        conn.execute("UPDATE faces SET person_id=? WHERE person_id=?", (target, person_id))
        conn.execute("UPDATE OR IGNORE rejects SET person_id=? WHERE person_id=?", (target, person_id))
        conn.execute("UPDATE OR IGNORE photo_people SET person_id=? WHERE person_id=?", (target, person_id))
        conn.execute("UPDATE tattoos SET person_id=? WHERE person_id=?", (target, person_id))
        conn.execute("UPDATE OR IGNORE tattoo_suggestions SET person_id=? WHERE person_id=?", (target, person_id))
        conn.execute("UPDATE OR IGNORE tattoo_rejects SET person_id=? WHERE person_id=?", (target, person_id))
        conn.execute("DELETE FROM persons WHERE id=?", (person_id,))
        conn.commit()
        return target
    conn.execute("UPDATE persons SET name=? WHERE id=?", (new_name, person_id))
    conn.commit()
    return person_id


def delete_person(conn, person_id):
    import known
    old = conn.execute("SELECT name FROM persons WHERE id=?", (person_id,)).fetchone()
    if old:
        known.forget(old[0])
    conn.execute("UPDATE faces SET person_id=NULL, confirmed=0 WHERE person_id=?", (person_id,))
    conn.execute("DELETE FROM persons WHERE id=?", (person_id,))
    conn.commit()


def remove_folder_photos(conn, folder):
    prefix = norm_path(folder).rstrip("\\/") + os.sep
    conn.execute("DELETE FROM photos WHERE substr(path,1,?)=?", (len(prefix), prefix))
    conn.execute("DELETE FROM dup_fine WHERE photo_id NOT IN (SELECT id FROM photos)")
    conn.execute("DELETE FROM dup_checked WHERE a NOT IN (SELECT id FROM photos) OR b NOT IN (SELECT id FROM photos)")
    conn.commit()


# ----------------------------------------------------------------- grouping
# Your existing folders are a strong hint: a folder often holds many photos of the same person.
# Faces from the same folder get this much extra similarity when grouping (not when suggesting
# names). Camera dump folders (DCIM, Camera, Downloads...) and very big folders get no help.
FOLDER_BONUS = {"sface": 0.05, "arcface": 0.04, "adaface": 0.04}
FOLDER_HINT_MAX_FACES = 400
GENERIC_FOLDER = re.compile(r"(dcim|\d{3}[a-z_]*|camera|camera roll|pictures|my pictures|photos|images|downloads|"
                            r"desktop|documents|img|screenshots|whatsapp images|onedrive|icloud photos|"
                            r"saved pictures|new folder.*|unsorted|misc|temp|tmp|import.*)", re.I)


def folder_name(path):
    return os.path.basename(os.path.dirname(path))


def is_generic_folder(name):
    return not name or bool(GENERIC_FOLDER.fullmatch(name.strip()))


def photo_dirs(conn):
    """{photo id: its folder}"""
    sep = os.sep
    return {pid: path.rpartition(sep)[0] for pid, path in conn.execute("SELECT id, path FROM photos")}


def unnamed_per_folder(conn, dirs=None):
    """{folder path: number of faces there that nobody has named yet}"""
    dirs = dirs if dirs is not None else photo_dirs(conn)
    out = {}
    for pid, n in conn.execute("""SELECT photo_id, COUNT(*) FROM faces WHERE person_id IS NULL AND ignored=0
                                  GROUP BY photo_id"""):
        d = dirs.get(pid, "")
        out[d] = out.get(d, 0) + n
    return out


def pick_folder(group_dirs, n_group, unnamed):
    """The folder a group of faces most likely 'belongs to': most of the group is in it, or
    most of the folder's unnamed faces are in the group. Camera-dump names never count."""
    best, best_score = None, 0
    for d, k in group_dirs.items():
        name = os.path.basename(d)
        if k < 2 or is_generic_folder(name):
            continue
        purity = k / max(unnamed.get(d, k), k)       # share of the folder's unnamed faces in this group
        share = k / max(n_group, 1)                  # share of the group in this folder
        if purity >= 0.8 or (share >= 0.6 and purity >= 0.5):
            score = (purity + share, k)
            if best is None or score > best_score:
                best, best_score = name, score
    return best


def group_folder(conn, face_ids, unnamed=None):
    """The folder name to suggest for these faces (e.g. 'Aunt May'), or None."""
    if not face_ids:
        return None
    counts = {}
    for i in range(0, len(face_ids), 900):
        chunk = face_ids[i:i + 900]
        for (path,) in conn.execute(f"""SELECT ph.path FROM faces f JOIN photos ph ON ph.id=f.photo_id
                                        WHERE f.id IN ({','.join('?' * len(chunk))})""", chunk):
            d = os.path.dirname(path)
            counts[d] = counts.get(d, 0) + 1
    return pick_folder(counts, len(face_ids), unnamed if unnamed is not None else unnamed_per_folder(conn))


def _write_flagged(conn, flagged):
    """Faces that look like what you marked "not a face": set aside (ignored=4) for you to check."""
    conn.execute("UPDATE faces SET ignored=0 WHERE ignored=4 AND confirmed=0")
    if flagged:
        conn.executemany("UPDATE faces SET ignored=4, person_id=NULL, cluster=NULL WHERE id=? AND confirmed=0 "
                         "AND ignored IN (0, 4)", flagged)


def regroup(conn, threshold=0.42, progress=None, cancelled=lambda: False, sync=True):
    """1) Suggest a name for unknown faces that clearly match someone you've named.
       2) Cluster the rest into unnamed groups of look-alike faces."""
    import known
    match_thr = threshold + 0.03
    margin = 0.04
    model = get_model(conn)
    try:
        if sync:                # (the window saves this separately, a while after you stop)
            known.sync_from_library(conn)
        known_refs = known.references(model)
    except Exception:
        known_refs = []
    try:
        cluster_later(conn, threshold, model)     # look-alike groups inside Sort Later
    except Exception:
        pass

    rows = conn.execute("SELECT id, person_id, emb, fhash, clarity FROM faces WHERE confirmed=1 AND person_id IS NOT NULL "
                        "AND ignored=0 AND model=? AND manual<2", (model,)).fetchall()
    rng = np.random.default_rng(0)
    by_person = {}
    lib_hashes = set()
    # someone's blurry, tiny or very dark photos are poor examples of them: when they have enough
    # clear ones, those are what other faces are compared with
    clear_count = {}
    for _fid, pid, _b, _h, c in rows:
        if c is None or c >= REF_MIN_CLARITY:
            clear_count[pid] = clear_count.get(pid, 0) + 1
    for fid, pid, b, h, c in rows:
        lib_hashes.add(h)
        if c is not None and c < REF_MIN_CLARITY and clear_count.get(pid, 0) >= 4:
            continue
        by_person.setdefault(pid, []).append(emb_from_blob(b))
    # people remembered in the known-people file (even if their photos aren't in the library)
    pid_by_name = {n.casefold(): i for i, n in conn.execute("SELECT id, name FROM persons")}
    temp_names = {}  # negative id -> name, for known people not in the library yet
    hidden_names = hidden_person_names(conn)       # people in a locked vault are never suggested
    for name, emb, h in known_refs:
        if h in lib_hashes or name.casefold() in hidden_names:
            continue
        pid = pid_by_name.get(name.casefold())
        if pid is None:
            pid = -(len(temp_names) + 1)
            temp_names[pid] = name
            pid_by_name[name.casefold()] = pid
        by_person.setdefault(pid, []).append(emb)
    ref_embs, ref_pids = [], []
    for pid, embs in by_person.items():
        if len(embs) > 300:
            embs = [embs[i] for i in rng.choice(len(embs), 300, replace=False)]
        ref_embs.extend(embs)
        ref_pids.extend([pid] * len(embs))

    rejected = {}
    for fid, pid in conn.execute("SELECT face_id, person_id FROM rejects"):
        rejected.setdefault(fid, set()).add(pid)

    # things that look like what you marked "not a face" are set aside for you to check
    # (nothing is written until the end, so confirming faces meanwhile never waits for this)
    nf_rows = conn.execute("SELECT emb, quality FROM faces WHERE ignored=3 AND model=?", (model,)).fetchall()
    notface = [emb_from_blob(b) for b, _ in nf_rows]
    nf_strong = np.array([(q or 0) >= NOTFACE_WEAK for _, q in nf_rows], dtype=bool)
    cand = conn.execute("""SELECT id, emb, px, score, face_ok, quality, clarity, yaw, pitch FROM faces WHERE confirmed=0
                           AND ignored IN (0, 4) AND model=?""", (model,)).fetchall()
    # what each face has now: only the faces whose suggestion or group changes are written
    before = {fid: (p, c) for fid, p, c in conn.execute(
        "SELECT id, person_id, cluster FROM faces WHERE confirmed=0 AND ignored=0 AND model=?", (model,))}
    conn.commit()                         # end the read, so nothing is held while working
    if cand and (notface or model == "arcface"):
        NF = np.stack(notface).astype(np.float32) if notface else None
        flagged = []
        keep = []
        # every candidate against every "not a face" at once (in chunks): best match, and best
        # match among the strong ones
        best_nf = best_strong = None
        if NF is not None:
            EC = np.stack([emb_from_blob(c[1]) for c in cand]).astype(np.float32)
            best_nf = np.empty(len(cand), dtype=np.float32)
            best_strong = np.full(len(cand), -2.0, dtype=np.float32)
            for s0 in range(0, len(cand), 4096):
                sims = EC[s0:s0 + 4096] @ NF.T
                best_nf[s0:s0 + 4096] = sims.max(axis=1)
                if nf_strong.any():
                    best_strong[s0:s0 + 4096] = sims[:, nf_strong].max(axis=1)
            del EC
        for n, c in enumerate(cand):
            q = c[5]
            weak = q is not None and q < WEAK_READING
            alike = False
            if NF is not None:
                if model != "arcface" or q is None:
                    alike = float(best_nf[n]) >= NOTFACE_SIM[model]
                else:
                    alike = (bool(best_strong[n] >= NOTFACE_STRONG_SIM) if nf_strong.any() else False) or \
                        (q < NOTFACE_WEAK and float(best_nf[n]) >= NOTFACE_SIM[model])
            if not c[4] and (weak or alike):
                flagged.append((c[0],))
            else:
                keep.append(c)
        cand = keep
    else:
        flagged = []
    if not cand:
        _write_flagged(conn, flagged)
        conn.execute("UPDATE faces SET cluster=NULL WHERE confirmed=1 OR ignored>0")
        conn.commit()
        return
    ids = np.array([c[0] for c in cand])
    E = np.stack([emb_from_blob(c[1]) for c in cand]).astype(np.float32)
    quality = np.array([(c[2] or 0) * (c[3] or 0) for c in cand])
    # clarity (0-1); faces scanned before 1.36 that haven't been read yet count as fairly clear
    clar = np.array([c[6] if c[6] is not None else DEFAULT_CLARITY for c in cand], dtype=np.float32)
    # a blurry face needs a closer match before it's suggested as someone (that's where wrong
    # suggestions came from), and counts less in the look-alike group it joins
    extra_thr = (LOW_CLARITY_EXTRA * (1.0 - clar)).astype(np.float32)
    # a face turned well to the side (head angle, when known) is matched more carefully too: the
    # fingerprints of side-on faces are less alike for the same person and more alike for others
    turn = np.array([max(abs(c[7] or 0) / 45.0, abs(c[8] or 0) / 35.0) for c in cand], dtype=np.float32)
    extra_thr += (SIDE_ON_EXTRA * np.clip(turn - 0.5, 0, 1.5) / 1.5).astype(np.float32)
    weights = (0.25 + 0.75 * clar).astype(np.float32)

    suggestion = np.zeros(len(ids), dtype=np.int64)   # 0 = no suggestion (real ids start at 1)
    if ref_embs:
        order_r = np.argsort(np.array(ref_pids), kind="stable")      # each person's references side by side
        R = np.stack([ref_embs[i] for i in order_r]).astype(np.float32)
        P = np.array(ref_pids)[order_r]
        persons, starts, counts_p = np.unique(P, return_index=True, return_counts=True)
        col_of = {int(p): j for j, p in enumerate(persons)}
        rej_rows, rej_cols = [], []
        pos_all = {int(f): i for i, f in enumerate(ids)}
        for fid, rps in rejected.items():
            i = pos_all.get(fid)
            if i is not None:
                for rp in rps:
                    if rp in col_of:
                        rej_rows.append(i)
                        rej_cols.append(col_of[rp])
        rej_rows, rej_cols = np.array(rej_rows, dtype=np.int64), np.array(rej_cols, dtype=np.int64)
        col_person = np.repeat(np.arange(len(persons)), counts_p)   # reference column -> person
        is_big = counts_p > 3
        small = np.where(counts_p <= 3)[0]              # people with 1-3 reference faces: all at once
        big = np.where(counts_p > 3)[0]
        sm_starts, sm_counts = starts[small], counts_p[small]
        for s in range(0, len(E), 2048):
            if cancelled():
                return
            sims = E[s:s + 2048] @ R.T                     # (chunk, refs)
            # best similarity per person = mean of the top-3 matches (robust to one fluke)
            per_person = np.empty((sims.shape[0], len(persons)), dtype=np.float32)
            if len(small):
                # their plain mean, added up in the same order numpy's mean uses
                acc = sims[:, sm_starts].copy()
                for t in (1, 2):
                    has = sm_counts > t
                    if has.any():
                        acc[:, has] += sims[:, sm_starts[has] + t]
                per_person[:, small] = acc / sm_counts.astype(np.float32)
            if len(big):
                # a person whose single best match is below (threshold - margin) can't decide
                # anything: their top-3 mean is lower still, so it can neither be the suggestion
                # nor come within the margin of one. Only the rest get the (slower) top-3 mean.
                # (found from the reference faces at or above that line: one pass over the scores)
                rr, cc = np.nonzero(sims >= (match_thr - margin))
                pj = col_person[cc]
                keep_ = is_big[pj]
                nr = sims.shape[0]
                key = np.unique(pj[keep_].astype(np.int64) * nr + rr[keep_])      # sorted by person
                need_p, need_rows = key // nr, key % nr
                per_person[:, big] = -1.0
                bounds = np.flatnonzero(np.diff(need_p)) + 1 if len(need_p) else []
                for n_, grp in enumerate(np.split(np.arange(len(need_p)), bounds) if len(need_p) else []):
                    j = int(need_p[grp[0]])
                    rows_ = need_rows[grp]
                    cols = sims[rows_, starts[j]:starts[j] + counts_p[j]]
                    per_person[rows_, j] = np.partition(cols, cols.shape[1] - 3, axis=1)[:, -3:].mean(axis=1)
                    if n_ % 64 == 63:
                        time.sleep(0)                      # let the window breathe
            if len(rej_rows):
                m = (rej_rows >= s) & (rej_rows < s + sims.shape[0])
                per_person[rej_rows[m] - s, rej_cols[m]] = -1
            if len(persons) > 1:
                top2 = np.partition(per_person, len(persons) - 2, axis=1)[:, -2:]
                best, second = top2.max(axis=1), top2.min(axis=1)
            else:
                best, second = per_person[:, 0], np.full(sims.shape[0], -1.0, np.float32)
            arg = per_person.argmax(axis=1)
            ex = extra_thr[s:s + sims.shape[0]]
            hit = (best >= match_thr + ex) & (best - second >= margin + ex / 2)
            suggestion[s:s + sims.shape[0]] = np.where(hit, persons[arg], 0)
            if progress:
                progress(min(len(E), s + 2048), len(E) * 2)
            time.sleep(0)


    # clustering of the faces with no suggestion (leader clustering, best faces first)
    rest = np.where(suggestion == 0)[0]
    rest = rest[np.lexsort((-quality[rest], -np.round(clar[rest], 1)))]     # clearest first, then biggest
    cluster_of = np.full(len(ids), -1, dtype=np.int64)
    # folder hints: folder number per face (-1 = no hint)
    folder_of = np.full(len(ids), -1, dtype=np.int64)
    bonus = 0.0
    try:
        hints_on = load_settings().get("folder_hints", True)
    except Exception:
        hints_on = True
    if hints_on and len(rest):
        bonus = FOLDER_BONUS.get(model, 0.04)
        pos = {int(f): i for i, f in enumerate(ids)}
        dirs, members = {}, {}
        for i in range(0, len(ids), 900):
            chunk = [int(x) for x in ids[i:i + 900]]
            for fid, path in conn.execute(f"""SELECT f.id, ph.path FROM faces f JOIN photos ph ON ph.id=f.photo_id
                                              WHERE f.id IN ({','.join('?' * len(chunk))})""", chunk):
                d = os.path.dirname(path)
                members.setdefault(d, []).append(pos[fid])
        for d, idx in members.items():
            if len(idx) < 2 or len(idx) > FOLDER_HINT_MAX_FACES or is_generic_folder(os.path.basename(d)):
                continue
            folder_of[idx] = dirs.setdefault(d, len(dirs))
    clusters_in_folder = {}     # folder number -> clusters holding a face from it
    if len(rest):
        def prog(done, total):
            if progress:
                progress(len(E) + done * len(E) // max(1, total), len(E) * 2)
        res, cents, clusters_in_folder = leader_cluster(E, rest, threshold, folder_of, bonus, cancelled, prog,
                                                        weights=weights)
        if res is None:
            return
        cluster_of[rest] = res[rest]
        k = len(cents)
        # refinement: put every face in its nearest final group
        if k:
            C = cents
            for s0 in range(0, len(rest), 2048):          # in chunks (many groups x many faces is big)
                part = rest[s0:s0 + 2048]
                sims = E[part] @ C.T
                if bonus:
                    for r, i in enumerate(part):
                        fo = int(folder_of[i])
                        if fo >= 0 and clusters_in_folder.get(fo):
                            sims[r, list(clusters_in_folder[fo])] += bonus
                best = sims.argmax(axis=1)
                ok = sims[np.arange(len(part)), best] >= threshold
                cluster_of[part] = np.where(ok, best, cluster_of[part])
                time.sleep(0)
        # "same person" answers from Quick review keep those faces in one group
        links = conn.execute("SELECT a, b FROM face_links").fetchall()
        if links:
            pos = {int(f): i for i, f in enumerate(ids)}
            parent = {}
            # a face linked to a named face (or to one suggested as someone) gets that person suggested too
            named = dict(conn.execute("""SELECT id, person_id FROM faces WHERE person_id IS NOT NULL AND confirmed=1
                                         AND id IN (SELECT a FROM face_links UNION SELECT b FROM face_links)"""))
            for a, b in links:
                for x, y in ((a, b), (b, a)):
                    if x in pos and cluster_of[pos[x]] >= 0 or x not in pos:
                        continue
                    who = named.get(y) or (int(suggestion[pos[y]]) if y in pos and suggestion[pos[y]] > 0 else 0)
                    if who and suggestion[pos[x]] == 0:
                        suggestion[pos[x]] = who

            def find(x):
                while parent.get(x, x) != x:
                    parent[x] = parent.get(parent[x], parent[x])
                    x = parent[x]
                return x
            for a, b in links:
                if a in pos and b in pos:
                    ca, cb = cluster_of[pos[a]], cluster_of[pos[b]]
                    if ca < 0 and cb < 0:
                        cluster_of[pos[a]] = cluster_of[pos[b]] = k
                        k += 1
                    elif ca < 0:
                        cluster_of[pos[a]] = cb
                    elif cb < 0:
                        cluster_of[pos[b]] = ca
                    elif ca != cb:
                        parent[find(int(cb))] = find(int(ca))
            if parent:
                for i in rest:
                    if cluster_of[i] >= 0:
                        cluster_of[i] = find(int(cluster_of[i]))
        # groups of one are just "unknown faces"
        uniq, cnt = np.unique(cluster_of[rest], return_counts=True)
        singles = set(uniq[cnt < 2].tolist())
        for i in rest:
            if cluster_of[i] in singles:
                cluster_of[i] = -1

    # one quick write at the end; WHERE confirmed=0 keeps anything you confirmed meanwhile
    _write_flagged(conn, flagged)
    for tmp in set(int(v) for v in suggestion if v < 0):      # known people suggested for the first time
        real = get_or_create_person(conn, temp_names[tmp])
        suggestion[suggestion == tmp] = real
    data = []
    for i, fid in enumerate(ids):
        p = int(suggestion[i]) if suggestion[i] > 0 else None
        c = int(cluster_of[i]) + 1 if cluster_of[i] >= 0 else None
        fid = int(fid)
        if fid in before and before[fid] == (p, c):
            continue                                       # unchanged: nothing to write
        data.append((p, c, fid))
    conn.executemany("UPDATE faces SET person_id=?, cluster=? WHERE id=? AND confirmed=0 AND ignored=0", data)
    conn.execute("UPDATE faces SET cluster=NULL WHERE (confirmed=1 OR ignored>0) AND cluster IS NOT NULL")
    conn.commit()
    settle_photo_tags(conn)
    ensure_person_folders(conn)
    conn.commit()


# ----------------------------------------------------------------- folders (your save layout)
# Normal folders form a tree; people can be placed in them. Person folders belong to one person
# (owner_id on the top one) and are saved inside that person's folder: <...>\<person>\<folder>.
# A name only has to be unique among its siblings (like Windows).
PATH_SEP = " › "


def folder_tree(conn):
    """{id: (name, parent id, owner person id of its top folder or None)}"""
    rows = {cid: (name, parent, owner) for cid, name, parent, owner in
            conn.execute("SELECT id, name, parent_id, owner_id FROM categories")}
    out = {}
    for cid in rows:
        cur, seen = cid, set()
        while rows[cur][1] in rows and cur not in seen:
            seen.add(cur)
            cur = rows[cur][1]
        out[cid] = (rows[cid][0], rows[cid][1], rows[cur][2])
    return out


def folder_owner(conn, cid):
    return folder_tree(conn).get(cid, ("", None, None))[2] if cid else None


def is_inside(conn, cid):
    """A person's folder (saved inside that person's folder)."""
    return folder_owner(conn, cid) is not None


def find_child(conn, name, parent=None, owner=None):
    name = " ".join(name.split())
    if parent:
        r = conn.execute("SELECT id FROM categories WHERE parent_id=? AND name=?", (parent, name)).fetchone()
    elif owner:
        r = conn.execute("SELECT id FROM categories WHERE parent_id IS NULL AND owner_id=? AND name=?",
                         (owner, name)).fetchone()
    else:
        r = conn.execute("SELECT id FROM categories WHERE parent_id IS NULL AND owner_id IS NULL AND name=?",
                         (name,)).fetchone()
    return r[0] if r else None


def make_folder(conn, name, parent=None, owner=None):
    """The folder called name in parent (or at the top of owner's folders / the normal tree);
    made if it doesn't exist."""
    name = " ".join(name.split())
    cid = find_child(conn, name, parent, owner)
    if cid:
        return cid
    return conn.execute("INSERT INTO categories(name, parent_id, owner_id) VALUES(?,?,?)",
                        (name, parent, None if parent else owner)).lastrowid


def folder_chain(conn, names, owner=None):
    """Make sure a > b > c exists (normal folders, or person `owner`'s folders). Returns c."""
    parent = None
    for name in names:
        if " ".join(name.split()):
            parent = make_folder(conn, name, parent, owner)
    return parent


person_folder_chain = folder_chain


def folder_label(conn, cid, tree=None, names=None):
    """'Models › Studio' for a normal folder, 'Jane Doe › Beach 2023' for one of Jane's."""
    tree = tree or folder_tree(conn)
    if cid not in tree:
        return ""
    chain, seen, cur = [], set(), cid
    while cur in tree and cur not in seen:
        seen.add(cur)
        chain.append(tree[cur][0])
        cur = tree[cur][1]
    owner = tree[cid][2]
    if owner:
        n = (names or {}).get(owner) or (conn.execute("SELECT name FROM persons WHERE id=?", (owner,)).fetchone()
                                         or ["?"])[0]
        chain.append(n)
    return PATH_SEP.join(reversed(chain))


def get_or_create_category(conn, name, people=()):
    """A folder from text: 'Models › Studio' (a path), 'Jane Doe › Beach' (one of Jane's), or a
    plain name (an existing normal folder of that name - top level first - or else one of the
    people's own folders with that name, or else a new top-level folder)."""
    parts = [" ".join(p.split()) for p in re.split(r"\s*[›>|\\/]\s*", name) if p.strip()]
    if not parts:
        return None
    if len(parts) > 1:
        r = conn.execute("SELECT id FROM persons WHERE name=?", (parts[0],)).fetchone()
        if r:
            return folder_chain(conn, parts[1:], owner=r[0])
        return folder_chain(conn, parts)
    n = parts[0]
    r = conn.execute("SELECT id FROM categories WHERE name=? AND owner_id IS NULL ORDER BY parent_id IS NOT NULL, id",
                     (n,)).fetchall()
    tree = folder_tree(conn)
    normal = [cid for (cid,) in r if tree.get(cid, (0, 0, None))[2] is None]
    if normal:
        return normal[0]
    for person in people:
        cid = find_child(conn, n, None, person)
        if cid:
            return cid
    return make_folder(conn, n)


def set_photo_folder(conn, photo_ids, cid):
    """Give photos a folder. A normal folder replaces the photo's normal folder; a person's
    folder replaces that person's folder on the photo - only theirs: everyone else on the photo
    keeps their own (or none)."""
    if not cid:
        return
    owner = folder_owner(conn, cid)
    if owner is None:
        conn.executemany("UPDATE photos SET category_id=? WHERE id=?", [(cid, p) for p in photo_ids])
    else:
        take_out_of_person_folders(conn, photo_ids, owner)
        conn.executemany("INSERT OR IGNORE INTO photo_folders(photo_id, folder_id) VALUES(?,?)",
                         [(p, cid) for p in photo_ids])
        ensure_person_folders(conn, photo_ids)
    conn.execute("UPDATE categories SET last_used=?, uses=uses+? WHERE id=?", (time.time(), len(photo_ids), cid))


def take_out_of_person_folders(conn, photo_ids, owner):
    """Take photos out of this person's own folders (nobody else's)."""
    mine = [k for k, v in folder_tree(conn).items() if v[2] == owner]
    if not mine:
        return
    q = ",".join(map(str, mine))
    conn.executemany(f"DELETE FROM photo_folders WHERE photo_id=? AND folder_id IN ({q})", [(p,) for p in photo_ids])


def copied_folder_placements(conn):
    """What versions before 1.37.3 copied: a group photo put in Jane's "Beach 2023" was also put in
    a "Beach 2023" made for everyone else on it. Those copies: [(photo id, folder id)] - a person's
    folder entry for a photo when someone else on the same photo has the same folder path, made
    earlier (the original), plus the copied folders that are left with nothing in them."""
    tree = folder_tree(conn)
    paths = category_paths(conn)
    by_photo = {}
    for pid, fid in conn.execute("SELECT photo_id, folder_id FROM photo_folders"):
        if fid in tree and tree[fid][2] is not None:
            by_photo.setdefault(pid, []).append(fid)
    copies = []
    for pid, fids in by_photo.items():
        first = {}                                  # path -> the earliest-made folder with it
        for fid in sorted(fids):
            chain = tuple(paths.get(fid, []))
            if chain in first and tree[fid][2] != tree[first[chain]][2]:
                copies.append((pid, fid))
            else:
                first.setdefault(chain, fid)
    return copies


def remove_copied_folders(conn):
    """Take out the copies (copied_folder_placements) and delete copied folders left empty (no
    photos, no people placed in them, no folders inside). Returns (entries, folders) removed."""
    copies = copied_folder_placements(conn)
    if not copies:
        return 0, 0
    touched = {fid for _p, fid in copies}
    conn.executemany("DELETE FROM photo_folders WHERE photo_id=? AND folder_id=?", copies)
    gone = 0
    for fid in sorted(touched, reverse=True):
        while fid:
            busy = (conn.execute("SELECT 1 FROM photo_folders WHERE folder_id=? LIMIT 1", (fid,)).fetchone()
                    or conn.execute("SELECT 1 FROM categories WHERE parent_id=? LIMIT 1", (fid,)).fetchone()
                    or conn.execute("SELECT 1 FROM persons WHERE category_id=? LIMIT 1", (fid,)).fetchone()
                    or conn.execute("SELECT 1 FROM photos WHERE category_id=? LIMIT 1", (fid,)).fetchone())
            if busy:
                break
            parent = conn.execute("SELECT parent_id FROM categories WHERE id=?", (fid,)).fetchone()
            conn.execute("DELETE FROM categories WHERE id=?", (fid,))
            gone += 1
            fid = parent[0] if parent and parent[0] in touched else None
    conn.commit()
    return len(copies), gone


def set_category(conn, photo_ids, name, people=()):
    """Folder bar / K: give photos a folder by name. name=None takes every folder off them."""
    if not name:
        conn.executemany("UPDATE photos SET category_id=NULL WHERE id=?", [(p,) for p in photo_ids])
        conn.executemany("DELETE FROM photo_folders WHERE photo_id=?", [(p,) for p in photo_ids])
        conn.commit()
        return None
    cid = get_or_create_category(conn, name, people)
    set_photo_folder(conn, photo_ids, cid)
    conn.commit()
    return cid


def people_on_photo(conn, photo_id):
    return [r[0] for r in conn.execute(
        """SELECT person_id FROM faces WHERE photo_id=? AND confirmed=1 AND ignored=0 AND person_id IS NOT NULL
           UNION SELECT person_id FROM photo_people WHERE photo_id=?""", (photo_id, photo_id))]


def ensure_person_folders(conn, photo_ids=None):
    """(Before 1.37.3 this copied a person's folder to everyone else on the photo - Jane's
    "Beach 2023" made a "Beach 2023" for Bob too. Each person's folders are their own now: a photo
    is put in a folder only for the person whose gallery you're in; the others on it are sorted
    separately. Kept so older calls still work.)"""
    return


def category_descendants(conn, cat_id):
    """All folders inside this one (any depth)."""
    kids = {}
    for cid, parent in conn.execute("SELECT id, parent_id FROM categories"):
        kids.setdefault(parent, []).append(cid)
    out, todo = set(), [cat_id]
    while todo:
        for k in kids.get(todo.pop(), []):
            if k not in out:
                out.add(k)
                todo.append(k)
    return out


def _convert_kind(conn, ids, old_owner, new_owner):
    """A folder moved between the normal tree and a person (or between people): photos keep it."""
    q = ",".join(map(str, ids))
    if old_owner is None and new_owner is not None:
        for pid, cid in conn.execute(f"SELECT id, category_id FROM photos WHERE category_id IN ({q})").fetchall():
            conn.execute("INSERT OR IGNORE INTO photo_folders(photo_id, folder_id) VALUES(?,?)", (pid, cid))
        conn.execute(f"UPDATE photos SET category_id=NULL WHERE category_id IN ({q})")
        conn.execute(f"UPDATE persons SET category_id=NULL WHERE category_id IN ({q})")
    elif old_owner is not None and new_owner is None:
        for pid, cid in conn.execute(f"SELECT photo_id, folder_id FROM photo_folders WHERE folder_id IN ({q})").fetchall():
            conn.execute("UPDATE photos SET category_id=? WHERE id=? AND category_id IS NULL", (cid, pid))
        conn.execute(f"DELETE FROM photo_folders WHERE folder_id IN ({q})")


def move_category(conn, cat_id, parent_id=None, owner=False):
    """Put a folder inside another (parent_id), or at the top of the normal tree (owner=None),
    or at the top of a person's folders (owner=person id). Returns False if impossible
    (inside itself, or a folder with that name is already there)."""
    if parent_id == cat_id or (parent_id and parent_id in category_descendants(conn, cat_id)):
        return False
    tree = folder_tree(conn)
    if cat_id not in tree:
        return False
    old_owner = tree[cat_id][2]
    if parent_id:
        new_owner = tree.get(parent_id, ("", None, None))[2]
    else:
        new_owner = None if owner is False else owner
        if owner is False:
            new_owner = old_owner if tree[cat_id][1] is None else None
    name = tree[cat_id][0]
    clash = find_child(conn, name, parent_id, new_owner)
    if clash and clash != cat_id:
        return False
    ids = [cat_id] + list(category_descendants(conn, cat_id))
    conn.execute("UPDATE categories SET parent_id=?, owner_id=? WHERE id=?",
                 (parent_id, None if parent_id else new_owner, cat_id))
    if old_owner != new_owner:
        _convert_kind(conn, ids, old_owner, new_owner)
    if new_owner is not None:
        ensure_person_folders(conn)
    conn.commit()
    return True


def place_person(conn, person_ids, cat_id=None):
    """Put people in a folder (one place each; None = take them out). Not in a person's folder."""
    if cat_id and is_inside(conn, cat_id):
        return False
    conn.executemany("UPDATE persons SET category_id=? WHERE id=?", [(cat_id, p) for p in person_ids])
    conn.commit()
    return True


def rename_category(conn, cat_id, new_name):
    """Rename; if a sibling already has that name the two folders are merged."""
    import known
    new_name = " ".join(new_name.split())
    tree = folder_tree(conn)
    old = conn.execute("SELECT name, uses, parent_id FROM categories WHERE id=?", (cat_id,)).fetchone()
    if not old:
        return cat_id
    target = find_child(conn, new_name, old[2], tree[cat_id][2])
    known.rename_category(old[0], new_name)
    if target and target != cat_id:
        conn.execute("UPDATE photos SET category_id=? WHERE category_id=?", (target, cat_id))
        conn.execute("UPDATE OR IGNORE photo_folders SET folder_id=? WHERE folder_id=?", (target, cat_id))
        conn.execute("DELETE FROM photo_folders WHERE folder_id=?", (cat_id,))
        conn.execute("UPDATE persons SET category_id=? WHERE category_id=?", (target, cat_id))
        for kid, kname in conn.execute("SELECT id, name FROM categories WHERE parent_id=?", (cat_id,)).fetchall():
            same = find_child(conn, kname, target)
            if same:
                rename_category(conn, kid, kname + " (2)")
            conn.execute("UPDATE categories SET parent_id=? WHERE id=?", (target, kid))
        conn.execute("UPDATE categories SET uses=uses+? WHERE id=?", (old[1] or 0, target))
        conn.execute("DELETE FROM categories WHERE id=?", (cat_id,))
        conn.commit()
        return target
    conn.execute("UPDATE categories SET name=? WHERE id=?", (new_name, cat_id))
    conn.commit()
    return cat_id


def delete_category(conn, cat_id):
    """Remove a folder. Its photos, people and sub-folders move up one level."""
    import known
    tree = folder_tree(conn)
    old = conn.execute("SELECT name, parent_id, owner_id FROM categories WHERE id=?", (cat_id,)).fetchone()
    if not old:
        return
    known.forget_category(old[0])
    parent, owner = old[1], tree[cat_id][2]
    if owner is None:
        conn.execute("UPDATE photos SET category_id=? WHERE category_id=?", (parent, cat_id))
    elif parent:
        conn.execute("UPDATE OR IGNORE photo_folders SET folder_id=? WHERE folder_id=?", (parent, cat_id))
    conn.execute("DELETE FROM photo_folders WHERE folder_id=?", (cat_id,))
    conn.execute("UPDATE persons SET category_id=? WHERE category_id=?", (parent, cat_id))
    conn.execute("UPDATE categories SET parent_id=?, owner_id=? WHERE parent_id=?",
                 (parent, None if parent else owner, cat_id))
    conn.execute("DELETE FROM categories WHERE id=?", (cat_id,))
    conn.commit()


def create_category(conn, name, parent_id=None, owner=None):
    cid = make_folder(conn, name, parent_id, owner)
    conn.commit()
    return cid


def category_paths(conn):
    """{folder id: [names from its top folder down]} (person folders: without the person)."""
    rows = {cid: (name, parent) for cid, name, parent in conn.execute("SELECT id, name, parent_id FROM categories")}
    out = {}
    for cid in rows:
        chain, seen, cur = [], set(), cid
        while cur in rows and cur not in seen:
            seen.add(cur)
            chain.append(rows[cur][0])
            cur = rows[cur][1]
        out[cid] = list(reversed(chain))
    return out


def photo_destinations(conn, include_suggestions=False, photo_ids=None):
    r"""Where each photo is saved by 'Sort into folders':
    {photo id: [(folder id or None, person id or None, person-folder id or None), ...]}, one
    entry per destination folder, the person with the biggest face first.
      <folder chain>\<person>\<person-folder chain>
    - a person placed in a folder always goes there; otherwise the photo's normal folder;
      otherwise ~Uncategorized (folder None)
    - each of the person's own folders the photo is in -> one destination each
    - nobody on it: (its folder, None, None)"""
    tree = folder_tree(conn)
    placed = dict(conn.execute("SELECT id, category_id FROM persons WHERE category_id IS NOT NULL"))
    photo_cat, people, pf = {}, {}, {}
    q = ("SELECT id, category_id FROM photos WHERE error IS NULL AND sort_later=0 AND discarded=0 "
         "AND id NOT IN (SELECT photo_id FROM faces WHERE ignored=2)")
    for pid, cid in conn.execute(q):
        if photo_ids is None or pid in photo_ids:
            photo_cat[pid] = cid
    both = include_suggestions == "both"      # -> (confirmed only, with suggestions), in one go
    cond = "" if include_suggestions else "AND confirmed=1"
    people_all = {}
    for pid, person, conf in conn.execute(f"""SELECT photo_id, person_id, MAX(confirmed) FROM faces WHERE ignored=0 {cond}
                                              AND person_id IS NOT NULL GROUP BY photo_id, person_id
                                              ORDER BY photo_id, MAX(w*h) DESC"""):
        if pid in photo_cat:
            if both:
                people_all.setdefault(pid, []).append(person)
            if conf or not both:
                people.setdefault(pid, []).append(person)
    for pid, person in conn.execute("""SELECT pp.photo_id, pp.person_id FROM photo_people pp
                                       JOIN persons p ON p.id=pp.person_id ORDER BY pp.photo_id, p.name"""):
        if pid in photo_cat:
            for d in ((people, people_all) if both else (people,)):
                if person not in d.setdefault(pid, []):
                    d[pid].append(person)
    for pid, fid in conn.execute("SELECT photo_id, folder_id FROM photo_folders"):
        if pid in photo_cat and fid in tree and tree[fid][2] is not None:
            pf.setdefault(pid, {}).setdefault(tree[fid][2], []).append(fid)
    if both:
        return _destinations(photo_cat, people, pf, tree, placed), _destinations(photo_cat, people_all, pf, tree, placed)
    return _destinations(photo_cat, people, pf, tree, placed)


def _destinations(photo_cat, people, pf, tree, placed):
    out = {}
    for pid, cid in photo_cat.items():
        normal = cid if cid in tree and tree[cid][2] is None else None
        who = people.get(pid)
        if not who:
            if normal:
                out[pid] = [(normal, None, None)]
            continue
        dests = []
        for person in who:
            base = (placed.get(person) if placed.get(person) in tree else None) or normal
            for inner in pf.get(pid, {}).get(person) or [None]:
                d = (base, person, inner)
                if d not in dests:
                    dests.append(d)
        out[pid] = dests
    return out


def photo_places(conn):
    """({folder id: set(photo ids saved under it)}, set(photo ids that go to ~Uncategorized))"""
    by_cat, uncategorized = {}, set()
    for pid, dests in photo_destinations(conn).items():
        for base, person, inner in dests:
            if base:
                by_cat.setdefault(base, set()).add(pid)
            elif person:
                uncategorized.add(pid)
            if inner:
                by_cat.setdefault(inner, set()).add(pid)
    return by_cat, uncategorized


def _vault_columns(conn):
    ccols = {r[1] for r in conn.execute("PRAGMA table_info(categories)")}
    for col, decl in (("vault", "INTEGER DEFAULT 0"), ("vault_pw", "TEXT"), ("vault_locked", "INTEGER DEFAULT 0"),
                      ("vault_path", "TEXT"),                   # 1.21: Vaults
                      ("dup_skip", "INTEGER DEFAULT 0")):       # 1.22: left out of duplicate checks
        if col not in ccols:
            conn.execute(f"ALTER TABLE categories ADD COLUMN {col} {decl}")
    if "dup_skip" not in {r[1] for r in conn.execute("PRAGMA table_info(photos)")}:
        conn.execute("ALTER TABLE photos ADD COLUMN dup_skip INTEGER DEFAULT 0")
    conn.commit()


def migrate_folders(conn):
    """1.13: folder names only unique among siblings (rebuild the table without UNIQUE), person
    folders (owner_id + photo_folders), and the old shared 'inside each person' folders turned
    into each person's own folders."""
    sql = (conn.execute("SELECT sql FROM sqlite_master WHERE name='categories'").fetchone() or [""])[0] or ""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(categories)")}
    if "UNIQUE" in sql.upper() or "owner_id" not in cols:
        conn.commit()
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("""CREATE TABLE categories_new(
            id INTEGER PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE, last_used REAL DEFAULT 0,
            uses INTEGER DEFAULT 0, parent_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
            inside_person INTEGER DEFAULT 0, owner_id INTEGER REFERENCES persons(id) ON DELETE CASCADE)""")
        have = [c for c in ("id", "name", "last_used", "uses", "parent_id", "inside_person") if c in cols]
        conn.execute(f"INSERT INTO categories_new({','.join(have)}) SELECT {','.join(have)} FROM categories")
        conn.execute("DROP TABLE categories")
        conn.execute("ALTER TABLE categories_new RENAME TO categories")
        conn.commit()
        conn.execute("PRAGMA foreign_keys=ON")
        _vault_columns(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS categories_parent ON categories(parent_id)")
    old = [cid for (cid,) in conn.execute(
        "SELECT id FROM categories WHERE inside_person=1 AND parent_id IS NULL AND owner_id IS NULL")]
    if not old:
        return
    paths = category_paths(conn)
    for top in old:
        ids = [top] + list(category_descendants(conn, top))
        q = ",".join(map(str, ids))
        keep = False
        for pid, cid in conn.execute(f"SELECT id, category_id FROM photos WHERE category_id IN ({q})").fetchall():
            who = people_on_photo(conn, pid)
            if not who:
                keep = True
                continue
            for person in who:
                f = folder_chain(conn, paths.get(cid, []), owner=person)
                conn.execute("INSERT OR IGNORE INTO photo_folders(photo_id, folder_id) VALUES(?,?)", (pid, f))
            conn.execute("UPDATE photos SET category_id=NULL WHERE id=?", (pid,))
        if keep:
            conn.execute(f"UPDATE categories SET inside_person=0 WHERE id IN ({q})")
        else:
            conn.execute(f"DELETE FROM categories WHERE id IN ({q})")
    conn.commit()


# ----------------------------------------------------------------- "to sort" lists by date / place
UNSORTED_SQL = """ph.error IS NULL AND ph.sort_later=0 AND ph.discarded=0 AND COALESCE(ph.is_other, 0)=0
    AND NOT EXISTS (SELECT 1 FROM faces d WHERE d.photo_id=ph.id AND d.ignored=2)
    AND (EXISTS (SELECT 1 FROM faces u WHERE u.photo_id=ph.id AND u.ignored=0 AND u.person_id IS NULL)
         OR (ph.nfaces=0 AND NOT EXISTS (SELECT 1 FROM photo_people x WHERE x.photo_id=ph.id)))"""


def file_created(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return getattr(st, "st_birthtime", None) or st.st_ctime


def fill_created(conn):
    """Look up 'date created' for photos scanned before it was recorded."""
    rows = conn.execute("SELECT id, path FROM photos WHERE created IS NULL AND error IS NULL").fetchall()
    conn.executemany("UPDATE photos SET created=? WHERE id=?", [(file_created(p) or 0, i) for i, p in rows])
    conn.commit()


def photo_buckets(conn, mode):
    """Photos still to sort, grouped for the lower list: {key: (label, sort key, [photo ids])}.
    mode: 'taken' / 'created' (by month) or 'location' (by folder)."""
    import datetime
    if mode == "created":
        fill_created(conn)
    col = {"taken": "ph.taken", "created": "ph.created", "location": "ph.path"}[mode]
    out = {}
    for pid, v in conn.execute(f"SELECT ph.id, {col} FROM photos ph WHERE {UNSORTED_SQL}"):
        if mode == "location":
            key = os.path.dirname(v)
            label, order = (os.sep.join(key.split(os.sep)[-2:]) or key), key.casefold()
        elif v:
            d = datetime.datetime.fromtimestamp(v)
            key = d.strftime("%Y-%m")
            label, order = d.strftime("%B %Y"), -int(d.strftime("%Y%m"))
        else:
            key, label, order = "none", "No date", 1
        out.setdefault(key, [label, order, []])[2].append(pid)
    return out


def move_prefix(conn, old_dir, new_dir):
    """A folder was renamed / moved: the photos inside it follow (names, tags, folders stay)."""
    old_dir, new_dir = old_dir.rstrip("\\/"), new_dir.rstrip("\\/")
    pre = old_dir + os.sep
    conn.execute("UPDATE photos SET path=? || substr(path, ?) WHERE substr(path, 1, ?)=?",
                 (new_dir, len(old_dir) + 1, len(pre), pre))
    conn.commit()


# ----------------------------------------------------------------- duplicates
# Two steps. 1) The 64-bit fingerprints pick pictures that look alike (cheap, but video stills
# and burst shots look alike too). 2) Each likely pair is compared closely: both pictures as
# 256 x 256 grey, tones matched (so re-saved / brightened copies still line up), and any spot
# that differs - a moved hand, a blink, someone walking - means they're different pictures.
DUP_SIMILAR = 6          # fingerprints this close (of 64 bits) are worth comparing closely
DUP_ASPECT = 0.02        # same shape (2%)
DUP_FINE = 256
DUP_PIXEL = 0.12         # close check: the most-changed 0.1% of the picture may differ this much...
DUP_SPOT = 0.09          # ...and no 1/32-wide spot may differ more than this on average


class _ShaCache(dict):
    """File checksums for the duplicate check, kept in the library (file_sha) so an exact-copy
    check never reads a whole file twice: reused while the file's size and date are unchanged.
    (Before, every duplicate check read every exact copy in full again - slow with big files.)"""

    def __init__(self, conn, info):
        super().__init__()
        self.conn, self.info, self.new = conn, info, []
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS file_sha(photo_id INTEGER PRIMARY KEY, size INTEGER, "
                         "mtime REAL, sha TEXT)")
            self.mt = dict(conn.execute("SELECT id, mtime FROM photos"))
            self.saved = {pid: (sz, mt, h) for pid, sz, mt, h in conn.execute("SELECT photo_id, size, mtime, sha FROM file_sha")}
        except Exception:
            self.mt, self.saved = {}, {}

    def of(self, pid):
        if pid in self:
            return self[pid]
        size, mt = self.info[pid][4], self.mt.get(pid)
        old = self.saved.get(pid)
        if old and old[0] == size and old[1] == mt and old[2]:
            h = old[2]
        else:
            h = _file_sha1(self.info[pid][6])
            if h:
                self.new.append((pid, size, mt, h))
        self[pid] = h
        return h

    def save(self):
        if self.new:
            try:
                self.conn.executemany("INSERT OR REPLACE INTO file_sha(photo_id, size, mtime, sha) VALUES(?,?,?,?)",
                                      self.new)
                self.conn.commit()
            except Exception:
                pass
            self.new = []


def _file_sha1(path):
    h = hashlib.sha1()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _fine_load(path, is_video, cover_t):
    import cv2
    import engine
    try:
        img = engine.load_image(path, 768, t=cover_t if is_video else None)
    except Exception:
        return None
    bgr = img.bgr if hasattr(img, "bgr") else img[0]
    if bgr is None or bgr.size == 0:
        return None
    h, w = bgr.shape[:2]
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (DUP_FINE, DUP_FINE), interpolation=cv2.INTER_AREA if min(h, w) >= DUP_FINE else cv2.INTER_CUBIC)
    return w / h, min(h, w), g


def _fine(conn, info, pid, cache):
    """(aspect, short side, 256x256 grey) for a photo, kept in the library so it's only read once."""
    if pid in cache:
        return cache[pid]
    r = info[pid]
    row = conn.execute("SELECT size, aspect, px FROM dup_fine WHERE photo_id=?", (pid,)).fetchone()
    out = None
    if row and row[0] == r[4] and row[2] is not None and len(row[2]) == DUP_FINE * DUP_FINE:
        out = (row[1], min(r[2] or DUP_FINE, r[3] or DUP_FINE),
               np.frombuffer(row[2], np.uint8).reshape(DUP_FINE, DUP_FINE))
    else:
        got = _fine_load(r[6], r[7], r[8])
        if got:
            aspect, short, g = got
            conn.execute("INSERT OR REPLACE INTO dup_fine(photo_id, size, aspect, px) VALUES(?,?,?,?)",
                         (pid, r[4], aspect, g.tobytes()))
            conn.commit()     # right away: never keep the library locked while the next pictures load
            out = (aspect, min(short, r[2] or short, r[3] or short), g)
    cache[pid] = out
    return out


def _fine_prep(f, n, memo=None):
    """The grey copy readied for comparing at n x n: blurred, its tones sorted, clipped spots."""
    import cv2
    key = (id(f[2]), n)
    if memo is not None and key in memo:
        return memo[key]
    g8 = f[2]
    g = g8.astype(np.float32)
    clip = ((g8 >= 250) | (g8 <= 5)).astype(np.uint8)
    if n != DUP_FINE:
        g = cv2.resize(g, (n, n), interpolation=cv2.INTER_AREA)
        clip = cv2.resize(clip, (n, n), interpolation=cv2.INTER_NEAREST)
    g = cv2.GaussianBlur(g, (0, 0), 1.5 * n / DUP_FINE).reshape(-1)
    order = np.argsort(g)
    out = (g, order, g[order], clip.reshape(-1).astype(bool))
    if memo is not None:
        memo[key] = out
    return out


def same_picture(fa, fb, memo=None):
    """Close check of two (aspect, short side, grey) - True only when nothing in the picture moved."""
    import cv2
    (aa, sa, _), (ab, sb, _) = fa, fb
    if abs(aa - ab) > DUP_ASPECT * max(aa, ab):
        return False
    # compare at the smaller picture's own sharpness (a small copy can't show fine detail)
    short = max(32, min(sa or DUP_FINE, sb or DUP_FINE, DUP_FINE))
    n = max(64, min(DUP_FINE, short // 32 * 32))
    loosen = DUP_FINE / n
    A, _, a_sorted, a_clip = _fine_prep(fa, n, memo)
    B, b_order, _, b_clip = _fine_prep(fb, n, memo)
    sd = A.std()
    if sd < 2.0:                          # nearly one colour: nothing to compare
        return bool(abs(A.mean() - B.mean()) < 4)
    Bm = np.empty_like(B)                 # B with A's tones (brightness, contrast, curves)
    Bm[b_order] = a_sorted
    d = np.abs(A - Bm) * (1.0 / sd)
    # leave out blown-out highlights / crushed shadows (a brightened copy loses detail there)
    clip = (a_clip | b_clip).reshape(n, n).astype(np.uint8)
    keep = (cv2.dilate(clip, np.ones((5, 5), np.uint8)) == 0).reshape(-1)
    if keep.mean() < 0.5:                 # mostly white / black: compare everything
        keep[:] = True
    d[~keep] = 0
    k = n // 32
    if d.reshape(32, k, 32, k).mean(axis=(1, 3)).max() > DUP_SPOT * loosen:     # one spot changed
        return False
    dk = d[keep]
    top = max(1, int(dk.size * 0.001))
    return bool(np.partition(dk, dk.size - top)[dk.size - top] <= DUP_PIXEL * loosen)


DUP_UNCHECKED = [0]      # likely pairs the last quick call left for the background check


def dup_skipped_folders(conn):
    out = set()
    for (cid,) in conn.execute("SELECT id FROM categories WHERE dup_skip=1"):
        out |= {cid} | category_descendants(conn, cid)
    return out


def dup_skipped_photos(conn):
    """Photos left out of duplicate checks: marked one by one, or in a folder you left out."""
    skip = {r[0] for r in conn.execute("SELECT id FROM photos WHERE dup_skip=1")}
    folders = dup_skipped_folders(conn)
    if folders:
        q = ",".join(map(str, folders))
        skip |= {r[0] for r in conn.execute(f"SELECT id FROM photos WHERE category_id IN ({q})")}
        skip |= {r[0] for r in conn.execute(f"SELECT photo_id FROM photo_folders WHERE folder_id IN ({q})")}
        skip |= {r[0] for r in conn.execute(f"""SELECT DISTINCT f.photo_id FROM faces f JOIN persons p ON p.id=f.person_id
                                               WHERE f.confirmed=1 AND p.category_id IN ({q})""")}
    return skip


def set_dup_skip(conn, photo_ids=(), folder_id=None, on=True):
    if folder_id is not None:
        conn.execute("UPDATE categories SET dup_skip=? WHERE id=?", (int(bool(on)), folder_id))
    if photo_ids:
        conn.executemany("UPDATE photos SET dup_skip=? WHERE id=?", [(int(bool(on)), p) for p in photo_ids])
    conn.commit()


def duplicate_sets(conn, threshold=DUP_SIMILAR, progress=None, cancelled=lambda: False, check_new=True):
    """Sets of the same picture: exact copies, and resized / re-saved / lightly edited copies.
    Not frames from the same video or shots in a burst - anything that moved makes them different.
    check_new=False: quick - only pairs already looked at closely (the rest wait for the background check).
    [{"ids": [photo ids], "best": id, "exact": bool}], biggest sets first. The best one is a camera
    RAW file if there is one (Settings), else the largest picture (then the biggest file, then the
    earliest taken). A RAW + JPEG pair of the same shot (same name, same folder) isn't a duplicate."""
    if not conn.execute("SELECT 1 FROM meta WHERE k='dup_video_exact'").fetchone():
        # 1.37.5: videos count as duplicates only when they're exact copies - forget the video pairs
        # older versions judged by their picture (two clips can start on the same frame)
        conn.execute("DELETE FROM dup_checked WHERE a IN (SELECT id FROM photos WHERE is_video=1) "
                     "OR b IN (SELECT id FROM photos WHERE is_video=1)")
        conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('dup_video_exact', '1')")
        conn.commit()
    rows = conn.execute("""SELECT id, phash, width, height, size, taken, path, is_video, cover_t FROM photos
                           WHERE (phash IS NOT NULL OR is_video=1 OR LOWER(path) LIKE '%.gif')
                           AND COALESCE(is_other, 0)=0 AND error IS NULL AND sort_later=0 AND discarded=0
                           AND copy_of IS NULL
                           AND id NOT IN (SELECT photo_id FROM faces WHERE ignored=2)""").fetchall()
    # anything already in a ~To Be Discarded folder is on its way out: not worth checking
    trash = re.compile(r"[\\/]~To Be Discarded[\\/]", re.I)
    skip = dup_skipped_photos(conn)          # photos / folders you left out of duplicate checks
    rows = [r for r in rows if not trash.search(r[6]) and r[0] not in skip]
    if len(rows) < 2:
        return []
    info = {r[0]: r for r in rows}
    ignore = set(conn.execute("SELECT a, b FROM dup_ignore").fetchall())
    st = load_settings()
    prefer_raw = bool(st.get("dup_prefer_raw", True))
    raw_pairs = bool(st.get("dup_raw_jpeg_pairs", True))
    from engine import RAW_EXTS
    is_raw = {pid: os.path.splitext(r[6])[1].lower() in RAW_EXTS for pid, r in info.items()}

    def raw_jpeg_pair(a, b):
        """A camera's RAW + JPEG of the same shot (same name, same folder): kept on purpose."""
        if not raw_pairs or is_raw[a] == is_raw[b]:
            return False
        pa, pb = info[a][6], info[b][6]
        return (os.path.normcase(os.path.dirname(pa)) == os.path.normcase(os.path.dirname(pb)) and
                os.path.splitext(os.path.basename(pa))[0].casefold() == os.path.splitext(os.path.basename(pb))[0].casefold())
    mask = (1 << 64) - 1
    parent = {}

    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x
    hashes = [(r[0], r[1] & mask) for r in rows if r[1] is not None and not r[7]]
    pairs = set()
    for shift in (0, 16, 32, 48):           # pictures within a few bits share at least one 16-bit band
        buckets = {}
        for pid, h in hashes:
            buckets.setdefault((h >> shift) & 0xFFFF, []).append((pid, h))
        for members in buckets.values():
            if len(members) < 2 or len(members) > 300:     # (huge buckets: blank / one-colour pictures)
                continue
            for i in range(len(members)):
                a, ha = members[i]
                for b, hb in members[i + 1:]:
                    key = (min(a, b), max(a, b))
                    if key in pairs or key in ignore or bin(ha ^ hb).count("1") > threshold:
                        continue
                    if raw_jpeg_pair(a, b):
                        continue
                    pairs.add(key)
    # exact copies, whatever the picture looks like: same file size, then the same contents. This
    # is how videos are compared (only exact copies: two different clips can start on the same
    # frame) and it also finds copies of GIFs and of blank / one-colour pictures
    by_size = {}
    for r in rows:
        if r[4]:
            by_size.setdefault((r[4], bool(r[7])), []).append(r[0])
    for members in by_size.values():
        if 2 <= len(members) <= 50:
            members.sort()
            for i in range(len(members)):
                for b in members[i + 1:]:
                    key = (members[i], b)
                    if key not in ignore and not raw_jpeg_pair(*key):
                        pairs.add(key)
    # photos of several people are copied under each of them on purpose: those copies aren't duplicates,
    # only two copies under the same person are
    # who is on each photo: confirmed or suggested faces, and whole-photo tags (a copy made for the
    # second person is often only suggested until you confirm it, so suggestions count here)
    people = {}
    for pid, name in conn.execute("""SELECT DISTINCT f.photo_id, p.name FROM faces f JOIN persons p ON p.id=f.person_id
                                     WHERE f.ignored=0
                                     UNION SELECT pp.photo_id, p.name FROM photo_people pp
                                     JOIN persons p ON p.id=pp.person_id"""):
        if pid in info:
            people.setdefault(pid, set()).add(name.casefold())

    def person_dir(pid, names):
        parts = [x.casefold() for x in re.split(r"[\\/]", info[pid][6])[:-1]]
        for i in range(len(parts) - 1, -1, -1):          # the deepest folder named after one of them
            if parts[i] in names:
                return parts[i], i, tuple(parts[:i])
        return None

    def expected_copies(a, b):
        names = people.get(a, set()) | people.get(b, set())
        if len(names) < 2:                               # one person: a real duplicate
            return False
        da, db = person_dir(a, names), person_dir(b, names)
        if da and db:
            return da[0] != db[0]                        # under different people: on purpose
        # folders not named after them (renamed since, or sorted by hand): copies of a group photo
        # in different folders are taken as made on purpose too
        return os.path.normcase(os.path.dirname(info[a][6])) != os.path.normcase(os.path.dirname(info[b][6]))
    pairs = {k for k in pairs if not expected_copies(*k)}
    cache, new, memo = {}, [], {}
    sha = _ShaCache(conn, info)
    done = {(a, b): (sa, sb, same) for a, b, sa, sb, same in conn.execute("SELECT a, b, sa, sb, same FROM dup_checked")}
    todo = sorted(pairs)
    unchecked = 0
    for n, (a, b) in enumerate(todo, 1):
        if cancelled():
            break
        if progress and (n % 50 == 0 or n == len(todo)):
            progress(n, len(todo))
        if find(a) == find(b):
            continue
        prev = done.get((a, b))
        if prev and prev[0] == info[a][4] and prev[1] == info[b][4]:
            same = bool(prev[2])
        elif not check_new:
            unchecked += 1
            continue
        else:
            same = bool(info[a][4] == info[b][4] and info[a][4] and
                        sha.of(a) == sha.of(b) and sha.of(a) is not None)
            if not same and (info[a][7] or info[b][7] or info[a][1] is None or info[b][1] is None):
                new.append((a, b, info[a][4], info[b][4], 0))     # videos (and pictures without a hash):
                continue                                          # only exact copies count
            if not same:
                fa, fb = _fine(conn, info, a, cache), _fine(conn, info, b, cache)
                if not (fa and fb):
                    continue                  # couldn't read one of them: try again next time
                same = same_picture(fa, fb, memo)
            new.append((a, b, info[a][4], info[b][4], int(same)))
            if len(new) >= 500:
                conn.executemany("INSERT OR REPLACE INTO dup_checked(a, b, sa, sb, same) VALUES(?,?,?,?,?)", new)
                conn.commit()
                new = []
        if same:
            parent[find(b)] = find(a)
    if new:
        conn.executemany("INSERT OR REPLACE INTO dup_checked(a, b, sa, sb, same) VALUES(?,?,?,?,?)", new)
    if new or cache:
        conn.commit()
    DUP_UNCHECKED[0] = unchecked
    groups = {}
    for pid in set(parent) | set(parent.values()):
        groups.setdefault(find(pid), set()).add(pid)
    out = []
    for ids in groups.values():
        ids = sorted(set(ids))
        if len(ids) < 2:
            continue
        best = max(ids, key=lambda i: (prefer_raw and is_raw[i], (info[i][2] or 0) * (info[i][3] or 0),
                                       info[i][4] or 0, -(info[i][5] or 9e12), -len(info[i][6])))
        sizes = {info[i][4] for i in ids}
        exact = len(sizes) == 1 and len({sha.of(i) for i in ids}) == 1
        out.append({"ids": ids, "best": best, "exact": exact})
    sha.save()
    out.sort(key=lambda d: (-len(d["ids"]), info[d["best"]][6]))
    return out


def names_from_copies(conn):
    """Exact copies of a photo (same file contents - the copies Sort into folders makes for each
    person on a group photo) get the names you confirmed on the original, face by face. Fixes
    copies that were scanned before their names were carried over. Returns faces named."""
    groups = {}
    for pid, size, ph in conn.execute("SELECT id, size, phash FROM photos WHERE phash IS NOT NULL AND size>0 "
                                      "AND error IS NULL"):
        groups.setdefault((size, ph), []).append(pid)
    named = 0
    for ids in groups.values():
        if len(ids) < 2:
            continue
        q = ",".join(map(str, ids))
        conf = {}
        for fpid, x, y, w, h, t, person in conn.execute(
                f"""SELECT photo_id, x, y, w, h, frame_t, person_id FROM faces WHERE photo_id IN ({q})
                    AND confirmed=1 AND person_id IS NOT NULL AND ignored=0"""):
            conf.setdefault(fpid, []).append(((x, y, w, h), t, person))
        if not conf:
            continue
        todo = conn.execute(f"""SELECT id, photo_id, x, y, w, h, frame_t FROM faces WHERE photo_id IN ({q})
                                AND ignored=0 AND (confirmed=0 OR person_id IS NULL)""").fetchall()
        if not todo:
            continue
        paths = dict(conn.execute(f"SELECT id, path FROM photos WHERE id IN ({q})"))
        sums = {}

        def same(a, b):
            for i in (a, b):
                if i not in sums:
                    sums[i] = _file_sha1(paths[i])
            return sums[a] is not None and sums[a] == sums[b]
        for fid, fpid, x, y, w, h, t in todo:
            best, who = 0.5, None
            for src, faces in conf.items():
                if src == fpid:
                    continue
                for box, t2, person in faces:
                    if abs((t or 0) - (t2 or 0)) > 0.6:
                        continue
                    iou = _iou((x, y, w, h), box)
                    if iou > best and same(fpid, src):
                        best, who = iou, person
            if who is not None and not conn.execute("SELECT 1 FROM rejects WHERE face_id=? AND person_id=?",
                                                    (fid, who)).fetchone():
                conn.execute("UPDATE faces SET person_id=?, confirmed=1, cluster=NULL WHERE id=?", (who, fid))
                named += 1
    if named:
        conn.commit()
    return named


def not_duplicates(conn, photo_id, others):
    conn.executemany("INSERT OR IGNORE INTO dup_ignore(a, b) VALUES(?,?)",
                     [(min(photo_id, o), max(photo_id, o)) for o in others if o != photo_id])
    conn.commit()


# ----------------------------------------------------------------- turning photos
def rotate_photo(conn, photo_id, clockwise, mtime, size, phash=None):
    """The file was turned a quarter turn: turn everything FaceSort keeps about it the same
    way (face boxes and their thumbnails, tattoo boxes, the size), so names stay put."""
    import cv2
    from rotate import turn_box
    for table in ("faces", "tattoos", "tattoo_suggestions"):
        rows = conn.execute(f"SELECT rowid, x, y, w, h FROM {table} WHERE photo_id=?", (photo_id,)).fetchall()
        conn.executemany(f"UPDATE {table} SET x=?, y=?, w=?, h=? WHERE rowid=?",
                         [turn_box(x, y, w, h, clockwise) + (rid,) for rid, x, y, w, h in rows
                          if None not in (x, y, w, h)])
    code = cv2.ROTATE_90_CLOCKWISE if clockwise else cv2.ROTATE_90_COUNTERCLOCKWISE
    for fid, blob in conn.execute("SELECT id, thumb FROM faces WHERE photo_id=? AND thumb IS NOT NULL",
                                  (photo_id,)).fetchall():
        img = cv2.imdecode(np.frombuffer(blob, np.uint8), cv2.IMREAD_COLOR)
        if img is not None:
            ok, enc = cv2.imencode(".jpg", cv2.rotate(img, code), [cv2.IMWRITE_JPEG_QUALITY, 90])
            if ok:
                conn.execute("UPDATE faces SET thumb=? WHERE id=?", (enc.tobytes(), fid))
    conn.execute("""UPDATE photos SET width=height, height=width, mtime=?, size=?,
                    phash=COALESCE(?, phash) WHERE id=?""", (mtime, size, phash, photo_id))
    conn.execute("DELETE FROM dup_fine WHERE photo_id=?", (photo_id,))
    conn.execute("DELETE FROM dup_checked WHERE a=? OR b=?", (photo_id, photo_id))
    conn.commit()


# ----------------------------------------------------------------- other files that came with a drop
def add_drop_files(conn, batch, items):
    """items: [(path, kind, dropped folder it came in)]."""
    conn.executemany("INSERT OR REPLACE INTO drop_files(batch, path, kind, root) VALUES(?,?,?,?)",
                     [(batch, norm_path(p), k, norm_path(r) if r else None) for p, k, r in items])
    conn.commit()


def drop_files(conn, batch=None):
    q = "SELECT id, batch, path, kind, root FROM drop_files"
    rows = conn.execute(q + (" WHERE batch=?" if batch is not None else "") + " ORDER BY kind, path",
                        (() if batch is None else (batch,))).fetchall()
    return rows


def forget_drop_files(conn, ids):
    conn.executemany("DELETE FROM drop_files WHERE id=?", [(i,) for i in ids])
    conn.commit()


# ----------------------------------------------------------------- Vaults
# A vault is a top-level folder whose photos, people and folders disappear from the whole app
# while it's locked (the files stay where they are on disk). It can have a password, and its
# own place on disk that Sort into folders puts its photos in.
VAULT_ITER = 240_000


def hash_password(pw):
    import secrets
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, VAULT_ITER)
    return f"pbkdf2${VAULT_ITER}${salt.hex()}${dk.hex()}"


def check_password(stored, pw):
    import hmac
    try:
        _, it, salt, dk = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(got.hex(), dk)
    except Exception:
        return False


def is_main_vault(conn, vid):
    r = conn.execute("SELECT vault FROM categories WHERE id=?", (vid,)).fetchone()
    return bool(r and r[0] == 2)


def vaults(conn):
    """[(id, name, locked, has password, own path)]"""
    return [(i, n, bool(lk), bool(pw), pth) for i, n, lk, pw, pth in conn.execute(
        "SELECT id, name, vault_locked, vault_pw, vault_path FROM categories WHERE vault>0 ORDER BY name COLLATE NOCASE")]


def ensure_vault(conn):
    """There's always at least one vault ("Vault")."""
    if not conn.execute("SELECT 1 FROM categories WHERE vault>0").fetchone():
        have = conn.execute("""SELECT id FROM categories WHERE name='Vault' COLLATE NOCASE AND parent_id IS NULL
                               AND owner_id IS NULL""").fetchone()
        vid = have[0] if have else create_vault(conn, "Vault")     # a folder you'd already called Vault becomes it
        conn.execute("UPDATE categories SET vault=2 WHERE id=?", (vid,))   # the main vault: always has a password
        conn.commit()


def create_vault(conn, name, password=None):
    name = " ".join(name.split()) or "Vault"
    taken = {r[0].casefold() for r in conn.execute("SELECT name FROM categories WHERE parent_id IS NULL AND owner_id IS NULL")}
    base, n = name, 2
    while name.casefold() in taken:
        name = f"{base} ({n})"
        n += 1
    cid = conn.execute("INSERT INTO categories(name, parent_id, vault, vault_pw, vault_locked) VALUES(?,?,1,?,0)",
                       (name, None, hash_password(password) if password else None)).lastrowid
    conn.commit()
    return cid


def set_vault_password(conn, vid, password):
    conn.execute("UPDATE categories SET vault_pw=? WHERE id=? AND vault>0",
                 (hash_password(password) if password else None, vid))
    conn.commit()


def vault_password_ok(conn, vid, password):
    r = conn.execute("SELECT vault_pw FROM categories WHERE id=?", (vid,)).fetchone()
    return not (r and r[0]) or check_password(r[0], password or "")


def set_vault_path(conn, vid, path):
    conn.execute("UPDATE categories SET vault_path=? WHERE id=? AND vault>0", (norm_path(path) if path else None, vid))
    conn.commit()


def vault_of(conn, cid, tree=None):
    """The vault a folder is in (its top folder, if that's a vault), else None."""
    if cid is None:
        return None
    vault_ids = {r[0] for r in conn.execute("SELECT id FROM categories WHERE vault>0")}
    parents = dict(conn.execute("SELECT id, parent_id FROM categories"))
    seen = set()
    while cid is not None and cid not in seen:
        if cid in vault_ids:
            return cid
        seen.add(cid)
        cid = parents.get(cid)
    return None


def vault_members(conn, vid):
    """(folder ids, person ids, photo ids) inside a vault: its folders, the people placed in
    them (and those people's own folders), and every photo filed there or showing one of them."""
    folders = {vid} | category_descendants(conn, vid)
    people = {r[0] for r in conn.execute(
        f"SELECT id FROM persons WHERE category_id IN ({','.join(map(str, folders))})")}
    if people:
        owned = {r[0] for r in conn.execute(
            f"SELECT id FROM categories WHERE owner_id IN ({','.join(map(str, people))})")}
        for o in list(owned):
            owned |= category_descendants(conn, o)
        folders |= owned
    fq = ",".join(map(str, folders))
    photos = {r[0] for r in conn.execute(f"SELECT id FROM photos WHERE category_id IN ({fq})")}
    photos |= {r[0] for r in conn.execute(f"SELECT photo_id FROM photo_folders WHERE folder_id IN ({fq})")}
    if people:
        pq = ",".join(map(str, people))
        photos |= {r[0] for r in conn.execute(f"""SELECT DISTINCT photo_id FROM faces WHERE person_id IN ({pq})
                                                 AND confirmed=1 AND (ignored IN (0, 5, 6))""")}
        photos |= {r[0] for r in conn.execute(f"SELECT photo_id FROM photo_people WHERE person_id IN ({pq})")}
    return folders, people, photos


def locked_vault_contents(conn):
    """(folders, people, photos) of every locked vault - what the app doesn't show."""
    F, P, Ph = set(), set(), set()
    for vid, _n, locked, _pw, _p in vaults(conn):
        if locked:
            f, p, ph = vault_members(conn, vid)
            F |= f
            P |= p
            Ph |= ph
    return F, P, Ph


def _hide_faces_for_vault(conn, photo_ids):
    for i in range(0, len(photo_ids), 900):
        chunk = list(photo_ids)[i:i + 900]
        q = ",".join("?" * len(chunk))
        conn.execute(f"UPDATE faces SET vault_prev=ignored, ignored=6, cluster=NULL WHERE photo_id IN ({q}) "
                     f"AND ignored<>6", chunk)


def apply_vault_hiding(conn):
    """Hide what's in locked vaults, show again what's in unlocked ones (exactly as it was)."""
    _, _, hide = locked_vault_contents(conn)
    hidden = {r[0] for r in conn.execute("SELECT id FROM photos WHERE sort_later=2")}
    show = sorted(hidden - hide)
    new = sorted(hide - hidden)
    for i in range(0, len(show), 900):
        chunk = show[i:i + 900]
        q = ",".join("?" * len(chunk))
        conn.execute(f"UPDATE faces SET ignored=COALESCE(vault_prev, 0), vault_prev=NULL WHERE photo_id IN ({q}) "
                     f"AND ignored=6", chunk)
        conn.execute(f"UPDATE photos SET sort_later=COALESCE(vault_prev, 0), vault_prev=NULL WHERE id IN ({q})", chunk)
    for i in range(0, len(new), 900):
        chunk = new[i:i + 900]
        q = ",".join("?" * len(chunk))
        conn.execute(f"UPDATE photos SET vault_prev=sort_later, sort_later=2 WHERE id IN ({q}) AND sort_later<>2", chunk)
    if new:
        _hide_faces_for_vault(conn, new)
    conn.commit()
    return len(new), len(show)


def lock_vault(conn, vid, locked=True):
    conn.execute("UPDATE categories SET vault_locked=? WHERE id=? AND vault>0", (int(bool(locked)), vid))
    conn.commit()
    return apply_vault_hiding(conn)


def hidden_person_names(conn):
    """Names of people in locked vaults (never suggested or offered while locked)."""
    _, people, _ = locked_vault_contents(conn)
    if not people:
        return set()
    return {r[0].casefold() for r in conn.execute(
        f"SELECT name FROM persons WHERE id IN ({','.join(map(str, people))})")}


def vault_root_paths(conn):
    """{vault id: its own place on disk} for vaults that have one."""
    return {i: p for i, _n, _l, _pw, p in vaults(conn) if p}
