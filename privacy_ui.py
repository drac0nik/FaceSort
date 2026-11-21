"""Password, recovery key, lock screen and Settings > Privacy (1.55)."""
import os
import shutil
import time

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QVBoxLayout,
                               QWidget)

import cryptbox
import privacy
import store


def _pw_field(placeholder=""):
    e = QLineEdit()
    e.setEchoMode(QLineEdit.Password)
    e.setPlaceholderText(placeholder)
    e.setMinimumWidth(280)
    return e


class NewPasswordDialog(QDialog):
    """A new password, typed twice."""

    def __init__(self, parent=None, title="Set a password", text=None, old=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        lay = QVBoxLayout(self)
        lab = QLabel(text or "This password protects FaceSort. There's no way back in without it - except the "
                             "recovery key you'll be given next.")
        lab.setWordWrap(True)
        lay.addWidget(lab)
        f = QFormLayout()
        self.old = _pw_field() if old else None
        if old:
            f.addRow("Current password:", self.old)
        self.pw = _pw_field()
        self.pw2 = _pw_field()
        f.addRow("New password:", self.pw)
        f.addRow("Type it again:", self.pw2)
        lay.addLayout(f)
        self.err = QLabel("")
        self.err.setStyleSheet("color: #d9534f;")
        lay.addWidget(self.err)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.check)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def check(self):
        if len(self.pw.text()) < 4:
            self.err.setText("Use at least 4 characters (longer is safer).")
        elif self.pw.text() != self.pw2.text():
            self.err.setText("The two passwords aren't the same.")
        else:
            self.accept()

    def password(self):
        return self.pw.text()

    def old_password(self):
        return self.old.text() if self.old else None


