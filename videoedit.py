"""Video edit mode's saving (1.54): trim, cut out parts and turn a video, exact to the frame, with low
light / Steady / a new frame rate on top (jobs.enhance_video). Always a new file next to the original."""
import os

import fsx
import re
import subprocess
import sys

import engine

QUALITY = {"high": 18, "smaller": 23}


def kept_parts(length, start=None, end=None, cuts=()):
    """[(from, to)] seconds: between start and end, without the cut-out parts."""
    a = max(0.0, start or 0.0)
    b = end if end is not None else length
    if b is None or b <= a:
        return []
    parts = [(a, b)]
    for x, y in sorted(cuts):
        nxt = []
        for p, q in parts:
            if y <= p or x >= q:
                nxt.append((p, q))
                continue
            if x > p:
                nxt.append((p, x))
            if y < q:
                nxt.append((y, q))
        parts = nxt
    return [(p, q) for p, q in parts if q - p > 0.02]


def _hidden():
    if sys.platform == "win32":
        return {"creationflags": 0x08000000}
    return {}


def has_audio(path):
    exe = engine.ffmpeg_exe()
    try:
        r = subprocess.run([exe, "-hide_banner", "-i", path], capture_output=True, timeout=30, **_hidden())
        return b"Audio:" in r.stderr
    except Exception:
        return False


def out_name(src, pattern="{name} (edited)"):
    folder = os.path.dirname(src)
    os.makedirs(folder, exist_ok=True)              # (1.58: the folder may exist only inside FaceSort)
    stem = os.path.splitext(os.path.basename(src))[0]
    base = (pattern or "{name} (edited)").replace("{name}", stem)
    out, n = os.path.join(folder, base + ".mp4"), 2
    while fsx.exists(out):
        out = os.path.join(folder, f"{base} ({n}).mp4")
        n += 1
    return out


def cut_command(exe, src, keep, turn, audio, out, crf=18, preset="veryfast"):
    """The FFmpeg command for the kept parts joined, turned."""
    rot = {1: ",transpose=1", 2: ",transpose=1,transpose=1", 3: ",transpose=2"}.get(turn % 4, "")
    fc = []
    for i, (a, b) in enumerate(keep):
        fc.append(f"[0:v]trim=start={a:.6f}:end={b:.6f},setpts=PTS-STARTPTS[v{i}]")
        if audio:
            fc.append(f"[0:a]atrim=start={a:.6f}:end={b:.6f},asetpts=PTS-STARTPTS[a{i}]")
    n = len(keep)
    if audio:
        fc.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[vc][ac]")
    else:
        fc.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[vc]")
    fc.append(f"[vc]scale=trunc(iw/2)*2:trunc(ih/2)*2{rot},format=yuv420p[vo]")
    cmd = [exe, "-y", "-loglevel", "error", "-progress", "pipe:1", "-nostats", "-i", src,
           "-filter_complex", ";".join(fc), "-map", "[vo]"]
    if audio:
        cmd += ["-map", "[ac]", "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-movflags", "+faststart",
            "-metadata:s:v:0", "rotate=0", out]
    return cmd


def _run(cmd, total, progress, cancelled, label):
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_hidden())
    pat = re.compile(rb"out_time_(?:us|ms)=(\d+)")
    try:
        for line in p.stdout:
            if cancelled():
                p.kill()
                return "cancelled"
            m = pat.search(line)
            if m and total:
                t = int(m.group(1)) / 1e6
                progress(int(min(t, total) * 10), int(total * 10), f"{label} {engine.clock(t)} / {engine.clock(total)}")
    finally:
        p.wait()
    if p.returncode != 0:
        err = (p.stderr.read() or b"").decode("utf-8", "replace").strip().splitlines()
        return err[-1] if err else "FFmpeg failed"
    return None


def edit_video(conn, src, opts, progress=lambda d, t, m: None, cancelled=lambda: False):
    """opts: keep [(from, to)], turn (quarter turns clockwise), strength (low light 0-2), steady, fps,
    method, quality ("high"/"smaller"), name (pattern). Returns the new file's path (None if stopped)."""
    import jobs
    exe = engine.ffmpeg_exe()
    if not exe:
        raise RuntimeError("FFmpeg isn't available")
    keep = [tuple(k) for k in (opts.get("keep") or [])]
    if not keep:
        raise ValueError("nothing would be left of the video")
    turn = int(opts.get("turn") or 0) % 4
    enhance = bool(opts.get("strength") or opts.get("steady") or opts.get("fps"))
    out = out_name(src, opts.get("name"))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    label = os.path.basename(src)
    src = fsx.plain(src)                     # (1.58) kept encrypted: the readable copy for FFmpeg
    total = sum(b - a for a, b in keep)
    audio = has_audio(src)
    crf = QUALITY.get(opts.get("quality", "high"), 18)
    if not enhance:
        tmp = out + ".part.mp4"
        err = _run(cut_command(exe, src, keep, turn, audio, tmp, crf), total, progress, cancelled,
                   f"Saving {label}:")
        if err:
            _rm(tmp)
            if err == "cancelled":
                return None
            raise RuntimeError(err)
        os.replace(tmp, out)
        return out
    # cut / turn first (nearly lossless), then low light / Steady / frame rate on that
    stage = os.path.join(os.path.dirname(src), f".fsedit-{os.getpid()}-{os.path.splitext(os.path.basename(out))[0]}.mkv")
    try:
        err = _run(cut_command(exe, src, keep, turn, audio, stage, 10, "ultrafast"), total,
                   lambda d, t, m: progress(d, t, "Step 1 of 2 - " + m), cancelled, f"Cutting {label}:")
        if err:
            if err == "cancelled":
                return None
            raise RuntimeError(err)
        made, _done, _total = jobs.enhance_video(conn, stage, {"strength": int(opts.get("strength") or 0),
                                                               "steady": bool(opts.get("steady")),
                                                               "fps": opts.get("fps"),
                                                               "method": opts.get("method", "motion")},
                                                 progress=lambda d, t, m: progress(d, t, "Step 2 of 2 - " + m),
                                                 cancelled=cancelled)
        if not made:
            return None
        os.replace(made, out)
        return out
    finally:
        _rm(stage)


def _rm(p):
    try:
        os.remove(p)
    except OSError:
        pass
