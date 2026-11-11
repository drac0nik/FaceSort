"""Updates (GitHub releases) and the face engines' settings."""
import hashlib
import http.server
import json
import os
import threading

import numpy as np
import pytest

import engine_hq
import store
import updater


# ------------------------------------------------------------------ updates
def test_versions_and_project_names():
    assert updater.repo({}) == "drac0nik/FaceSort"             # built in: updates come from here
    assert updater.vtuple("v1.38.0") == (1, 38, 0) > updater.vtuple("1.37.12")
    assert updater.vtuple("nonsense") == (0, 0, 0)
    assert updater.repo({"update_repo": "someone/FaceSort"}) == "someone/FaceSort"
    assert updater.repo({"update_repo": "https://evil.example/x"}) == ""
    assert updater.repo({"update_repo": " someone/FaceSort/ "}) == "someone/FaceSort"
    assert not updater.is_installed()                      # (tests run from source)


def release(version="1.99.0", digest=True, sha_file=False, extra=()):
    inst = {"name": f"FaceSort-Setup-{version}.exe", "size": 1234,
            "browser_download_url": f"https://example.invalid/FaceSort-Setup-{version}.exe"}
    if digest:
        inst["digest"] = "sha256:" + "ab" * 32
    assets = [inst] + list(extra)
    if sha_file:
        assets.append({"name": f"FaceSort-Setup-{version}.exe.sha256", "browser_download_url": "https://example.invalid/s"})
    return {"tag_name": f"v{version}", "body": "What's new", "html_url": "https://github.com/x/y/releases/v1",
            "assets": assets, "draft": False, "prerelease": False}


def test_latest_release(monkeypatch):
    calls = []

    def fake_get(url, timeout=20):
        calls.append(url)
        if url.endswith("/s"):
            return (("cd" * 32) + "  FaceSort-Setup-1.99.0.exe\n").encode()
        return json.dumps(release(**fake_get.kw)).encode()
    fake_get.kw = {}
    monkeypatch.setattr(updater, "_get", fake_get)
    monkeypatch.setattr(updater, "UPDATE_REPO", "")
    assert updater.latest({}) is None and not calls          # no project set: nothing is asked
    info = updater.latest({"update_repo": "x/y"})
    assert calls[0] == "https://api.github.com/repos/x/y/releases/latest"
    assert info["version"] == "1.99.0" and info["sha256"] == "ab" * 32 and info["notes"] == "What's new"
    fake_get.kw = {"digest": False, "sha_file": True}
    assert updater.latest({"update_repo": "x/y"})["sha256"] == "cd" * 32
    assert updater.check_job(None, {"update_repo": "x/y"})["version"] == "1.99.0"
    fake_get.kw = {"version": "0.0.1"}
    assert updater.check_job(None, {"update_repo": "x/y"}) is None       # older than this one


def test_check_is_due_once_a_day(monkeypatch):
    import time
    monkeypatch.delenv("FACESORT_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(updater, "UPDATE_REPO", "")
    assert not updater.due({})                                  # no project
    assert updater.due({"update_repo": "x/y"})
    assert not updater.due({"update_repo": "x/y", "update_checked": time.time() - 3600})
    assert not updater.due({"update_repo": "x/y", "check_updates": False})


@pytest.fixture
def served(tmp_path):
    payload = os.urandom(300_000)
    (tmp_path / "FaceSort-Setup-1.99.0.exe").write_bytes(payload)

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(tmp_path), **k)

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/FaceSort-Setup-1.99.0.exe", payload
    srv.shutdown()


def test_download_checks_the_installer(served, monkeypatch, tmp_path):
    import tempfile
    url, payload = served
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path / "dl"))
    os.makedirs(tmp_path / "dl")
    good = {"version": "1.99.0", "name": "FaceSort-Setup-1.99.0.exe", "installer": url, "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest()}
    path = updater.download(good)
    assert open(path, "rb").read() == payload
    os.remove(path)
    with pytest.raises(RuntimeError, match="checksum"):
        updater.download(dict(good, sha256="00" * 32))
    assert os.listdir(tmp_path / "dl") == []                   # a bad download isn't left behind
    with pytest.raises(RuntimeError, match="no checksum"):
        updater.download(dict(good, sha256=None))
    assert updater.download(good, cancelled=lambda: True) is None


# ------------------------------------------------------------------ engines
def test_engine_settings():
    s = dict(store.DEFAULT_SETTINGS)
    assert store.threshold(s, "adaface") == 0.32 and store.threshold(s, "arcface") == 0.32
    assert store.threshold(s, "sface") == 0.42
    s["threshold_adaface"] = 0.36
    assert store.threshold(s, "adaface") == 0.36 and store.threshold(s, "arcface") == 0.32
    assert store.threshold_key("adaface") == "threshold_adaface"
    assert store.model_dim("adaface") == store.model_dim("arcface") == 512 and store.model_dim("sface") == 128
    for table in (store.THRESHOLD_RANGE, store.NOTFACE_SIM, store.FOLDER_BONUS):
        assert set(table) >= {"sface", "arcface", "adaface"}
    info = engine_hq.RECOGNIZERS["adaface"]
    assert info["url"].startswith("https://github.com/") and len(info["sha256"]) == 64


def test_adaface_not_available_without_its_model(data_dir, monkeypatch):
    monkeypatch.setattr(engine_hq.engine, "resource_path", lambda *p: str(data_dir.root / "nowhere"))
    assert not engine_hq.available("adaface")
    assert not engine_hq.available("unknown")


REAL = os.environ.get("FACESORT_ADAFACE_MODELS")       # a folder with det_10g.onnx and adaface_ir_101.onnx


@pytest.mark.skipif(not REAL, reason="set FACESORT_ADAFACE_MODELS to test the real AdaFace model")
def test_adaface_reads_faces(monkeypatch):
    monkeypatch.setattr(engine_hq, "model_dir", lambda model="arcface": REAL)
    engine_hq._Sessions._inst.clear()
    eng = engine_hq.HQFaceEngine(use_gpu=False, model="adaface")
    rng = np.random.default_rng(0)
    bgr = rng.integers(0, 255, (200, 200, 3), dtype=np.uint8)
    f = np.zeros(15, np.float32)
    f[:4] = (50, 50, 100, 100)
    f[4:14] = (engine_hq.ARCFACE_DST / 112 * 100 + 50).flatten()
    (emb, q), = eng.embed_with_quality(bgr, [f])
    assert emb.shape == (512,) and abs(np.linalg.norm(emb) - 1) < 1e-4 and q is None
    engine_hq._Sessions._inst.clear()
