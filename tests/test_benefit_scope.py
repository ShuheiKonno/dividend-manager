"""「対象」の優待チェック：選んでいない資産の優待銘柄を、配当を数えずに加える。

資産（日本株 / 米国株 / 投信）を絞って優待を選ぶと、外した資産の銘柄でも優待が
あれば一覧・KPI に入る（評価額・簿価・損益・優待は数え、配当は数えない）。
資産を1つも選ばず優待だけを選ぶ（「優待のみ」）こともできる。

PySide6 非依存。表示層の配線は tests/headless_smoke.py:_check_asset_scope が見る。
"""

from __future__ import annotations

from itertools import combinations

import pytest

from src.calc import (
    ASSET_CLASSES,
    asset_scope_label,
    compute_rows,
    is_valid_selection,
    monthly_breakdown,
    scope_holdings,
)
from src.history import (
    INCOME_BASE_KEYS,
    benefit_holder_breakdown,
    class_breakdown,
    combine_income,
    scope_history_record,
    scoped_history_start,
)
from src.models import (
    BENEFIT_HOLDER_HISTORY_FIELDS,
    HISTORY_FIELDS,
    HistoryRecord,
    benefit_holder_history_key,
)
from tests.test_asset_scope import _holding, _market

FX = 150.0


def _portfolio():
    """各クラスに優待あり・なしの銘柄を混ぜる（投信の優待は珍しいが分類は効く）。"""
    holdings = [
        _holding("7203", annual_benefit_jpy=3000.0),  # 日本株・優待あり
        _holding("8306", shares=200.0, avg_cost=1000.0),  # 日本株・優待なし
        _holding("AAPL", currency="USD", shares=10.0, avg_cost=150.0),  # 米国株
        _holding(  # 米国株・優待あり（分類が通貨で効くことを見る）
            "USB", currency="USD", shares=20.0, avg_cost=50.0, annual_benefit_jpy=1000.0,
        ),
        _holding(  # 投信・優待あり。取崩の額が除外されることを見る
            "FUND1", asset_type="fund", shares=50.0, avg_cost=10000.0,
            withdrawal_fixed_jpy=120000.0, withdrawal_rate=0.04, annual_benefit_jpy=500.0,
        ),
        _holding("FUND2", asset_type="fund", shares=30.0, avg_cost=10000.0),
        _holding("9999", active=False, annual_benefit_jpy=9000.0),  # 無効は入らない
    ]
    market = {
        "7203": _market("7203"),
        "8306": _market("8306", last_price=1500.0, gross=40.0),
        "AAPL": _market("AAPL", "USD", 200.0, 1.0),
        "USB": _market("USB", "USD", 60.0, 2.0),
        "FUND1": _market("FUND1", last_price=12000.0, gross=100.0),
        "FUND2": _market("FUND2", last_price=11000.0, gross=50.0),
        "9999": _market("9999"),
    }
    return holdings, market


def _selection(holdings, market, scope, mode=None, dividend_mode="gross"):
    picked = [h for _, h in scope_holdings(holdings, scope, with_benefit=True)]
    return compute_rows(
        picked, market, FX, dividend_mode, mode, dividend_scope=scope
    )


SUBSETS = [
    combo for n in range(0, len(ASSET_CLASSES) + 1)
    for combo in combinations(ASSET_CLASSES, n)
]


# ---- 選択の妥当性・表示名 ----

def test_selection_needs_at_least_one_of_four():
    assert is_valid_selection((), True)  # 優待のみ
    assert not is_valid_selection((), False)  # 全部外すと全画面が空
    assert is_valid_selection(("fund",), False)


def test_selection_label():
    assert asset_scope_label(ASSET_CLASSES, True) == "全て"
    assert asset_scope_label(ASSET_CLASSES, False) == "全て"
    assert asset_scope_label(("us_stock",), True) == "米国株＋優待"
    assert asset_scope_label(("us_stock",), False) == "米国株"
    assert asset_scope_label((), True) == "優待のみ"


# ---- 対象の銘柄 ----

def test_scope_holdings_adds_benefit_holders_of_unselected_classes():
    holdings, _ = _portfolio()
    symbols = lambda scope, b: [h.symbol for _, h in scope_holdings(holdings, scope, with_benefit=b)]  # noqa: E731
    assert symbols(("us_stock",), False) == ["AAPL", "USB"]
    assert symbols(("us_stock",), True) == ["7203", "AAPL", "USB", "FUND1"]
    assert symbols((), True) == ["7203", "USB", "FUND1"]  # 優待のみ（無効は入らない）
    # 全クラスなら優待の有無で集合は変わらない。
    assert symbols(ASSET_CLASSES, True) == symbols(ASSET_CLASSES, False)


# ---- 配当を数えない行 ----

