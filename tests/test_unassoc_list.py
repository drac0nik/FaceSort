"""1.64: Unassociated files as a Details-style list."""
import os
import struct
import time

import describe
from conftest import pump
from test_ui import window  # noqa: F401  (fixture; data_dir is in conftest)


def _lnk(path, target):
    """A minimal Windows shortcut with a LinkInfo local path."""
    t = target.encode("latin-1") + b"\0"
    li_head = 28
    li = struct.pack("<7I", li_head + len(t) + 1, li_head, 1, 0, li_head, 0, li_head + len(t)) + t + b"\0"
    head = struct.pack("<I", 0x4C) + b"\x01\x14\x02\x00\x00\x00\x00\x00\xc0\x00\x00\x00\x00\x00\x00\x46"
    head += struct.pack("<I", 0x02) + b"\0" * (0x4C - len(head) - 4)
    with open(path, "wb") as f:
        f.write(head + li)


def test_describe_every_kind(tmp_path):
    p = tmp_path
    (p / "a.url").write_text("[InternetShortcut]\r\nURL=https://www.flickr.com/photos/jane-doe/\r\n")
    (p / "b.html").write_text('<!-- saved from url=(0026)https://site.com/gallery/9 --><title>T</title>')
    (p / "c.html").write_text('<meta http-equiv="refresh" content="0; url=https://r.example/x">')
    (p / "d.html").write_text("<title>Just a title</title>")
    (p / "e.txt").write_text("\n\n  first line\nsecond")
    (p / "f.pdf").write_bytes(b"%PDF-1.4 << /Title (Quarterly \\(draft\\)) >>")
    (p / "g.zip").write_bytes(b"PK\x03\x04")
    _lnk(str(p / "h.lnk"), r"C:\Photos\Jane\set 1")
    d = {n: describe.describe(str(p / n)) for n in os.listdir(p)}
    assert d["a.url"]["desc"] == "https://www.flickr.com/photos/jane-doe/"
    assert d["b.html"]["url"] == "https://site.com/gallery/9" and d["b.html"]["title"] == "T"
    assert d["c.html"]["url"] == "https://r.example/x"
    assert d["d.html"] == {"desc": "Just a title", "url": "", "title": "Just a title"}
    assert d["e.txt"]["desc"] == "first line"
    assert d["f.pdf"]["desc"] == "Quarterly (draft)"
    assert d["g.zip"]["desc"] == "ZIP archive"
    assert d["h.lnk"]["desc"] == r"→ C:\Photos\Jane\set 1"
    assert describe.norm_url("https://WWW.flickr.com/photos/jane-doe/#x") == describe.norm_url(
        "http://flickr.com/photos/jane-doe")
    assert "jane doe" in describe.folder_tokens("https://www.flickr.com/photos/jane-doe/")


def test_unassociated_list(window, qapp, data_dir, monkeypatch):
    from PySide6.QtGui import QDesktopServices
    import store
    import unassoc_view as uv
    w = window
    folder = os.path.join(str(data_dir.lib), "Holiday 2019")
    for name, body in (("jane.url", "[InternetShortcut]\nURL=https://www.flickr.com/photos/beach-day/\n"),
                       ("jane copy.url", "[InternetShortcut]\nURL=https://flickr.com/photos/beach-day\n"),
                       ("notes.txt", "packing list\n"), ("tickets.pdf", "%PDF-1.4 /Title (Tickets)")):
        with open(os.path.join(folder, name), "w") as f:
            f.write(body)
    w.start_scan(opts={"others": True, "mode": "new", "folders": None})
    w.idle()
    store.create_category(w.conn, "Beach Day", None, None)
    w.conn.commit()
    w.show()
    w.view = ("unassoc", None)
    w.refresh_grid()
    pump(qapp, 0.3)
    v = w.unassoc_view
    assert w.center.currentWidget() is v
    end = time.time() + 10
    while time.time() < end and len(v._info) < 4:
        pump(qapp, 0.1)

    def rows():
        out = {}
        for r in range(v.proxy.rowCount()):
            ix = v.proxy.index(r, 0)
            out[ix.data()] = [v.proxy.index(r, c).data() for c in range(len(uv.COLS))] + [ix.data(uv.SAME)]
        return out
    got = rows()
    assert set(got) == {"jane", "jane copy", "notes", "tickets"}
    assert got["jane"][uv.C_EXT] == "url" and got["jane"][uv.C_LOC].endswith("Holiday 2019")
    assert got["jane"][uv.C_DESC] == "https://www.flickr.com/photos/beach-day/"
    assert got["jane"][-1] == 1 and got["jane copy"][-1] == 1 and not got["notes"][-1]   # same address
    assert got["notes"][uv.C_DESC] == "packing list" and got["tickets"][uv.C_DESC] == "Tickets"
    assert got["notes"][uv.C_SIZE]
    # filters
    v.kind.setCurrentIndex(v.kind.findData("links"))
    assert set(rows()) == {"jane", "jane copy"}
    v.kind.setCurrentIndex(v.kind.findData("same"))
    assert set(rows()) == {"jane", "jane copy"}
    v.kind.setCurrentIndex(0)
    v.find.setText("packing")
    assert set(rows()) == {"notes"}
    v.find.clear()
    # the Location column is shortened from the left
    assert isinstance(v.view.itemDelegateForColumn(uv.C_LOC), uv.LeftElide)
    # picking rows picks them in the gallery
    r = next(i for i in range(v.proxy.rowCount()) if v.proxy.index(i, 0).data() == "jane")
    v.view.selectionModel().select(v.proxy.index(r, 0), v.view.selectionModel().SelectionFlag.ClearAndSelect
                                   | v.view.selectionModel().SelectionFlag.Rows)
    pump(qapp, 0.1)
    assert len(w.sel_photos()) == 1
    assert w.selbar.isVisibleTo(w)
    # the folder named in the address is offered
    sugg = v.folder_suggestions([(None, "https://www.flickr.com/photos/beach-day/", "")])
    assert sugg and sugg[0][1] == "Beach Day"
    # double-click: the address in the default browser
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda u: opened.append(u.toString()) or True)
    v.open_row(v.proxy.index(r, 0))
    assert opened == ["https://www.flickr.com/photos/beach-day/"]
    # grouped by type
    v.group_box.setChecked(True)
    pump(qapp, 0.1)
    heads = [v.proxy.index(i, 0).data() for i in range(v.proxy.rowCount())]
    assert heads[0].startswith("Links and web pages") and any(h.startswith("Documents") for h in heads)
    v.group_box.setChecked(False)
    # columns are remembered
    v.view.header().resizeSection(uv.C_LOC, 222)
    v.save_layout()
    assert w.settings.get("unassoc_header")
