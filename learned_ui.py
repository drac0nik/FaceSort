"""Settings > Tags > "Share what FaceSort learned…" (1.48): export / import of tag and level
models without anything personal (learned.py)."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout)

import learned


class LearnedDialog(QDialog):
    def __init__(self, parent, conn, settings):
        super().__init__(parent)
        self.conn, self.settings = conn, settings
        self.changed = False
        self.setWindowTitle("Share what FaceSort learned")
        self.setMinimumSize(520, 520)
        lay = QVBoxLayout(self)
        note = QLabel("Save what FaceSort has learned about your tags (and Safe / Suggestive / Explicit) to a small "
                      "file another FaceSort - a fresh install, say - can import as a head start.<br><br>"
                      "<b>Never in the file:</b> photos, faces, people or names, folders, file names, dates - only "
                      "numbers that turn a photo's description into “how much is this a Beach photo”. "
                      "Photos in a vault are never learned from.")
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addWidget(QLabel("<b>Tags to include</b> (built-in ones are ticked; your own tags only if you tick them - "
                             "their names go in the file):"))
        self.list = QListWidget()
        try:
            rows = learned.shareable_tags(conn)
        except Exception:
            rows = []
        for name, n, builtin in rows:
            it = QListWidgetItem(f"{name}   ({n:,} photo{'s' if n != 1 else ''})" + ("" if builtin else "   · your tag"))
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if builtin else Qt.Unchecked)
            it.setData(Qt.UserRole, name)
            self.list.addItem(it)
        if not rows:
            self.list.addItem("(no tag has 5 photos yet - tag a few more first)")
        lay.addWidget(self.list, 1)
        self.cb_levels = QCheckBox("Include Safe / Suggestive / Explicit (from your corrections)")
        self.cb_levels.setChecked(True)
        lay.addWidget(self.cb_levels)
        self.state = QLabel()
        self.state.setStyleSheet("color: gray;")
        self.state.setWordWrap(True)
        lay.addWidget(self.state)
        row = QHBoxLayout()
        b_exp = QPushButton("Export…")
        b_exp.clicked.connect(self.do_export)
        b_exp.setEnabled(bool(rows) or True)
        b_imp = QPushButton("Import…")
        b_imp.clicked.connect(self.do_import)
        self.b_forget = QPushButton("Forget imported")
        self.b_forget.clicked.connect(self.do_forget)
        for b in (b_exp, b_imp, self.b_forget):
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.show_state()

    def chosen(self):
        out = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.UserRole) and it.checkState() == Qt.Checked:
                out.append(it.data(Qt.UserRole))
        return out

    def show_state(self):
        got = dict(learned.imported_summary(self.conn))
        if got:
            self.state.setText(f"Imported here: {got.get('tag', 0)} tag model(s)"
                               + (", Safe / Suggestive / Explicit" if got.get("level") else "") + ".")
        else:
            self.state.setText("Nothing imported here yet.")
        self.b_forget.setEnabled(bool(got))

    def do_export(self):
        tags = self.chosen()
        if not tags and not self.cb_levels.isChecked():
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export what FaceSort learned", "FaceSort learned.fslearn",
                                              "FaceSort learning (*.fslearn)")
        if not path:
            return
        from tagcolumn import tag_list
        self.setCursor(Qt.WaitCursor)
        try:
            res = learned.export(self.conn, path, tags, self.cb_levels.isChecked(), tag_list=tag_list(self.settings))
        except Exception as e:
            self.unsetCursor()
            QMessageBox.warning(self, "Export", f"Couldn't export: {e}")
            return
        self.unsetCursor()
        skipped = len(tags) - res["tags"]
        QMessageBox.information(self, "Export", f"Saved {res['tags']} tag model(s)"
                                + (" and Safe / Suggestive / Explicit" if res["levels"] else "") + "."
                                + (f"\n{skipped} tag(s) didn't have enough described photos (outside vaults) "
                                   "to learn from." if skipped else ""))

    def do_import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import what another FaceSort learned", "",
                                              "FaceSort learning (*.fslearn)")
        if not path:
            return
        try:
            res = learned.import_file(self.conn, path, self.settings)
        except Exception as e:
            QMessageBox.warning(self, "Import", f"Couldn't import: {e}")
            return
        self.changed = True
        self.show_state()
        QMessageBox.information(self, "Import", f"Imported {res['tags']} tag model(s)"
                                + (" and Safe / Suggestive / Explicit" if res["levels"] else "") + "."
                                + (f"\nNew in your tag list: {', '.join(res['new_tags'])}" if res["new_tags"] else ""))

    def do_forget(self):
        learned.forget_imported(self.conn)
        self.changed = True
        self.show_state()
