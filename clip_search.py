"""Search photos by description ("beach at sunset", "red dress", "dog"), entirely on this PC.

A small CLIP model (OpenAI's ViT-B/32, as ONNX, about 150 MB) turns every photo into a list
of numbers that describes what's in it, and a few words of text into the same kind of list;
photos whose numbers point the same way as the words' match. The model is downloaded once,
the first time you search; after that nothing leaves the computer.

Tokenizer: OpenAI CLIP's byte-pair encoding (MIT licence), vocabulary file shipped in models/."""
import gzip
import html
import json
import os
import re

import numpy as np

BASE = "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/onnx/"
FILES = {"vision": ("vision_model_quantized.onnx", 88), "text": ("text_model_quantized.onnx", 64)}
DIM = 512
SOT, EOT, CTX = 49406, 49407, 77
_MEAN = np.array((0.48145466, 0.4578275, 0.40821073), np.float32)
_STD = np.array((0.26862954, 0.26130258, 0.27577711), np.float32)


def model_dir():
    import store
    return os.path.join(store.data_dir(), "models", "clip")


def model_path(kind):
    return os.path.join(model_dir(), FILES[kind][0])


def available():
    return all(os.path.exists(model_path(k)) for k in FILES)


def download(progress=lambda d, t, m: None, cancelled=lambda: False):
    """Get the two model files (about 150 MB, once). False if cancelled."""
    import urllib.request
    os.makedirs(model_dir(), exist_ok=True)
    total_mb = sum(mb for _, mb in FILES.values())
    done_mb = 0
    for kind, (name, mb) in FILES.items():
        dest = model_path(kind)
        if os.path.exists(dest):
            done_mb += mb
            continue
        tmp = dest + ".part"
        req = urllib.request.Request(BASE + name, headers={"User-Agent": "FaceSort"})
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            got = 0
            while True:
                if cancelled():
                    f.close()
                    os.remove(tmp)
                    return False
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                progress(done_mb + got // (1 << 20), total_mb,
                         f"Downloading the search model… {(done_mb << 20) / 1e6 + got / 1e6:.0f} of ~{total_mb} MB")
        if os.path.getsize(tmp) < 1 << 20:
            os.remove(tmp)
            raise RuntimeError("the search model didn't download properly")
        os.replace(tmp, dest)
        done_mb += mb
    return True


# ------------------------------------------------------------------ CLIP's tokenizer
def _bytes_to_unicode():
    bs = list(range(ord("!"), ord("~") + 1)) + list(range(ord("¡"), ord("¬") + 1)) + list(range(ord("®"), ord("ÿ") + 1))
    cs = bs[:]
    n = 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    return dict(zip(bs, [chr(c) for c in cs]))


class Tokenizer:
    PAT = re.compile(r"<\|startoftext\|>|<\|endoftext\|>|'s|'t|'re|'ve|'m|'ll|'d|[^\W\d_]+|\d|[^\s\w]+|_+",
                     re.IGNORECASE)

    def __init__(self, path=None):
        if path is None:
            import engine
            path = engine.resource_path(os.path.join("models", "bpe_simple_vocab_16e6.txt.gz"))
        merges = gzip.open(path).read().decode("utf-8").split("\n")[1:49152 - 256 - 2 + 1]
        merges = [tuple(m.split()) for m in merges]
        self.byte_encoder = _bytes_to_unicode()
        vocab = list(self.byte_encoder.values())
        vocab += [v + "</w>" for v in vocab]
        vocab += ["".join(m) for m in merges]
        vocab += ["<|startoftext|>", "<|endoftext|>"]
        self.encoder = {v: i for i, v in enumerate(vocab)}
        self.ranks = {m: i for i, m in enumerate(merges)}
        self.cache = {}

    def bpe(self, token):
        if token in self.cache:
            return self.cache[token]
        word = tuple(token[:-1]) + (token[-1] + "</w>",)
        while len(word) > 1:
            pairs = {(word[i], word[i + 1]) for i in range(len(word) - 1)}
            best = min(pairs, key=lambda p: self.ranks.get(p, 1 << 30))
            if best not in self.ranks:
                break
            a, b = best
            out, i = [], 0
            while i < len(word):
                if i < len(word) - 1 and word[i] == a and word[i + 1] == b:
                    out.append(a + b)
                    i += 2
                else:
                    out.append(word[i])
                    i += 1
            word = tuple(out)
        self.cache[token] = word
        return word

    def encode(self, text):
        text = html.unescape(html.unescape(text))
        text = re.sub(r"\s+", " ", text).strip().lower()
        ids = []
        for tok in self.PAT.findall(text):
            tok = "".join(self.byte_encoder[b] for b in tok.encode("utf-8"))
            ids += [self.encoder[t] for t in self.bpe(tok)]
        return ids

    def __call__(self, text):
        ids = [SOT] + self.encode(text)[:CTX - 2] + [EOT]
        return ids + [EOT] * (CTX - len(ids))        # padded the way the model was exported