def test_unselected_benefit_holder_counts_value_and_benefit_but_not_dividend():
    holdings, market = _portfolio()
    rows, summary = _selection(holdings, market, ("us_stock",))
    by_symbol = {r["symbol"]: r for r in rows}
    toyota = by_symbol["7203"]
    assert toyota["dividend_excluded"] is True
    assert toyota["annual_dividend_jpy"] is None
    assert toyota["annual_div_ps"] is None
    # 利回りは優待だけが分子（評価額・簿価は数える）。
    assert toyota["book_value_jpy"] == 200000.0
    assert toyota["yield_on_cost"] == pytest.approx(3000.0 / 200000.0)
    assert toyota["yield_on_cost_div_only"] is None
    assert by_symbol["AAPL"]["dividend_excluded"] is False
    # 配当は米国株だけ、優待は全クラスの優待銘柄。
    us_dividend = (10 * 1.0 + 20 * 2.0) * FX
    assert summary.total_dividend_jpy == pytest.approx(us_dividend)
    assert summary.total_benefit_jpy == pytest.approx(3000.0 + 1000.0 + 500.0)
    assert summary.total_book_value_jpy == pytest.approx(
        200000.0 + (10 * 150.0 + 20 * 50.0) * FX + 500000.0
    )


@pytest.mark.parametrize("mode", [None, "fixed", "rate"])
def test_withdrawal_of_unselected_fund_is_not_counted(mode):
    """取崩の上書きより後で外す（前で外すと、選んでいない投信の取崩額が漏れる）。"""
    holdings, market = _portfolio()
    rows, summary = _selection(holdings, market, ("jp_stock",), mode)
    fund = next(r for r in rows if r["symbol"] == "FUND1")
    assert fund["annual_dividend_jpy"] is None
    jp_dividend = (100 * 60.0) + (200 * 40.0)
    assert summary.total_dividend_jpy == pytest.approx(jp_dividend)


def test_benefit_only_dividend_is_zero_not_unknown():
    """優待のみ: 配当は「不明（N/A）」ではなく 0（推移の線と揃える）。"""
    holdings, market = _portfolio()
    rows, summary = _selection(holdings, market, ())
    assert all(r["dividend_excluded"] for r in rows)
    assert summary.total_dividend_jpy == 0.0
    assert summary.total_benefit_jpy == pytest.approx(4500.0)
    assert summary.yield_on_cost == pytest.approx(4500.0 / summary.total_book_value_jpy)


def test_excluded_rows_do_not_enter_monthly_dividend():
    """配当月に ¥0 の内訳行を出さない・N/A にも月不明にも数えない。"""
    holdings, market = _portfolio()
    holdings[0].benefit_months = [6]
    holdings[0].dividend_months = [3, 9]  # 手動の支払月があっても配当側は飛ばす
    rows, _ = _selection(holdings, market, ())
    result = monthly_breakdown(rows)
    assert result.known_dividend_jpy == 0.0
    assert result.na_symbols == [] and result.unknown_dividend_jpy == 0.0
    for bucket in result.buckets:
        assert bucket.dividend_jpy == 0.0
        assert all(item.benefit_jpy > 0 for item in bucket.items)
    assert result.buckets[5].benefit_jpy == pytest.approx(3000.0)


# ---- 推移（履歴）----

def test_benefit_holder_columns_are_recorded():
    assert set(BENEFIT_HOLDER_HISTORY_FIELDS) <= set(HISTORY_FIELDS)
    holdings, market = _portfolio()
    breakdown = benefit_holder_breakdown(holdings, market, FX)
    assert set(breakdown) == set(BENEFIT_HOLDER_HISTORY_FIELDS)
    assert breakdown[benefit_holder_history_key("jp_stock", "book_value")] == 200000.0
    assert breakdown[benefit_holder_history_key("us_stock", "book_value")] == pytest.approx(
        20 * 50.0 * FX
    )


def _recorded(holdings, market) -> dict:
    """当日スナップショットを CSV の形で往復させた1点（gui._history_as_dicts と同じ形）。"""
    by_class = {
        **class_breakdown(holdings, market, FX),
        **benefit_holder_breakdown(holdings, market, FX),
    }
    rec = HistoryRecord.from_dict({"record_date": "2026-10-26", **{
        k: ("" if v is None else v) for k, v in by_class.items()
    }})
    return {"date": "2026-10-26", **rec.by_class}


