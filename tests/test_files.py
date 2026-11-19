"""1.53: bulk rename, renaming while sorting, export (sizes, zip, metadata), contact sheets, the
click-anywhere video bar."""
import os
import zipfile
from io import BytesIO

import pytest
from PIL import Image


def _named(lib):
    """(photo id, path, person) for photos with a confirmed face."""
    return lib.conn.execute("""SELECT ph.id, ph.path, pe.name FROM faces f JOIN photos ph ON ph.id=f.photo_id
                               JOIN persons pe ON pe.id=f.person_id WHERE f.confirmed=1 ORDER BY ph.id""").fetchall()


def test_format_name_parts():
    import renamer
    info = {"path": os.path.join("x", "IMG_0001.jpg"), "taken": 1721044800, "people": ["Anna", "Ben", "Cy", "Di"],
            "person": "Anna", "folder": "Holiday"}
    assert renamer.format_name("{person} {n}", info, 7, 3) == "Anna 007"
    assert renamer.format_name("{people}", info, 1) == "Anna, Ben & 2 more"
    assert renamer.format_name("{folder} - {name}", info, 1) == "Holiday - IMG_0001"
    assert renamer.format_name("{n:5}", info, 42) == "00042"
    assert renamer.format_name("{date:%Y}", info, 1) == "2024"
    assert renamer.format_name('a/b:c*?"<>|', info, 1) == "a_b_c______"
    assert renamer.format_name("   ", info, 1) == "IMG_0001"          # nothing left: the old name
    assert renamer.clean("CON") == "_CON"


def test_plan_and_apply_with_extras_and_undo(library):
    import history
    import renamer
    rows = _named(library)
    pid, path, name = rows[0]
    sc = os.path.splitext(path)[0] + ".xmp"
    js = path + ".json"
    open(sc, "w").close()
    open(js, "w").close()
    pids = list(dict.fromkeys(r[0] for r in rows))[:4]
    plan = renamer.plan(library.conn, pids, "{person} {n}", order="name")
    assert len(plan) == len(pids)
    new_names = [os.path.basename(n) for _p, _o, n in plan]
    assert len(set(n.casefold() for n in new_names)) == len(new_names)
    assert all(n.split(" ")[0] in {r[2] for r in rows} for n in new_names)
    res = renamer.apply(library.conn, plan)
    assert res["renamed"] == len(plan) and not res["errors"]
    for p, old, new in plan:
        assert os.path.exists(new) and not os.path.exists(old)
        assert library.conn.execute("SELECT path FROM photos WHERE id=?", (p,)).fetchone()[0] == new
    first_new = next(n for p, _o, n in plan if p == pid)
    assert os.path.exists(os.path.splitext(first_new)[0] + ".xmp") and os.path.exists(first_new + ".json")
    assert not os.path.exists(sc) and not os.path.exists(js)
    # undo from History puts every name back (with the extra files)
    history._state.pending = []                # (left over from earlier tests in this thread)
    history.record_files("Renamed", res["moves"])
    eid = history.flush(library.conn)
    done, skipped = history.undo(library.conn, eid)
    assert skipped == 0
    for p, old, new in plan:
        assert os.path.exists(old) and not os.path.exists(new)
        assert library.conn.execute("SELECT path FROM photos WHERE id=?", (p,)).fetchone()[0] == old
    assert os.path.exists(sc) and os.path.exists(js)


