"""Is this image made (or edited) with AI?

1. Labels inside the file (reliable when present): Stable Diffusion / ComfyUI / InvokeAI /
   Fooocus / NovelAI generation settings, the IPTC "trained algorithmic media" source type
   (Google, Meta, Midjourney, stock sites), C2PA Content Credentials (Adobe Firefly, OpenAI,
   Microsoft...), and generator names in the file's details. Only the start and end of the
   file are read, so this is quick.
2. An optional detector model that judges the pixels (off by default; often wrong - it only
   makes a "possibly AI" list to look at).
"""
import os
import re

HEAD = 384 * 1024      # metadata lives near the start of the file...
TAIL = 128 * 1024      # ...or, in some PNGs, near the end

# (bytes to look for, reason shown) - first match wins, most specific first
GENERATOR_MARKS = [
    (rb"compositeWithTrainedAlgorithmicMedia", "AI-edited (marked in the file)"),
    (rb"trainedAlgorithmicMedia", "AI-generated (marked in the file)"),
    (rb"algorithmicMedia", "AI-generated (marked in the file)"),
    (rb'"class_type"', "ComfyUI - generation settings saved in the file"),
    (rb"ComfyUI", "ComfyUI"),
    (rb"sd-metadata", "InvokeAI - generation settings saved in the file"),
    (rb"invokeai_metadata", "InvokeAI - generation settings saved in the file"),
    (rb"Fooocus", "Fooocus"),
    (rb"NovelAI", "NovelAI"),
    (rb"Midjourney", "Midjourney"),
    (rb"midjourney", "Midjourney"),
    (rb"DALL\xc2\xb7E", "DALL-E (OpenAI)"),
    (rb"DALL-E", "DALL-E (OpenAI)"),
    (rb"Adobe Firefly", "Adobe Firefly"),
    (rb"Firefly", "Adobe Firefly"),
    (rb"Stable Diffusion", "Stable Diffusion"),
    (rb"StableDiffusion", "Stable Diffusion"),
    (rb"stable-diffusion", "Stable Diffusion"),
    (rb"DreamStudio", "DreamStudio (Stable Diffusion)"),
    (rb"Leonardo.Ai", "Leonardo.Ai"),
    (rb"Ideogram", "Ideogram"),
    (rb"Bing Image Creator", "Bing Image Creator"),
    (rb"Microsoft Designer", "Microsoft Designer"),
    (rb"Image Creator from Microsoft", "Bing Image Creator"),
    (rb"Craiyon", "Craiyon"),
    (rb"Google AI", "Google AI"),
    (rb"Imagen", "Google Imagen"),
    (rb"Gemini", "Google Gemini"),
    (rb"OpenAI", "OpenAI"),
    (rb"ChatGPT", "ChatGPT"),
    (rb"Flux.1", "FLUX"),
    (rb"FLUX.1", "FLUX"),
]
# Automatic1111 / Forge / SD.Next write a "parameters" text chunk like "... Steps: 30, Sampler: ..."
A1111 = re.compile(rb"parameters.{0,4096}?Steps: \d+.{0,200}?Sampler", re.S)
PROMPT_CHUNK = re.compile(rb"(tEXt|iTXt)(prompt|workflow)\x00", re.S)
C2PA = re.compile(rb"c2pa", re.I)


def read_ends(path):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(HEAD)
            tail = b""
            if size > HEAD + TAIL:
                f.seek(size - TAIL)
                tail = f.read(TAIL)
        return head + tail
    except OSError:
        return b""


def label_check(path):
    """(1, reason) when the file says it was made / edited with AI, else (0, None)."""
    ext = os.path.splitext(path)[1].lower()
    import engine
    if ext in engine.VIDEO_EXTS:
        data = read_ends(path)
        if b"trainedAlgorithmicMedia" in data:
            return 1, "AI-generated (marked in the file)"
        return 0, None
    data = read_ends(path)
    if not data:
        return 0, None
    if A1111.search(data):
        return 1, "Stable Diffusion - generation settings saved in the file"
    if PROMPT_CHUNK.search(data):
        return 1, "ComfyUI - generation settings saved in the file"
    for pat, reason in GENERATOR_MARKS:
        if pat in data:
            # generic words only count inside metadata, not by chance in pixel data: require them
            # to sit in a text-like stretch
            if len(pat) < 9:
                i = data.find(pat)
                ctx = data[max(0, i - 16):i + len(pat) + 16]
                if sum(32 <= b < 127 or b == 0 for b in ctx) < 0.6 * len(ctx):
                    continue
            if C2PA.search(data) and reason not in ("AI-edited (marked in the file)",):
                return 1, f"{reason} (Content Credentials)" if "marked" not in reason else reason
            return 1, reason
    return 0, None


# ---------------------------------------------------------------- optional detector model
DETECTOR_URL = ("https://huggingface.co/onnx-community/CommunityForensics-DeepfakeDet-ViT-ONNX/resolve/main/"
                "onnx/model_int8.onnx")
DETECTOR_FILE = "ai_detector_int8.onnx"
DETECTOR_MB = 37
POSSIBLY_AI = 0.90          # only quite sure guesses go on the "Possibly AI" list
_MEAN = (0.48145466, 0.4578275, 0.40821073)
_STD = (0.26862954, 0.26130258, 0.27577711)


def detector_path():
    import store
    return os.path.join(store.data_dir(), "models", DETECTOR_FILE)


def detector_available():
    return os.path.exists(detector_path())


def download_detector(progress=lambda d, t, m: None, cancelled=lambda: False):
    import urllib.request
    dest = detector_path()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    req = urllib.request.Request(DETECTOR_URL, headers={"User-Agent": "FaceSort"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or DETECTOR_MB * 1024 * 1024)
        done = 0
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
            progress(done // (1 << 20), total // (1 << 20), f"Downloading the AI detector… {done / 1e6:.0f} MB")
    os.replace(tmp, dest)
    return True


class Detector:
    """CommunityForensics ViT (MIT licence), trained on 2.7M images from 4,600+ generators.
    Gives a score 0..1 (1 = looks AI-generated)."""

    def __init__(self):
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.log_severity_level = 3
        self.s = ort.InferenceSession(detector_path(), so, providers=["CPUExecutionProvider"])
        inp = self.s.get_inputs()[0]
        self.name = inp.name
        shp = inp.shape
        self.side = shp[-1] if isinstance(shp[-1], int) else 384

    def score(self, bgr):
        import cv2
        import numpy as np
        h, w = bgr.shape[:2]
        # resize the short side to 440 and take the middle 384 x 384 (how the model was trained)
        k = 440 / min(h, w)
        img = cv2.resize(bgr, (max(self.side, round(w * k)), max(self.side, round(h * k))), interpolation=cv2.INTER_CUBIC)
        h, w = img.shape[:2]
        y0, x0 = (h - self.side) // 2, (w - self.side) // 2
        img = img[y0:y0 + self.side, x0:x0 + self.side, ::-1].astype(np.float32) / 255.0
        img = (img - np.array(_MEAN, np.float32)) / np.array(_STD, np.float32)
        x = img.transpose(2, 0, 1)[None]
        out = np.asarray(self.s.run(None, {self.name: x})[0]).reshape(-1)
        if out.size == 1:
            return float(1 / (1 + np.exp(-out[0])))
        e = np.exp(out - out.max())
        p = e / e.sum()
        return float(p[-1])
