"""Settings > Backups (1.52): where, how often, how many, Back up now, and Restore."""
import os
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QSpinBox, QVBoxLayout, QWidget)

import backup


def _size(n):
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.0f} {unit}" if unit == "bytes" else f"{n:,.1f} {unit}"
        n /= 1024


class BackupsPage(QWidget):
    def __init__(self, win, settings, parent=None):
        super().__init__(parent)
        self.win, self.s = win, settings
        self.worker = None
        self.restore_asked = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 14, 10)
        note = QLabel("A backup is everything FaceSort knows - faces, names, groups, tags, albums, rules, history, "
                      "settings - in one compressed file. Your photos themselves aren't in it (they belong in your "
                      "normal file backup); restoring brings back all your work without rescanning.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        lay.addWidget(note)
        form = QFormLayout()
        row = QHBoxLayout()
        self.folder = QLineEdit(backup.folder(settings))
        pick = QPushButton("…")
        pick.setFixedWidth(30)
        pick.clicked.connect(self.pick_folder)
        row.addWidget(self.folder)
        row.addWidget(pick)
        form.addRow("Save backups in:", row)
        self.freq = QComboBox()
        for key, label in backup.FREQUENCIES:
            self.freq.addItem(label, key)
        self.freq.setCurrentIndex(max(0, self.freq.findData(settings.get("backup_frequency", "close"))))
        form.addRow("Back up:", self.freq)
        self.keep = QSpinBox()
        self.keep.setRange(1, 200)
        self.keep.setValue(int(settings.get("backup_keep", 10)))
        self.keep.setSuffix(" newest")
        form.addRow("Keep:", self.keep)
        lay.addLayout(form)
        hint = QLabel("A backup on close runs by itself after the window has closed, so closing isn't slowed down. "
                      "If nothing changed since the last backup, none is made.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        lay.addWidget(hint)
        brow = QHBoxLayout()
        self.b_now = QPushButton("Back up now")
        self.b_now.clicked.connect(self.back_up_now)
        brow.addWidget(self.b_now)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        brow.addWidget(self.progress, 1)
        brow.addStretch()
        lay.addLayout(brow)
        lay.addSpacing(8)
        lay.addWidget(QLabel("<b>Backups</b>"))
        self.list = QListWidget()
        self.list.setMinimumHeight(160)
        lay.addWidget(self.list, 1)
        rrow = QHBoxLayout()
        b = QPushButton("Restore…")
        b.clicked.connect(self.restore_selected)
        rrow.addWidget(b)
        b = QPushButton("Restore from a file…")
        b.clicked.connect(self.restore_file)
        rrow.addWidget(b)
        b = QPushButton("Open folder")
        b.clicked.connect(self.open_folder)
        rrow.addWidget(b)
        rrow.addStretch()
        lay.addLayout(rrow)
        self.state = QLabel()
        self.state.setStyleSheet("color: gray;")
        self.state.setWordWrap(True)
        lay.addWidget(self.state)
        self.fill()

    def pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Save backups in…", self.folder.text())
        if d:
            self.folder.setText(os.path.normpath(d))
            self.fill()

    def fill(self):
        self.list.clear()
        rows = backup.listing(self.folder.text())
        for p, t, size, _m in rows:
            it = QListWidgetItem(f"{time.strftime('%a %d %b %Y, %H:%M', time.localtime(t))}   ·   {_size(size)}")
            it.setData(Qt.UserRole, p)
            it.setToolTip(p)
            self.list.addItem(it)
        if not rows:
            self.list.addItem("No backups here yet.")
        st = backup.state()
        self.state.setText(("Last backup: " + time.strftime("%d %b %Y, %H:%M", time.localtime(st["at"])))
                           if st.get("at") else "No backup made yet.")

    def save_to(self, s):
        s["backup_folder"] = self.folder.text().strip() or backup.default_folder()
        s["backup_frequency"] = self.freq.currentData()
        s["backup_keep"] = int(self.keep.value())

    def back_up_now(self):
        from ui_common import Worker
        if self.worker is not None and self.worker.isRunning():
            return
        s = dict(self.s)
        self.save_to(s)
        try:
            self.win.conn.commit()
        except Exception:
            pass
        self.b_now.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.worker = Worker(lambda conn, progress=None, cancelled=None: backup.make(s, progress=progress or
                                                                                     (lambda *a: None)))
        self.worker.progress.connect(self._progress)
        self.worker.ok.connect(self._done)
        self.worker.failed.connect(self._failed)
        self.worker.start()

    def _progress(self, done, total, msg):
        self.progress.setRange(0, max(1, int(total)))
        self.progress.setValue(int(done))
        self.progress.setFormat(msg)

    def _done(self, path):
        self.b_now.setEnabled(True)
        self.progress.setVisible(False)
        self.fill()
        self.state.setText(f"Backup made: {os.path.basename(path)} ({_size(os.path.getsize(path))})")

    def _failed(self, tb):
        self.b_now.setEnabled(True)
        self.progress.setVisible(False)
        QMessageBox.warning(self, "Backup", "The backup couldn't be made:\n\n" + str(tb).strip().splitlines()[-1])

    def open_folder(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        os.makedirs(self.folder.text(), exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.folder.text()))

    def restore_selected(self):
        it = self.list.currentItem()
        p = it.data(Qt.UserRole) if it is not None else None
        if p:
            self.restore(p)

    def restore_file(self):
        p, _ = QFileDialog.getOpenFileName(self, "Restore a FaceSort backup", self.folder.text(),
                                           "FaceSort backup (*.zip)")
        if p:
            self.restore(p)

    def restore(self, path):
        try:
            info = backup.verify(path)
        except Exception as e:
            QMessageBox.warning(self, "Restore", f"This backup can't be used: {e}")
            return
        photos = info.get("photos")
        msg = (f"Restore the backup from {info.get('made', '?')}"
               + (f" ({photos:,} photos)" if photos else "") + "?\n\n"
               "FaceSort restarts. Your library as it is now is kept first, in \"Before restore <date>\" in "
               "FaceSort's data folder, so nothing is lost. Everything done since that backup won't be in the "
               "restored library.")
        if QMessageBox.question(self, "Restore backup", msg) != QMessageBox.Yes:
            return
        try:
            backup.ask_restore(path)
        except Exception as e:
            QMessageBox.warning(self, "Restore", f"Couldn't restore: {e}")
            return
        self.restore_asked = path
        win = self.window()
        if hasattr(win, "reject"):
            win.reject()
        try:
            self.win.restart_for_restore()
        except Exception:
            QMessageBox.information(self, "Restore", "Close FaceSort and open it again to finish restoring.")
