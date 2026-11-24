"""1.58.3: Sort by type (Micro / Macro)."""
import os

import filetypes
import libmove
import store
from conftest import write_photo
from test_import import pid_of
from test_ui import window  # noqa: F401


def test_types():
    m = {p: filetypes.macro(p) for p in ("a.JPG", "b.jpeg", "c.png", "d.webp", "e.gif", "f.mov", "g.mp4", "h.mkv",
                                         "i.docx", "j.xlsx", "k.pptx", "l.pdf", "m.url", "n.zip", "o.txt",
                                         "p.mp3", "q.cr2", "r.xyz")}
    assert [m[k] for k in ("a.JPG", "b.jpeg", "c.png", "d.webp")] == ["Images"] * 4
    assert m["e.gif"] == "GIF" and m["f.mov"] == m["g.mp4"] == m["h.mkv"] == "Video"
    assert m["i.docx"] == m["j.xlsx"] == m["k.pptx"] == "Office documents"
    assert (m["l.pdf"], m["m.url"], m["n.zip"], m["o.txt"], m["p.mp3"], m["q.cr2"], m["r.xyz"]) == \
        ("PDF", "Web links", "Archives", "Text", "Audio", "RAW photos", "Other files")
    assert filetypes.micro("a.JPEG") == filetypes.micro("b.jpg") == "JPG" and filetypes.micro("x.Mp4") == "MP4"


def test_gallery_sorted_by_type(window, tmp_path):
    w, c = window, window.conn
    lib = tmp_path / "My Library"
    lib.mkdir()
    w.settings["library_root"] = store.norm_path(str(lib))
    d = tmp_path / "Mix"
    write_photo(str(d / "Sub" / "a.jpg"), 1301)
    write_photo(str(d / "b.jpg"), 1302)
    from PIL import Image
    Image.new("RGB", (40, 30), "red").save(str(d / "c.png"))
    Image.new("RGB", (40, 30), "blue").save(str(d / "d.gif"))
    open(str(d / "notes.txt"), "w").write("n")
    open(str(d / "plan.pdf"), "wb").write(b"%PDF")
    open(str(d / "sheet.xlsx"), "wb").write(b"PK")
    assert w.handle_drop([str(d)], answers={"folders": [(os.path.join(str(d), "Sub"), "person", "Ann")]})
    w.idle()
    batch = c.execute("SELECT dropped FROM photos WHERE path LIKE '%notes.txt'").fetchone()[0]
    rows, _ = w.dropped_rows(batch)
    heads = lambda rs: [r[1].split("   ·   ")[0] for r in rs if r[0] == 0]
    w.settings["grid_sort"] = "type_macro"
    macro = w.sort_rows(rows)
    assert heads(macro) == ["Images", "GIF", "Office documents", "PDF", "Text"]
    assert macro[1:3] and all(r[0] for r in macro[1:3]) and sum(1 for r in macro if r[0]) == len({r[0] for r in rows if r[0]})
    w.settings["grid_sort"] = "type_micro"
    assert heads(w.sort_rows(rows)) == ["JPG", "PNG", "GIF", "XLSX", "PDF", "TXT"]
    w.update_sort_button()
    assert w.sort_btn.text() == "Sort: Type (Micro)"
    w.settings["grid_sort"] = "default"
