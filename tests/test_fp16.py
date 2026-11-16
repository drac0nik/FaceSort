"""FP16 models (1.45.2): converted once, checked against the full-precision model on the first
real input, used only if the answers match; the verdict is remembered; anything that goes wrong
leaves the model in full precision."""
import os

import numpy as np
import onnxruntime as ort
import pytest

import engine
import fp16

pytest.importorskip("onnxconverter_common")
SO = ort.SessionOptions()
SO.log_severity_level = 3
CPU = ["CPUExecutionProvider"]


def test_compare():
    a = [np.random.default_rng(1).normal(size=(4, 128)).astype(np.float32)]
    assert fp16.compare(a, [a[0] + 1e-4])[0]
    assert not fp16.compare(a, [np.random.default_rng(2).normal(size=(4, 128)).astype(np.float32)])[0]
    s = [np.linspace(0, 1, 50, dtype=np.float32).reshape(1, 50)]
    assert fp16.compare(s, [s[0] + 0.005])[0] and not fp16.compare(s, [s[0] + 0.1])[0]
    assert not fp16.compare(s, [np.full_like(s[0], np.nan)])[0]


def test_detector_switches_to_fp16_after_checking(data_dir):
    rng = np.random.default_rng(3)
    feed = {"input": (rng.random((1, 3, 320, 320)) * 255).astype(np.float32)}
    s = fp16.Session(engine.DET_MODEL, SO, CPU, "face finder", force=True)
    assert s.state == "checking"
    first = s.run(None, feed)                                # checked on this input (full precision answer)
    assert s.state == "fp16" and s.s32 is None and os.path.exists(fp16.fp16_path(engine.DET_MODEL))
    second = s.run(None, feed)
    assert fp16.compare(first, second)[0]
    again = fp16.Session(engine.DET_MODEL, SO, CPU, force=True)   # remembered: FP16 at once, no check
    assert again.state == "fp16"
    assert any(v[2] for v in fp16.status())


def test_mismatch_stays_full_precision(data_dir, monkeypatch):
    monkeypatch.setattr(fp16, "COS_MIN", 0.9999999)          # (stricter than FP16 can be)
    rng = np.random.default_rng(4)
    s = fp16.Session(engine.REC_MODEL, SO, CPU, force=True)
    s.run(None, {"data": (rng.random((4, 3, 112, 112)) * 255).astype(np.float32)})
    assert s.state == "full" and s.s16 is None
    assert not os.path.exists(fp16.fp16_path(engine.REC_MODEL))
    assert fp16.Session(engine.REC_MODEL, SO, CPU, force=True).state == "full"     # remembered


def test_off_in_settings_and_on_cpu(data_dir, monkeypatch):
    assert isinstance(fp16.session(engine.DET_MODEL, SO, CPU), ort.InferenceSession)   # CPU: never FP16
    monkeypatch.setattr(fp16, "_settings_on", lambda: False)
    assert fp16.Session(engine.DET_MODEL, SO, CPU, force=True).state == "cpu"

