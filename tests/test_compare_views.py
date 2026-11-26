"""1.59.1: the Name window shows what's being named beside the highlighted person (close up + whole
photo, click a small picture to see it big); Quick review switches to whole photos and swaps faces."""
from conftest import pump
from test_ui import window  # noqa: F401


def test_name_window_compares(window, qapp):
    from viewer_parts import PersonPicker
    w, c = window, window.conn
    fids = [r[0] for r in c.execute("SELECT id FROM faces WHERE ignored=0 ORDER BY id LIMIT 3")]
    dlg = PersonPicker(w, "Name 3 faces", faces=fids)
    assert dlg.width() >= 1000
    assert [i[0] for i in dlg.tagging.items] == fids and dlg.tagging.isVisibleTo(dlg)
    assert dlg.tagging.strip.count() >= 3                  # the small pictures along the bottom
    dlg.tagging.set_main(2)
    assert dlg.tagging.cur == 2
    assert not dlg.tagging.frame.pixmap().isNull()         # the whole photo
    # highlight a known person: their faces, the clicked one shown big
    row = next(i for i in range(dlg.list.count())
               if (dlg.list.item(i).data(0x0100) or ("",))[0] != "new"
               and dlg.list.item(i).data(0x0100)[1][1])
    dlg.list.setCurrentRow(row)
    dlg.show_person(row)
    if len(dlg.person.items) > 1:
        dlg.person.set_main(1)
        assert dlg.person.cur == 1
    dlg.reject()
    photos = [r[0] for r in c.execute("SELECT id FROM photos ORDER BY id LIMIT 2")]
    dlg = PersonPicker(w, "Who's in these 2 photos?", photos=photos)
    assert len(dlg.tagging.items) == 2 and dlg.tagging.items[0][0] is None
    dlg.reject()
    dlg = PersonPicker(w, "Name")                           # nothing given: no Naming panel
    assert not dlg.tagging.items and not dlg.tagging.isVisibleTo(dlg)
    dlg.reject()
    pump(qapp, 0.2)
    w.idle()


def test_quick_review_whole_photo_and_swap(window, qapp):
    from types import SimpleNamespace
    w, c = window, window.conn
    w.resize(1300, 850)
    w.quick_review()
    pump(qapp, 0.3)
    r = w.review
    fids = [x[0] for x in c.execute("SELECT id FROM faces WHERE ignored=0 ORDER BY id LIMIT 4")]
    a = SimpleNamespace(kind="group", name="", size=3, show=fids[:3])
    b = SimpleNamespace(kind="person", name="Alice", size=2, show=fids[3:])
    r.q = SimpleNamespace(a=a, b=b)
    for s in (r.left, r.right):
        s.setVisible(True)
    r.left.show_unit(a)
    r.right.show_unit(b)
    assert r.left.row.count() >= 3                          # small faces, clickable
    r.left.pick(2)
    assert r.left.main_i == 2
    r.left.show_unit(a)                                     # (a redraw keeps the one picked)
    assert r.left.main_i == 2
    face = r.left.big.pixmap().toImage()
    r.toggle_full()
    assert r.left.full and r.right.full and r.full_b.isChecked()
    pump(qapp, 0.2)
    assert r.left.big.pixmap().toImage() != face            # the whole photo now
    r.left.big.mousePressEvent(_click())                    # clicking the big picture switches back
    assert not r.left.full and not r.full_b.isChecked()
    r.left.show_unit(SimpleNamespace(kind="single", name="", size=1, show=fids[:1]))
    assert r.left.main_i == 0                               # a new question starts at its first face
    w.close_review()
    pump(qapp, 0.3)
    w.idle()


def _click():
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent
    return QMouseEvent(QEvent.MouseButtonPress, QPointF(5, 5), QPointF(5, 5), Qt.LeftButton, Qt.LeftButton,
                       Qt.NoModifier)
