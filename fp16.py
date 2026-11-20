"""FP16 models for the graphics card (1.45.2).

RTX cards (and other recent graphics cards) do half-precision (FP16) maths on their tensor
cores several times faster than full precision, and an FP16 model is half the size to move
around. FaceSort's models come in full precision, so for the graphics card each one is
converted to FP16 once (kept in data/models/fp16) - the inputs and outputs stay as they were,
so nothing else changes.

Converting can change the results very slightly, so every model is checked before it's used:
the first time it runs, FaceSort runs the real input through both versions and compares. Only
if the FP16 answers match closely (a face fingerprint pointing the same way to 0.999 or better,
everything else within 2% of its range) is the FP16 version used from then on; otherwise the
model stays in full precision. The verdict is remembered (verdicts.json), per model and
graphics card setup, so the check happens once.

Only for the graphics card: on the CPU, FP16 is no quicker. Settings > Scanning / CPU / GPU:
"Faster FP16 models on the graphics card" (on by default) turns it off."""
import json
import os
import threading
import warnings

import numpy as np

COS_MIN = 0.999          # a fingerprint (embedding) must point the same way to at least this
REL_MAX = 0.02           # anything else: the biggest difference within 2% of the output's range
VERDICTS = "verdicts.json"
_lock = threading.Lock()


def _settings_on():
    if os.environ.get("FACESORT_NO_FP16") == "1":
        return False                      # (1.51.1) this job crashed the graphics card helper once
    try:
        import store
        return bool(store.load_settings().get("fp16", True))
    except Exception:
        return True


def folder():
    import store
    d = os.path.join(store.data_dir(), "models", "fp16")
    os.makedirs(d, exist_ok=True)
    return d


def _key(src):
    st = os.stat(src)
    return f"{os.path.splitext(os.path.basename(src))[0]}-{st.st_size:x}-{int(st.st_mtime):x}"


def fp16_path(src):
    return os.path.join(folder(), _key(src) + ".onnx")


