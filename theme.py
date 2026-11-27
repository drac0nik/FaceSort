"""The look (1.63): Graphite - dark surfaces, one accent, amber for "waiting for you", green for
"confirmed" - so the photos are the colour and the chrome stays quiet. "Classic" keeps the old light
look (Settings > Appearance)."""
from PySide6.QtGui import QColor, QPalette

ACCENT = "#6E9BFF"
ACCENT_INK = "#0E1630"           # text on the accent
AMBER = "#F2B544"
GREEN = "#57C27A"
GROUND = "#18191C"               # the window
PANEL = "#1E1F23"                # side panels
RAISED = "#24262B"               # buttons, cards
MENU = "#26282D"
LINE = "#2E3035"
LINE2 = "#3A3C43"
TEXT = "#E6E7EA"
TEXT2 = "#A9ABB1"                # secondary
TEXT3 = "#8D8F96"                # labels
SELECT = "#2C3550"               # a picked row
HOVER = "#2A2D34"

THEMES = ("graphite", "classic")


def current(settings):
    t = (settings or {}).get("theme", "graphite")
    return t if t in THEMES else "graphite"


def is_dark(settings):
    return current(settings) == "graphite"


def palette(base):
    pal = QPalette(base)
    c = {QPalette.Window: GROUND, QPalette.WindowText: TEXT, QPalette.Base: "#141518",
         QPalette.AlternateBase: PANEL, QPalette.ToolTipBase: MENU, QPalette.ToolTipText: TEXT,
         QPalette.Text: TEXT, QPalette.Button: RAISED, QPalette.ButtonText: TEXT, QPalette.BrightText: "#FFFFFF",
         QPalette.Highlight: SELECT, QPalette.HighlightedText: "#FFFFFF", QPalette.Link: "#8FB0FF",
         QPalette.Mid: LINE2, QPalette.Midlight: HOVER, QPalette.Dark: "#101113", QPalette.Shadow: "#000000",
         QPalette.Light: LINE2, QPalette.PlaceholderText: TEXT3}
    for group in (QPalette.Active, QPalette.Inactive):
        for role, col in c.items():
            pal.setColor(group, role, QColor(col))
    for role, col in ((QPalette.Text, "#6B6D74"), (QPalette.WindowText, "#6B6D74"), (QPalette.ButtonText, "#6B6D74")):
        pal.setColor(QPalette.Disabled, role, QColor(col))
    return pal


