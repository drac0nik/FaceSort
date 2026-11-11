"""MainWindow, part: data helpers (photo info, destinations, captions…)."""
import os
import re
import time
import engine
import jobs
import known
import store
from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QDialog
from viewer_parts import PersonPicker


class DataMixin:
    """Part of MainWindow (see facesort.py): data helpers (photo info, destinations, captions…)."""

    # ------------------------------------------------------------------ data helpers
    def face_pixmap(self, fid, path=None, want_px=None, face_only=False):
        """A face's picture (or, in the Viewer tab, the whole photo). want_px: the size wanted
        (default: the gallery's thumbnail size); face_only: always the face, even in the Viewer tab."""
        dpr = self.grid.devicePixelRatioF() if hasattr(self, "grid") else 1.0
        want = want_px or self.cell * dpr
        shoot = getattr(self, "_shoot", None)
        if shoot and self.view[0] == "shoot" and fid in shoot and len(shoot[fid]) == 1 and not face_only:
            bid = shoot[fid][0][0]
            pm = self.pix_cache.get(("body", bid))
            if pm is None:
                row = self.conn.execute("SELECT thumb FROM bodies WHERE id=?", (bid,)).fetchone()
                pm = QPixmap()
                if row and row[0]:
                    pm.loadFromData(row[0], "JPG")
                    side = max(pm.width(), pm.height())        # square, figure in the middle
                    sq = QPixmap(side, side)
                    sq.fill(QColor(40, 40, 40))
                    from PySide6.QtGui import QPainter
                    qp = QPainter(sq)
                    qp.drawPixmap((side - pm.width()) // 2, (side - pm.height()) // 2, pm)
                    qp.end()
                    pm = sq
                self.pix_cache[("body", bid)] = pm
            return pm
        if fid > 0 and self.viewer_mode and path and not face_only:
            pid = self.photo_id_of(path)
            if pid:
                fid = -pid          # Viewer: the whole photo instead of the face
        if fid < 0 and path and jobs.media_kind(path) == "other":
            return self.file_icon_pixmap(path, int(want))
        if fid < 0:  # a whole photo (no face box): loaded in the background
            size = int(max(128, -(-want // 64) * 64))
            pm = self.hi_cache.get((fid, size))
            if pm is None and path:
                info = self.photo_info(path)
                img = self.hires.get(fid, path, info[2] if info[1] else None, size)   # video: its cover frame
                if img is not None:
                    pm = QPixmap.fromImage(img)
                    self.hi_cache[(fid, size)] = pm
            if pm is None:
                pm = self.pix_cache.get("placeholder")
                if pm is None:
                    pm = QPixmap(64, 64)
                    pm.fill(QColor(60, 60, 60))
                    self.pix_cache["placeholder"] = pm
            return pm
        if path and want > engine.THUMB * 1.1:
            size = int(-(-want // 64) * 64)  # steps of 64 px so dragging the slider doesn't reload constantly
            pm = self.hi_cache.get((fid, size))
            if pm is not None:
                return pm
            box = self.conn.execute("SELECT x, y, w, h, frame_t FROM faces WHERE id=?", (fid,)).fetchone()
            if box:
                box = tuple(box) if box[4] is not None else tuple(box[:4])
                img = self.hires.get(fid, path, box, size)
                if img is not None:
                    pm = QPixmap.fromImage(img)
                    if len(self.hi_cache) > 400:
                        self.hi_cache.clear()
                    self.hi_cache[(fid, size)] = pm
                    return pm
        pm = self.pix_cache.get(fid)
        if pm is None:
            row = self.conn.execute("SELECT thumb FROM faces WHERE id=?", (fid,)).fetchone()
            pm = QPixmap()
            if row and row[0]:
                pm.loadFromData(row[0], "JPG")
            if len(self.pix_cache) > 6000:
                # only face pictures go: the list icons (people, groups) and file icons stay, so the
                # next list refresh doesn't have to remake thousands of them
                for k in [k for k in self.pix_cache if not isinstance(k, tuple)]:
                    del self.pix_cache[k]
            self.pix_cache[fid] = pm
        return pm

    def file_icon_pixmap(self, path, want):
        """An other file in the gallery: the file type's icon from Windows on a tile, with its
        ending written underneath (one per ending and size, so thousands of files cost nothing)."""
        ext = os.path.splitext(path)[1].lower()
        side = max(64, int(-(-want // 32) * 32))
        key = ("fileicon", ext, side)
        pm = self.pix_cache.get(key)
        if pm is not None:
            return pm
        from PySide6.QtCore import QFileInfo, QRect
        from PySide6.QtGui import QFont, QPainter
        from PySide6.QtWidgets import QFileIconProvider
        if not hasattr(self, "_icon_provider"):
            self._icon_provider = QFileIconProvider()
        icon = self._icon_provider.icon(QFileInfo(path))
        pm = QPixmap(side, side)
        pm.fill(QColor(48, 52, 58))
        qp = QPainter(pm)
        qp.setRenderHint(QPainter.Antialiasing)
        isz = int(side * 0.42)
        ipm = icon.pixmap(isz, isz)
        if not ipm.isNull():
            ipm = ipm.scaled(isz, isz, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            qp.drawPixmap((side - ipm.width()) // 2, int(side * 0.10) + (isz - ipm.height()) // 2, ipm)
        f = QFont()
        f.setBold(True)
        f.setPixelSize(max(10, side // 9))
        qp.setFont(f)
        qp.setPen(QColor(220, 225, 235))
        qp.drawText(QRect(0, int(side * 0.55), side, int(side * 0.17)), Qt.AlignCenter,
                    (ext[1:].upper() or "FILE")[:8])
        qp.end()
        self.pix_cache[key] = pm
        return pm

    def hires_ready(self, fid):
        self.model.refresh_face(fid)

    def photo_info(self, path):
        """(photo id, is video, cover frame seconds) - cached."""
        info = self._pid_cache.get(path)
        if info is None:
            r = self.conn.execute("SELECT id, is_video, cover_t, ai, width, height FROM photos WHERE path=?",
                                  (path,)).fetchone()
            info = tuple(r) if r else (0, 0, None, 0, None, None)
            if len(self._pid_cache) > 50000:
                self._pid_cache.clear()
            self._pid_cache[path] = info
        return info

    def photo_id_of(self, path):
        return self.photo_info(path)[0]

    def apply_grid_layout(self):
        """Fixed grid normally; with section headings, items size themselves so a heading
        can take a whole row."""
        headed = any(r[0] == 0 for r in self.model.rows)
        if self.grid.set_indent(int(self.cell * 0.25) if headed else 0):
            self.grid.doItemsLayout()
        self.grid.setUniformItemSizes(not headed)
        self.grid.setGridSize(QSize() if headed else QSize(self.cell, self.cell))
        self.grid.setSpacing(0)

    def set_media_filter(self, i):
        self.settings["media_filter"] = ("all", "photos", "videos")[i]
        store.save_settings(self.settings)
        self.refresh_grid(keep_selection=True)

    def rows_photo_ids(self, rows):
        out = {-r[0] for r in rows if r[0] < 0}
        faces = [r[0] for r in rows if r[0] > 0]
        for i in range(0, len(faces), 900):
            out.update(self.photos_of_faces(faces[i:i + 900]))
        return out

    def gallery_photo_ids(self):
        """The photos in the gallery as it was last shown (for the viewer's Compare)."""
        if getattr(self, "_gallery_pids", None) is None:
            self._gallery_pids = self.rows_photo_ids(getattr(self, "_all_rows", None) or self.model.rows)
        return self._gallery_pids

    def gallery_dupes_rows(self, rows, kind):
        """The Duplicates button above the gallery: only the copies of the same picture that are
        both in this gallery, each set under its own heading. Greyed out when there are none."""
        self._all_rows = rows
        btn = getattr(self, "dupes_btn", None)
        if btn is None:
            return rows
        if kind == "dupes":
            btn.setEnabled(False)
            return rows
        here = self.rows_photo_ids(rows)
        self._gallery_pids = here
        sets = []
        for d in self.dup_sets():
            ids = [p for p in d["ids"] if p in here]
            if len(ids) >= 2:
                sets.append((d, ids))
        btn.setEnabled(bool(sets))
        btn.setToolTip(("Show only the duplicates in this gallery" if sets else
                        "No duplicates in this gallery") + (f"  ({len(sets)} set{'s' if len(sets) != 1 else ''})" if sets else ""))
        if not sets:
            if btn.isChecked():
                btn.blockSignals(True)
                btn.setChecked(False)
                btn.blockSignals(False)
            return rows
        if not btn.isChecked():
            return rows
        meta = {}
        allids = [p for _, ids in sets for p in ids]
        for i in range(0, len(allids), 900):
            chunk = allids[i:i + 900]
            for r in self.conn.execute(f"SELECT id, path, width, height, size FROM photos WHERE id IN "
                                       f"({','.join('?' * len(chunk))})", chunk):
                meta[r[0]] = r
        out = []
        for i, (d, ids) in enumerate(sets, 1):
            out.append((0, f"Duplicates {i}  ·  {len(ids)} copies  ·  {'exact copies' if d['exact'] else 'same picture'}",
                        ("dup", i - 1), 1))
            for pid in sorted(ids, key=lambda x: x != d["best"]):
                if pid not in meta:
                    continue
                _, path, w_, h_, sz = meta[pid]
                out.append((-pid, path, ("Keep · " if pid == d["best"] else "") +
                            f"{w_ or '?'}×{h_ or '?'} · {(sz or 0) / 1e6:.1f} MB", 1))
        return out

    def media_filter_rows(self, rows):
        """Only photos or only videos (All media / Pictures / Video above the gallery). Headings
        with nothing left under them go; the rest show their new count."""
        mode = self.settings.get("media_filter", "all")
        scoped = self.scope_on()
        if (mode == "all" and not scoped) or not rows:
            return rows
        want_video = mode == "videos"
        kept = [r for r in rows if not r[0] or ((mode == "all" or (bool(engine.is_video(r[1])) == want_video
                                                                   and jobs.media_kind(r[1]) != "other"))
                                                and (not scoped or self.in_scope(r[1])))]
        out = []
        for i, r in enumerate(kept):
            if r[0]:
                out.append(r)
                continue
            n = 0
            for r2 in kept[i + 1:]:
                if not r2[0]:
                    break
                n += 1
            if n:
                out.append((r[0], re.sub(r"(\s·\s+)[\d,]+$", lambda m: f"{m.group(1)}{n:,}", r[1]),) + tuple(r[2:]))
        return out

    def set_view_mode(self, i):
        self.viewer_mode = i == 1
        self.settings["view_mode"] = "viewer" if self.viewer_mode else "sort"
        store.save_settings(self.settings)
        self.hint.setText("")
        self.refresh_grid(keep_selection=True)

    def set_cell(self, v):
        self.cell = int(v)
        self.settings["thumb_size"] = self.cell
        self.apply_grid_layout()
        self.grid.doItemsLayout()

    def eventFilter(self, obj, ev):
        if ev.type() in (QEvent.KeyPress, QEvent.MouseButtonPress, QEvent.Wheel):
            self._last_input = time.monotonic()
        # Ctrl + mouse wheel resizes the thumbnails
        if ev.type() == QEvent.Wheel and getattr(self, "grid", None) is not None and obj is self.grid.viewport() \
                and ev.modifiers() & Qt.ControlModifier:
            self.size_slider.setValue(self.size_slider.value() + (24 if ev.angleDelta().y() > 0 else -24))
            return True
        return super().eventFilter(obj, ev)

    def person_names(self):
        """Every name FaceSort knows: people in the library plus everyone in the known-people
        file (people whose photos are no longer in your folders still show up here)."""
        names = {r[0].casefold(): r[0] for r in self.conn.execute("SELECT name FROM persons")}
        try:
            for n in known.names():
                names.setdefault(n.casefold(), n)
        except Exception:
            pass
        hidden = self.hidden_names()
        return sorted((n for k, n in names.items() if k not in hidden), key=str.casefold)

    def ask_name(self, title="Name these faces", current="", suggest=None):
        names = self.person_names()
        if suggest and not current:
            # a folder name: use the spelling of a person you already have, if it's them
            suggest = next((n for n in names if n.casefold() == suggest.casefold()), suggest)
            current = suggest
        dlg = PersonPicker(self, title, current)
        if dlg.exec() != QDialog.Accepted:
            return None
        return dlg.result_name

    def selected_ids(self):
        """The selection - with the exact copies each tile stands for (hidden in the gallery), so
        naming, confirming, tagging or setting a folder covers every copy."""
        ids = [self.model.rows[i.row()][0] for i in self.grid.selectionModel().selectedIndexes()]
        copies = getattr(self, "_copies", None)
        if copies:
            ids += [c for k in list(ids) for c in copies.get(k, ())]
        return ids

    def hide_copies_rows(self, rows):
        """Exact copies of a photo (same file size, same face in the same place) - e.g. a group photo
        that Sort into folders saved in each person's folder - show once. The tile stands for all
        of them (self._copies: shown id -> hidden ids); nothing is removed from the library."""
        self._copies = {}
        if not self.settings.get("hide_exact_copies", True) or not rows:
            return rows
        fids = [r[0] for r in rows if r[0] > 0]
        pids = [-r[0] for r in rows if r[0] < 0]
        key = {}
        for i in range(0, len(fids), 900):
            part = fids[i:i + 900]
            for fid, size, x, y, w, h in self.conn.execute(
                    f"SELECT f.id, ph.size, f.x, f.y, f.w, f.h FROM faces f JOIN photos ph ON ph.id=f.photo_id "
                    f"WHERE f.id IN ({','.join('?' * len(part))}) AND f.frame_t IS NULL", part):
                if size:
                    key[fid] = ("f", size, round(x, 4), round(y, 4), round(w, 4), round(h, 4))
        for i in range(0, len(pids), 900):
            part = pids[i:i + 900]
            for pid, size, w, h, vid in self.conn.execute(
                    f"SELECT id, size, width, height, is_video FROM photos WHERE id IN ({','.join('?' * len(part))})",
                    part):
                if size and w and not vid:
                    key[-pid] = ("p", size, w, h)
        first, out = {}, []
        for r in rows:
            k = key.get(r[0])
            if k is not None:
                if k in first:
                    self._copies.setdefault(first[k], []).append(r[0])
                    continue
                first[k] = r[0]
            out.append(r)
        if not self._copies:
            return rows
        # headings: their new counts
        fixed = []
        for i, r in enumerate(out):
            if r[0] == 0:
                n = 0
                for r2 in out[i + 1:]:
                    if r2[0] == 0:
                        break
                    n += 1
                if n:
                    r = (0, re.sub(r"(\s·\s+)[\d,]+$", lambda m: f"{m.group(1)}{n:,}", r[1])) + tuple(r[2:])
            fixed.append(r)
        return fixed

    def copies_of(self, rid):
        """Paths of the exact copies a tile stands for (for its tooltip)."""
        hidden = getattr(self, "_copies", {}).get(rid)
        if not hidden:
            return []
        out = []
        for h in hidden[:20]:
            row = (self.conn.execute("SELECT ph.path FROM faces f JOIN photos ph ON ph.id=f.photo_id WHERE f.id=?",
                                     (h,)) if h > 0 else
                   self.conn.execute("SELECT path FROM photos WHERE id=?", (-h,))).fetchone()
            if row:
                out.append(row[0])
        return out

    def sel_faces(self):
        return [k for k in self.selected_ids() if k > 0]

    def sel_photos(self):
        """Photos of the selection: whole-photo items, plus the photos of selected faces."""
        keys = self.selected_ids()
        ids = [-k for k in keys if k < 0]
        faces = [k for k in keys if k > 0]
        if faces:
            q = ",".join("?" * len(faces))
            ids += [r[0] for r in self.conn.execute(f"SELECT DISTINCT photo_id FROM faces WHERE id IN ({q})", faces)]
        return sorted(set(ids))

    def all_ids(self):
        copies = getattr(self, "_copies", {})
        return list(dict.fromkeys(k for r in self.model.rows if r[0] for k in [r[0]] + copies.get(r[0], [])))
