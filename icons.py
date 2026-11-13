"""FaceSort's icons, drawn in the style of the app icon: a blue rounded tile with a bold white
symbol. Drawn in code (sharp at any size, nothing extra to ship)."""
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

TOP = QColor(47, 116, 230)       # the app icon's blue, top to bottom
BOTTOM = QColor(72, 80, 190)
ORANGE_TOP = QColor(240, 150, 60)
ORANGE_BOTTOM = QColor(214, 104, 40)
GREY_TOP = QColor(130, 138, 150)
GREY_BOTTOM = QColor(96, 104, 118)
RED_TOP = QColor(226, 84, 84)
RED_BOTTOM = QColor(180, 52, 70)

_cache = {}


def _tile(p, s, tint):
    top, bottom = {"orange": (ORANGE_TOP, ORANGE_BOTTOM), "grey": (GREY_TOP, GREY_BOTTOM),
                   "red": (RED_TOP, RED_BOTTOM)}.get(tint, (TOP, BOTTOM))
    g = QLinearGradient(0, 0, 0, s)
    g.setColorAt(0, top)
    g.setColorAt(1, bottom)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(g))
    p.drawRoundedRect(QRectF(s * 0.03, s * 0.03, s * 0.94, s * 0.94), s * 0.22, s * 0.22)


def _person(p, cx, top, k, s):
    """Head + shoulders like the app icon. k = scale (1 = the app icon's size)."""
    r = 9.5 * k
    p.drawEllipse(QPointF(cx, top + r), r, r)
    body = QPainterPath()
    w, h = 21 * k, 15 * k
    y = top + 2 * r + 3.5 * k
    body.moveTo(cx - w, y + h)
    body.cubicTo(cx - w, y - 1 * k, cx + w, y - 1 * k, cx + w, y + h)
    body.closeSubpath()
    p.drawPath(body)


def _folder(p, s, x=12, y=17, w=40, h=30, open_=False):
    path = QPainterPath()
    path.moveTo(x, y + 4)
    path.quadTo(x, y, x + 4, y)
    path.lineTo(x + w * 0.38, y)
    path.lineTo(x + w * 0.48, y + 5)
    path.lineTo(x + w - 3, y + 5)
    path.quadTo(x + w, y + 5, x + w, y + 8)
    path.lineTo(x + w, y + h - 3)
    path.quadTo(x + w, y + h, x + w - 3, y + h)
    path.lineTo(x + 3, y + h)
    path.quadTo(x, y + h, x, y + h - 3)
    path.closeSubpath()
    p.drawPath(path)
    if open_:
        p.save()
        p.setBrush(QColor(255, 255, 255, 120))
        p.setPen(Qt.NoPen)
        front = QPainterPath()
        front.moveTo(x + 6, y + 12)
        front.lineTo(x + w + 4, y + 12)
        front.lineTo(x + w - 4, y + h)
        front.lineTo(x - 2, y + h)
        front.closeSubpath()
        p.drawPath(front)
        p.restore()


def _text(p, s, text, size=26):
    f = QFont()
    f.setBold(True)
    f.setPixelSize(size)
    p.setFont(f)
    p.setPen(QColor(255, 255, 255))
    p.drawText(QRectF(0, 0, 64, 64), Qt.AlignCenter, text)