class RecoveryKeyDialog(QDialog):
    """The recovery key, shown once: copy it or save it, and say you've kept it."""

    def __init__(self, rk, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Your recovery key")
        self.rk = rk
        lay = QVBoxLayout(self)
        lab = QLabel("<b>Keep this recovery key somewhere safe</b> - on paper, or in a password manager. If you "
                     "forget your password, it's the only way back in. FaceSort won't show it again "
                     "(you can make a new one in Settings > Privacy).")
        lab.setWordWrap(True)
        lay.addWidget(lab)
        key = QLabel(rk)
        f = QFont("Consolas")
        f.setStyleHint(QFont.Monospace)
        f.setPointSize(16)
        f.setBold(True)
        key.setFont(f)
        key.setTextInteractionFlags(Qt.TextSelectableByMouse)
        key.setAlignment(Qt.AlignCenter)
        key.setStyleSheet("padding: 14px; border: 2px dashed #888; border-radius: 8px;")
        lay.addWidget(key)
        row = QHBoxLayout()
        cp = QPushButton("Copy")
        cp.clicked.connect(lambda: QGuiApplication.clipboard().setText(rk))
        sv = QPushButton("Save as a text file…")
        sv.clicked.connect(self.save)
        row.addWidget(cp)
        row.addWidget(sv)
        row.addStretch()
        lay.addLayout(row)
        self.ok_cb = QCheckBox("I've written it down or saved it somewhere safe (not only on this PC)")
        lay.addWidget(self.ok_cb)
        bb = QDialogButtonBox(QDialogButtonBox.Ok)
        self.ok = bb.button(QDialogButtonBox.Ok)
        self.ok.setEnabled(False)
        self.ok_cb.toggled.connect(self.ok.setEnabled)
        bb.accepted.connect(self.accept)
        lay.addWidget(bb)

    def save(self):
        p, _ = QFileDialog.getSaveFileName(self, "Save the recovery key", os.path.join(os.path.expanduser("~"),
                                           "FaceSort recovery key.txt"), "Text (*.txt)")
        if p:
            with open(p, "w", encoding="utf-8") as f:
                f.write(f"FaceSort recovery key (made {time.strftime('%Y-%m-%d')}):\n\n{self.rk}\n\n"
                        "Keep this somewhere safe - a USB stick in a drawer, or print it and delete this file.\n")

    def reject(self):
        pass                                     # (it has to be acknowledged)


class UnlockDialog(QDialog):
    """FaceSort is locked: the password (or the recovery key) opens it."""

    def __init__(self, parent=None, closing_text="Close FaceSort"):
        super().__init__(parent)
        self.setWindowTitle("FaceSort is locked")
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        lay = QVBoxLayout(self)
        t = QLabel("<h3>🔒 FaceSort is locked</h3>Type your password to open it.")
        lay.addWidget(t)
        self.pw = _pw_field("Password")
        self.pw.returnPressed.connect(self.try_unlock)
        lay.addWidget(self.pw)
        self.err = QLabel("")
        self.err.setStyleSheet("color: #d9534f;")
        lay.addWidget(self.err)
        row = QHBoxLayout()
        forgot = QPushButton("Forgot it? Use the recovery key…")
        forgot.setFlat(True)
        forgot.clicked.connect(self.recover)
        row.addWidget(forgot)
        row.addStretch()
        lay.addLayout(row)
        bb = QDialogButtonBox()
        self.open_b = bb.addButton("Unlock", QDialogButtonBox.AcceptRole)
        bb.addButton(closing_text, QDialogButtonBox.RejectRole)
        bb.accepted.connect(self.try_unlock)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.fails = 0

    def try_unlock(self):
        self.open_b.setEnabled(False)
        QApplication.processEvents()
        try:
            cryptbox.unlock(self.pw.text())
            self.accept()
        except cryptbox.WrongPassword:
            self.fails += 1
            self.err.setText("That's not the password." + ("  (Caps Lock?)" if self.fails >= 2 else ""))
            self.pw.selectAll()
            if self.fails >= 3:
                time.sleep(min(5, self.fails - 2))      # (slower each time after a few wrong tries)
        finally:
            self.open_b.setEnabled(True)

    def recover(self):
        d = QDialog(self)
        d.setWindowTitle("Use the recovery key")
        lay = QVBoxLayout(d)
        lay.addWidget(QLabel("Type the recovery key you saved when you set the password, then choose a new password."))
        f = QFormLayout()
        rk = QLineEdit()
        rk.setPlaceholderText("XXXXX-XXXXX-XXXXX-XXXXX-XXXXX")
        pw, pw2 = _pw_field(), _pw_field()
        f.addRow("Recovery key:", rk)
        f.addRow("New password:", pw)
        f.addRow("Type it again:", pw2)
        lay.addLayout(f)
        err = QLabel("")
        err.setStyleSheet("color: #d9534f;")
        lay.addWidget(err)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        lay.addWidget(bb)
        bb.rejected.connect(d.reject)

        def go():
            if len(pw.text()) < 4 or pw.text() != pw2.text():
                err.setText("The new password needs at least 4 characters, typed the same twice.")
                return
            try:
                cryptbox.unlock_with_recovery(rk.text(), pw.text())
            except cryptbox.WrongPassword:
                err.setText("That recovery key doesn't fit.")
                return
            d.accept()
        bb.accepted.connect(go)
        if d.exec() == QDialog.Accepted:
            QMessageBox.information(self, "FaceSort", "Unlocked - your new password works from now on. "
                                                      "Your recovery key still works too.")
            self.accept()


def gate():
    """At start: unlock when the profile asks for it. False = stay closed."""
    if not cryptbox.has_password():
        return True
    o = cryptbox.options()
    if not (o["ask_on_open"] or cryptbox.data_encrypted() or privacy.pending()):
        return True
    return UnlockDialog().exec() == QDialog.Accepted


def finish_pending():
    """Encrypting / decrypting asked for in Settings - done now, before the library opens."""
    p = privacy.pending()
    if not p or not cryptbox.unlocked():
        return
    from PySide6.QtWidgets import QProgressDialog
    on = p.get("encrypt_data")
    dlg = QProgressDialog(("Encrypting" if on else "Decrypting") + " your library…", None, 0, 0)
    dlg.setWindowTitle("FaceSort")
    dlg.setMinimumDuration(0)
    dlg.show()
    QApplication.processEvents()
    try:
        privacy.convert(on, progress=lambda m: (dlg.setLabelText(m), QApplication.processEvents()))
    except Exception as e:
        dlg.close()
        privacy.cancel_request()
        QMessageBox.warning(None, "FaceSort", f"The library couldn't be {'encrypted' if on else 'decrypted'} - "
                                              f"it's unchanged:\n{e}")
        return
    dlg.close()
    QMessageBox.information(None, "FaceSort", "Your library is encrypted now." if on else
                            "Your library isn't encrypted any more.")


def _system_idle():
    """Seconds since the last key press or mouse move on this PC (Windows), else None."""
    import sys
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class LII(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
        li = LII()
        li.cbSize = ctypes.sizeof(LII)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
            return None
        return ((ctypes.windll.kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:
        return None


class LockController(QObject):
    """Locks the window after a while without use, or when it's minimized (Settings > Privacy).
    (1.56: "without use" comes from Windows' own idle time - an application-wide event filter, the
    way 1.55 watched for input, could crash with the built-in browser.)"""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.last = time.monotonic()
        self.locked = False
        self._cursor = None
        self.timer = QTimer(self, interval=15000)
        self.timer.timeout.connect(self.check)
        self.timer.start()

    def idle(self):
        s = _system_idle()
        if s is not None:
            return min(s, time.monotonic() - self.last)
        from PySide6.QtGui import QCursor
        pos = QCursor.pos()
        if pos != self._cursor:
            self._cursor = pos
            self.last = time.monotonic()
        return time.monotonic() - self.last

    def check(self):
        if self.locked or not cryptbox.has_password():
            return
        mins = cryptbox.options().get("idle_minutes") or 0
        if mins and self.idle() > mins * 60:
            self.lock_now()

    def minimized(self):
        if cryptbox.has_password() and cryptbox.options().get("on_minimize") and not self.locked:
            QTimer.singleShot(0, self.lock_now)

    def lock_now(self):
        if self.locked or not cryptbox.has_password():
            return
        self.locked = True
        app = QApplication.instance()
        shown = [w for w in app.topLevelWidgets() if w.isVisible() and w is not self.win]
        win_state = self.win.windowState()
        v = getattr(self.win, "viewer", None)
        if v is not None:
            try:
                v.slideshow.stop()
                if v.player:
                    v.player.pause()
            except Exception:
                pass
        for w in shown:
            w.hide()
        self.win.hide()
        try:
            import fsx
            fsx.wipe()                           # (1.58) readable copies of encrypted photos go
        except Exception:
            pass
        try:
            dlg = UnlockDialog(closing_text="Close FaceSort")
            ok = dlg.exec() == QDialog.Accepted
        finally:
            self.locked = False
            self.last = time.monotonic()
        if not ok:
            self.win.close()
            return
        self.win.setWindowState(win_state & ~Qt.WindowMinimized)
        self.win.show()
        for w in shown:
            w.show()


class PrivacyPage(QWidget):
    """Settings > Privacy."""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self.want_encrypt = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 14, 10)
        lay.addWidget(QLabel("<b>Password</b>"))
        self.pw_state = QLabel()
        self.pw_state.setWordWrap(True)
        lay.addWidget(self.pw_state)
        row = QHBoxLayout()
        self.b_set = QPushButton("Set a password…")
        self.b_change = QPushButton("Change password…")
        self.b_rk = QPushButton("New recovery key…")
        self.b_remove = QPushButton("Remove password…")
        for b, fn in ((self.b_set, self.set_password), (self.b_change, self.change_password),
                      (self.b_rk, self.new_rk), (self.b_remove, self.remove_password)):
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        lay.addSpacing(10)
        lay.addWidget(QLabel("<b>Locking</b>"))
        f = QFormLayout()
        o = cryptbox.options()
        self.ask_open = QCheckBox("Ask for the password when FaceSort opens")
        self.ask_open.setChecked(bool(o["ask_on_open"]))
        f.addRow("", self.ask_open)
        self.idle = QSpinBox()
        self.idle.setRange(0, 600)
        self.idle.setSuffix(" minutes without use")
        self.idle.setSpecialValueText("never")
        self.idle.setValue(int(o.get("idle_minutes") or 0))
        f.addRow("Lock after:", self.idle)
        self.on_min = QCheckBox("Lock when FaceSort is minimized")
        self.on_min.setChecked(bool(o.get("on_minimize")))
        f.addRow("", self.on_min)
        lay.addLayout(f)
        note = QLabel("FaceSort always locks when it closes. Locking hides every FaceSort window until the "
                      "password is typed (Lock now: Ctrl+Shift+L).")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        lay.addWidget(note)
        lay.addSpacing(10)
        lay.addWidget(QLabel("<b>Encryption</b>"))
        self.enc = QCheckBox("Encrypt the library")
        self.enc.setChecked(cryptbox.data_encrypted() if not privacy.pending()
                            else bool(privacy.pending().get("encrypt_data")))
        lay.addWidget(self.enc)
        enote = QLabel("Everything FaceSort keeps about your photos - names, faces, tags, folders, albums, "
                       "history, thumbnails and settings - is encrypted on this PC, readable only with your "
                       "password (or recovery key). Paths and file names are kept out of the logs. "
                       "Turning this on or off happens when FaceSort restarts.")
        enote.setWordWrap(True)
        enote.setStyleSheet("color: gray;")
        lay.addWidget(enote)
        self.enc_state = QLabel()
        self.enc_state.setStyleSheet("color: gray;")
        lay.addWidget(self.enc_state)
        # (1.58) the photo and video files themselves - optional, off unless you turn it on
        lay.addSpacing(10)
        lay.addWidget(QLabel("<b>Photo and video files</b>"))
        import fsx
        self.files = QCheckBox("Encrypt the photo and video files too")
        self.files.setChecked(fsx.enabled())
        lay.addWidget(self.files)
        fnote = QLabel("Each photo and video is kept as an encrypted file with a random name in a hidden folder "
                       "in your library - no names or folders show on disk; FaceSort shows them as before. "
                       "Your originals stay until you remove them (Review originals). Anything you play or "
                       "open in another app is a temporary copy, wiped when FaceSort locks or closes. Names "
                       "aren't written into encrypted files (they're in FaceSort's encrypted data). "
                       "Off by default.")
        fnote.setWordWrap(True)
        fnote.setStyleSheet("color: gray;")
        lay.addWidget(fnote)
        self.auto_rm = QCheckBox("Remove originals as soon as they're encrypted (no review)")
        self.auto_rm.setChecked(fsx.auto_remove())
        lay.addWidget(self.auto_rm)
        row = QHBoxLayout()
        self.files_state = QLabel()
        self.files_state.setStyleSheet("color: gray;")
        self.files_state.setWordWrap(True)
        row.addWidget(self.files_state, 1)
        self.b_review = QPushButton("Review originals…")
        self.b_review.clicked.connect(lambda: self.win.review_originals(parent=self.window()) and self.refresh())
        row.addWidget(self.b_review)
        lay.addLayout(row)
        self.want_files = None
        lay.addStretch()
        self.refresh()

    def refresh(self):
        has = cryptbox.has_password()
        self.pw_state.setText("A password protects FaceSort." if has else
                              "No password: anyone using this PC can open FaceSort.")
        self.b_set.setVisible(not has)
        for b in (self.b_change, self.b_rk, self.b_remove):
            b.setVisible(has)
        enc = cryptbox.data_encrypted()
        self.b_remove.setEnabled(not enc)
        self.b_remove.setToolTip("Turn encryption off first" if enc else "")
        for w in (self.ask_open, self.idle, self.on_min):
            w.setEnabled(has)
        if enc:
            self.ask_open.setChecked(True)
            self.ask_open.setEnabled(False)
        p = privacy.pending()
        self.enc_state.setText(("Encrypted." if enc else "Not encrypted.") +
                               ("  (Changes when FaceSort restarts.)" if p else ""))
        import fsx
        can = has and enc and not p
        on = fsx.enabled()
        self.files.setEnabled(can or on)
        self.files.setToolTip("" if can or on else "Turn on “Encrypt the library” first (FaceSort restarts) - "
                              "it keeps the list of what's in each encrypted file")
        self.auto_rm.setEnabled(on or can)
        try:
            n, waiting = fsx.counts(self.win.conn)
        except Exception:
            n, waiting = 0, 0
        if on or n:
            self.files_state.setText(f"{n:,} encrypted" + (f" · {waiting:,} original{'s' if waiting != 1 else ''} "
                                                           "still on disk, waiting for you" if waiting else
                                                           " · no originals left on disk"))
        else:
            self.files_state.setText("Not encrypted." if can else
                                     "Needs “Encrypt the library” (above) to be on first.")
        self.b_review.setVisible(bool(waiting))

    def set_password(self):
        d = NewPasswordDialog(self)
        if d.exec() != QDialog.Accepted:
            return
        rk = cryptbox.setup(d.password())
        RecoveryKeyDialog(rk, self).exec()
        self.ask_open.setChecked(True)
        self.refresh()

    def apply_files(self, win, on):
        """(1.58) Turning encryption of the photo and video files on or off."""
        import fsx
        if on:
            if not (cryptbox.has_password() and cryptbox.data_encrypted()):
                return
            vault = fsx.choose_vault(win.settings)
            used = win.conn.execute("SELECT COALESCE(SUM(size), 0) FROM photos WHERE COALESCE(is_other, 0) = 0") \
                .fetchone()[0] or 0
            try:
                free = shutil.disk_usage(os.path.dirname(vault) if os.path.isdir(os.path.dirname(vault))
                                         else store.data_dir()).free
            except OSError:
                free = None
            space = (f"\n\nThe encrypted copies need about {used / 1e9:,.1f} GB until you remove the originals"
                     + (f" ({free / 1e9:,.1f} GB free there)." if free is not None else "."))
            if free is not None and free < used * 1.05:
                space += "\nThat's not enough room for all of them - FaceSort encrypts what fits; remove " \
                         "originals as you go (Review originals) to make room."
            if QMessageBox.question(
                    win, "Encrypt the photo and video files",
                    "Every photo and video is encrypted into a hidden folder:\n" + vault +
                    "\n\nYour originals stay where they are until you remove them (Settings › Privacy › "
                    "Review originals). New photos are encrypted as they come in." + space +
                    "\n\nStart now?") != QMessageBox.Yes:
                return
            fsx.set_enabled(True, vault=vault)
            if win.seal_when_free() is False:
                win.statusBar().showMessage("Photo and video files will be encrypted as soon as FaceSort is free.",
                                            10000)
        else:
            n, _w = fsx.counts(win.conn)
            if n and QMessageBox.question(
                    win, "Decrypt the photo and video files",
                    f"{n:,} encrypted photos and videos are turned back into normal files at their places in "
                    "your library (needs the room for them), then the hidden folder is removed.\n\nStart now?") \
                    != QMessageBox.Yes:
                return
            if n:
                win.start_unseal()
            else:
                fsx.set_enabled(False)

    def change_password(self):
        d = NewPasswordDialog(self, "Change password", "Type your current password, then the new one.", old=True)
        if d.exec() != QDialog.Accepted:
            return
        try:
            cryptbox.change_password(d.old_password(), d.password())
            QMessageBox.information(self, "Password", "Password changed.")
        except cryptbox.WrongPassword:
            QMessageBox.warning(self, "Password", "The current password wasn't right - nothing changed.")

    def _ask(self, title, text):
        from PySide6.QtWidgets import QInputDialog
        pw, ok = QInputDialog.getText(self, title, text, QLineEdit.Password)
        return pw if ok else None

    def new_rk(self):
        pw = self._ask("New recovery key", "Your password (the old recovery key stops working):")
        if pw is None:
            return
        try:
            rk = cryptbox.new_recovery(pw)
        except cryptbox.WrongPassword:
            QMessageBox.warning(self, "Recovery key", "That's not the password.")
            return
        RecoveryKeyDialog(rk, self).exec()

    def remove_password(self):
        pw = self._ask("Remove password", "Your password (FaceSort will open without asking):")
        if pw is None:
            return
        try:
            cryptbox.remove_password(pw)
        except cryptbox.WrongPassword:
            QMessageBox.warning(self, "Password", "That's not the password.")
            return
        except ValueError as e:
            QMessageBox.warning(self, "Password", str(e).capitalize() + ".")
            return
        self.refresh()

    def save_to(self, s):
        """(Settings OK) the lock options, and turning encryption on / off."""
        if cryptbox.has_password():
            cryptbox.set_options({"ask_on_open": self.ask_open.isChecked(), "idle_minutes": self.idle.value(),
                                  "on_minimize": self.on_min.isChecked()})
        import fsx
        if fsx.enabled() or self.files.isEnabled():
            try:
                cryptbox.set_flag("remove_originals_auto", self.auto_rm.isChecked())
            except Exception:
                pass
        self.want_files = None if self.files.isChecked() == fsx.enabled() else self.files.isChecked()
        want = self.enc.isChecked()
        now = cryptbox.data_encrypted()
        p = privacy.pending()
        target = bool(p.get("encrypt_data")) if p else now
        self.want_encrypt = None if want == target else want

    def apply_after(self, win):
        """After Settings closes: start encrypting / decrypting (at a restart)."""
        if self.want_files is not None:
            self.apply_files(win, self.want_files)
        want = self.want_encrypt
        if want is None:
            return
        if want and not cryptbox.has_password():
            QMessageBox.information(win, "Encrypt the library", "Encryption needs a password - set one now.")
            d = NewPasswordDialog(win)
            if d.exec() != QDialog.Accepted:
                return
            rk = cryptbox.setup(d.password())
            RecoveryKeyDialog(rk, win).exec()
        if want == cryptbox.data_encrypted():
            privacy.cancel_request()
            return
        msg = ("FaceSort will restart and encrypt your library (a minute or two on a big library).\n\n"
               "From then on it can only be opened with your password or recovery key." if want else
               "FaceSort will restart and decrypt your library (names, faces and thumbnails readable on this PC "
               "again).")
        if QMessageBox.question(win, "Encrypt the library" if want else "Decrypt the library", msg + "\n\nRestart now?") \
                != QMessageBox.Yes:
            privacy.request(want)
            win.statusBar().showMessage("The library will be " + ("encrypted" if want else "decrypted") +
                                        " the next time FaceSort starts.", 10000)
            return
        privacy.request(want)
        if want:
            old = privacy.old_backups(win.settings)
            if old and QMessageBox.question(
                    win, "Old backups", f"{len(old)} backup{'s' if len(old) != 1 else ''} made before now "
                    "aren't encrypted - anyone could read your names and faces from them.\n\nDelete them? "
                    "(New backups will be encrypted.)") == QMessageBox.Yes:
                for p in old:
                    try:
                        os.remove(p)
                    except OSError:
                        pass
        win.restart_for_restore()                # (the same restart a restore uses)
