"""The Export… window (1.53)."""
import os

from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton, QSpinBox,
                               QVBoxLayout, QWidget)

import engine
import exporter


def _default_dest():
    home = os.path.expanduser("~")
    pics = os.path.join(home, "Pictures")
    return os.path.join(pics if os.path.isdir(pics) else home, "FaceSort exports")


class ExportFilesDialog(QDialog):
    def __init__(self, win, pids, label):
        super().__init__(win)
        st = win.settings
        self.win = win
        pids = list(pids)
        paths = []
        for i in range(0, len(pids), 900):
            ch = list(pids[i:i + 900])
            paths += [r[0] for r in win.conn.execute(f"SELECT path FROM photos WHERE id IN ({','.join('?' * len(ch))})",
                                                     ch)]
        nv = sum(1 for p in paths if engine.is_video(p))
        npx = len(paths) - nv
        self.setWindowTitle("Export")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        what = [f"{npx:,} photo{'s' if npx != 1 else ''}"] if npx else []
        if nv:
            what.append(f"{nv:,} video{'s' if nv != 1 else ''}")
        head = QLabel(f"<b>Export {' and '.join(what) or 'nothing'}</b> from {label}")
        lay.addWidget(head)
        form = QFormLayout()
        row = QHBoxLayout()
        self.dest = QLineEdit(st.get("files_export_dest") or _default_dest())
        b = QPushButton("Browse…")
        b.clicked.connect(self.browse)
        row.addWidget(self.dest, 1)
        row.addWidget(b)
        form.addRow("Save in:", row)
        self.name = QLineEdit(label)
        form.addRow("Name:", self.name)
        # size
        size = QWidget()
        sl = QVBoxLayout(size)
        sl.setContentsMargins(0, 0, 0, 0)
        self.size_group = QButtonGroup(self)
        self.rb_orig = QRadioButton("Original size")
        r1 = QHBoxLayout()
        self.rb_pct = QRadioButton("Percentage:")
        self.pct = QSpinBox()
        self.pct.setRange(1, 100)
        self.pct.setSuffix(" %")
        self.pct.setValue(int(st.get("files_export_pct", 50)))
        r1.addWidget(self.rb_pct)
        r1.addWidget(self.pct)
        r1.addStretch()
        r2 = QHBoxLayout()
        self.rb_max = QRadioButton("Fit inside:")
        self.max_w = QSpinBox()
        self.max_h = QSpinBox()
        for s, key, dflt in ((self.max_w, "files_export_w", 1920), (self.max_h, "files_export_h", 1920)):
            s.setRange(16, 20000)
            s.setSuffix(" px")
            s.setValue(int(st.get(key, dflt)))
        r2.addWidget(self.rb_max)
        r2.addWidget(self.max_w)
        r2.addWidget(QLabel("×"))
        r2.addWidget(self.max_h)
        r2.addStretch()
        for i, rb in enumerate((self.rb_orig, self.rb_pct, self.rb_max)):
            self.size_group.addButton(rb, i)
        sl.addWidget(self.rb_orig)
        sl.addLayout(r1)
        sl.addLayout(r2)
        mode = st.get("files_export_size", "original")
        {"percent": self.rb_pct, "max": self.rb_max}.get(mode, self.rb_orig).setChecked(True)
        form.addRow("Size:", size)
        note = QLabel("Never made bigger than the original. Width and height keep the picture's shape.")
        note.setStyleSheet("color: gray;")
        form.addRow("", note)
        # folder / zip
        kind = QWidget()
        kl = QHBoxLayout(kind)
        kl.setContentsMargins(0, 0, 0, 0)
        self.rb_folder = QRadioButton("A folder")
        self.rb_zip = QRadioButton("One compressed zip file")
        (self.rb_zip if st.get("files_export_zip") else self.rb_folder).setChecked(True)
        kl.addWidget(self.rb_folder)
        kl.addWidget(self.rb_zip)
        kl.addStretch()
        form.addRow("Save as:", kind)
        self.vnote = QLabel("Videos are made smaller in a zip (H.264, at most 1280 px).")
        self.vnote.setStyleSheet("color: gray;")
        self.vnote.setVisible(nv > 0)
        form.addRow("", self.vnote)
        self.strip = QCheckBox("Strip metadata (EXIF: camera, date, GPS location · names and tags written in files)")
        self.strip.setChecked(bool(st.get("files_export_strip", True)))
        form.addRow("", self.strip)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = bb.button(QDialogButtonBox.Ok)
        self.ok.setText("Export")
        self.ok.setEnabled(bool(paths))
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def browse(self):
        d = QFileDialog.getExistingDirectory(self, "Save the export in…", self.dest.text())
        if d:
            self.dest.setText(os.path.normpath(d))

    def options(self):
        size = {0: "original", 1: "percent", 2: "max"}[self.size_group.checkedId()]
        return {"dest": os.path.normpath(self.dest.text().strip() or _default_dest()),
                "name": self.name.text().strip() or "FaceSort export", "size": size, "percent": self.pct.value(),
                "max_w": self.max_w.value(), "max_h": self.max_h.value(), "zip": self.rb_zip.isChecked(),
                "strip": self.strip.isChecked()}

    def accept(self):
        o = self.options()
        st = self.win.settings
        st["files_export_dest"] = o["dest"]
        st["files_export_size"] = o["size"]
        st["files_export_pct"] = o["percent"]
        st["files_export_w"], st["files_export_h"] = o["max_w"], o["max_h"]
        st["files_export_zip"] = o["zip"]
        st["files_export_strip"] = o["strip"]            # remembered: the next export starts from your choice
        super().accept()


def _size(n):
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.0f} {unit}" if unit == "bytes" else f"{n:,.1f} {unit}"
        n /= 1024


def run_export(win, pids, label):
    from ui_common import open_file, show_in_folder
    if not pids:
        return
    dlg = ExportFilesDialog(win, pids, label)
    if dlg.exec() != QDialog.Accepted:
        return
    opts = dlg.options()

    def done(st):
        if not st:
            return
        if not st.get("out"):
            win.statusBar().showMessage("Export stopped.", 6000)
            return
        parts = []
        if st["photos"]:
            parts.append(f"{st['photos']:,} photo{'s' if st['photos'] != 1 else ''}")
        if st["videos"]:
            parts.append(f"{st['videos']:,} video{'s' if st['videos'] != 1 else ''}")
        msg = f"Exported {' and '.join(parts) or 'nothing'} ({_size(st['bytes'])}) to:\n{st['out']}"
        if st["errors"]:
            msg += f"\n\n{len(st['errors'])} couldn't be exported:\n" + "\n".join(
                f"• {os.path.basename(p)}: {e}" for p, e in st["errors"][:8])
        box = QMessageBox(win)
        box.setWindowTitle("Export")
        box.setText(msg)
        op = box.addButton("Show it", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Ok)
        box.exec()
        if box.clickedButton() is op:
            if os.path.isdir(st["out"]):
                open_file(st["out"])
            else:
                show_in_folder(st["out"])

    if not win.run_job("exportfiles", exporter.export, list(pids), opts, on_done=done):
        QMessageBox.information(win, "Export", "Wait for the current job to finish first.")
