"""theme.py の QSS 生成スモークテスト（Qt 非依存の純ロジック）。"""

from __future__ import annotations

import re

from src import theme
from src.theme import build_app_qss


def test_build_app_qss_returns_str_with_required_selectors():
    qss = build_app_qss()
    assert isinstance(qss, str)
    required = [
        "QMainWindow, QWidget",
        "QMenuBar",
        "QMenu",
        "QTabBar::tab",
        "QTableView",
        "QHeaderView::section",
        "QPushButton",
        "QLineEdit",
        'QLineEdit[error="true"]',
        "QComboBox",
        "QAbstractSpinBox",
        "QCheckBox::indicator",
        "QScrollBar",
        "QProgressBar",
        "QStatusBar",
        "QToolTip",
        "QGroupBox",
        "QSplitter::handle",
        "#kpiCard",
        "#kpiTitle",
        "#kpiValue",
        "#infoLabel",
        "#dialogError",
        "#primaryAction",
    ]
    for selector in required:
        assert selector in qss, f"QSS に {selector} がありません"


def test_qss_does_not_define_arrow_subcontrols():
    """矢印サブコントロールは画像リソースが必要になるため定義しない（Fusion に任せる）。"""
    qss = build_app_qss()
    assert "::down-arrow" not in qss
    assert "::up-arrow" not in qss


def test_color_tokens_are_hex():
    for name in (
        "BG_WINDOW", "BG_PANEL", "BG_INPUT", "BG_TABLE", "BG_HOVER",
        "BORDER", "BORDER_FOCUS", "ACCENT",
        "ACCENT_HOVER", "ACCENT_PRESSED", "ACCENT_TEXT", "ACCENT_FOCUS",
        "TEXT_PRIMARY", "TEXT_SECONDARY", "TEXT_BRIGHT",
        "ERROR", "CHART_BG",
    ):
        value = getattr(theme, name)
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", value), f"{name}={value!r} が #rrggbb 形式でない"
