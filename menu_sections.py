"""The right-click menu in sections (1.63): what this view needs first, then naming and tags, faces,
organising, editing, files, opening - each under a small heading, only when it has something in it.
The menu is built as before (every action, on the same conditions); this sorts it."""
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QLineEdit, QWidgetAction

SECTIONS = [
    ("view", "In this view", [r"^Keep this copy", r"^Not a duplicate", r"^Merge these two", r"^Not the same person",
                              r"^Yes - tag", r"^No - not", r"^Add to album", r"^Not for this album",
                              r"^Remove from album", r"^Restore", r"^Not AI \(take off", r"^Put ", r"^Not part of"]),
    ("tag", "Name and tag", [r"^Name ", r"^Assign to", r"^Tag ", r"^Yes, it's them", r"^Not this person",
                             r"^Doesn't belong", r"^Unassign", r"^Remove tag", r"generic name"]),
    ("faces", "Faces", [r"^Ignore", r"^Not a face", r"^Never track", r"^Looks"]),
    ("organise", "Organise", [r"^Put in folder “", r"^Set folder", r"^Move to folder", r"^Create Folder", r"^Sort later",
                              r"^Take out of Sort Later", r"^Discard"]),
    ("edit", "Edit", [r"^Turn", r"^Make a JPEG", r"^Make a low light", r"^Content", r"^Safe", r"^Not AI - take off"]),
    ("files", "Files", [r"^Copy address", r"^Copy path", r"^Rename", r"^Export", r"^Contact sheet", r"^Rescan", r"^Make a 12-shot", r"^Save the best",
                        r"^Leave out of duplicate", r"^Check for duplicates"]),
    ("open", "Open", [r"^Open in browser", r"^Play video", r"^Open photo", r"^Open window to this image", r"^Show in folder"]),
]


def _section_of(text):
    t = text.replace("&", "").split("\t")[0]
    for key, _title, pats in SECTIONS:
        if any(re.search(p, t) for p in pats):
            return key
    return "view"                      # (anything else is this view's own)


def heading(menu, text, color=None):
    lb = QLabel(text.upper())
    lb.setObjectName("sectionlabel")
    lb.setContentsMargins(12, 8, 12, 2)
    if color:
        lb.setStyleSheet(f"color: {color};")
    wa = QWidgetAction(menu)
    wa.setDefaultWidget(lb)
    wa.setEnabled(False)
    return wa


def organize(menu, title=None):
    """Re-order menu's actions into the sections, with headings; title: a line at the top saying what was
    right-clicked."""
    acts = [a for a in menu.actions() if not a.isSeparator()]
    by = {}
    for a in acts:
        text = a.menu().title() if a.menu() is not None else a.text()
        if text.startswith("Open window to this image"):
            a.setText(a.text().replace("Open window to this image", "Show in folder"))
        by.setdefault(_section_of(text), []).append(a)
        if a.menu() is not None and a.menu().title() == "Assign to":
            add_search(a.menu())
    for a in menu.actions():
        menu.removeAction(a)
    if title:
        t = QLabel(title)
        t.setStyleSheet("font-weight: 600; padding: 6px 12px 8px;")
        wa = QWidgetAction(menu)
        wa.setDefaultWidget(t)
        wa.setEnabled(False)
        menu.addAction(wa)
    first = True
    for key, name, _p in SECTIONS:
        items = by.get(key)
        if not items:
            continue
        if not first:
            menu.addSeparator()
        first = False
        menu.addAction(heading(menu, name, "#F2B544" if key == "view" else None))
        for a in items:
            menu.addAction(a)
    return menu


def add_search(sub):
    """Assign to: a box at the top that narrows the names as you type."""
    box = QLineEdit()
    box.setPlaceholderText("Find a person…")
    box.setClearButtonEnabled(True)
    box.setMinimumWidth(200)
    wa = QWidgetAction(sub)
    wa.setDefaultWidget(box)
    first = sub.actions()[0] if sub.actions() else None
    sub.insertAction(first, wa)

    def narrow(text):
        t = text.casefold().strip()
        for a in sub.actions():
            if a is wa or a.isSeparator():
                continue
            a.setVisible(not t or t in a.text().casefold() or a.text().startswith("Everyone else"))
    box.textChanged.connect(narrow)
    sub.aboutToShow.connect(lambda: box.setFocus(Qt.PopupFocusReason))
