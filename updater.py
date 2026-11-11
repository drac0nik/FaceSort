"""Updates: FaceSort asks GitHub (at most once a day) whether a newer version has been released,
and if so shows an "Update to x.y.z" button. One click downloads the installer, checks it
against the checksum published with it, and runs it; the installer closes FaceSort, replaces
the program and starts it again. Your library, settings and photos are in a separate folder and
aren't touched.

What is sent: an ordinary request for the project's latest-release page on GitHub, with the
user agent "FaceSort-updater". Nothing about you, your library or your photos.
"""
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.request

# The GitHub project the releases come from ("owner/repo"). Empty = update checks are off.
UPDATE_REPO = "drac0nik/FaceSort"

CHECK_EVERY = 24 * 3600
API = "https://api.github.com/repos/{repo}/releases/latest"
INSTALLER_RE = re.compile(r"^FaceSort-Setup-(\d+\.\d+\.\d+)\.exe$", re.I)
UA = {"User-Agent": "FaceSort-updater", "Accept": "application/vnd.github+json"}


def repo(settings=None):
    r = (settings or {}).get("update_repo") or UPDATE_REPO
    return r.strip().strip("/") if r and re.fullmatch(r"[\w.-]+/[\w.-]+", r.strip().strip("/")) else ""


def vtuple(v):
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", v or "")
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def is_installed():
    """Running the program the installer put in place (not from source, not a Setup.bat build)."""
    if not getattr(sys, "frozen", False):
        return False
    here = os.path.dirname(sys.executable)
    return os.path.exists(os.path.join(here, "unins000.exe"))


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def latest(settings=None, timeout=20):
    """The newest release: {"version", "notes", "page", "installer", "size", "sha256" (or None)},
    or None when update checks are off or nothing usable was published."""
    rp = repo(settings)
    if not rp:
        return None
    data = json.loads(_get(API.format(repo=rp), timeout))
    if data.get("draft") or data.get("prerelease"):
        return None
    inst = sha_asset = None
    for a in data.get("assets", []):
        name = a.get("name", "")
        if INSTALLER_RE.match(name):
            inst = a
        elif name.lower().endswith(".sha256"):
            sha_asset = a
    if not inst:
        return None
    digest = inst.get("digest") or ""          # GitHub publishes "sha256:..." for each asset
    sha = digest.split(":", 1)[1].lower() if digest.lower().startswith("sha256:") else None
    if not sha and sha_asset:
        try:
            text = _get(sha_asset["browser_download_url"], timeout).decode("utf-8", "replace")
            m = re.search(r"\b([0-9a-f]{64})\b", text, re.I)
            sha = m.group(1).lower() if m else None
        except Exception:
            sha = None
    return {"version": INSTALLER_RE.match(inst["name"]).group(1), "notes": data.get("body") or "",
            "page": data.get("html_url") or f"https://github.com/{rp}/releases/latest",
            "installer": inst["browser_download_url"], "size": int(inst.get("size") or 0), "sha256": sha,
            "name": inst["name"]}


def due(settings):
    if os.environ.get("FACESORT_NO_UPDATE_CHECK"):          # (the test suite never asks the internet)
        return False
    return bool(repo(settings)) and settings.get("check_updates", True) and \
        time.time() - float(settings.get("update_checked", 0) or 0) >= CHECK_EVERY


def check_job(conn, settings, progress=lambda d, t, m: None, cancelled=lambda: False):
    """(Background) the newest release if it's newer than this version, else None."""
    from ui_common import APP_VERSION
    info = latest(settings)
    if info and vtuple(info["version"]) > vtuple(APP_VERSION):
        return info
    return None


def download(info, progress=lambda d, t, m: None, cancelled=lambda: False):
    """The installer, downloaded to the temp folder and checked. Returns its path, or None if
    cancelled. Raises if the download is incomplete or doesn't match its checksum."""
    if not info.get("sha256"):
        raise RuntimeError("the release has no checksum to check the installer against")
    dest = os.path.join(tempfile.gettempdir(), info["name"])
    part = dest + ".part"
    h = hashlib.sha256()
    req = urllib.request.Request(info["installer"], headers={"User-Agent": UA["User-Agent"]})
    done = 0
    with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
        total = int(r.headers.get("Content-Length") or info.get("size") or 0)
        while True:
            if cancelled():
                f.close()
                os.remove(part)
                return None
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            progress(done, total, f"Downloading FaceSort {info['version']}… {done / 1e6:.0f} / {total / 1e6:.0f} MB")
    if info.get("size") and done != info["size"]:
        os.remove(part)
        raise RuntimeError("the download was incomplete")
    if h.hexdigest().lower() != info["sha256"]:
        os.remove(part)
        raise RuntimeError("the downloaded installer doesn't match its published checksum - not run")
    os.replace(part, dest)
    return dest


def run_installer(path):
    """Start the installer for an update: it closes FaceSort, installs, and starts it again."""
    import subprocess
    flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0     # DETACHED_PROCESS | NEW_PROCESS_GROUP
    subprocess.Popen([path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                     creationflags=flags, close_fds=True)
