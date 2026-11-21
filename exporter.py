"""Export (1.53): copies of photos and videos, made smaller if you like, into a folder or one zip.

- Size: as they are, a percentage, or fit inside a maximum width × height (never made bigger).
- Strip metadata (on by default): EXIF (camera, date, GPS location), XMP (names, tags) and IPTC
  come off; the color profile stays so colors look the same. A JPEG or PNG that isn't resized
  loses its metadata without being re-compressed.
- Videos come along. In a zip they're made smaller (H.264, at most 1280 px, unless your size
  setting is smaller); in a folder they're copied (metadata stripped without re-encoding).
- RAW photos become JPEGs, and formats that don't open everywhere (HEIC, WebP, AVIF…) become
  JPEG (or PNG when they have see-through parts).
- The originals are never touched. File names stay; a name used twice gets (2)."""
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from io import BytesIO

from PIL import Image, ImageOps

import convert
import engine

KEEP_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "GIF": ".gif"}
VIDEO_MAX = 1280
QUALITY = 90


def target_size(w, h, opts):
    """The exported picture size for w × h (never bigger than the original)."""
    mode = opts.get("size", "original")
    if mode == "percent":
        f = max(1, min(100, int(opts.get("percent", 50)))) / 100
    elif mode == "max":
        mw, mh = int(opts.get("max_w") or 0), int(opts.get("max_h") or 0)
        f = min((mw / w) if mw else 1.0, (mh / h) if mh else 1.0, 1.0)
    else:
        f = 1.0
    if f >= 1.0:
        return w, h
    return max(1, round(w * f)), max(1, round(h * f))


# ------------------------------------------------------------------ lossless metadata stripping
def strip_jpeg(data):
    """JPEG bytes without EXIF / XMP / IPTC / comments (APP0 and the ICC profile in APP2 stay).
    None if the file isn't laid out as expected."""
    if data[:2] != b"\xff\xd8":
        return None
    out = [b"\xff\xd8"]
    i = 2
    n = len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:                      # padding
            i += 1
            continue
        if marker == 0xDA:                      # start of scan: the rest is picture data
            out.append(data[i:])
            return b"".join(out)
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            out.append(data[i:i + 2])
            i += 2
            continue
        size = struct.unpack(">H", data[i + 2:i + 4])[0]
        seg = data[i:i + 2 + size]
        drop = marker in (0xE1, 0xED, 0xFE) or (0xE3 <= marker <= 0xEF)   # EXIF/XMP, IPTC, comment, other APPn
        if not drop:
            out.append(seg)
        i += 2 + size
    return None


def strip_png(data):
    """PNG bytes without text / EXIF / time chunks (the color profile chunks stay)."""
    sig = b"\x89PNG\r\n\x1a\n"
    if data[:8] != sig:
        return None
    out = [sig]
    i = 8
    while i + 8 <= len(data):
        length = struct.unpack(">I", data[i:i + 4])[0]
        ctype = data[i + 4:i + 8]
        chunk = data[i:i + 12 + length]
        if ctype not in (b"tEXt", b"iTXt", b"zTXt", b"eXIf", b"tIME"):
            out.append(chunk)
        i += 12 + length
        if ctype == b"IEND":
            break
    return b"".join(out)


def _orientation(im):
    try:
        return int(im.getexif().get(0x0112, 1) or 1)
    except Exception:
        return 1


# ------------------------------------------------------------------ one photo
def photo_bytes(path, opts):
    """(bytes, file ending) of the exported photo."""
    strip = opts.get("strip", True)
    tmp = None
    if engine.is_raw(path):
        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False).name
        convert.raw_to_jpeg(path, tmp)
        path = tmp
    try:
        with open(path, "rb") as f:
            raw = f.read()
        with convert._open(path) as im:
            fmt = im.format
            w, h = im.size
            orient = _orientation(im)
            animated = getattr(im, "n_frames", 1) > 1
            tw, th = target_size(*((h, w) if orient in (5, 6, 7, 8) else (w, h)), opts)
            resize = (tw, th) != ((h, w) if orient in (5, 6, 7, 8) else (w, h))
            if fmt in KEEP_FORMATS and not resize and (animated or fmt == "GIF" or not strip or orient == 1):
                ext = KEEP_FORMATS[fmt]
                if not strip:
                    return raw, ext
                if fmt == "JPEG":
                    s = strip_jpeg(raw)
                    if s:
                        return s, ext
                elif fmt == "PNG":
                    s = strip_png(raw)
                    if s:
                        return s, ext
                elif fmt == "GIF":
                    return raw, ext
            if animated and fmt == "GIF":
                return raw, ".gif"
            im.seek(0)
            exif = None if strip else im.getexif()
            icc = im.info.get("icc_profile")
            im = ImageOps.exif_transpose(im)
            if resize:
                im = im.resize((tw, th), Image.LANCZOS)
            kw = {}
            if icc:
                kw["icc_profile"] = icc
            if exif:
                exif[0x0112] = 1
                kw["exif"] = exif.tobytes()
            alpha = convert.has_transparency(im)
            buf = BytesIO()
            if fmt == "PNG" or alpha:
                if im.mode not in ("RGB", "RGBA", "L", "LA", "P"):
                    im = im.convert("RGBA" if alpha else "RGB")
                im.save(buf, "PNG", optimize=True, **kw)
                return buf.getvalue(), ".png"
            im.convert("RGB").save(buf, "JPEG", quality=int(opts.get("quality", QUALITY)), subsampling=0
                                   if not resize else 2, **kw)
            return buf.getvalue(), ".jpg"
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


# ------------------------------------------------------------------ one video
def _hidden():
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return {"startupinfo": si, "creationflags": 0x08000000}
    return {}