def test_names_can_swap_and_taken_names_get_numbers(library):
    import renamer
    rows = library.conn.execute("SELECT id, path FROM photos ORDER BY id LIMIT 3").fetchall()
    (a, pa), (b, pb), (c, pc) = rows
    folder = os.path.dirname(pa)
    # a and b swap names (in one go)
    stem_a, stem_b = os.path.splitext(os.path.basename(pa))[0], os.path.splitext(os.path.basename(pb))[0]
    if os.path.dirname(pb) == folder:
        res = renamer.apply(library.conn, [(a, pa, pb), (b, pb, pa)])
        assert res["renamed"] == 2
        assert library.conn.execute("SELECT path FROM photos WHERE id=?", (a,)).fetchone()[0] == pb
    # a name already used by a file outside the rename gets (2)
    taken = os.path.join(folder, "Same.jpg")
    open(taken, "w").close()
    plan = renamer.plan(library.conn, [c], "Same")
    if os.path.dirname(pc) == folder:
        assert os.path.basename(plan[0][2]) == "Same (2).jpg"
    assert stem_a and stem_b


def test_sort_into_folders_can_rename(library, tmp_path):
    import jobs
    dest = tmp_path / "sorted"
    st = jobs.export_folders(library.conn, str(dest), rename="{person} {n}")
    assert st["copied"] > 0
    names = [f for _r, _d, fs in os.walk(dest) for f in fs if f.lower().endswith(".jpg")]
    assert names and all(" " in n and n.split(" ")[-1][:3].isdigit() for n in names), names[:5]
    st2 = jobs.export_folders(library.conn, str(tmp_path / "plain"))
    plain = [f for _r, _d, fs in os.walk(tmp_path / "plain") for f in fs]
    originals = {os.path.basename(p) for (p,) in library.conn.execute("SELECT path FROM photos")}
    assert st2["copied"] and set(plain) <= originals | {f for f in plain if f.endswith(".csv")}


def _jpeg_with_exif(path, size=(400, 300), orientation=1):
    im = Image.new("RGB", size, (200, 50, 50))
    ex = Image.Exif()
    ex[0x010F] = "TestCam"
    ex[0x0112] = orientation
    im.save(path, "JPEG", exif=ex.tobytes(), quality=90)


def test_strip_and_resize(tmp_path):
    import exporter
    p = str(tmp_path / "a.jpg")
    _jpeg_with_exif(p)
    raw = open(p, "rb").read()
    data, ext = exporter.photo_bytes(p, {"strip": True})
    assert ext == ".jpg" and len(data) < len(raw)
    with Image.open(BytesIO(data)) as im:
        assert not im.getexif() and im.size == (400, 300)
    assert raw[raw.index(b"\xff\xda"):] == data[data.index(b"\xff\xda"):]      # not re-compressed
    data, _ = exporter.photo_bytes(p, {"strip": False})
    assert data == raw
    data, _ = exporter.photo_bytes(p, {"strip": False, "size": "percent", "percent": 50})
    with Image.open(BytesIO(data)) as im:
        assert im.size == (200, 150) and im.getexif().get(0x010F) == "TestCam"
    data, _ = exporter.photo_bytes(p, {"strip": True, "size": "max", "max_w": 100, "max_h": 100})
    with Image.open(BytesIO(data)) as im:
        assert im.size == (100, 75) and not im.getexif()
    assert exporter.target_size(400, 300, {"size": "max", "max_w": 4000, "max_h": 4000}) == (400, 300)  # never bigger
    q = str(tmp_path / "turned.jpg")
    _jpeg_with_exif(q, orientation=6)
    data, _ = exporter.photo_bytes(q, {"strip": True})
    with Image.open(BytesIO(data)) as im:
        assert im.size == (300, 400) and not im.getexif()                      # turn baked in
    png = str(tmp_path / "b.png")
    im = Image.new("RGBA", (50, 40), (0, 0, 0, 0))
    from PIL import PngImagePlugin
    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "secret")
    im.save(png, pnginfo=info)
    data, ext = exporter.photo_bytes(png, {"strip": True})
    assert ext == ".png" and b"secret" not in data


