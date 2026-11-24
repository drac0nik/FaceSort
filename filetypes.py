"""File types for the gallery's "Sort by type" (1.58.3).

Micro: one heading per file type (JPG, PNG, GIF, MOV, MP4, PDF, ZIP…).
Macro: similar types together - Images, GIF (on its own), Video, Office documents, PDF, Web links,
Archives, Text, Audio, Other."""
import os

import engine

# the order the headings come in
MACRO = ["Images", "RAW photos", "GIF", "Video", "Office documents", "PDF", "Web links", "Archives", "Text",
         "Audio", "Other files"]

_GROUPS = {
    "GIF": {".gif"},
    "Office documents": {".doc", ".docx", ".docm", ".dot", ".dotx", ".xls", ".xlsx", ".xlsm", ".xlsb", ".csv",
                         ".ppt", ".pptx", ".pptm", ".pps", ".ppsx", ".odt", ".ods", ".odp", ".rtf", ".pages",
                         ".numbers", ".key", ".pub", ".vsd", ".vsdx", ".one"},
    "PDF": {".pdf"},
    "Web links": {".url", ".webloc", ".website", ".lnk", ".html", ".htm", ".mht", ".mhtml", ".desktop"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".cab", ".iso"},
    "Text": {".txt", ".md", ".nfo", ".log", ".json", ".xml", ".ini", ".yaml", ".yml", ".srt", ".vtt", ".ass"},
    "Audio": {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".aiff", ".aif", ".mid", ".midi"},
}
_SAME = {".jpeg": ".jpg", ".jpe": ".jpg", ".jfif": ".jpg", ".tif": ".tiff", ".htm": ".html", ".heif": ".heic",
         ".mpeg": ".mpg", ".yml": ".yaml", ".aif": ".aiff", ".midi": ".mid"}


def ext_of(path):
    e = os.path.splitext(path)[1].lower()
    return _SAME.get(e, e)


def macro(path):
    e = os.path.splitext(path)[1].lower()
    for name, exts in _GROUPS.items():
        if e in exts:
            return name
    if engine.is_raw(path):
        return "RAW photos"
    if engine.is_video(path):
        return "Video"
    if e in engine.IMAGE_EXTS:
        return "Images"
    return "Other files"


def micro(path):
    e = ext_of(path)
    return e[1:].upper() if e else "No extension"


def micro_order(label_and_path):
    """Micro headings: grouped like the macro ones (images first), then A-Z."""
    label, path = label_and_path
    return (MACRO.index(macro(path)), label)
