"""分割の補正アクション・旗の表示（QMessageBox をスタブ化し、本体を実行する）。

実ウィンドウ（メニュー・ツールバー・選択に応じた有効/無効）は
tests/headless_smoke.py:_check_split_flags が担当する。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from PySide6.QtGui import QColor

import src.gui as gui
from src import splits, theme
from src.models import Holding, MarketData

YES = gui.QMessageBox.StandardButton.Yes
NO = gui.QMessageBox.StandardButton.No


def _holding(symbol="5334", **overrides) -> Holding:
    base = dict(
        symbol=symbol, name="日特殊陶", asset_type="stock", currency="JPY",
        shares=300.0, avg_cost=2503.0, account="特定",
        annual_div_ps_gross_override=None, annual_div_ps_net_override=None,
        div_forecast_as_of_override=None, annual_benefit_jpy=0.0,
        withdrawal_fixed_jpy=None, withdrawal_rate=None, tax_rate=None, active=True,
    )
    base.update(overrides)
    return Holding(**base)


def _split_day() -> str:
    return (date.today() - timedelta(days=3)).isoformat()


def _market(symbol="5334", price=3737.0, with_split=True) -> MarketData:
    return MarketData(
        symbol=symbol, currency="JPY", last_price=price, price_as_of="2026-09-30",
        annual_div_ps_gross=108.5, annual_div_ps_net=None, div_forecast_as_of=None,
        source="yfinance", fetched_at=None,
        recent_splits=[(_split_day(), 2.0)] if with_split else [],
    )


class _Stub:
    """DividendWindow の分割アクションが触る最小の土台（メソッドは本物を借りる）。"""

    _holding_label = staticmethod(gui.DividendWindow._holding_label)
    _commit_split_holdings = gui.DividendWindow._commit_split_holdings
    _split_notice = gui.DividendWindow._split_notice

    def __init__(self, holdings, market_map, selected_index=None, show_inactive=False):
        self.holdings = holdings
        self.market_data_map = market_map
        self._busy = False
        self._selected_index = selected_index
        self.show_inactive = show_inactive
        self.reloaded = False
        self.notes: list[tuple[str, bool]] = []

    def _selected_pending_splits(self):
        if self._selected_index is None:
            return None
        holding = self.holdings[self._selected_index]
        events = splits.pending_splits(holding, self.market_data_map.get(holding.symbol))
        return (self._selected_index, events) if events else None

    def _save_or_notify(self, action):
        action()
        return True

    def _reload_all(self):
        self.reloaded = True

    def _notify(self, message, error=False):
        self.notes.append((message, error))


@pytest.fixture
def saved(monkeypatch):
    calls: dict = {"rows": None}
    monkeypatch.setattr(gui, "save_holdings", lambda rows: calls.update(rows=rows))
    return calls


def _answer(monkeypatch, button):
    asked: dict = {}

    def fake_question(_parent, title, text, *_a, **_k):
        asked.update(title=title, text=text)
        return button

    monkeypatch.setattr(gui.QMessageBox, "question", fake_question)
    return asked


# ---- 補正（株数×比率・単価÷比率・配当上書き÷比率） ----

def test_apply_split_corrects_selected_row_after_confirm(monkeypatch, saved):
    _answer(monkeypatch, YES)
    stub = _Stub(
        [_holding(annual_div_ps_gross_override=108.0), _holding("8316", name="三井住友")],
        {"5334": _market(), "8316": _market("8316")},
        selected_index=0,
    )
    gui.DividendWindow.action_apply_split(stub)
    row = saved["rows"][0]
    assert (row.shares, row.avg_cost, row.annual_div_ps_gross_override) == (600.0, 1251.5, 54.0)
    assert row.split_applied_through == _split_day()
    # 選択していない行は触らない。
    assert saved["rows"][1] == stub.holdings[1]
    assert stub.reloaded is True
    assert stub.notes and stub.notes[-1][1] is False


def test_apply_split_confirmation_shows_before_after_price_and_escape_hatch(monkeypatch, saved):
    asked = _answer(monkeypatch, NO)
    stub = _Stub([_holding()], {"5334": _market()}, selected_index=0)
    gui.DividendWindow.action_apply_split(stub)
    text = asked["text"]
    assert "株数: 300 → 600" in text and "購入単価: 2,503 → 1,251.5" in text
    assert "現在株価: 3,737" in text
    # 手で直済みの行への二重適用を避ける逃げ道を、確認文面で案内する。
    assert "分割を反映済みにする" in text and "二重" in text


def test_apply_split_declined_changes_nothing(monkeypatch, saved):
    _answer(monkeypatch, NO)
    stub = _Stub([_holding()], {"5334": _market()}, selected_index=0)
    gui.DividendWindow.action_apply_split(stub)
    assert saved["rows"] is None and stub.reloaded is False


def test_apply_split_does_nothing_when_busy_or_nothing_pending(monkeypatch, saved):
    asked = _answer(monkeypatch, YES)
    busy = _Stub([_holding()], {"5334": _market()}, selected_index=0)
    busy._busy = True
    gui.DividendWindow.action_apply_split(busy)
    no_flag = _Stub([_holding()], {"5334": _market(with_split=False)}, selected_index=0)
    gui.DividendWindow.action_apply_split(no_flag)
    no_selection = _Stub([_holding()], {"5334": _market()}, selected_index=None)
    gui.DividendWindow.action_apply_split(no_selection)
    assert asked == {} and saved["rows"] is None


def test_pressing_apply_twice_does_not_double_apply(monkeypatch, saved):
    """補正後は旗が消えるので、続けて押しても株数が4倍にならない。"""
    _answer(monkeypatch, YES)
    stub = _Stub([_holding()], {"5334": _market()}, selected_index=0)
    gui.DividendWindow.action_apply_split(stub)
    stub.holdings = saved["rows"]
    saved["rows"] = None
    gui.DividendWindow.action_apply_split(stub)
    assert saved["rows"] is None
    assert stub.holdings[0].shares == 600.0


# ---- 反映済みにする（数値は変えない） ----

def test_dismiss_split_records_baseline_without_touching_numbers(monkeypatch, saved):
    _answer(monkeypatch, YES)
    original = _holding(annual_div_ps_gross_override=108.5)
    stub = _Stub([original], {"5334": _market()}, selected_index=0)
    gui.DividendWindow.action_dismiss_split(stub)
    row = saved["rows"][0]
    assert (row.shares, row.avg_cost, row.annual_div_ps_gross_override) == (300.0, 2503.0, 108.5)
    assert row.split_applied_through == _split_day()
    assert splits.pending_splits(row, _market()) == []


def test_dismiss_split_declined_changes_nothing(monkeypatch, saved):
    _answer(monkeypatch, NO)
    stub = _Stub([_holding()], {"5334": _market()}, selected_index=0)
    gui.DividendWindow.action_dismiss_split(stub)
    assert saved["rows"] is None


# ---- まとめて反映 ----

def test_apply_all_corrects_every_pending_row_and_skips_the_rest(monkeypatch, saved):
    asked = _answer(monkeypatch, YES)
    done = _holding("8316", name="三井住友", split_applied_through=_split_day())
    holdings = [_holding(), done, _holding("9999", name="対象外")]
    market = {"5334": _market(), "8316": _market("8316"), "9999": _market("9999", with_split=False)}
    stub = _Stub(holdings, market)
    gui.DividendWindow.action_apply_all_splits(stub)
    rows = saved["rows"]
    assert rows[0].shares == 600.0
    assert rows[1] == done and rows[2] == holdings[2]
    assert "1 行" in asked["text"]  # 対象は未反映の1行だけ
    assert stub.reloaded is True


def test_apply_all_declined_or_nothing_pending_changes_nothing(monkeypatch, saved):
    _answer(monkeypatch, NO)
    gui.DividendWindow.action_apply_all_splits(_Stub([_holding()], {"5334": _market()}))
    assert saved["rows"] is None
    asked = _answer(monkeypatch, YES)
    gui.DividendWindow.action_apply_all_splits(
        _Stub([_holding()], {"5334": _market(with_split=False)})
    )
    assert asked == {} and saved["rows"] is None


# ---- 取得後の通知 ----

def test_split_notice_lists_symbols_and_is_empty_when_none():
    stub = _Stub([_holding(), _holding("8316")], {"5334": _market(), "8316": _market("8316")})
    notice = gui.DividendWindow._split_notice(stub)
    assert "2件" in notice and "5334" in notice and "8316" in notice
    assert gui.DividendWindow._split_notice(
        _Stub([_holding()], {"5334": _market(with_split=False)})
    ) == ""


def test_inactive_rows_follow_show_inactive_for_notice_and_apply_all(monkeypatch, saved):
    """非表示の無効銘柄は通知・一括反映から外れ、表示中のみ対象になること。"""
    inactive = _holding("5334", account="旧NISA", active=False)
    market_map = {"5334": _market()}

    # 無効銘柄が非表示の既定状態では通知も一括反映も発火しない
    hidden_stub = _Stub([inactive], market_map, show_inactive=False)
    assert gui.DividendWindow._split_notice(hidden_stub) == ""
    asked = _answer(monkeypatch, YES)
    gui.DividendWindow.action_apply_all_splits(hidden_stub)
    assert asked == {} and saved["rows"] is None

    # 無効銘柄を表示しているときは通知にも一括反映にも含まれる
    shown_stub = _Stub([inactive], market_map, show_inactive=True)
    assert "1件" in gui.DividendWindow._split_notice(shown_stub)
    asked = _answer(monkeypatch, YES)
    gui.DividendWindow.action_apply_all_splits(shown_stub)
    assert "1 行" in asked["text"]
    assert saved["rows"][0].shares == 600.0


# ---- 旗の表示（列・文字・色・並び替え） ----

def _row(**overrides) -> dict:
    row = {"symbol": "5334", "name": "日特殊陶", "active": True,
           "pending_splits": [(_split_day(), 2.0)]}
    row.update(overrides)
    return row


def test_flag_column_shows_short_text_in_flag_color():
    text, color = gui._format_cell(_row(), "split_flag")
    assert text.startswith("⚑ 2:1(")
    assert color == QColor(theme.SPLIT_FLAG)


def test_flag_column_is_blank_without_pending_split():
    assert gui._format_cell(_row(pending_splits=[]), "split_flag") == ("", None)
    assert gui._format_cell({"symbol": "5334"}, "split_flag") == ("", None)


def test_applied_split_shows_a_done_mark_in_a_different_color():
    """反映済みにした行は、旗を消さず「✓」と別の色で残す（押したことが画面で読める）。"""
    row = _row(pending_splits=[], applied_splits=[(_split_day(), 2.0)])
    text, color = gui._format_cell(row, "split_flag")
    assert text.startswith("✓ 2:1(")
    assert color == QColor(theme.SPLIT_DONE)
    assert color != QColor(theme.SPLIT_FLAG)


def test_pending_takes_precedence_over_done_mark():
    row = _row(applied_splits=[("2026-03-30", 4.0)])  # 未反映の旗が優先
    text, color = gui._format_cell(row, "split_flag")
    assert text.startswith("⚑ ") and color == QColor(theme.SPLIT_FLAG)


def test_sort_order_is_pending_then_done_then_blank():
    pending = gui.cell_sort_value(_row(), "split_flag")
    done = gui.cell_sort_value(
        _row(pending_splits=[], applied_splits=[(_split_day(), 2.0)]), "split_flag"
    )
    blank = gui.cell_sort_value(_row(pending_splits=[]), "split_flag")
    assert pending < done and blank is None  # 昇順で 旗 → 完了 → 空(末尾)


def test_done_tooltip_explains_the_mark_and_when_it_fades():
    stub = _Stub([_holding()], {"5334": _market()})
    row = _row(pending_splits=[], applied_splits=[(_split_day(), 2.0)])
    tip = gui.DividendWindow._split_flag_tooltip(stub, row, 0)
    assert "反映済み" in tip and "2:1" in tip
    assert str(splits.APPLIED_DISPLAY_DAYS) in tip
    assert gui.DividendWindow._split_flag_tooltip(stub, _row(pending_splits=[]), 0) == ""


def test_done_color_follows_the_theme():
    try:
        theme.set_theme("light")
        light = gui._format_cell(
            _row(pending_splits=[], applied_splits=[(_split_day(), 2.0)]), "split_flag"
        )[1]
        theme.set_theme("dark")
        dark = gui._format_cell(
            _row(pending_splits=[], applied_splits=[(_split_day(), 2.0)]), "split_flag"
        )[1]
    finally:
        theme.set_theme(theme.DEFAULT_THEME)
    assert light != dark


def test_flag_color_follows_the_theme():
    """色は描画時に theme から読む（値で import すると切替後も旧テーマの色が残る）。"""
    try:
        theme.set_theme("light")
        light = gui._format_cell(_row(), "split_flag")[1]
        theme.set_theme("dark")
        dark = gui._format_cell(_row(), "split_flag")[1]
    finally:
        theme.set_theme(theme.DEFAULT_THEME)
    assert light != dark


def test_flagged_rows_sort_first_and_blank_rows_sink():
    assert gui.cell_sort_value(_row(), "split_flag") == 1.0
    assert gui.cell_sort_value(_row(pending_splits=[]), "split_flag") is None


def test_flag_column_sits_after_the_filtered_columns_in_both_tables():
    # 絞り込みは列 0/1/2（コード・銘柄名・口座）を見るので、旗はその後ろに置く。
    for columns in (gui.PRICE_COLUMNS, gui.INCOME_COLUMNS):
        keys = [key for _, key in columns]
        assert keys[:3] == ["symbol", "name", "account"]
        assert keys.index("split_flag") >= 3
    assert "split_flag" in gui.NUMERIC_SORT_COLUMNS
    assert "split_flag" in gui.LEFT_ALIGNED_SORT_COLUMNS


def test_split_actions_are_defined_once_with_safe_shortcuts():
    defs = {key: seq for key, _label, seq, _slot in gui.DividendWindow._ACTION_DEFS}
    for key in ("split_apply", "split_dismiss", "split_apply_all"):
        assert key in defs
    # 一文字キーは表で銘柄コードを打つだけで誤爆する。修飾キー付きのみ。
    assert defs["split_apply"] == "Ctrl+Shift+S"
    assert defs["split_dismiss"] is None and defs["split_apply_all"] is None
