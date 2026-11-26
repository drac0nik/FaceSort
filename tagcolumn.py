"""Recommended tags (1.44): a column beside the photo in the viewer with up to 10 tag buttons.
Click once to add a tag (it's highlighted), again to take it off. A tag is an album of the same
name - made the first time - and its name is written into the file as a keyword. The tags it
suggests come from what's in the photo (content.py) and, for your own tags and albums, from the
photos you've put in them."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QInputDialog, QLabel, QMenu, QPushButton, QSizePolicy, QToolButton,
                               QVBoxLayout)

import content
import store

MAX_TAGS = 10
import pose  # noqa: E402

DEFAULT_TAGS = [{"name": t, "menu": True} for t in content.CONTENT_TAGS + content.SCENE_TAGS + pose.POSES]
ON_STYLE = ("QPushButton { background: #2563eb; color: white; border: 1px solid #1d4ed8; border-radius: 12px;"
            " padding: 4px 10px; font-weight: bold; text-align: left; }"
            "QPushButton:hover { background: #1d4ed8; }")
OFF_STYLE = ("QPushButton { background: transparent; border: 1px solid palette(mid); border-radius: 12px;"
             " padding: 4px 10px; text-align: left; }"
             "QPushButton:hover { border-color: #2563eb; }")


def tag_list(settings):
    """[{"name", "menu"}] - the tags FaceSort suggests ("menu" = suggest it)."""
    from ui_common import generic_names
    raw = settings.get("tag_list")
    if not isinstance(raw, list):
        return [dict(t) for t in DEFAULT_TAGS]
    if not settings.get("pose_tags_added"):
        # (1.46) the pose tags join a list you've already edited - once (delete them and they stay gone)
        have = {(t.get("name") if isinstance(t, dict) else str(t)).casefold() for t in raw}
        raw.extend({"name": p, "menu": True} for p in pose.POSES if p.casefold() not in have)
        settings["pose_tags_added"] = True
    return generic_names({"generic_names": raw})


_WARM = {"key": None, "scorer": None, "busy": None, "waiters": []}


def _scorer_key(win):
    return (repr(tag_list(win.settings)), win.conn.execute("SELECT COUNT(*) FROM albums").fetchone()[0],
            bool(win.settings.get("content_on")))


def warm_scorer(win, on_ready=None):
    """(1.61) The shared tag scorer if it's ready for the current tags and albums; otherwise it's made on a
    background thread (with its own connection) and on_ready is called once it's there."""
    import threading
    from PySide6.QtCore import QTimer
    key = _scorer_key(win)
    if _WARM["key"] == key and _WARM["scorer"] is not None:
        _WARM["scorer"].conn = win.conn
        return _WARM["scorer"]
    if on_ready is not None:
        _WARM["waiters"].append(on_ready)
    if _WARM["busy"] == key:
        return None
    _WARM["busy"] = key
    names = [t["name"] for t in tag_list(win.settings) if t["menu"]]
    box = {}

    def work():
        conn = None
        try:
            conn = store.connect()
            sc = content.TagScorer(conn, names)
            if sc.clip:
                sc._baseline()
                for name in list(sc.albums):
                    sc._album_model(name)
            box["scorer"] = sc
        except Exception as e:
            box["error"] = e
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    th = threading.Thread(target=work, daemon=True, name="tag scorer")
    th.start()

    def check():
        if th.is_alive():
            QTimer.singleShot(150, check)
            return
        _WARM["busy"] = None
        sc = box.get("scorer")
        if sc is None:
            from ui_common import log
            log(f"tag suggestions: {box.get('error')}")
            sc = content.TagScorer(win.conn, names)     # (made here after all - without the head start)
        sc.conn = win.conn
        _WARM["key"], _WARM["scorer"] = key, sc
        waiters, _WARM["waiters"] = _WARM["waiters"], []
        for cb in waiters:
            try:
                cb()
            except RuntimeError:                        # (the viewer closed meanwhile)
                pass
    QTimer.singleShot(150, check)
    return None