def _draw(p, name):
    """The white symbol on a 64 x 64 canvas."""
    white = QColor(255, 255, 255)
    thick = QPen(white, 6, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin)
    round_ = QPen(white, 5.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setBrush(white)
    p.setPen(Qt.NoPen)
    if name in ("person", "app"):
        if name == "app":
            p.setPen(QPen(white, 4.5, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
            for x, y, dx, dy in ((12, 12, 1, 1), (52, 12, -1, 1), (12, 52, 1, -1), (52, 52, -1, -1)):
                p.drawLine(QPointF(x, y), QPointF(x + 9 * dx, y))
                p.drawLine(QPointF(x, y), QPointF(x, y + 9 * dy))
            p.setPen(Qt.NoPen)
            _person(p, 32, 17, 0.85, 64)
        else:
            _person(p, 32, 12, 1.0, 64)
    elif name == "people":
        p.setBrush(QColor(255, 255, 255, 170))
        _person(p, 41, 14, 0.72, 64)
        p.setBrush(white)
        _person(p, 25, 18, 0.82, 64)
    elif name in ("folder", "folder_open", "folder_add", "export", "drop", "folder_person"):
        _folder(p, 64, open_=(name == "folder_open"))
        if name in ("folder_add", "export", "drop", "folder_person"):
            p.setPen(QPen(BOTTOM, 5, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
            if name == "folder_add":
                p.drawLine(QPointF(32, 26), QPointF(32, 42))
                p.drawLine(QPointF(24, 34), QPointF(40, 34))
            elif name == "export":
                p.drawLine(QPointF(23, 35), QPointF(39, 35))
                p.drawLine(QPointF(33, 29), QPointF(39, 35))
                p.drawLine(QPointF(33, 41), QPointF(39, 35))
            elif name == "drop":
                p.drawLine(QPointF(32, 25), QPointF(32, 41))
                p.drawLine(QPointF(26, 35), QPointF(32, 41))
                p.drawLine(QPointF(38, 35), QPointF(32, 41))
            else:
                p.setPen(Qt.NoPen)
                p.setBrush(BOTTOM)
                _person(p, 32, 25, 0.45, 64)
    elif name == "trash":
        p.drawRect(QRectF(16, 17, 32, 5))
        p.drawRect(QRectF(26, 12, 12, 5))
        path = QPainterPath()
        path.moveTo(19, 25)
        path.lineTo(45, 25)
        path.lineTo(42, 52)
        path.lineTo(22, 52)
        path.closeSubpath()
        p.drawPath(path)
        p.setPen(QPen(BOTTOM, 2.5))
        for x in (27, 32, 37):
            p.drawLine(QPointF(x, 30), QPointF(x, 47))
    elif name == "question":
        _text(p, 64, "?", 38)
    elif name == "help":
        p.setPen(round_)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(32, 32), 19, 19)
        p.setPen(white)
        _text(p, 64, "?", 26)
    elif name == "info":
        p.setPen(round_)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(32, 32), 19, 19)
        p.setPen(Qt.NoPen)
        p.setBrush(white)
        p.drawEllipse(QPointF(32, 23), 3.5, 3.5)
        p.drawRect(QRectF(29, 29, 6, 15))
    elif name == "clock":
        p.setPen(round_)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(32, 32), 19, 19)
        p.drawLine(QPointF(32, 32), QPointF(32, 21))
        p.drawLine(QPointF(32, 32), QPointF(40, 37))
    elif name in ("image", "nofaces"):
        p.setPen(QPen(white, 5, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(12, 15, 40, 34), 4, 4)
        p.setPen(Qt.NoPen)
        p.setBrush(white)
        tri = QPainterPath()
        tri.moveTo(16, 46)
        tri.lineTo(28, 30)
        tri.lineTo(36, 40)
        tri.lineTo(41, 34)
        tri.lineTo(49, 46)
        tri.closeSubpath()
        p.drawPath(tri)
        p.drawEllipse(QPointF(41, 24), 4, 4)
    elif name == "album":                     # photos stacked in a book
        p.setPen(QPen(white, 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(18, 12, 34, 30), 3, 3)
        p.drawLine(QPointF(12, 20), QPointF(12, 48))
        p.drawLine(QPointF(12, 48), QPointF(44, 48))
        p.setPen(Qt.NoPen)
        p.setBrush(white)
        tri = QPainterPath()
        tri.moveTo(22, 38)
        tri.lineTo(31, 26)
        tri.lineTo(37, 33)
        tri.lineTo(41, 29)
        tri.lineTo(48, 38)
        tri.closeSubpath()
        p.drawPath(tri)
    elif name == "search":
        p.setPen(QPen(white, 5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(28, 28), 13, 13)
        p.drawLine(QPointF(38, 38), QPointF(50, 50))
    elif name == "compare":                   # two pictures side by side
        p.setPen(QPen(white, 4, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(9, 18, 20, 28), 3, 3)
        p.drawRoundedRect(QRectF(35, 18, 20, 28), 3, 3)
        p.drawLine(QPointF(32, 12), QPointF(32, 52))
    elif name == "wand":                      # a magic wand with sparkles (photo tools)
        p.setPen(QPen(white, 6, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(14, 50), QPointF(38, 26))
        p.setPen(Qt.NoPen)
        p.setBrush(white)
        for cx, cy, r in ((44, 16, 7), (24, 14, 4), (52, 34, 4)):
            p.drawPolygon(QPolygonF([QPointF(cx, cy - r), QPointF(cx + r * 0.3, cy - r * 0.3), QPointF(cx + r, cy),
                                     QPointF(cx + r * 0.3, cy + r * 0.3), QPointF(cx, cy + r),
                                     QPointF(cx - r * 0.3, cy + r * 0.3), QPointF(cx - r, cy),
                                     QPointF(cx - r * 0.3, cy - r * 0.3)]))
    elif name == "dupes":
        p.setPen(QPen(white, 5, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(13, 13, 26, 26), 3, 3)
        p.setBrush(white)
        p.drawRoundedRect(QRectF(25, 25, 26, 26), 3, 3)
    elif name == "ai":
        p.setPen(white)
        _text(p, 64, "AI", 26)
    elif name == "sparkle":
        path = QPainterPath()
        path.moveTo(32, 10)
        path.quadTo(35, 29, 54, 32)
        path.quadTo(35, 35, 32, 54)
        path.quadTo(29, 35, 10, 32)
        path.quadTo(29, 29, 32, 10)
        p.drawPath(path)
    elif name == "warning":
        tri = QPainterPath()
        tri.moveTo(32, 11)
        tri.lineTo(54, 51)
        tri.lineTo(10, 51)
        tri.closeSubpath()
        p.drawPath(tri)
        p.setBrush(BOTTOM)
        p.drawRect(QRectF(29.5, 24, 5, 15))
        p.drawEllipse(QPointF(32, 45), 3, 3)
    elif name in ("expand", "collapse"):
        p.setPen(thick)
        p.setBrush(Qt.NoBrush)
        d = 1 if name == "expand" else -1
        for y in (24, 38):
            yy = y if d == 1 else 64 - y
            p.drawLine(QPointF(19, yy - 5 * d), QPointF(32, yy + 6 * d))
            p.drawLine(QPointF(32, yy + 6 * d), QPointF(45, yy - 5 * d))
    elif name in ("back", "next", "prev"):
        p.setPen(thick)
        if name == "back":
            p.drawLine(QPointF(48, 32), QPointF(18, 32))
            p.drawLine(QPointF(30, 20), QPointF(18, 32))
            p.drawLine(QPointF(30, 44), QPointF(18, 32))
        else:
            d = 1 if name == "next" else -1
            p.drawLine(QPointF(32 - 7 * d, 18), QPointF(32 + 7 * d, 32))
            p.drawLine(QPointF(32 + 7 * d, 32), QPointF(32 - 7 * d, 46))
    elif name in ("scan", "rotate_left", "rotate_right"):
        p.setPen(round_)
        p.setBrush(Qt.NoBrush)
        rect = QRectF(14, 14, 36, 36)
        if name == "rotate_left":
            # anticlockwise: the line runs 9 → 6 → 3 → 12 o'clock and the arrow at its end points
            # left (the gap is top-left, like ↺)
            p.drawArc(rect, 180 * 16, 270 * 16)
            p.setPen(Qt.NoPen)
            p.setBrush(white)
            tri = QPainterPath()
            tri.moveTo(30, 6)
            tri.lineTo(30, 22)
            tri.lineTo(20, 14)
            tri.closeSubpath()
            p.drawPath(tri)
        else:
            # clockwise (rotate right): 3 → 6 → 9 → 12 o'clock, the arrow at the end points right
            # (gap top-right, like ↻). (The scan icon was drawn the old way - it keeps that.)
            p.drawArc(rect, (0 if name == "rotate_right" else 90) * 16, -270 * 16)
            p.setPen(Qt.NoPen)
            p.setBrush(white)
            tri = QPainterPath()
            tri.moveTo(34, 6)
            tri.lineTo(34, 22)
            tri.lineTo(44, 14)
            tri.closeSubpath()
            p.drawPath(tri)
    elif name == "video":
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(10, 20, 30, 24), 4, 4)
        tri = QPainterPath()
        tri.moveTo(42, 32)
        tri.lineTo(54, 22)
        tri.lineTo(54, 42)
        tri.closeSubpath()
        p.drawPath(tri)
    elif name == "play":
        tri = QPainterPath()
        tri.moveTo(23, 15)
        tri.lineTo(49, 32)
        tri.lineTo(23, 49)
        tri.closeSubpath()
        p.drawPath(tri)
    elif name == "pause":
        p.drawRect(QRectF(20, 16, 9, 32))
        p.drawRect(QRectF(35, 16, 9, 32))
    elif name == "tag":
        path = QPainterPath()
        path.moveTo(12, 14)
        path.lineTo(32, 14)
        path.lineTo(52, 34)
        path.lineTo(34, 52)
        path.lineTo(12, 30)
        path.closeSubpath()
        p.drawPath(path)
        p.setBrush(BOTTOM)
        p.drawEllipse(QPointF(22, 24), 4, 4)
    elif name == "save":
        p.drawRoundedRect(QRectF(13, 13, 38, 38), 4, 4)
        p.setBrush(BOTTOM)
        p.drawRect(QRectF(20, 13, 22, 12))
        p.drawRect(QRectF(20, 33, 24, 14))
    elif name == "settings":
        p.setPen(Qt.NoPen)
        p.save()
        p.translate(32, 32)
        for i in range(8):
            p.save()
            p.rotate(i * 45)
            p.drawRect(QRectF(-4.5, -22, 9, 10))
            p.restore()
        p.drawEllipse(QPointF(0, 0), 15, 15)
        p.setBrush(BOTTOM)
        p.drawEllipse(QPointF(0, 0), 6, 6)
        p.restore()
    elif name in ("zoom_in", "zoom_out", "search"):
        p.setPen(round_)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(28, 28), 13, 13)
        p.drawLine(QPointF(38, 38), QPointF(51, 51))
        if name != "search":
            p.setPen(QPen(white, 4, Qt.SolidLine, Qt.SquareCap))
            p.drawLine(QPointF(22, 28), QPointF(34, 28))
            if name == "zoom_in":
                p.drawLine(QPointF(28, 22), QPointF(28, 34))
    elif name in ("fit", "fullscreen"):
        p.setPen(QPen(white, 5, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
        out = name == "fullscreen"
        for x, y, dx, dy in ((14, 14, 1, 1), (50, 14, -1, 1), (14, 50, 1, -1), (50, 50, -1, -1)):
            p.drawLine(QPointF(x, y), QPointF(x + 10 * dx, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + 10 * dy))
        if not out:
            p.setPen(Qt.NoPen)
            p.drawRect(QRectF(24, 24, 16, 16))
    elif name == "sort":
        p.setPen(thick)
        for i, w in enumerate((32, 24, 16)):
            p.drawLine(QPointF(14, 20 + i * 12), QPointF(14 + w, 20 + i * 12))
        p.setPen(round_)
        p.drawLine(QPointF(50, 16), QPointF(50, 46))
        p.drawLine(QPointF(44, 40), QPointF(50, 47))
        p.drawLine(QPointF(56, 40), QPointF(50, 47))
    elif name == "grid":                      # a 4 x 3 collage
        p.setPen(Qt.NoPen)
        for r in range(3):
            for c in range(4):
                p.drawRoundedRect(QRectF(11 + c * 11, 17 + r * 11, 9, 9), 1.5, 1.5)
    elif name == "calendar":
        p.drawRoundedRect(QRectF(12, 15, 40, 36), 4, 4)
        p.setBrush(BOTTOM)
        p.drawRect(QRectF(16, 27, 32, 20))
        p.setBrush(white)
        for i in range(3):
            for j in range(2):
                p.drawRect(QRectF(19 + i * 10, 30 + j * 9, 6, 5))
    elif name == "pin":
        path = QPainterPath()
        path.moveTo(32, 54)
        path.cubicTo(18, 38, 14, 32, 14, 26)
        path.arcTo(QRectF(14, 8, 36, 36), 180, -180)
        path.cubicTo(50, 32, 46, 38, 32, 54)
        p.drawPath(path)
        p.setBrush(BOTTOM)
        p.drawEllipse(QPointF(32, 26), 7, 7)
    elif name == "cancel":
        p.setPen(thick)
        p.drawLine(QPointF(19, 19), QPointF(45, 45))
        p.drawLine(QPointF(45, 19), QPointF(19, 45))
    elif name == "check":
        p.setPen(thick)
        p.drawLine(QPointF(16, 33), QPointF(27, 44))
        p.drawLine(QPointF(27, 44), QPointF(48, 21))
    elif name in ("lock", "unlock"):
        p.setPen(QPen(white, 5.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        arc = QPainterPath()
        if name == "lock":
            arc.moveTo(22, 30)
            arc.lineTo(22, 22)
            arc.arcTo(QRectF(22, 11, 20, 22), 180, -180)
            arc.lineTo(42, 30)
        else:
            arc.moveTo(22, 30)
            arc.lineTo(22, 22)
            arc.arcTo(QRectF(22, 11, 20, 22), 180, -180)
            arc.lineTo(42, 24)
            arc = arc.translated(9, -3)
        p.drawPath(arc)
        p.setPen(Qt.NoPen)
        p.setBrush(white)
        p.drawRoundedRect(QRectF(16, 29, 32, 24), 4, 4)
        p.setBrush(BOTTOM)
        p.drawEllipse(QPointF(32, 39), 3.5, 3.5)
        p.drawRect(QRectF(30.5, 40, 3, 7))
    elif name == "filmstrip":
        p.drawRect(QRectF(10, 18, 44, 28))
        p.setBrush(BOTTOM)
        for i in range(5):
            p.drawRect(QRectF(13 + i * 8.5, 21, 4, 3))
            p.drawRect(QRectF(13 + i * 8.5, 40, 4, 3))
    elif name == "partial":                   # two people, one ticked (a group photo, partly tagged)
        p.setPen(Qt.NoPen)
        p.setBrush(white)
        p.drawEllipse(QPointF(22, 22), 7, 7)
        p.drawPie(QRectF(9, 31, 26, 26), 0, 180 * 16)
        p.setBrush(QColor(255, 255, 255, 150))
        p.drawEllipse(QPointF(42, 22), 7, 7)
        p.drawPie(QRectF(29, 31, 26, 26), 0, 180 * 16)
        p.setPen(QPen(QColor(34, 197, 94), 5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPolyline(QPolygonF([QPointF(12, 49), QPointF(18, 55), QPointF(30, 41)]))
    elif name == "men":                       # the male sign
        p.setPen(QPen(white, 6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(27, 37), 13, 13)
        p.drawLine(QPointF(36.5, 27.5), QPointF(50, 14))
        p.drawPolyline(QPolygonF([QPointF(36, 14), QPointF(50, 14), QPointF(50, 28)]))
    elif name == "unsure":                    # a head with a question mark
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 120))
        p.drawEllipse(QPointF(32, 30), 18, 18)
        p.drawPie(QRectF(12, 46, 40, 30), 0, 180 * 16)
        p.setPen(white)
        _text(p, 64, "?", 30)
    else:
        _text(p, 64, name[:1].upper(), 30)


def app_icon(name, tint=None):
    """QIcon of a symbol (see _draw) on the app's blue tile. tint: "orange", "grey", "red"."""
    key = (name, tint)
    if key not in _cache:
        icon = QIcon()
        for size in (16, 20, 24, 32, 36, 48, 64):
            pm = QPixmap(size, size)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            _tile(p, size, tint)
            p.scale(size / 64, size / 64)
            _draw(p, name)
            p.end()
            icon.addPixmap(pm)
        _cache[key] = icon
    return _cache[key]