def video_file(path, out, opts, shrink, cancelled=lambda: False):
    """Write the exported video to out. Returns out's real path (the ending may change to .mp4)."""
    strip = opts.get("strip", True)
    if not shrink and not strip:
        shutil.copy2(path, out)
        return out
    exe = engine.ffmpeg_exe()
    if not exe:
        shutil.copy2(path, out)
        return out
    if not shrink:                                   # metadata off, picture untouched
        cmd = [exe, "-y", "-loglevel", "error", "-i", path, "-map", "0", "-c", "copy", "-map_metadata", "-1",
               "-map_chapters", "-1", out]
    else:
        out = os.path.splitext(out)[0] + ".mp4"
        try:
            W, H = engine.ffmpeg_info(path)[:2]
        except Exception:
            W = H = 0
        tw, th = target_size(W, H, opts) if W and H else (0, 0)
        if W and H:
            f = min(1.0, VIDEO_MAX / max(tw, th))
            tw, th = int(tw * f) // 2 * 2, int(th * f) // 2 * 2
        vf = ["-vf", f"scale={tw}:{th}"] if tw and th and (tw, th) != (W, H) else []
        cmd = [exe, "-y", "-loglevel", "error", "-i", path, "-map", "0:v:0", "-map", "0:a:0?"] + vf + [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"]
        if strip:
            cmd += ["-map_metadata", "-1"]
        cmd += [out]
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **_hidden())
    while p.poll() is None:
        if cancelled():
            p.kill()
            raise RuntimeError("cancelled")
        try:
            p.wait(0.3)
        except subprocess.TimeoutExpired:
            pass
    if p.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
        err = (p.stderr.read() or b"").decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(err[-1] if err else "ffmpeg failed")
    return out


# ------------------------------------------------------------------ the whole export
def _free(name, used):
    base, ext = os.path.splitext(name)
    cand, i = name, 2
    while cand.casefold() in used:
        cand = f"{base} ({i}){ext}"
        i += 1
    used.add(cand.casefold())
    return cand


def _free_path(path):
    base, ext = os.path.splitext(path)
    cand, i = path, 2
    while os.path.exists(cand):
        cand = f"{base} ({i}){ext}"
        i += 1
    return cand


def export(conn, pids, opts, progress=lambda d, t, m: None, cancelled=lambda: False):
    """opts: dest, name, size/percent/max_w/max_h, zip, strip. Returns stats with "out" (folder or zip)."""
    rows = []
    pids = list(pids)
    for i in range(0, len(pids), 900):
        ch = list(pids[i:i + 900])
        got = dict(conn.execute(f"SELECT id, path FROM photos WHERE id IN ({','.join('?' * len(ch))})", ch).fetchall())
        rows += [got[p] for p in ch if p in got]
    dest = opts["dest"]
    os.makedirs(dest, exist_ok=True)
    use_zip = bool(opts.get("zip"))
    name = convert_name(opts.get("name") or "FaceSort export")
    stats = {"photos": 0, "videos": 0, "errors": [], "out": None, "bytes": 0}
    used = set()
    if use_zip:
        out = _free_path(os.path.join(dest, name + ".zip"))
        part = out + ".part"
        z = zipfile.ZipFile(part, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True)
        work = tempfile.mkdtemp(prefix=".facesort-export-", dir=dest)
    else:
        out = _free_path(os.path.join(dest, name))
        os.makedirs(out)
        z, part, work = None, None, None
    stats["out"] = out
    try:
        for k, path in enumerate(rows):
            if cancelled():
                break
            progress(k, len(rows), os.path.basename(path))
            fname = os.path.basename(path)
            import fsx
            path = fsx.plain(path, conn)      # (1.58) kept encrypted: exported from a readable copy
            if not os.path.exists(path):
                stats["errors"].append((path, "file not found"))
                continue
            try:
                stem = os.path.splitext(fname)[0]
                if engine.is_video(path):
                    tmp = os.path.join(work or out, f".v{k}" + os.path.splitext(path)[1].lower()) if use_zip \
                        else os.path.join(out, _free(fname, used))
                    made = video_file(path, tmp, opts, shrink=use_zip, cancelled=cancelled)
                    if use_zip:
                        arc = _free(stem + os.path.splitext(made)[1], used)
                        z.write(made, arc, compress_type=zipfile.ZIP_STORED)    # (already compressed)
                        os.remove(made)
                    elif made != tmp:
                        os.replace(made, os.path.join(out, _free(stem + os.path.splitext(made)[1], used)))
                    stats["videos"] += 1
                else:
                    data, ext = photo_bytes(path, opts)
                    fn = _free(stem + ext, used)
                    if use_zip:
                        z.writestr(fn, data, compress_type=zipfile.ZIP_STORED if ext in (".jpg", ".png", ".gif")
                                   else zipfile.ZIP_DEFLATED)
                    else:
                        with open(os.path.join(out, fn), "wb") as f:
                            f.write(data)
                    stats["photos"] += 1
            except Exception as e:
                if str(e) == "cancelled":
                    break
                stats["errors"].append((path, str(e)))
        if z is not None:
            z.close()
            z = None
            if cancelled():
                os.remove(part)
                stats["out"] = None
            else:
                os.replace(part, out)
        stats["bytes"] = (os.path.getsize(out) if use_zip and stats["out"] else
                          sum(os.path.getsize(os.path.join(out, f)) for f in os.listdir(out)) if stats["out"] else 0)
        progress(len(rows), len(rows), "Exported")
        return stats
    finally:
        if z is not None:
            z.close()
            try:
                os.remove(part)
            except OSError:
                pass
        if work:
            shutil.rmtree(work, ignore_errors=True)


def convert_name(name):
    import renamer
    return renamer.clean(name) or "FaceSort export"
