"""1.63: the new look - people waiting for you first, smart groups overview, the selection bar,
the right-click menu in sections, the theme setting."""
import store
import theme
from conftest import pump
from test_ui import all_keys, window  # noqa: F401  (the window fixture)


def people_order(w):
    names = dict(w.conn.execute("SELECT id, name FROM persons"))
    return [names[k[1]] for k in all_keys(w, w.tree) if k[0] == "person"]


def test_pending_people_first_then_az(window, qapp):
    w = window
    c = w.conn
    c.execute("UPDATE faces SET confirmed=1 WHERE person_id IS NOT NULL")   # nothing waiting
    c.execute("DELETE FROM tattoo_suggestions")
    c.commit()
    w.rebuild_tree()
    pump(qapp, 0.2)
    before = people_order(w)
    assert before == sorted(before, key=str.casefold)
    carol = c.execute("SELECT id FROM persons WHERE name='Carol'").fetchone()[0]
    fid = c.execute("SELECT id FROM faces WHERE person_id=? LIMIT 1", (carol,)).fetchone()[0]
    c.execute("UPDATE faces SET confirmed=0 WHERE id=?", (fid,))     # a suggestion waiting
    c.commit()
    w.rebuild_tree()
    pump(qapp, 0.2)
    assert people_order(w)[0] == "Carol"
    c.execute("UPDATE faces SET confirmed=1 WHERE id=?", (fid,))     # confirmed: back in A-Z
    c.commit()
    w.rebuild_tree()
    pump(qapp, 0.2)
    assert people_order(w) == before


def test_smart_overview_and_pairs(window, qapp):
    w = window
    w.rebuild_tree()
    pump(qapp, 0.2)
    w.show_view(("smart_home", None))
    pump(qapp, 0.2)
    assert w.center.currentWidget() is w.smart_home
    assert not w.selbar.isVisible()
    w.show_view(("same", None))
    pump(qapp, 0.2)
    assert w.center.currentWidget() is w.pairs_page


def test_selection_bar(window, qapp):
    w = window
    alice = w.conn.execute("SELECT id FROM persons WHERE name='Alice'").fetchone()[0]
    w.show()
    w.show_view(("person", alice))
    pump(qapp, 0.3)
    w.grid.selectAll()
    pump(qapp, 0.2)
    w.update_hint()
    assert w.selbar.isVisibleTo(w)
    texts = [b.text() for b in w.selbar.buttons]
    assert texts[0].startswith("Name") and any(t.startswith("Not a face") for t in texts)
    assert texts[-1] == "More…"
    w.grid.clearSelection()
    pump(qapp, 0.2)
    w.update_hint()
    assert not w.selbar.isVisibleTo(w)


def test_menu_sections(qapp):
    from PySide6.QtWidgets import QMenu
    import menu_sections
    m = QMenu()
    for t in ("Open photo", "Name these…", "Discard", "Not a face   (N)", "Rename…", "Keep this copy",
              "Open window to this image"):
        m.addAction(t)
    m.addSeparator()
    sub = m.addMenu("Assign to")
    sub.addAction("Alice")
    sub.addAction("Bob")
    menu_sections.organize(m, "2 faces")
    texts = [a.text() for a in m.actions() if not a.isSeparator() and a.text()]
    order = [t for t in texts]
    assert order.index("Keep this copy") < order.index("Name these…") < order.index("Not a face   (N)") \
        < order.index("Discard") < order.index("Rename…") < order.index("Open photo")
    assert "Show in folder" in texts and "Open window to this image" not in texts
    heads = [a.defaultWidget().text() for a in m.actions() if hasattr(a, "defaultWidget") and a.defaultWidget()]
    assert heads[0] == "2 faces" and "IN THIS VIEW" in heads and "OPEN" in heads
    # Assign to: a find box that narrows the names
    from PySide6.QtWidgets import QLineEdit
    box = next(a.defaultWidget() for a in sub.actions() if hasattr(a, "defaultWidget") and a.defaultWidget())
    assert isinstance(box, QLineEdit)
    box.setText("bo")
    vis = [a.text() for a in sub.actions() if a.isVisible() and a.text()]
    assert vis == ["Bob"]


def test_theme_setting():
    assert theme.current({}) == "graphite"
    assert theme.current({"theme": "classic"}) == "classic"
    assert theme.current({"theme": "nonsense"}) == "graphite"
    assert "QToolBar#header" in theme.stylesheet()
    s = store.load_settings()
    assert theme.current(s) in theme.THEMES