class TagColumn(QFrame):
    def __init__(self, viewer):
        super().__init__()
        self.viewer = viewer
        self.win = viewer.win
        self.photo = None
        self.setFixedWidth(170)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 6, 6)
        lay.setSpacing(5)
        head = QLabel("Tags")
        f = head.font()
        f.setBold(True)
        head.setFont(f)
        head.setToolTip("Recommended tags. Click to add (highlighted), click again to take it off. Each tag is "
                        "also an album, and is written into the file as a keyword.")
        lay.addWidget(head)
        self.level = QToolButton()                 # Safe / Suggestive / Explicit, with the correction menu
        self.level.setPopupMode(QToolButton.InstantPopup)
        self.level.setToolTip("How explicit FaceSort thinks this photo is - click to correct it")
        self.level.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        lay.addWidget(self.level)
        self.btns = []
        for _ in range(MAX_TAGS):
            b = QPushButton()
            b.setCheckable(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.setAutoDefault(False)
            b.clicked.connect(lambda on, b=b: self.toggle(b, on))
            b.hide()
            lay.addWidget(b)
            self.btns.append(b)
        self.empty = QLabel("No suggestions yet")
        self.empty.setStyleSheet("color: gray;")
        self.empty.setWordWrap(True)
        lay.addWidget(self.empty)
        lay.addStretch(1)
        add = QPushButton("＋ Add a tag…")
        add.setFocusPolicy(Qt.NoFocus)
        add.setAutoDefault(False)
        add.clicked.connect(self.add_tag)
        lay.addWidget(add)
        self._scorer = None
        self._scorer_key = None

    def scorer(self):
        """The tag scorer, made ready in the background (1.61: loading the description model and training the
        album models used to freeze the viewer for a second or two on the first photo). None until ready."""
        s = warm_scorer(self.win, on_ready=lambda: self.photo is not None and self.isVisible()
                        and self.refresh(self.photo))
        return s

    def refresh(self, photo_id):
        self.photo = photo_id
        show = bool(self.win.settings.get("tag_column", True)) and photo_id is not None
        self.setVisible(show)
        if not show:
            return
        c = self.win.conn
        have = store.photo_tags(c, photo_id)
        try:
            sc = self.scorer()
            names = sc.suggest(photo_id, have, MAX_TAGS) if sc is not None else have[:MAX_TAGS]
        except Exception as e:                     # (a model missing or a database hiccup: just what it has)
            from ui_common import log
            log(f"tag suggestions: {e}")
            names = have[:MAX_TAGS]
        for b, name in zip(self.btns, names + [None] * MAX_TAGS):
            if name is None:
                b.hide()
                continue
            on = name in have
            b.setText(("✓ " if on else "") + name)
            b.setProperty("tag", name)
            b.setChecked(on)
            b.setStyleSheet(ON_STYLE if on else OFF_STYLE)
            b.setToolTip(f"Take the “{name}” tag off" if on else f"Tag this photo “{name}” (adds it to that album)")
            b.show()
        self.empty.setVisible(not names)
        row = c.execute("SELECT level, level_set FROM photo_content WHERE photo_id=?", (photo_id,)).fetchone()
        if self.win.settings.get("content_on") and row and row[0] is not None:
            self.level.setText(content.LEVELS.get(row[0], "?") + (" ✓" if row[1] is not None else "") + "  ▾")
            m = QMenu(self.level)
            self.win.content_menu(m, [photo_id])
            sub = m.actions()[0].menu() if m.actions() else None
            self.level.setMenu(sub or m)
            self._level_menu = m
            self.level.show()
        else:
            self.level.hide()

    def toggle(self, b, on):
        name = b.property("tag")
        if not name or self.photo is None:
            return
        store.tag_photo(self.win.conn, self.photo, name, on)
        self.viewer.toast.setText(f"Tagged “{name}”" if on else f"“{name}” taken off")
        self.win.after_change(regroup=False, defer=True)
        self.refresh(self.photo)

    def add_tag(self):
        if self.photo is None:
            return
        name, ok = QInputDialog.getText(self, "Add a tag", "Tag (also an album of that name):")
        name = " ".join((name or "").split())
        if ok and name:
            store.tag_photo(self.win.conn, self.photo, name, True)
            tags = tag_list(self.win.settings)
            if name.casefold() not in {t["name"].casefold() for t in tags}:
                tags.append({"name": name, "menu": True})        # suggested from now on
                self.win.settings["tag_list"] = tags
                store.save_settings(self.win.settings)
            self.win.after_change(regroup=False, defer=True)
            self.refresh(self.photo)
