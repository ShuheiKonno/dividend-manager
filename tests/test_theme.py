"""theme.py の QSS 生成・パレット整合のテスト（Qt 非依存の純ロジック）。"""

from __future__ import annotations

import re

import pytest

from src import theme
from src.theme import build_app_qss


@pytest.fixture(autouse=True)
def _restore_theme():
    """テーマはモジュールグローバルなので、各テストの後で既定へ戻す。"""
    yield
    theme.set_theme(theme.DEFAULT_THEME)


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
    for theme_name in theme.THEMES:
        theme.set_theme(theme_name)
        qss = build_app_qss()
        for selector in required:
            assert selector in qss, f"{theme_name} の QSS に {selector} がありません"


def test_qss_does_not_define_arrow_subcontrols():
    """矢印サブコントロールは画像リソースが必要になるため定義しない（Fusion に任せる）。"""
    qss = build_app_qss()
    assert "::down-arrow" not in qss
    assert "::up-arrow" not in qss


def test_palettes_share_the_same_token_set():
    """片方のパレットへ足し忘れると、そのテーマだけ AttributeError になる。"""
    keys = [set(p) for p in theme.PALETTES.values()]
    for other in keys[1:]:
        assert other == keys[0], f"パレットのキーが不一致: {other ^ keys[0]}"
    assert set(theme.TOKEN_NAMES) == keys[0]


def test_color_tokens_are_hex():
    for theme_name, palette in theme.PALETTES.items():
        for name, value in palette.items():
            assert re.fullmatch(r"#[0-9a-fA-F]{6}", value), (
                f"{theme_name}.{name}={value!r} が #rrggbb 形式でない"
            )


def test_set_theme_rebinds_module_attributes():
    """トークンは「定数」ではなく現在値。set_theme が張り替える。"""
    theme.set_theme("dark")
    dark_bg = theme.BG_WINDOW
    assert theme.CURRENT_THEME == "dark" and theme.is_dark()

    theme.set_theme("light")
    assert theme.CURRENT_THEME == "light" and not theme.is_dark()
    assert theme.BG_WINDOW == theme.PALETTES["light"]["BG_WINDOW"] != dark_bg
    assert theme.BG_WINDOW in build_app_qss()


def test_set_theme_falls_back_for_unknown_name():
    """設定ファイルを手編集して壊れた値が入っても起動不能にしない。"""
    assert theme.set_theme("solarized") == theme.DEFAULT_THEME
    assert theme.CURRENT_THEME == theme.DEFAULT_THEME


def test_light_theme_inverts_surface_and_text():
    """ライトは面が明るく文字が暗いこと（配色を取り違えていない最低限の確認）。"""

    def luminance(hex_color: str) -> float:
        r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
        return 0.299 * r + 0.587 * g + 0.114 * b

    for key in ("BG_WINDOW", "BG_TABLE", "CHART_BG"):
        assert luminance(theme.PALETTES["light"][key]) > 0.7, key
        assert luminance(theme.PALETTES["dark"][key]) < 0.3, key
    for key in ("TEXT_PRIMARY", "TEXT_HEADER"):
        assert luminance(theme.PALETTES["light"][key]) < 0.5, key
        assert luminance(theme.PALETTES["dark"][key]) > 0.5, key