def _verdicts():
    try:
        with open(os.path.join(folder(), VERDICTS), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_verdict(key, value):
    with _lock:
        v = _verdicts()
        v[key] = value
        tmp = os.path.join(folder(), VERDICTS + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(v, f, indent=1)
        os.replace(tmp, os.path.join(folder(), VERDICTS))


def convert(src, dst=None):
    """Write the FP16 version of a model (inputs and outputs stay full precision)."""
    import onnx
    from onnxconverter_common import float16
    dst = dst or fp16_path(src)
    if os.path.exists(dst):
        return dst
    m = onnx.load(src)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")              # ("number truncated to 1e-07" for every tiny weight)
        m16 = float16.convert_float_to_float16(m, keep_io_types=True)
    tmp = dst + ".tmp"
    onnx.save(m16, tmp)
    os.replace(tmp, dst)
    return dst


def compare(a_outs, b_outs, names=()):
    """(same enough, detail): FP16 outputs b against full-precision a."""
    worst = []
    for i, (a, b) in enumerate(zip(a_outs, b_outs)):
        a, b = np.asarray(a), np.asarray(b)
        if a.shape != b.shape:
            return False, f"output {i}: shape {b.shape} instead of {a.shape}"
        if not np.issubdtype(a.dtype, np.floating):
            if not np.array_equal(a, b):
                return False, f"output {i}: different"
            continue
        a64, b64 = a.astype(np.float64), b.astype(np.float64)
        if not np.all(np.isfinite(b64)):
            return False, f"output {i}: not a number in FP16"
        name = names[i] if i < len(names) else str(i)
        # a fingerprint (rows of 128 / 512 numbers): compare where it points
        if a64.ndim == 2 and a64.shape[1] >= 64:
            na = np.linalg.norm(a64, axis=1)
            nb = np.linalg.norm(b64, axis=1)
            cos = (a64 * b64).sum(1) / np.maximum(na * nb, 1e-12)
            low = float(cos.min()) if len(cos) else 1.0
            worst.append(f"{name}: cos {low:.5f}")
            if low < COS_MIN:
                return False, "; ".join(worst)
            continue
        rng = float(a64.max() - a64.min()) if a64.size else 0.0
        diff = float(np.abs(a64 - b64).max()) if a64.size else 0.0
        rel = diff / max(rng, 1e-6)
        worst.append(f"{name}: {rel:.4f}")
        if rel > REL_MAX:
            return False, "; ".join(worst)
    return True, "; ".join(worst)


class Session:
    """An ONNX Runtime session that switches itself to the FP16 model once it has checked, on the
    first real input, that the answers match (see the top of this file). Looks like an
    InferenceSession to the code that uses it."""

    def __init__(self, src, so, providers, label="", force=False):
        import onnxruntime as ort
        self.src, self.so, self.providers = src, so, providers
        self.label = label or os.path.basename(src)
        self.s32 = ort.InferenceSession(src, so, providers=providers)
        self.s16 = None
        self.cur = self.s32
        self.state = "full"              # full / checking / fp16
        self.detail = ""
        self.lock = threading.Lock()
        prov = self.s32.get_providers()[0]
        self.key = f"{_key(src)}|{prov}"
        if (prov == "CPUExecutionProvider" and not force) or not _settings_on():
            self.state = "cpu"           # (no FP16 on the CPU - it isn't quicker there)
            return
        v = _verdicts().get(self.key)
        if v and v.get("ok"):
            try:
                self._load16()
                self.cur, self.state, self.detail = self.s16, "fp16", v.get("detail", "")
                self.s32 = None           # (half the memory)
            except Exception as e:
                self.state, self.detail = "full", f"FP16 model didn't load: {e}"
        elif v is not None:
            self.state, self.detail = "full", v.get("detail", "")
        else:
            self.state = "checking"

    def _load16(self):
        import onnxruntime as ort
        p = convert(self.src)
        self.s16 = ort.InferenceSession(p, self.so, providers=self.providers)
        if self.s16.get_providers()[0] != self.s32_provider():
            raise RuntimeError("the graphics card can't run the FP16 model")

    def s32_provider(self):
        return self.providers[0] if isinstance(self.providers[0], str) else self.providers[0][0]

    def run(self, output_names, feed, run_options=None):
        if self.state == "checking":
            with self.lock:
                if self.state == "checking":
                    return self._check(output_names, feed, run_options)
        return self.cur.run(output_names, feed, run_options)

    def _check(self, output_names, feed, run_options):
        a = self.s32.run(output_names, feed, run_options)
        try:
            self._load16()
            b = self.s16.run(output_names, feed, run_options)
            ok, detail = compare(a, b, [o.name for o in self.s32.get_outputs()])
        except Exception as e:
            ok, detail = False, f"FP16 didn't work: {e}"
        self.detail = detail
        try:
            _save_verdict(self.key, {"ok": ok, "detail": detail})
        except Exception:
            pass
        _log(f"FP16 check {self.label}: {'using FP16' if ok else 'staying full precision'} ({detail})")
        if ok:
            self.cur, self.state, self.s32 = self.s16, "fp16", None
        else:
            self.s16, self.state = None, "full"
            try:
                os.remove(fp16_path(self.src))          # (not used: don't keep it)
            except OSError:
                pass
        return a                         # (this time: the full-precision answer)

    def get_inputs(self):
        return self.cur.get_inputs()

    def get_outputs(self):
        return self.cur.get_outputs()

    def get_providers(self):
        return self.cur.get_providers()


def _log(msg):
    try:
        import time
        import store
        with open(os.path.join(store.data_dir(), "facesort.log"), "a", encoding="utf-8") as f:
            import cryptbox
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + cryptbox.scrub(msg) + "\n")
    except Exception:
        pass


def session(src, so, providers, label=""):
    """An InferenceSession - or, on the graphics card, one that uses FP16 once it's checked. Falls
    back to a plain session if anything about FP16 goes wrong."""
    import onnxruntime as ort
    first = providers[0] if isinstance(providers[0], str) else providers[0][0]
    if first == "CPUExecutionProvider" or not _settings_on():
        return ort.InferenceSession(src, so, providers=providers)
    try:
        import onnx  # noqa: F401
        import onnxconverter_common  # noqa: F401
    except Exception:
        return ort.InferenceSession(src, so, providers=providers)
    try:
        return Session(src, so, providers, label)
    except Exception:
        return ort.InferenceSession(src, so, providers=providers)


def status():
    """[(model, verdict)] for Settings."""
    out = []
    for k, v in sorted(_verdicts().items()):
        name, _, prov = k.partition("|")
        out.append((name.rsplit("-", 2)[0], prov.replace("ExecutionProvider", ""), bool(v.get("ok")), v.get("detail", "")))
    return out