def stylesheet():
    return f"""
QMainWindow, QDialog {{ background: {GROUND}; }}
QWidget {{ color: {TEXT}; }}
QToolTip {{ background: {MENU}; color: {TEXT}; border: 1px solid {LINE2}; padding: 5px 7px; border-radius: 6px; }}
QToolBar#header {{ background: #1F2024; border: 0; border-bottom: 1px solid {LINE}; padding: 6px 10px; spacing: 8px; }}
QToolBar#header QToolButton {{ border-radius: 8px; padding: 5px 10px; }}
QToolBar#header QToolButton:hover {{ background: {HOVER}; }}
QToolButton[pill="true"], QPushButton[pill="true"] {{ background: {RAISED}; border: 1px solid {LINE2}; border-radius: 8px;
    padding: 5px 12px; }}
QToolButton[primary="true"], QPushButton[primary="true"] {{ background: {ACCENT}; color: {ACCENT_INK}; border: 0;
    border-radius: 8px; padding: 6px 14px; font-weight: 600; }}
QToolButton[primary="true"]:hover, QPushButton[primary="true"]:hover {{ background: #86ACFF; }}
QToolButton[primary="true"]::menu-indicator, QToolButton[pill="true"]::menu-indicator {{ image: none; width: 0; }}
QLabel#statuspill {{ background: {RAISED}; color: {TEXT2}; border-radius: 13px; padding: 4px 12px; }}
QLineEdit {{ background: #141518; border: 1px solid {LINE}; border-radius: 8px; padding: 5px 8px; color: {TEXT};
    selection-background-color: {SELECT}; }}
QLineEdit:focus {{ border: 1px solid {ACCENT}; }}
QLineEdit#omni {{ padding: 6px 10px; font-size: 10.5pt; }}
QPushButton {{ background: {RAISED}; border: 1px solid {LINE2}; border-radius: 8px; padding: 6px 14px; color: {TEXT}; }}
QPushButton:hover {{ background: #2C2E34; }}
QPushButton:pressed {{ background: #303238; }}
QPushButton:disabled {{ color: #6B6D74; border-color: {LINE}; }}
QPushButton:default {{ background: {ACCENT}; color: {ACCENT_INK}; border: 0; font-weight: 600; }}
QToolButton {{ border-radius: 7px; padding: 4px 8px; color: {TEXT}; }}
QToolButton:hover {{ background: {HOVER}; }}
QToolButton:checked {{ background: #2C2E34; color: #FFFFFF; }}
QToolButton[rail="true"] {{ border-radius: 10px; padding: 6px 2px; color: {TEXT3}; font-size: 8pt; }}
QToolButton[rail="true"]:checked {{ background: {HOVER}; color: {TEXT}; }}
QToolButton[rail="true"]:hover:!checked {{ color: {TEXT}; }}
QWidget#rail {{ background: #1B1C20; border-right: 1px solid #2A2C31; }}
QWidget#sidepanel {{ background: {PANEL}; }}
QWidget#details {{ background: {PANEL}; }}
QLabel#paneltitle {{ font-weight: 600; font-size: 11pt; }}
QLabel#sectionlabel {{ color: {TEXT3}; font-size: 8pt; font-weight: 600; letter-spacing: 1px; }}
QFrame#card {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 14px; }}
QFrame#card[warn="true"] {{ border: 1px solid #4A3E20; }}
QFrame#selbar {{ background: #2A2C32; border: 1px solid {LINE2}; border-radius: 12px; }}
QFrame#selbar QToolButton {{ padding: 7px 11px; border-radius: 8px; }}
QTreeView, QListView, QTreeWidget, QListWidget {{ background: {PANEL}; border: 0; outline: 0;
    selection-background-color: {SELECT}; selection-color: #FFFFFF; }}
QTreeView::item {{ padding: 3px 0; border-radius: 6px; }}
QTreeView::item:hover {{ background: {HOVER}; }}
QTreeView::item:selected {{ background: {SELECT}; color: #FFFFFF; }}
QListView#gallery {{ background: {GROUND}; }}
QHeaderView::section {{ background: {PANEL}; color: {TEXT3}; border: 0; border-bottom: 1px solid {LINE}; padding: 4px 6px; }}
QMenu {{ background: {MENU}; border: 1px solid {LINE2}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 6px 26px 6px 12px; border-radius: 6px; color: {TEXT}; }}
QMenu::item:selected {{ background: {SELECT}; color: #FFFFFF; }}
QMenu::item:disabled {{ color: #6B6D74; }}
QMenu::separator {{ height: 1px; background: {LINE2}; margin: 5px 6px; }}
QMenu::icon {{ padding-left: 8px; }}
QComboBox {{ background: {RAISED}; border: 1px solid {LINE2}; border-radius: 8px; padding: 4px 10px; color: {TEXT}; }}
QComboBox QAbstractItemView {{ background: {MENU}; border: 1px solid {LINE2}; selection-background-color: {SELECT}; }}
QCheckBox, QRadioButton {{ spacing: 7px; color: {TEXT}; }}
QSlider::groove:horizontal {{ height: 4px; background: {LINE2}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: #FFFFFF; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QProgressBar {{ background: {LINE}; border: 0; border-radius: 3px; height: 6px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #3A3C43; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #4A4C54; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #3A3C43; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QSplitter::handle {{ background: #2A2C31; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QStatusBar {{ background: #1B1C20; color: {TEXT2}; border-top: 1px solid #2A2C31; }}
QTabBar::tab {{ background: transparent; color: {TEXT3}; padding: 6px 12px; border: 0; border-radius: 6px; }}
QTabBar::tab:selected {{ background: #2C2E34; color: {TEXT}; }}
QTabWidget::pane {{ border: 1px solid {LINE}; border-radius: 8px; }}
QGroupBox {{ border: 1px solid {LINE}; border-radius: 10px; margin-top: 14px; padding-top: 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {TEXT2}; }}
QFrame#prog_strip {{ background: #1B1C20; border: 0; border-top: 1px solid #2A2C31; }}
"""


def apply(app, settings):
    """Sets the look for the whole app. Returns True if it's the dark one."""
    if not is_dark(settings):
        return False
    app.setPalette(palette(app.palette()))
    app.setStyleSheet(stylesheet())
    return True