def test_export_folder_and_zip(library, tmp_path):
    import exporter
    pids = [r[0] for r in library.conn.execute("SELECT id FROM photos ORDER BY id LIMIT 5")]
    st = exporter.export(library.conn, pids, {"dest": str(tmp_path), "name": "Anna", "strip": True,
                                               "size": "percent", "percent": 50})
    assert st["photos"] == 5 and os.path.isdir(st["out"]) and len(os.listdir(st["out"])) == 5
    st = exporter.export(library.conn, pids, {"dest": str(tmp_path), "name": "Anna", "zip": True, "strip": True})
    assert st["out"].endswith("Anna.zip") and os.path.exists(st["out"])
    with zipfile.ZipFile(st["out"]) as z:
        assert len(z.namelist()) == 5 and z.testzip() is None
    st2 = exporter.export(library.conn, pids, {"dest": str(tmp_path), "name": "Anna", "zip": True})
    assert st2["out"].endswith("Anna (2).zip")
    assert not [f for f in os.listdir(tmp_path) if f.startswith(".facesort-export") or f.endswith(".part")]


def test_export_video_shrunk_in_zip(tmp_path, data_dir):
    import engine
    import exporter
    import store
    exe = engine.ffmpeg_exe()
    if not exe:
        pytest.skip("no ffmpeg")
    import subprocess
    v = str(tmp_path / "clip.mov")
    subprocess.run([exe, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=10",
                    "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-metadata", "title=secret", v], check=True)
    conn = store.connect()
    try:
        conn.execute("INSERT INTO photos(path, is_video) VALUES(?, 1)", (v,))
        conn.commit()
        pid = conn.execute("SELECT id FROM photos WHERE path=?", (v,)).fetchone()[0]
        st = exporter.export(conn, [pid], {"dest": str(tmp_path / "out"), "name": "v", "zip": True, "strip": True})
        assert st["videos"] == 1 and not st["errors"], st["errors"]
        with zipfile.ZipFile(st["out"]) as z:
            assert z.namelist() == ["clip.mp4"]
            z.extract("clip.mp4", str(tmp_path / "x"))
        W, H = engine.ffmpeg_info(str(tmp_path / "x" / "clip.mp4"))[:2]
        assert max(W, H) <= exporter.VIDEO_MAX
        st = exporter.export(conn, [pid], {"dest": str(tmp_path / "out"), "name": "f", "strip": False})
        assert os.listdir(st["out"]) == ["clip.mov"]                           # a folder: copied as it is
    finally:
        conn.close()


def test_contact_sheet(library, tmp_path, qapp):
    import contactsheet
    paths = [r[0] for r in library.conn.execute("SELECT path FROM photos ORDER BY id LIMIT 30")]
    files = contactsheet.render(paths, {"out": str(tmp_path / "Anna - contact sheet"), "fmt": "pdf", "title": "Anna",
                                        "font": "DejaVu Serif", "italic": True, "captions": True})
    assert files == [str(tmp_path / "Anna - contact sheet.pdf")]
    assert open(files[0], "rb").read(5) == b"%PDF-" and os.path.getsize(files[0]) > 5000
    files = contactsheet.render(paths, {"out": str(tmp_path / "jpgs"), "fmt": "jpg", "title": "Anna", "cols": 3,
                                        "dark": True})
    assert len(files) >= 2 and all(f.endswith(".jpg") for f in files)        # 3 across: more than one page
    with Image.open(files[0]) as im:
        assert im.size[0] > 1000 and im.getpixel((5, 5))[0] < 40


def test_click_anywhere_on_the_video_bar(qapp):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from viewer_parts import MarkSlider
    s = MarkSlider(Qt.Horizontal)
    s.resize(400, 24)
    s.setRange(0, 10000)
    s.show()
    moved = []
    s.sliderMoved.connect(moved.append)
    QTest.mouseClick(s, Qt.LeftButton, Qt.NoModifier, QPoint(300, 12))
    assert moved and 6500 < moved[0] < 8000 and 6500 < s.value() < 8000
    QTest.mouseClick(s, Qt.LeftButton, Qt.NoModifier, QPoint(40, 12))
    assert s.value() < 1500
    s.close()
