"""Settings > Viewer (1.54): viewing, editing photos, videos."""
from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout, QWidget


class ViewerSettingsPage(QWidget):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        s = settings
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 14, 10)

        def head(text):
            lb = QLabel(f"<b>{text}</b>")
            lay.addWidget(lb)

        head("Viewing")
        f = QFormLayout()
        self.slide = QComboBox()
        for sec in (2, 3, 5, 8, 10, 15):
            self.slide.addItem(f"{sec} seconds a photo", sec)
        self.slide.setCurrentIndex(max(0, self.slide.findData(int(s.get("viewer_slideshow_s", 3)))))
        f.addRow("Slideshow speed:", self.slide)
        self.boxes = QCheckBox("Show face boxes when the viewer opens (B switches them)")
        self.boxes.setChecked(not s.get("viewer_hide_boxes", False))
        f.addRow("", self.boxes)
        lay.addLayout(f)
        lay.addSpacing(8)
        head("Editing photos")
        f = QFormLayout()
        note = QLabel("Save copy is always the default · Replace original is in the Save menu")
        note.setStyleSheet("color: gray;")
        f.addRow("Saving:", note)
        self.copy_name = QLineEdit(s.get("edit_copy_name") or "{name} (edited)")
        self.copy_name.setToolTip("{name} is the original's name. Also used for clips saved from video edit mode.")
        f.addRow("Copy name:", self.copy_name)
        self.quality = QComboBox()
        for q, label in ((95, "95 (very high)"), (90, "90 (high)"), (85, "85 (smaller files)")):
            self.quality.addItem(label, q)
        self.quality.setCurrentIndex(max(0, self.quality.findData(int(s.get("edit_jpeg_quality", 95)))))
        f.addRow("JPEG quality:", self.quality)
        self.keep_people = QCheckBox("Edited copies keep the people and tags of the original")
        self.keep_people.setChecked(bool(s.get("edit_keep_people", True)))
        f.addRow("", self.keep_people)
        lay.addLayout(f)
        lay.addSpacing(8)
        head("Videos")
        f = QFormLayout()
        self.autoplay = QCheckBox("Start playing when a video opens")
        self.autoplay.setChecked(bool(s.get("video_autoplay", False)))
        f.addRow("", self.autoplay)
        self.keep_speed = QComboBox()
        self.keep_speed.addItem("Back to 1× for each video", False)
        self.keep_speed.addItem("Keep the last speed", True)
        self.keep_speed.setCurrentIndex(1 if s.get("video_keep_speed") else 0)
        f.addRow("Speed:", self.keep_speed)
        vol = QLabel("Volume and mute are remembered between videos and restarts.")
        vol.setStyleSheet("color: gray;")
        f.addRow("Volume:", vol)
        self.clip_q = QComboBox()
        self.clip_q.addItem("High quality (bigger file)", "high")
        self.clip_q.addItem("Smaller file", "smaller")
        self.clip_q.setCurrentIndex(max(0, self.clip_q.findData(s.get("edit_clip_quality", "high"))))
        f.addRow("Saved clips:", self.clip_q)
        lay.addLayout(f)
        lay.addStretch()

    def save_to(self, s):
        s["viewer_slideshow_s"] = self.slide.currentData()
        s["viewer_hide_boxes"] = not self.boxes.isChecked()
        s["edit_copy_name"] = self.copy_name.text().strip() or "{name} (edited)"
        s["edit_jpeg_quality"] = self.quality.currentData()
        s["edit_keep_people"] = self.keep_people.isChecked()
        s["video_autoplay"] = self.autoplay.isChecked()
        s["video_keep_speed"] = bool(self.keep_speed.currentData())
        s["edit_clip_quality"] = self.clip_q.currentData()