@pytest.mark.parametrize("scope", SUBSETS[:-1])  # 全クラスは合計列をそのまま使う
@pytest.mark.parametrize("mode", [None, "fixed", "rate"])
def test_trend_matches_kpi_for_every_selection_with_benefit(scope, mode):
    """推移の右端（記録日の値）が、同じ選択の KPI と一致する。"""
    holdings, market = _portfolio()
    _, summary = _selection(holdings, market, scope, mode)
    point = scope_history_record(_recorded(holdings, market), scope, with_benefit=True)
    assert point["total_market_value_jpy"] == pytest.approx(summary.total_market_value_jpy)
    assert point["total_book_value_jpy"] == pytest.approx(summary.total_book_value_jpy)
    assert point["total_benefit_jpy"] == pytest.approx(summary.total_benefit_jpy)
    assert point[INCOME_BASE_KEYS[mode]] == pytest.approx(summary.total_dividend_jpy)
    # グラフに描く1点（配当＋優待）も KPI「合計」と同じ値になる。
    assert combine_income(point, True, mode) == pytest.approx(summary.total_numerator_jpy)


def _unknown_us_dividend_portfolio():
    holdings, market = _portfolio()
    market["AAPL"].annual_div_ps_gross = None
    market["USB"].annual_div_ps_gross = None
    return holdings, market


@pytest.mark.parametrize("scope", [("us_stock",), ASSET_CLASSES])
def test_unknown_dividend_keeps_known_benefit_in_trend(scope):
    """配当が不明でも、既知の優待は推移に残る（KPI「合計」と同じ数え方）。

    基準値（配当）が None だからと線ごと切ると、合計カードに出ている優待の額が
    推移からだけ消える。全クラスを選んでいるときも同じ（合計列の配当が空の日）。
    """
    holdings, market = _unknown_us_dividend_portfolio()
    if scope == ASSET_CLASSES:
        for h in holdings:  # 全クラスで配当が不明になるようにする
            if h.symbol in market:
                market[h.symbol].annual_div_ps_gross = None
        holdings = [h for h in holdings if h.asset_type != "fund"]
    _, summary = _selection(holdings, market, scope)
    assert summary.total_dividend_jpy is None
    assert summary.total_numerator_jpy == pytest.approx(summary.total_benefit_jpy)
    rec = _recorded(holdings, market)
    if scope == ASSET_CLASSES:
        rec.update(
            total_dividend_jpy=summary.total_dividend_jpy,
            total_benefit_jpy=summary.total_benefit_jpy,
        )
    point = scope_history_record(rec, scope, with_benefit=True)
    assert combine_income(point, True, None) == pytest.approx(summary.total_numerator_jpy)
    # 優待を含めないなら配当だけの線なので、不明は不明のまま（KPI の年間配当も N/A）。
    assert combine_income(point, False, None) is None


def test_unrecorded_day_still_breaks_the_income_line():
    """資産別の列を足す前の日の空欄は「不明」ではなく「列が無い」なので線を切る。"""
    rec = {"date": "2024-06-27", "total_dividend_jpy": None, "total_benefit_jpy": 5000.0}
    assert combine_income(rec, True, None) is None
    assert combine_income(rec, True, "rate") is None


@pytest.mark.parametrize(
    "scope, with_benefit",
    [((), True), (("jp_stock",), False), (("jp_stock",), True)],
)
def test_selection_without_holdings_is_blank_in_both_kpi_and_trend(scope, with_benefit):
    """選んだ対象に銘柄が1つも無いと、KPI は N/A、推移も 0 ではなく空にする。"""
    holdings = [_holding("AAPL", currency="USD", shares=10.0, avg_cost=150.0)]
    market = {"AAPL": _market("AAPL", "USD", 200.0, 1.0)}
    picked = [h for _, h in scope_holdings(holdings, scope, with_benefit=with_benefit)]
    assert picked == []
    _, summary = compute_rows(picked, market, FX, "gross", None, dividend_scope=scope)
    assert summary.total_market_value_jpy is None
    assert summary.total_numerator_jpy is None
    point = scope_history_record(_recorded(holdings, market), scope, with_benefit)
    assert point["total_market_value_jpy"] is None
    assert point["total_book_value_jpy"] is None
    assert combine_income(point, with_benefit, None) is None


def test_trend_before_benefit_holder_columns_is_cut():
    """優待銘柄の列を足す前の日は線を切る（合計から按分・推定しない）。"""
    holdings, market = _portfolio()
    old = _recorded(holdings, market)
    for key in BENEFIT_HOLDER_HISTORY_FIELDS:
        old.pop(key, None)
    old["date"] = "2026-10-25"
    new = _recorded(holdings, market)
    point = scope_history_record(old, ("us_stock",), with_benefit=True)
    assert point["total_market_value_jpy"] is None
    assert point["total_benefit_jpy"] is None
    assert scoped_history_start([old, new], ("us_stock",), with_benefit=True) == "2026-10-26"
    # 優待を選んでいなければ従来どおり（優待銘柄の列は要らない）。
    assert scoped_history_start([old, new], ("us_stock",)) == "2026-10-25"
