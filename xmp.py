"""Lossless XMP metadata writing.

* JPEG and PNG: the XMP packet inside the file is updated without re-encoding the image.
* Other formats (HEIC, TIFF, WEBP, BMP...): an .xmp sidecar file next to the photo.

People names go into dc:subject (the "Tags"/keywords Windows Explorer, Lightroom,
digiKam, Bridge, etc. read). Face rectangles go into MWG regions (mwg-rs), which
digiKam, Lightroom, ACDSee and others show as named faces.
Existing metadata from other apps is kept.
"""
import os
import struct
import xml.etree.ElementTree as ET
import zlib

NS = {
    "x": "adobe:ns:meta/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "MicrosoftPhoto": "http://ns.microsoft.com/photo/1.0/",
    "mwg-rs": "http://www.metadataworkinggroup.com/schemas/regions/",
    "stArea": "http://ns.adobe.com/xmp/sType/Area#",
    "stDim": "http://ns.adobe.com/xap/1.0/sType/Dimensions#",
    "facesort": "http://ns.facesort.local/1.0/",
    # common ones, registered only so other apps' data keeps readable prefixes
    "xmp": "http://ns.adobe.com/xap/1.0/",
    "xmpMM": "http://ns.adobe.com/xap/1.0/mm/",
    "stEvt": "http://ns.adobe.com/xap/1.0/sType/ResourceEvent#",
    "stRef": "http://ns.adobe.com/xap/1.0/sType/ResourceRef#",
    "xmpRights": "http://ns.adobe.com/xap/1.0/rights/",
    "xmpNote": "http://ns.adobe.com/xmp/note/",
    "exif": "http://ns.adobe.com/exif/1.0/",
    "exifEX": "http://cipa.jp/exif/1.0/",
    "tiff": "http://ns.adobe.com/tiff/1.0/",
    "photoshop": "http://ns.adobe.com/photoshop/1.0/",
    "crs": "http://ns.adobe.com/camera-raw-settings/1.0/",
    "aux": "http://ns.adobe.com/exif/1.0/aux/",
    "lr": "http://ns.adobe.com/lightroom/1.0/",
    "Iptc4xmpCore": "http://iptc.org/std/Iptc4xmpCore/1.0/xmlns/",
    "Iptc4xmpExt": "http://iptc.org/std/Iptc4xmpExt/2008-02-29/",
    "digiKam": "http://www.digikam.org/ns/1.0/",
    "MP": "http://ns.microsoft.com/photo/1.2/",
    "MPRI": "http://ns.microsoft.com/photo/1.2/t/RegionInfo#",
    "MPReg": "http://ns.microsoft.com/photo/1.2/t/Region#",
    "GPano": "http://ns.google.com/photos/1.0/panorama/",
    "acdsee": "http://ns.acdsee.com/iptc/1.0/",
}
for _p, _u in NS.items():
    ET.register_namespace(_p, _u)


def q(prefix, local):
    return "{%s}%s" % (NS[prefix], local)


RDF_DESC = q("rdf", "Description")
RDF_LI = q("rdf", "li")
RES = {q("rdf", "parseType"): "Resource"}
MANAGED = q("facesort", "regions")

JPEG_XMP_ID = b"http://ns.adobe.com/xap/1.0/\x00"
PNG_XMP_KEY = b"XML:com.adobe.xmp"
MAX_JPEG_XMP = 65533 - len(JPEG_XMP_ID)

IN_FILE_EXTS = {".jpg", ".jpeg", ".jpe", ".png"}
CATEGORY_KEYWORD = "Category: "      # how a category appears among the photo's tags
CATEGORY_HIER = "Categories|"        # ...and as a hierarchical keyword (Lightroom, digiKam)


class XmpError(Exception):
    pass


