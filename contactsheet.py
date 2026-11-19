"""Contact sheet (1.53): the chosen photos on pages, with a title (a person's name) in a font
installed on your PC. Saved as a PDF, or as JPEG pictures (one per page)."""
import math
import os
import time

import cv2
from PySide6.QtCore import QMarginsF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPageLayout, QPageSize, QPainter, QPdfWriter, QPen

import engine

PAGES = [("a4p", "A4, portrait", QPageSize.A4, False), ("a4l", "A4, landscape", QPageSize.A4, True),
         ("letp", "Letter, portrait", QPageSize.Letter, False), ("letl", "Letter, landscape", QPageSize.Letter, True)]
DPI = 150
JPG_DPI = 200


def page_px(page, dpi):
    for key, _l, ps, land in PAGES:
        if key == page:
            sz = QPageSize(ps).sizePoints()
            w, h = sz.width() / 72 * dpi, sz.height() / 72 * dpi
            return (h, w) if land else (w, h)
    return page_px("a4p", dpi)


def layout(n, W, H, cols, caption, title_h):
    """(columns, rows per page, cell size, top of the grid, margin)."""
    m = W * 0.05
    top = m + title_h
    avail_w, avail_h = W - 2 * m, H - top - m - H * 0.03
    cap = caption * W * 0.022
    if not cols:                                          # about square cells, everything as big as fits
        cols = max(2, min(8, round(math.sqrt(n * avail_w / max(1, avail_h)))))
        while cols > 2 and (n / cols) * (avail_w / cols + cap) < avail_h * 0.5:
            cols -= 1
    gap = W * 0.012
    cell = (avail_w - gap * (cols - 1)) / cols
    rows = max(1, int((avail_h + gap) // (cell + cap + gap)))
    return cols, rows, cell, top, m, gap, cap


def _thumb(path, side):
    li = engine.load_image(path, max_side=max(64, int(side)))
    rgb = cv2.cvtColor(li.bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, w * 3, QImage.Format_RGB888).copy()


def render(paths, opts, progress=lambda d, t, m: None, cancelled=lambda: False):
    """opts: out (path without ending), fmt "pdf"/"jpg", title, subtitle, font, bold, italic, cols (0 = auto),
    page, captions, dark. Returns the list of files written."""
    pdf = opts.get("fmt", "pdf") == "pdf"
    dpi = DPI if pdf else JPG_DPI
    W, H = page_px(opts.get("page", "a4p"), dpi)
    bg, fg = (QColor(18, 18, 20), QColor(240, 240, 240)) if opts.get("dark") else (QColor(255, 255, 255), QColor(25, 25, 28))
    title = opts.get("title") or ""
    tfont = QFont(opts.get("font") or "Georgia")
    tfont.setPixelSize(max(12, int(W * 0.065)))
    tfont.setBold(bool(opts.get("bold")))
    tfont.setItalic(bool(opts.get("italic")))
    tm = QFontMetricsF(tfont)
    sfont = QFont()
    sfont.setPixelSize(max(8, int(W * 0.016)))
    cfont = QFont()
    cfont.setPixelSize(max(7, int(W * 0.012)))
    title_h = (tm.height() * 1.15 if title else 0) + QFontMetricsF(sfont).height() * 1.6
    cols, rows, cell, top, m, gap, cap = layout(len(paths), W, H, int(opts.get("cols") or 0),
                                                bool(opts.get("captions")), title_h)
    per = cols * rows
    pages = max(1, math.ceil(len(paths) / per))
    files = []
    writer = None
    if pdf:
        out = opts["out"] + ".pdf"
        writer = QPdfWriter(out)
        writer.setResolution(dpi)
        land = opts.get("page", "a4p").endswith("l")
        ps = QPageSize(QPageSize.Letter if opts.get("page", "a4p").startswith("let") else QPageSize.A4)
        writer.setPageLayout(QPageLayout(ps, QPageLayout.Landscape if land else QPageLayout.Portrait,
                                         QMarginsF(0, 0, 0, 0)))
        writer.setTitle(title or "Contact sheet")
        writer.setCreator("FaceSort")
        p = QPainter(writer)
        files.append(out)
    sub = opts.get("subtitle") or f"{len(paths):,} photo{'s' if len(paths) != 1 else ''} · {time.strftime('%d %B %Y')}"
    done = 0
    try:
        for pg in range(pages):
            if cancelled():
                break
            if pdf:
                if pg:
                    writer.newPage()
                img = None
            else:
                img = QImage(int(W), int(H), QImage.Format_RGB32)
                p = QPainter(img)
            p.setRenderHint(QPainter.Antialiasing)
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.setRenderHint(QPainter.TextAntialiasing)
            p.fillRect(QRectF(0, 0, W, H), bg)
            y = m
            if title:
                p.setFont(tfont)
                p.setPen(fg)
                p.drawText(QRectF(m, y, W - 2 * m, tm.height() * 1.15), Qt.AlignLeft | Qt.AlignVCenter, title)
                y += tm.height() * 1.15
            p.setFont(sfont)
            p.setPen(QColor(fg.red(), fg.green(), fg.blue(), 150))
            p.drawText(QRectF(m, y, W - 2 * m, QFontMetricsF(sfont).height() * 1.4), Qt.AlignLeft | Qt.AlignVCenter,
                       sub + (f" · page {pg + 1} of {pages}" if pages > 1 else ""))
            p.setPen(QPen(QColor(fg.red(), fg.green(), fg.blue(), 60), max(1.0, W / 1500)))
            p.drawLine(int(m), int(top - gap), int(W - m), int(top - gap))
            for i, path in enumerate(paths[pg * per:(pg + 1) * per]):
                if cancelled():
                    break
                r, c = divmod(i, cols)
                x0 = m + c * (cell + gap)
                y0 = top + r * (cell + cap + gap)
                done += 1
                progress(done, len(paths), os.path.basename(path))
                try:
                    q = _thumb(path, cell * 1.5)
                    s = min(cell / q.width(), cell / q.height())
                    w, h = q.width() * s, q.height() * s
                    p.drawImage(QRectF(x0 + (cell - w) / 2, y0 + (cell - h) / 2, w, h), q)
                except Exception:
                    p.fillRect(QRectF(x0, y0, cell, cell), QColor(128, 128, 128, 60))
                if cap:
                    p.setFont(cfont)
                    p.setPen(QColor(fg.red(), fg.green(), fg.blue(), 170))
                    fm = QFontMetricsF(cfont)
                    p.drawText(QRectF(x0, y0 + cell + 2, cell, cap), Qt.AlignHCenter | Qt.AlignTop,
                               fm.elidedText(os.path.basename(path), Qt.ElideMiddle, cell))
            if not pdf:
                p.end()
                out = opts["out"] + (f" {pg + 1}" if pages > 1 else "") + ".jpg"
                img.save(out, "JPG", 92)
                files.append(out)
    finally:
        if pdf:
            p.end()
    progress(len(paths), len(paths), "Contact sheet made")
    return files