# ------------------------------------------------------------------ the model
class Clip:
    def __init__(self):
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.log_severity_level = 3
        so.intra_op_num_threads = max(1, (os.cpu_count() or 2) // 2)    # leaves the rest of the PC responsive
        self.vision = ort.InferenceSession(model_path("vision"), so, providers=["CPUExecutionProvider"])
        self.text = None
        self.tok = None
        self._so = so

    @staticmethod
    def _pick(outputs, names, want):
        for n, o in zip(names, outputs):
            if n == want:
                return o
        return next(o for o in outputs if np.asarray(o).ndim == 2)

    @staticmethod
    def prepare(rgb):
        """224 x 224, the short side fitted and the middle taken (how CLIP was trained)."""
        import cv2
        h, w = rgb.shape[:2]
        k = 224 / min(h, w)
        img = cv2.resize(rgb, (max(224, round(w * k)), max(224, round(h * k))),
                         interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC)
        h, w = img.shape[:2]
        y0, x0 = (h - 224) // 2, (w - 224) // 2
        img = img[y0:y0 + 224, x0:x0 + 224].astype(np.float32) / 255.0
        return ((img - _MEAN) / _STD).transpose(2, 0, 1)

    def images(self, rgbs):
        """[RGB arrays] -> (n, 512) unit vectors."""
        x = np.stack([self.prepare(r) for r in rgbs]).astype(np.float32)
        names = [o.name for o in self.vision.get_outputs()]
        inp = self.vision.get_inputs()[0].name
        out = self.vision.run(None, {inp: x})
        e = np.asarray(self._pick(out, names, "image_embeds"), np.float32)
        return e / np.maximum(np.linalg.norm(e, axis=1, keepdims=True), 1e-6)

    def words(self, text):
        """'beach at sunset' -> a 512 unit vector."""
        if self.text is None:
            import onnxruntime as ort
            self.text = ort.InferenceSession(model_path("text"), self._so, providers=["CPUExecutionProvider"])
            self.tok = Tokenizer()
        ids = np.array([self.tok(text)], np.int64)
        feeds = {}
        for i in self.text.get_inputs():
            if "mask" in i.name:
                feeds[i.name] = (ids != EOT).astype(np.int64) | (np.arange(CTX)[None] <= int(np.argmax(ids == EOT)))
            else:
                feeds[i.name] = ids
        names = [o.name for o in self.text.get_outputs()]
        e = np.asarray(self._pick(self.text.run(None, feeds), names, "text_embeds"), np.float32).reshape(-1)
        return e / max(float(np.linalg.norm(e)), 1e-6)


_CLIP = [None]


def model():
    if _CLIP[0] is None:
        _CLIP[0] = Clip()
    return _CLIP[0]


# ------------------------------------------------------------------ the index (in the library)
def index_photos(conn, progress=lambda d, t, m: None, cancelled=lambda: False, batch=8):
    """Work out the description numbers for every photo (and video) that doesn't have them yet.
    Saved as it goes, so it carries on where it left off. Returns how many were done."""
    import engine
    import time as _t
    todo = conn.execute("""SELECT ph.id, ph.path, ph.is_video, ph.cover_t FROM photos ph
                           LEFT JOIN clip_emb e ON e.photo_id=ph.id
                           WHERE e.photo_id IS NULL AND ph.error IS NULL AND COALESCE(ph.is_other, 0)=0
                           ORDER BY ph.id""").fetchall()
    if not todo:
        return 0
    m = model()
    done, last = 0, _t.monotonic()
    for i in range(0, len(todo), batch):
        if cancelled():
            break
        chunk = todo[i:i + batch]
        pics, ids, failed = [], [], []
        for pid, path, is_video, cover in chunk:
            try:
                li = engine.load_image(path, max_side=448, t=cover if is_video else None)
                pics.append(np.ascontiguousarray(li.bgr[:, :, ::-1]))
                ids.append(pid)
            except Exception:
                failed.append(pid)                     # (marked below, so it isn't tried again)
        vecs = m.images(pics).astype(np.float16) if pics else []
        # all the slow work (loading, the model) is done with the library untouched; then one
        # quick write, saved at once. (A file that couldn't be read used to be written down
        # straight away - which kept the library locked while the rest of the batch loaded and
        # the model ran, and on a library with many such files tagging got "database is locked".)
        rows = [(pid, v.tobytes()) for pid, v in zip(ids, vecs)] + [(pid, None) for pid in failed]
        if rows:
            conn.executemany("INSERT OR REPLACE INTO clip_emb(photo_id, vec) VALUES(?,?)", rows)
            conn.commit()
        done += len(chunk)
        last = _t.monotonic()
        progress(done, len(todo), f"Making photos searchable by description… {done:,} / {len(todo):,}")
    conn.commit()
    return done


def index_counts(conn):
    total = conn.execute("SELECT COUNT(*) FROM photos WHERE error IS NULL AND COALESCE(is_other, 0)=0").fetchone()[0]
    have = conn.execute("""SELECT COUNT(*) FROM clip_emb e JOIN photos ph ON ph.id=e.photo_id
                           WHERE ph.error IS NULL""").fetchone()[0]
    return have, total


def search(conn, text, limit=500):
    """[(photo id, score)] best first, for a description. Only photos that fit noticeably better
    than the rest are kept (CLIP always ranks everything, so the tail would be noise)."""
    q = model().words(text)
    rows = conn.execute("""SELECT e.photo_id, e.vec FROM clip_emb e JOIN photos ph ON ph.id=e.photo_id
                           WHERE e.vec IS NOT NULL AND ph.error IS NULL AND COALESCE(ph.sort_later, 0) != 2""").fetchall()
    if not rows:
        return []
    ids = np.array([r[0] for r in rows])
    mat = np.frombuffer(b"".join(r[1] for r in rows), np.float16).reshape(len(rows), DIM).astype(np.float32)
    sims = mat @ q
    order = np.argsort(-sims)
    best = float(sims[order[0]])
    mean, sd = float(sims.mean()), float(sims.std()) + 1e-6
    # keep: clearly above the library's usual score, and not far below the best match
    keep = [i for i in order[:limit] if sims[i] >= max(mean + 1.5 * sd, best - 0.06, 0.18)]
    if not keep:
        keep = list(order[:min(24, len(order))])
    return [(int(ids[i]), float(sims[i])) for i in keep]
