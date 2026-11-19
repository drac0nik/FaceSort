"""The Contact sheet… window (1.53): title, a font installed on your PC, layout, PDF or JPEG."""
import os

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFontComboBox, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton, QSpinBox,
                               QVBoxLayout, QWidget)

import contactsheet


class ContactSheetDialog(QDialog):
    def __init__(self, win, n, title):
        super().__init__(win)
        st = win.settings
        self.win = win
        self.setWindowTitle("Contact sheet")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"<b>A contact sheet of {n:,} photo{'s' if n != 1 else ''}</b>"))
        form = QFormLayout()
        self.title = QLineEdit(title or "")
        self.title.setPlaceholderText("No title")
        form.addRow("Title:", self.title)
        row = QHBoxLayout()
        self.font = QFontComboBox()                       # every font installed on this PC
        if st.get("sheet_font"):
            self.font.setCurrentFont(QFont(st["sheet_font"]))
        self.bold = QCheckBox("Bold")
        self.bold.setChecked(bool(st.get("sheet_bold", False)))
        self.italic = QCheckBox("Italic")
        self.italic.setChecked(bool(st.get("sheet_italic", False)))
        row.addWidget(self.font, 1)
        row.addWidget(self.bold)
        row.addWidget(self.italic)
        form.addRow("Font:", row)
        self.preview = QLabel()
        self.preview.setMinimumHeight(64)
        self.preview.setStyleSheet("background: white; color: #19191c; padding: 6px 12px; border: 1px solid #ccc;")
        form.addRow("", self.preview)
        lay.addLayout(form)
        form2 = QFormLayout()
        self.page = QComboBox()
        for key, label, _ps, _l in contactsheet.PAGES:
            self.page.addItem(label, key)
        self.page.setCurrentIndex(max(0, self.page.findData(st.get("sheet_page", "a4p"))))
        form2.addRow("Page:", self.page)
        self.cols = QSpinBox()
        self.cols.setRange(0, 12)
        self.cols.setSpecialValueText("as many as fit nicely")
        self.cols.setValue(int(st.get("sheet_cols", 0)))
        form2.addRow("Photos across:", self.cols)
        opts = QWidget()
        ol = QHBoxLayout(opts)
        ol.setContentsMargins(0, 0, 0, 0)
        self.captions = QCheckBox("File names under the photos")
        self.captions.setChecked(bool(st.get("sheet_captions", False)))
        self.dark = QCheckBox("Dark background")
        self.dark.setChecked(bool(st.get("sheet_dark", False)))
        ol.addWidget(self.captions)
        ol.addWidget(self.dark)
        ol.addStretch()
        form2.addRow("", opts)
        fmt = QWidget()
        fl = QHBoxLayout(fmt)
        fl.setContentsMargins(0, 0, 0, 0)
        self.rb_pdf = QRadioButton("PDF")
        self.rb_jpg = QRadioButton("JPEG (one picture per page)")
        (self.rb_jpg if st.get("sheet_fmt") == "jpg" else self.rb_pdf).setChecked(True)
        fl.addWidget(self.rb_pdf)
        fl.addWidget(self.rb_jpg)
        fl.addStretch()
        form2.addRow("Save as:", fmt)
        prow = QHBoxLayout()
        home = os.path.expanduser("~")
        docs = os.path.join(home, "Pictures") if os.path.isdir(os.path.join(home, "Pictures")) else home
        self.dest = QLineEdit(st.get("sheet_dest") or docs)
        b = QPushButton("Browse…")
        b.clicked.connect(self.browse)
        prow.addWidget(self.dest, 1)
        prow.addWidget(b)
        form2.addRow("Save in:", prow)
        lay.addLayout(form2)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Make contact sheet")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        for sig in (self.font.currentFontChanged, self.bold.toggled, self.italic.toggled, self.title.textChanged):
            sig.connect(self.update_preview)
        self.update_preview()

    def update_preview(self, *_):
        f = QFont(self.font.currentFont())
        f.setPixelSize(34)
        f.setBold(self.bold.isChecked())
        f.setItalic(self.italic.isChecked())
        self.preview.setFont(f)
        self.preview.setText(self.title.text() or "No title")

    def browse(self):
        d = QFileDialog.getExistingDirectory(self, "Save the contact sheet in…", self.dest.text())
        if d:
            self.dest.setText(os.path.normpath(d))

    def options(self):
        import renamer
        dest = os.path.normpath(self.dest.text().strip() or os.path.expanduser("~"))
        base = renamer.clean(f"{self.title.text().strip() or 'Photos'} - contact sheet")
        fmt = "jpg" if self.rb_jpg.isChecked() else "pdf"
        out, i = os.path.join(dest, base), 2
        while os.path.exists(out + "." + fmt) or os.path.exists(out + " 1." + fmt):
            out = os.path.join(dest, f"{base} ({i})")
            i += 1
        return {"out": out, "fmt": fmt, "title": self.title.text().strip(), "font": self.font.currentFont().family(),
                "bold": self.bold.isChecked(), "italic": self.italic.isChecked(), "cols": self.cols.value(),
                "page": self.page.currentData(), "captions": self.captions.isChecked(), "dark": self.dark.isChecked(),
                "dest": dest}

    def accept(self):
        o = self.options()
        st = self.win.settings
        st.update(sheet_font=o["font"], sheet_bold=o["bold"], sheet_italic=o["italic"], sheet_page=o["page"],
                  sheet_cols=o["cols"], sheet_captions=o["captions"], sheet_dark=o["dark"], sheet_fmt=o["fmt"],
                  sheet_dest=o["dest"])
        try:
            os.makedirs(o["dest"], exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "Contact sheet", f"Can't use that folder:\n{e}")
            return
        super().accept()


def run_contact_sheet(win, pids, title):
    from ui_common import open_file, show_in_folder
    if not pids:
        return
    paths = []
    pids = list(pids)
    for i in range(0, len(pids), 900):
        ch = list(pids[i:i + 900])
        got = dict(win.conn.execute(f"SELECT id, path FROM photos WHERE id IN ({','.join('?' * len(ch))})", ch))
        paths += [got[p] for p in ch if p in got]
    dlg = ContactSheetDialog(win, len(paths), title)
    if dlg.exec() != QDialog.Accepted:
        return
    opts = dlg.options()

    def job(conn, progress=None, cancelled=None):
        return contactsheet.render(paths, opts, progress=progress, cancelled=cancelled)

    def done(files):
        if not files:
            return
        box = QMessageBox(win)
        box.setWindowTitle("Contact sheet")
        box.setText("Contact sheet saved:\n" + "\n".join(files[:5]) + (f"\n… and {len(files) - 5} more" if len(files) > 5 else ""))
        op = box.addButton("Open it", QMessageBox.ActionRole)
        fo = box.addButton("Show in folder", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Ok)
        box.exec()
        if box.clickedButton() is op:
            open_file(files[0])
        elif box.clickedButton() is fo:
            show_in_folder(files[0], files[1:])

    if not win.run_job("contactsheet", job, on_done=done):
        QMessageBox.information(win, "Contact sheet", "Wait for the current job to finish first.")
