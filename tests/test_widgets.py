"""widgets.py の描画に使う純関数の検証（ラベル・タイル文字・損益の色段階）。

描画そのもの（QPainter）はテストしない。文字列の組み立てと色の境界だけを固定する。
"""

from __future__ import annotations

import pytest

from src import theme
from src.widgets import (
    TILE_RATE_MID,
    TILE_RATE_STRONG,
    _duplicate_symbols,
    _label_text,
    _tooltip_head,
    profit_tile_colors,
    tile_lines,
)


def _tile(symbol, name="", account="", **extra):
    return {"symbol": symbol, "name": name, "account": account, **extra}


# ---- 同じ銘柄を複数口座で持つ行の区別（T6） ----


def test_duplicate_symbols_finds_only_repeated_codes():
    tiles = [_tile("A", account="特定"), _tile("B"), _tile("A", account="NISA")]
    assert _duplicate_symbols(tiles) == {"A"}
    assert _duplicate_symbols([_tile("A"), _tile("B")]) == set()
    assert _duplicate_symbols([]) == set()


def test_label_text_adds_account_only_for_duplicated_symbols():
    a1 = _tile("8316", "三井住友", "特定")
    a2 = _tile("8316", "三井住友", "NISA（成長）")
    dups = _duplicate_symbols([a1, a2])
    assert _label_text(a1, dups) == "三井住友(特定・8316)"
    assert _label_text(a2, dups) == "三井住友(NISA（成長）・8316)"


def test_label_text_unchanged_without_duplicates_or_account():
    assert _label_text(_tile("8316", "三井住友", "特定")) == "三井住友(8316)"
    # 重複していても口座が空なら従来どおり。
    assert _label_text(_tile("8316", "三井住友", ""), {"8316"}) == "三井住友(8316)"
    # 名前が無ければコードだけ（重複なら口座付き）。
    assert _label_text(_tile("PFF", "", "特定"), {"PFF"}) == "特定・PFF"
    assert _label_text(_tile("PFF", "", "特定")) == "PFF"


def test_label_text_keeps_other_row():
    other = {"symbol": "その他", "name": "(3銘柄)", "is_other": True}
    assert _label_text(other, {"その他"}) == "その他(3銘柄)"


def test_tooltip_head_shows_account_line_when_present():
    assert _tooltip_head(_tile("8316", "三井住友", "特定")) == "8316  三井住友\n口座: 特定"
    assert _tooltip_head(_tile("8316", "三井住友", "")) == "8316  三井住友"
    other = {"symbol": "その他", "name": "(3銘柄)", "is_other": True}
    assert _tooltip_head(other) == "その他  (3銘柄)"


# ---- ヒートマップのタイル文字（T11） ----


def test_tile_lines_stock_keeps_code_then_name():
    tile = _tile("7203", "トヨタ", "特定", asset_type="stock")
    assert tile_lines(tile) == ("7203", "トヨタ")


def test_tile_lines_fund_uses_name_as_heading_and_account_as_sub():
    tile = _tile("JP90C000J569", "SBI V S&P500", "NISA（成長）", asset_type="fund")
    assert tile_lines(tile) == ("SBI V S&P500", "NISA（成長）")


def test_tile_lines_fund_without_account_has_no_sub():
    tile = _tile("JP90C000J569", "SBI V S&P500", "", asset_type="fund")
    assert tile_lines(tile) == ("SBI V S&P500", "")


def test_tile_lines_fund_without_name_falls_back_to_code():
    tile = _tile("JP90C000J569", "", "特定", asset_type="fund")
    assert tile_lines(tile) == ("JP90C000J569", "特定")


# ---- 損益の色段階（T7） ----


def test_tile_rate_boundaries_are_ten_and_fifty_percent():
    assert (TILE_RATE_MID, TILE_RATE_STRONG) == (0.10, 0.50)


@pytest.mark.parametrize("sign, strong, mid", [
    (1, "TILE_UP_STRONG", "TILE_UP_MID"),
    (-1, "TILE_DOWN_STRONG", "TILE_DOWN_MID"),
])
def test_profit_tile_colors_three_steps_at_ten_and_fifty_percent(sign, strong, mid):
    def bg(rate):
        return profit_tile_colors(sign * rate)[0].name()

    from PySide6.QtGui import QColor

    def token(name):
        return QColor(getattr(theme, name)).name()

    assert bg(0.0999) == token("TILE_WEAK_BG")
    assert bg(0.10) == token(mid)
    assert bg(0.4999) == token(mid)
    assert bg(0.50) == token(strong)
    assert bg(3.0) == token(strong)


def test_profit_tile_colors_zero_and_unknown_are_neutral():
    from PySide6.QtGui import QColor

    neutral = QColor(theme.TILE_NEUTRAL_BG).name()
    assert profit_tile_colors(0)[0].name() == neutral
    assert profit_tile_colors(None)[0].name() == neutral
