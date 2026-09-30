"""Dark theme + colour helpers."""
from PySide6.QtGui import QColor

BG = "#0f1117"
PANEL = "#171a23"
BORDER = "#2a2f3d"
TEXT = "#e6e8ef"
MUTED = "#8b91a3"
ACCENT = "#14f195"   # Solana green
PURPLE = "#9945ff"
GREEN = "#22c55e"
YELLOW = "#eab308"
ORANGE = "#f97316"
RED = "#ef4444"

STYLESHEET = f"""
QWidget {{ background: {BG}; color: {TEXT}; font-family: 'Segoe UI'; font-size: 10pt; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; background: {PANEL}; }}
QTabBar::tab {{ background: {BG}; color: {MUTED}; padding: 7px 14px; border: 1px solid {BORDER};
               border-bottom: none; font-weight: 600; }}
QTabBar::tab:selected {{ background: {PANEL}; color: {ACCENT}; }}
QTableView {{ background: {PANEL}; alternate-background-color: #1b1f2a; gridline-color: {BORDER};
             selection-background-color: #2b3350; border: none; }}
QHeaderView::section {{ background: {BG}; color: {MUTED}; padding: 5px; border: none;
                        border-bottom: 1px solid {BORDER}; font-weight: 600; }}
QPushButton {{ background: #232838; border: 1px solid {BORDER}; padding: 6px 12px; border-radius: 4px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton#primary {{ background: {PURPLE}; border: none; font-weight: 700; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background: {PANEL}; border: 1px solid {BORDER};
             padding: 4px; border-radius: 3px; }}
QPlainTextEdit {{ background: {PANEL}; color: {MUTED}; border: 1px solid {BORDER}; font-family: Consolas; font-size: 9pt; }}
QTextBrowser {{ background: {PANEL}; border: 1px solid {BORDER}; }}
QFrame#card {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px; }}
QFrame#card:hover {{ border-color: {ACCENT}; }}
QLabel#title {{ font-size: 16pt; font-weight: 800; color: {ACCENT}; }}
QLabel#muted {{ color: {MUTED}; }}
QGroupBox {{ border: 1px solid {BORDER}; margin-top: 12px; padding-top: 8px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; color: {ACCENT}; }}
QStatusBar {{ color: {MUTED}; }}
"""


def score_color(v) -> QColor:
    if v is None:
        return QColor(MUTED)
    return QColor(GREEN if v >= 75 else YELLOW if v >= 55 else ORANGE if v >= 35 else MUTED)


def risk_color(v) -> QColor:
    if v is None:
        return QColor(MUTED)
    return QColor(GREEN if v <= 30 else YELLOW if v <= 60 else ORANGE if v <= 80 else RED)


def risk_hex(v) -> str:
    return risk_color(v).name()


def score_hex(v) -> str:
    return score_color(v).name()
