"""MainWindow, part: vaults."""
import os
import sys
import jobs
import store
from PySide6.QtWidgets import QFileDialog, QInputDialog, QLineEdit, QMessageBox


class VaultsMixin:
    """Part of MainWindow (see facesort.py): vaults."""

    # ---------------------------------------------------------------- vaults
    def ask_password(self, title, text, confirm=False):
        pw, ok = QInputDialog.getText(self, title, text, QLineEdit.Password)
        if not ok:
            return None
        if confirm and pw:
            again, ok = QInputDialog.getText(self, title, "Type it again:", QLineEdit.Password)
            if not ok:
                return None
            if again != pw:
                QMessageBox.warning(self, title, "The two passwords weren't the same - nothing was changed.")
                return None
        return pw

    def new_vault(self):
        name, ok = QInputDialog.getText(self, "New vault", "Name of the vault (e.g. “Private”, “Family only”):")
        name = " ".join(name.split()) if ok else ""
        if not name:
            return
        pw = self.ask_password("New vault", f"Password for “{name}” (leave empty for none):", confirm=True)
        if pw is None:
            return
        vid = store.create_vault(self.conn, name, pw or None)
        self.statusBar().showMessage(f"Vault “{name}” made - drag people, folders or photos onto it, "
                                     "then right-click > Lock to hide them.", 10000)
        self.view = ("category", vid)
        self.structure_changed()

    def lock_vault(self, vid):
        c = self.conn
        name = next((v[1] for v in store.vaults(c) if v[0] == vid), "Vault")
        has_pw = next((v[3] for v in store.vaults(c) if v[0] == vid), False)
        if not has_pw:
            main = store.is_main_vault(c, vid)
            pw = self.ask_password(f"Lock “{name}”", f"Choose a password for “{name}”"
                                   + ("" if main else " (or leave it empty to lock without one)") + ":", confirm=True)
            if pw is None or (main and not pw):
                if main and pw == "":
                    QMessageBox.information(self, "Vault", "The Vault needs a password. (Other vaults you make can "
                                                           "lock without one.)")
                return
            if pw:
                store.set_vault_password(c, vid, pw)
        self.close_viewer(refresh=False)
        self.close_review()
        _, people, _ = store.vault_members(c, vid)
        if people:   # suggestions of these people on other photos would give them away
            q = ",".join(map(str, people))
            c.execute(f"UPDATE faces SET person_id=NULL WHERE confirmed=0 AND person_id IN ({q})")
            c.execute(f"DELETE FROM tattoo_suggestions WHERE person_id IN ({q})")
            c.commit()
        hidden, _ = store.lock_vault(c, vid, True)
        self.pix_cache.clear()
        self.hi_cache.clear()
        self._dup_key = None
        self.view = ("vault", vid)
        self.after_change()
        self.statusBar().showMessage(f"“{name}” is locked: {hidden:,} photo{'s' if hidden != 1 else ''} hidden "
                                     "(the files are still where they are on disk).", 10000)

    def unlock_vault(self, vid):
        c = self.conn
        name = next((v[1] for v in store.vaults(c) if v[0] == vid), "Vault")
        has_pw = next((v[3] for v in store.vaults(c) if v[0] == vid), False)
        if has_pw:
            pw = self.ask_password(f"Unlock “{name}”", f"Password for “{name}”:")
            if pw is None:
                return
            if not store.vault_password_ok(c, vid, pw):
                QMessageBox.warning(self, "Vault", "That's not the password.")
                return
        _, shown = store.lock_vault(c, vid, False)
        self._dup_key = None
        self.view = ("category", vid)
        self.after_change()
        self.statusBar().showMessage(f"“{name}” is unlocked ({shown:,} photo{'s' if shown != 1 else ''}) - it stays "
                                     "open until you lock it again (right-click > Lock).", 10000)

    def vault_password(self, vid, remove=False):
        c = self.conn
        name = next((v[1] for v in store.vaults(c) if v[0] == vid), "Vault")
        if remove:
            store.set_vault_password(c, vid, None)
            self.statusBar().showMessage(f"“{name}” has no password now.", 6000)
            return
        pw = self.ask_password(f"Password for “{name}”", "New password:", confirm=True)
        if not pw:
            return
        store.set_vault_password(c, vid, pw)
        self.statusBar().showMessage(f"Password for “{name}” set.", 6000)

    def vault_location(self, vid):
        c = self.conn
        name = next((v[1] for v in store.vaults(c) if v[0] == vid), "Vault")
        cur = next((v[4] for v in store.vaults(c) if v[0] == vid), None)
        d = QFileDialog.getExistingDirectory(self, f"Where should “{name}” keep its photos?", cur or "")
        if not d:
            return
        d = store.norm_path(d)
        store.set_vault_path(c, vid, d)
        # keep that place in the library, so photos sorted there stay in FaceSort
        folders = self.settings["folders"]
        if not any(d == f or d.startswith(f.rstrip("\\/") + os.sep) for f in folders):
            folders.append(d)
            store.save_settings(self.settings)
        self.statusBar().showMessage(f"“{name}”: Sort into folders now puts its photos in {d}", 10000)
        self.rebuild_tree()

    def person_photo_ids(self, pid):
        c = self.conn
        return {r[0] for r in c.execute("SELECT DISTINCT photo_id FROM faces WHERE person_id=? AND confirmed=1 AND ignored=0",
                                        (pid,))} | {r[0] for r in c.execute("SELECT photo_id FROM photo_people WHERE person_id=?",
                                                                            (pid,))}

    def recycle_discarded(self):
        c = self.conn
        n = c.execute("""SELECT COUNT(*) FROM photos WHERE sort_later=0 AND (discarded=1
                         OR id IN (SELECT photo_id FROM faces WHERE ignored=2))""").fetchone()[0]
        roots = [r for r in dict.fromkeys([self.settings.get("library_root"), self.settings.get("export_dir")]
                                          + list(self.settings["folders"])) if r]
        # finding the "~To Be Discarded" folders looks at every photo folder and drive - a sleeping
        # or disconnected drive can take many seconds to answer, so it's done in the background
        def look(conn, roots, progress=None, cancelled=None):
            d = jobs.discard_dirs(conn, roots)
            return d, len(jobs.discard_contents(d))
        if not self.run_job("discardlook", look, roots, on_done=lambda res: res and self._recycle_ask(n, roots, *res)):
            QMessageBox.information(self, "Busy", "Wait for the current task to finish, then try again.")
            return
        self.statusBar().showMessage("Looking in the ~To Be Discarded folders…")

    def _recycle_ask(self, n, roots, dirs, extra):
        if not n and not extra:
            QMessageBox.information(self, "Recycle Bin", "~To Be Discarded is empty.")
            return
        bin_name = "Recycle Bin" if sys.platform == "win32" else "Trash"
        parts = []
        if n:
            parts.append(f"{n:,} photo{'s' if n != 1 else ''} you marked to discard")
        if extra:
            parts.append(f"everything in the “{jobs.DISCARD_FOLDER}” folder{'s' if len(dirs) > 1 else ''} "
                         f"({extra:,} file{'s' if extra != 1 else ''} and folders - .webp originals, empty folders…)")
        if QMessageBox.question(self, bin_name, f"Move {' and '.join(parts)} to the {bin_name}?\n\n"
                                f"They're taken out of FaceSort. You can still restore them from the {bin_name}.",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return

        def done(st):
            msg = f"{st['trashed']:,} item{'s' if st['trashed'] != 1 else ''} moved to the {bin_name}."
            left = st.get("left") or []
            if st["errors"] or left:
                bad = left or [p for p, _ in st["errors"]]
                QMessageBox.warning(self, bin_name, msg + f"\n\n{len(bad)} item{'s' if len(bad) != 1 else ''} couldn't be "
                                    "moved (open in another program, or no permission):\n"
                                    + "\n".join(bad[:12]) + ("\n…" if len(bad) > 12 else ""))
            self.statusBar().showMessage(msg, 15000)
            self.view = ("none", None)
            self.refresh_all()
        if not self.run_job("recycle", jobs.recycle_discarded, roots, on_done=done):
            QMessageBox.information(self, "Busy", "Wait for the current task to finish, then try again.")

    def confirm_person(self, pid):
        """Right-click a person: confirm every face suggested as them (and tattoo matches)."""
        ids = [r[0] for r in self.conn.execute(
            "SELECT id FROM faces WHERE person_id=? AND confirmed=0 AND ignored=0", (pid,))]
        if ids:
            store.confirm(self.conn, ids)
        photos = [r[0] for r in self.conn.execute("SELECT photo_id FROM tattoo_suggestions WHERE person_id=?", (pid,))]
        if photos:
            store.confirm_tattoo_matches(self.conn, photos, pid)
        self.after_change()
        self.statusBar().showMessage(f"Confirmed {len(ids) + len(photos):,} for {self.person_name(pid)}", 6000)

    def add_person_to(self, cid):
        name = self.ask_name(f"Who goes in “{self.cat_name(cid)}”?")
        if name:
            self.place_people([store.get_or_create_person(self.conn, name)], cid)