# ======================================================================= XMP doc
class XmpDoc:
    def __init__(self, packet=None):
        root = None
        if packet:
            txt = packet.strip("\x00 \r\n\t﻿")
            try:
                root = ET.fromstring(txt)
            except ET.ParseError:
                root = None  # unreadable XMP: start fresh rather than fail
        if root is None:
            root = ET.Element(q("x", "xmpmeta"))
            ET.SubElement(root, q("rdf", "RDF"))
        elif root.tag == q("rdf", "RDF"):
            meta = ET.Element(q("x", "xmpmeta"))
            meta.append(root)
            root = meta
        self.root = root
        rdf = root.find(q("rdf", "RDF"))
        if rdf is None:
            rdf = ET.SubElement(root, q("rdf", "RDF"))
        self.rdf = rdf

    def descriptions(self):
        d = self.rdf.findall(RDF_DESC)
        if not d:
            el = ET.SubElement(self.rdf, RDF_DESC)
            el.set(q("rdf", "about"), "")
            d = [el]
        return d

    def get_bag(self, tag):
        out = []
        for d in self.descriptions():
            prop = d.find(tag)
            if prop is None:
                continue
            for li in prop.iter(RDF_LI):
                if li.text and li.text.strip():
                    out.append(li.text.strip())
        return out

    def set_bag(self, tag, values):
        descs = self.descriptions()
        for d in descs:
            for prop in d.findall(tag):
                d.remove(prop)
        if values:
            prop = ET.SubElement(descs[0], tag)
            bag = ET.SubElement(prop, q("rdf", "Bag"))
            for v in values:
                ET.SubElement(bag, RDF_LI).text = v

    # ---- MWG face regions
    def regions_managed_by_us(self):
        has = False
        for d in self.descriptions():
            if d.find(q("mwg-rs", "Regions")) is not None:
                has = True
                if d.get(MANAGED) == "1":
                    return True
        return not has  # nothing there yet -> we may write

    def set_regions(self, regions, width, height):
        """regions: list of (name, x, y, w, h) normalized top-left boxes (display orientation)."""
        descs = self.descriptions()
        for d in descs:
            for r in d.findall(q("mwg-rs", "Regions")):
                d.remove(r)
            if MANAGED in d.attrib:
                del d.attrib[MANAGED]
        if not regions:
            return
        d = descs[0]
        d.set(MANAGED, "1")
        reg = ET.SubElement(d, q("mwg-rs", "Regions"), RES)
        dim = ET.SubElement(reg, q("mwg-rs", "AppliedToDimensions"))
        dim.set(q("stDim", "w"), str(int(width)))
        dim.set(q("stDim", "h"), str(int(height)))
        dim.set(q("stDim", "unit"), "pixel")
        bag = ET.SubElement(ET.SubElement(reg, q("mwg-rs", "RegionList")), q("rdf", "Bag"))
        for name, x, y, w, h in regions:
            li = ET.SubElement(bag, RDF_LI, RES)
            ET.SubElement(li, q("mwg-rs", "Name")).text = name
            ET.SubElement(li, q("mwg-rs", "Type")).text = "Face"
            a = ET.SubElement(li, q("mwg-rs", "Area"))
            a.set(q("stArea", "x"), "%.6f" % (x + w / 2))   # MWG uses the box centre
            a.set(q("stArea", "y"), "%.6f" % (y + h / 2))
            a.set(q("stArea", "w"), "%.6f" % w)
            a.set(q("stArea", "h"), "%.6f" % h)
            a.set(q("stArea", "unit"), "normalized")

    def serialize(self, padding=2048):
        body = ET.tostring(self.root, encoding="unicode")
        pad = "\n".join([" " * 99] * (padding // 100)) if padding else ""
        return ('<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
                + body + "\n" + pad + '\n<?xpacket end="w"?>')


# ======================================================================= JPEG
def _jpeg_split(data):
    if data[:2] != b"\xff\xd8":
        raise XmpError("not a JPEG file")
    pos, n, segs = 2, len(data), []
    while pos + 4 <= n:
        if data[pos] != 0xFF:
            raise XmpError("damaged JPEG header")
        m = data[pos + 1]
        if m == 0xFF:
            pos += 1
            continue
        if m == 0x01 or 0xD0 <= m <= 0xD8:
            pos += 2
            continue
        if m in (0xDA, 0xD9):
            break
        length = struct.unpack(">H", data[pos + 2:pos + 4])[0]
        segs.append(data[pos:pos + 2 + length])
        pos += 2 + length
    if pos >= n:
        raise XmpError("damaged JPEG (no image data)")
    return segs, data[pos:]


def _is_jpeg_xmp(seg):
    return seg[1] == 0xE1 and seg[4:4 + len(JPEG_XMP_ID)] == JPEG_XMP_ID


def _jpeg_read(data):
    segs, _ = _jpeg_split(data)
    for s in segs:
        if _is_jpeg_xmp(s):
            return s[4 + len(JPEG_XMP_ID):].decode("utf-8", "replace")
    return None


def _jpeg_write(data, packet_bytes):
    if len(packet_bytes) > MAX_JPEG_XMP:
        raise XmpError("XMP too large for JPEG")
    segs, rest = _jpeg_split(data)
    payload = JPEG_XMP_ID + packet_bytes
    new = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    out, placed = [], False
    for s in segs:
        if _is_jpeg_xmp(s):
            if not placed:
                out.append(new)
                placed = True
            continue
        out.append(s)
    if not placed:
        idx = 0
        for i, s in enumerate(out):
            if s[1] == 0xE0 or (s[1] == 0xE1 and s[4:10] == b"Exif\x00\x00"):
                idx = i + 1
            else:
                break
        out.insert(idx, new)
    return b"\xff\xd8" + b"".join(out) + rest


# ======================================================================= PNG
PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _png_chunks(data):
    if data[:8] != PNG_SIG:
        raise XmpError("not a PNG file")
    pos, out = 8, []
    while pos + 12 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        out.append((ctype, data[pos:pos + 12 + length]))
        pos += 12 + length
        if ctype == b"IEND":
            break
    return out


def _png_itxt_text(chunk):
    body = chunk[8:-4]
    if not body.startswith(PNG_XMP_KEY + b"\x00"):
        return None
    rest = body[len(PNG_XMP_KEY) + 1:]
    comp = rest[0]
    rest = rest[2:]
    _lang, rest = rest.split(b"\x00", 1)
    _tkey, text = rest.split(b"\x00", 1)
    if comp:
        text = zlib.decompress(text)
    return text.decode("utf-8", "replace")


def _png_read(data):
    for ctype, chunk in _png_chunks(data):
        if ctype == b"iTXt":
            t = _png_itxt_text(chunk)
            if t is not None:
                return t
    return None


def _png_write(data, packet_bytes):
    body = PNG_XMP_KEY + b"\x00\x00\x00\x00\x00" + packet_bytes
    new = struct.pack(">I", len(body)) + b"iTXt" + body + struct.pack(">I", zlib.crc32(b"iTXt" + body) & 0xFFFFFFFF)
    out, placed = [PNG_SIG], False
    for ctype, chunk in _png_chunks(data):
        if ctype == b"iTXt" and _png_itxt_text(chunk) is not None:
            if not placed:
                out.append(new)
                placed = True
            continue
        if ctype == b"IDAT" and not placed:
            out.append(new)
            placed = True
        out.append(chunk)
    return b"".join(out)


# ======================================================================= file level
def sidecar_path(path):
    return os.path.splitext(path)[0] + ".xmp"


def _jpeg_read_header(path):
    """XMP from a JPEG reading only the metadata at the start of the file (not the image)."""
    with open(path, "rb") as f:
        if f.read(2) != b"\xff\xd8":
            raise XmpError("not a JPEG file")
        while True:
            b = f.read(1)
            if not b:
                return None
            if b != b"\xff":
                return None
            m = f.read(1)
            while m == b"\xff":
                m = f.read(1)
            if not m:
                return None
            mk = m[0]
            if mk == 0x01 or 0xD0 <= mk <= 0xD8:
                continue
            if mk in (0xDA, 0xD9):   # image data starts: no XMP before it
                return None
            ln = f.read(2)
            if len(ln) < 2:
                return None
            n = struct.unpack(">H", ln)[0] - 2
            if mk == 0xE1 and n > len(JPEG_XMP_ID):
                head = f.read(len(JPEG_XMP_ID))
                if head == JPEG_XMP_ID:
                    return f.read(n - len(JPEG_XMP_ID)).decode("utf-8", "replace")
                f.seek(n - len(JPEG_XMP_ID), 1)
            else:
                f.seek(n, 1)


def read_xmp(path, sidecar=False):
    if not sidecar:
        ext = os.path.splitext(path)[1].lower()
        if ext != ".png":
            return _jpeg_read_header(path)
        with open(path, "rb") as f:
            data = f.read()
        return _png_read(data)
    sp = sidecar_path(path)
    if os.path.exists(sp):
        with open(sp, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    return None


def _overwrite_in_place(path, new_bytes):
    """Keep the same file (creation date, OneDrive/hardlinks) but never leave a broken
    photo behind: a full copy is written first and removed only after success."""
    backup = path + ".facesort-tmp"
    with open(backup, "wb") as f:
        f.write(new_bytes)
        f.flush()
        os.fsync(f.fileno())
    with open(path, "r+b") as f:
        f.write(new_bytes)
        f.truncate()
        f.flush()
        os.fsync(f.fileno())
    os.remove(backup)


def update_file(path, keywords_add, keywords_remove, regions=None, dims=None,
                always_sidecar=False, keep_mtime=True, private_faces=None, private_people=None,
                category=False):
    """Add/remove keywords (and optionally replace FaceSort's face regions).
    Returns (where_written, final_keywords)."""
    st = os.stat(path)
    ext = os.path.splitext(path)[1].lower()
    use_sidecar = always_sidecar or ext not in IN_FILE_EXTS

    data = None
    if not use_sidecar:
        with open(path, "rb") as f:
            data = f.read()
        packet = _png_read(data) if ext == ".png" else _jpeg_read(data)
    else:
        packet = read_xmp(path, sidecar=True)

    doc = XmpDoc(packet)
    current = doc.get_bag(q("dc", "subject"))
    remove = {k.casefold() for k in keywords_remove}
    final = [k for k in current if k.casefold() not in remove]
    have = {k.casefold() for k in final}
    for k in keywords_add:
        if k.casefold() not in have:
            final.append(k)
            have.add(k.casefold())
    doc.set_bag(q("dc", "subject"), final)
    doc.set_bag(q("MicrosoftPhoto", "LastKeywordXMP"), final)
    if regions is not None and dims and doc.regions_managed_by_us():
        doc.set_regions(regions, *dims)
    if private_faces is not None:
        doc.set_bag(q("facesort", "faces"), [encode_face(*f) for f in private_faces])
    if private_people is not None:
        doc.set_bag(q("facesort", "people"), list(private_people))
    if category is not False:
        # FaceSort's own copy, plus Lightroom/digiKam hierarchical keyword "Categories|<name>"
        for d in doc.descriptions():
            for prop in d.findall(q("facesort", "category")):
                d.remove(prop)
        if category:
            ET.SubElement(doc.descriptions()[0], q("facesort", "category")).text = category
        hier = [h for h in doc.get_bag(q("lr", "hierarchicalSubject")) if not h.startswith(CATEGORY_HIER)]
        if category:
            hier.append(CATEGORY_HIER + category)
        doc.set_bag(q("lr", "hierarchicalSubject"), hier)

    if not use_sidecar:
        try:
            for pad in (2048, 0):
                pb = doc.serialize(pad).encode("utf-8")
                if ext != ".png" and len(pb) > MAX_JPEG_XMP:
                    continue
                new = _png_write(data, pb) if ext == ".png" else _jpeg_write(data, pb)
                _overwrite_in_place(path, new)
                if keep_mtime:
                    os.utime(path, (st.st_atime, st.st_mtime))
                return "file", final
        except XmpError:
            pass
        # metadata too large / unusual file: fall back to a sidecar
    sp = sidecar_path(path)
    tmp = sp + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(doc.serialize(0))
    os.replace(tmp, sp)
    return "sidecar", final


# ======================================================================= reading names back
def encode_face(name, x, y, w, h):
    return "%.5f %.5f %.5f %.5f %s" % (x, y, w, h, name)


def _decode_face(txt):
    parts = txt.split(" ", 4)
    if len(parts) != 5:
        return None
    try:
        x, y, w, h = (float(v) for v in parts[:4])
    except ValueError:
        return None
    name = parts[4].strip()
    return (name, x, y, w, h) if name and w > 0 and h > 0 else None


def _prop(el, tag):
    """A property given as attribute, child element, or inside a nested rdf:Description."""
    if tag in el.attrib:
        return el.attrib[tag]
    for holder in [el] + el.findall(RDF_DESC):
        if tag in holder.attrib:
            return holder.attrib[tag]
        c = holder.find(tag)
        if c is not None:
            return c
    return None


def _text(v):
    if v is None:
        return None
    if isinstance(v, str):
        return v.strip()
    return (v.text or "").strip()


def face_hints_from_packet(packet, orientation=1):
    """Named faces stored in XMP, as (name, x, y, w, h) normalized top-left boxes in
    display orientation. Sources, best first:
      1. FaceSort's own list (any photo)
      2. MWG face regions (digiKam, Lightroom, Picasa exports, FaceSort) - unrotated photos only
      3. Windows Photo Gallery people tags - unrotated photos only"""
    if not packet:
        return []
    try:
        doc = XmpDoc(packet)
    except Exception:
        return []
    own = [f for f in (_decode_face(t) for t in doc.get_bag(q("facesort", "faces"))) if f]
    if own:
        return own
    if (orientation or 1) != 1:
        return []  # apps disagree about rotated photos; don't guess
    out = []
    for rl in doc.root.iter(q("mwg-rs", "RegionList")):
        for li in rl.iter(RDF_LI):
            name = _text(_prop(li, q("mwg-rs", "Name")))
            typ = _text(_prop(li, q("mwg-rs", "Type")))
            area = _prop(li, q("mwg-rs", "Area"))
            if not name or (typ and typ.lower() != "face") or area is None or isinstance(area, str):
                continue
            vals = {}
            for k in ("x", "y", "w", "h", "unit"):
                vals[k] = _text(_prop(area, q("stArea", k)))
            try:
                if vals["unit"] not in (None, "", "normalized"):
                    continue
                cx, cy, w, h = (float(vals[k]) for k in ("x", "y", "w", "h"))
            except (TypeError, ValueError):
                continue
            out.append((name, cx - w / 2, cy - h / 2, w, h))
    if out:
        return out
    for li in doc.root.iter(RDF_LI):
        name = _text(_prop(li, q("MPReg", "PersonDisplayName")))
        rect = _text(_prop(li, q("MPReg", "Rectangle")))
        if not name or not rect:
            continue
        try:
            x, y, w, h = (float(v) for v in rect.replace(" ", "").split(","))
        except ValueError:
            continue
        out.append((name, x, y, w, h))
    return out


def read_face_hints(path, orientation=1):
    """Names saved in the photo itself, or else in its .xmp sidecar."""
    hints = []
    ext = os.path.splitext(path)[1].lower()
    if ext in IN_FILE_EXTS:
        try:
            hints = face_hints_from_packet(read_xmp(path), orientation)
        except Exception:
            hints = []
    if not hints:
        try:
            hints = face_hints_from_packet(read_xmp(path, sidecar=True), orientation)
        except Exception:
            hints = []
    return hints


def read_photo_people(path):
    """People FaceSort tagged on this photo without a face box."""
    for sidecar in (False, True):
        if not sidecar and os.path.splitext(path)[1].lower() not in IN_FILE_EXTS:
            continue
        try:
            packet = read_xmp(path, sidecar=sidecar)
            if packet:
                names = XmpDoc(packet).get_bag(q("facesort", "people"))
                if names:
                    return names
        except Exception:
            pass
    return []


def read_category(path):
    """The category FaceSort saved in this photo (or its sidecar), or None."""
    for sidecar in (False, True):
        if not sidecar and os.path.splitext(path)[1].lower() not in IN_FILE_EXTS:
            continue
        try:
            packet = read_xmp(path, sidecar=sidecar)
            if not packet:
                continue
            doc = XmpDoc(packet)
            for d in doc.descriptions():
                c = d.find(q("facesort", "category"))
                if c is not None and (c.text or "").strip():
                    return c.text.strip()
            for k in doc.get_bag(q("dc", "subject")):
                if k.startswith(CATEGORY_KEYWORD) and k[len(CATEGORY_KEYWORD):].strip():
                    return k[len(CATEGORY_KEYWORD):].strip()
        except Exception:
            pass
    return None


PEOPLE_ROOTS = ("people", "persons", "person", "personen", "personnes", "people tags", "faces")


def read_keywords(path):
    """Keywords already on a photo, split into (people, plain):
    people = names from hierarchical people tags (Lightroom/digiKam "People|Jake", "Persons/Jake")
    plain  = ordinary keywords (Windows Explorer tags, dc:subject, XPKeywords), categories excluded."""
    people, plain = [], []

    def add(lst, v):
        v = " ".join(str(v).split())
        if v and v.casefold() not in {x.casefold() for x in lst}:
            lst.append(v)

    packets = []
    for sidecar in (False, True):
        if not sidecar and os.path.splitext(path)[1].lower() not in IN_FILE_EXTS:
            continue
        try:
            pk = read_xmp(path, sidecar=sidecar)
            if pk:
                packets.append(pk)
        except Exception:
            pass
    for pk in packets:
        try:
            doc = XmpDoc(pk)
        except Exception:
            continue
        hier = doc.get_bag(q("lr", "hierarchicalSubject")) + doc.get_bag(q("digiKam", "TagsList"))
        for h in hier:
            parts = [x.strip() for x in h.replace("/", "|").split("|") if x.strip()]
            if len(parts) >= 2 and parts[0].casefold() in PEOPLE_ROOTS:
                add(people, parts[-1])
        for k in doc.get_bag(q("dc", "subject")) + doc.get_bag(q("MicrosoftPhoto", "LastKeywordXMP")):
            if not k.startswith(CATEGORY_KEYWORD) and "|" not in k:
                add(plain, k)
    try:  # Windows "Tags" stored in EXIF (XPKeywords, UTF-16)
        from PIL import Image
        with Image.open(path) as im:
            raw = im.getexif().get(0x9C9E)
        if raw:
            txt = bytes(raw).decode("utf-16-le", "ignore") if not isinstance(raw, str) else raw
            for k in txt.replace("\x00", "").split(";"):
                if k.strip() and not k.strip().startswith(CATEGORY_KEYWORD):
                    add(plain, k)
    except Exception:
        pass
    return people, plain
