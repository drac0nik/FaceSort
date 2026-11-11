"""Keeps FaceSort's background work (updating groups and suggestions, same-shoot matching) from
taking over the PC. Must be imported before numpy / OpenCV / onnxruntime are, because the maths
libraries read their thread counts once, when they load."""
import json
import os
import sys

APP_NAME = "FaceSort"
LIB_VARS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS",
            "VECLIB_MAXIMUM_THREADS")


def _settings_path():
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(os.environ.get("FACESORT_DATA") or os.path.join(base, APP_NAME), "settings.json")


def mode():
    """"gentle" (default) or "full" - from settings.json, read without loading anything heavy."""
    try:
        with open(_settings_path(), encoding="utf-8") as f:
            m = json.load(f).get("background_cpu", "gentle")
    except Exception:
        m = "gentle"
    return m if m in ("gentle", "full") else "gentle"


def threads_for(m, cores=None):
    cores = cores or os.cpu_count() or 2
    return max(1, cores // 2) if m == "gentle" else cores


def apply(m=None):
    """Cap the maths libraries' threads (only if the user hasn't set them themselves)."""
    m = m or mode()
    n = threads_for(m)
    for v in LIB_VARS:
        os.environ.setdefault(v, str(n))
    return n


APPLIED = None
