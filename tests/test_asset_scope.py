"""資産クラス（日本株 / 米国株 / 投信）の分類・表示対象の絞り込み・クラス別履歴。

PySide6 非依存。表示層の配線は tests/headless_smoke.py:_check_asset_scope が見る。
"""

from __future__ import annotations

import csv

import pytest

from src import io_csv
from src.calc import (
    ASSET_CLASSES,
    asset_class,
    asset_scope_label,
    compute_rows,
    is_all_asset_scope,
    normalize_asset_scope,
    scope_holdings,
)
from src.history import class_breakdown
from src.models import (
    CLASS_HISTORY_FIELDS,
    CLASS_HISTORY_METRICS,
    HISTORY_FIELDS,
    Holding,
    HistoryRecord,
    MarketData,
    class_history_key,
)


def _holding(symbol="7203", **overrides) -> Holding:
    base = dict(
        symbol=symbol,
        name=symbol,
        asset_type="stock",
        currency="JPY",
        shares=100.0,
        avg_cost=2000.0,
        account="",
        annual_div_ps_gross_override=None,
        annual_div_ps_net_override=None,
        div_forecast_as_of_override=None,
        annual_benefit_jpy=0.0,
        withdrawal_fixed_jpy=None,
        withdrawal_rate=None,
        tax_rate=None,
        active=True,
    )
    base.update(overrides)
    return Holding(**base)


def _market(symbol, currency="JPY", last_price=2500.0, gross=60.0) -> MarketData:
    return MarketData(
        symbol=symbol,
        currency=currency,
        last_price=last_price,
        price_as_of="2026-10-23",
        annual_div_ps_gross=gross,
        annual_div_ps_net=None,
        div_forecast_as_of="2026-03-31",
        source="yfinance",
        fetched_at="2026-10-25T09:00:00+09:00",
    )


def _portfolio():
    holdings = [
        _holding("7203", annual_benefit_jpy=3000.0),
        _holding("AAPL", currency="USD", shares=10.0, avg_cost=150.0),
        _holding(
            "FUND1", asset_type="fund", shares=50.0, avg_cost=10000.0,
            withdrawal_fixed_jpy=120000.0, withdrawal_rate=0.04,
        ),
        _holding("8306", shares=200.0, avg_cost=1000.0),
        # 無効銘柄は集計・内訳のどちらにも入らない。
        _holding("9999", active=False),
    ]
    market = {
        "7203": _market("7203"),
        "AAPL": _market("AAPL", "USD", 200.0, 1.0),
        "FUND1": _market("FUND1", last_price=12000.0, gross=0.0),
        "8306": _market("8306", last_price=1500.0, gross=40.0),
        "9999": _market("9999"),
    }
    return holdings, market


# ---- 分類 ----

@pytest.mark.parametrize(
    "asset_type, currency, expected",
    [
        ("stock", "JPY", "jp_stock"),
        ("stock", "USD", "us_stock"),
        ("fund", "JPY", "fund"),
        # 通貨より先に投信を判定する（USD 建て投信を米国株にしない）。
        ("fund", "USD", "fund"),
        # 想定外の通貨・種別も必ずどれかのクラスへ落とす。
        ("stock", "EUR", "jp_stock"),
        ("", "", "jp_stock"),
    ],
)
def test_asset_class(asset_type, currency, expected):
    assert asset_class(asset_type, currency) == expected
    assert expected in ASSET_CLASSES


def test_class_history_metrics_cover_every_class():
    # models と calc で同じクラスの並びを使う（calc は再エクスポート）。
    assert tuple(CLASS_HISTORY_METRICS) == ASSET_CLASSES


@pytest.mark.parametrize(
    "value, expected",
    [
        (["fund", "jp_stock"], ("jp_stock", "fund")),  # 順序を正規化
        (["jp_stock", "jp_stock"], ("jp_stock",)),  # 重複を潰す
        (list(ASSET_CLASSES), ASSET_CLASSES),
        ([], None),  # 空集合は許さない
        (["jp_stock", "bond"], None),
        ("jp_stock", None),  # 文字列はリストではない
        (None, None),
        ([1], None),
    ],
)
def test_normalize_asset_scope(value, expected):
    assert normalize_asset_scope(value) == expected


def test_asset_scope_label():
    assert asset_scope_label(ASSET_CLASSES) == "全て"
    assert asset_scope_label(("fund", "jp_stock")) == "日本株・投信"
    assert asset_scope_label(("us_stock",)) == "米国株"
    assert is_all_asset_scope(ASSET_CLASSES)
    assert not is_all_asset_scope(("jp_stock", "us_stock"))


# ---- 絞り込み ----

def test_scope_holdings_keeps_original_indices():
    holdings, _ = _portfolio()
    picked = scope_holdings(holdings, ("jp_stock", "fund"))
    # インデックスは holdings 内の位置そのまま（表示行 ↔ 保有の対応に使う）。
    assert [i for i, _ in picked] == [0, 2, 3]
    assert all(holdings[i] is h for i, h in picked)


def test_scope_holdings_inactive_follows_flag():
    holdings, _ = _portfolio()
    assert [i for i, _ in scope_holdings(holdings, ("jp_stock",))] == [0, 3]
    assert [
        i for i, _ in scope_holdings(holdings, ("jp_stock",), include_inactive=True)
    ] == [0, 3, 4]


def test_scoped_summary_equals_sum_of_classes():
    """クラスごとに絞った Summary の和が全体の Summary と一致する。"""
    holdings, market = _portfolio()
    _, total = compute_rows(holdings, market, 150.0, "gross")
    parts = [
        compute_rows([h for _, h in scope_holdings(holdings, (c,))], market, 150.0, "gross")[1]
        for c in ASSET_CLASSES
    ]
    for field in ("total_market_value_jpy", "total_book_value_jpy", "total_dividend_jpy"):
        assert sum(getattr(p, field) or 0.0 for p in parts) == pytest.approx(
            getattr(total, field)
        )


# ---- クラス別の履歴 ----

def test_class_breakdown_sums_to_totals():
    """記録日ごとに Σクラス別 ＝ 合計列（推移グラフの全選択と部分選択の整合）。"""
    holdings, market = _portfolio()
    breakdown = class_breakdown(holdings, market, 150.0)
    assert set(breakdown) == set(CLASS_HISTORY_FIELDS)
    _, base = compute_rows(holdings, market, 150.0, "gross", None)
    _, rate = compute_rows(holdings, market, 150.0, "gross", "rate")
    _, fixed = compute_rows(holdings, market, 150.0, "gross", "fixed")
    totals = {
        "market_value": base.total_market_value_jpy,
        "book_value": base.total_book_value_jpy,
        "dividend": base.total_dividend_jpy,
        "benefit": base.total_benefit_jpy,
        "withdrawal_income": rate.total_dividend_jpy,
        "fixed_withdrawal_income": fixed.total_dividend_jpy,
    }
    for metric, total in totals.items():
        # 取崩の列を持たない株式クラスは、取崩込み収入＝配当として足す。
        summed = sum(
            breakdown[
                class_history_key(
                    cls, metric if metric in CLASS_HISTORY_METRICS[cls] else "dividend"
                )
            ]
            for cls in ASSET_CLASSES
        )
        assert summed == pytest.approx(total or 0.0), metric


def test_class_breakdown_fills_empty_class_with_zero():
    # 米国株を持っていなくても列は 0.0 で埋まる（None だと「未記録」と区別できない）。
    holdings = [_holding("7203")]
    breakdown = class_breakdown(holdings, {"7203": _market("7203")}, 150.0)
    assert breakdown[class_history_key("us_stock", "market_value")] == 0.0
    assert breakdown[class_history_key("fund", "withdrawal_income")] == 0.0
    assert breakdown[class_history_key("jp_stock", "market_value")] == 250000.0


def test_class_breakdown_ignores_display_modes():
    """取崩は投信の列だけに入り、株式の配当列は取崩の有無で変わらない。"""
    holdings, market = _portfolio()
    breakdown = class_breakdown(holdings, market, 150.0)
    assert breakdown[class_history_key("fund", "fixed_withdrawal_income")] == 120000.0
    assert breakdown[class_history_key("fund", "withdrawal_income")] == pytest.approx(
        50.0 * 12000.0 * 0.04
    )
    assert breakdown[class_history_key("fund", "dividend")] == 0.0


def _record(**by_class) -> HistoryRecord:
    return HistoryRecord(
        record_date="2026-10-25",
        total_market_value_jpy=100.0,
        total_book_value_jpy=80.0,
        total_dividend_jpy=5.0,
        total_benefit_jpy=None,
        total_profit_jpy=20.0,
        total_withdrawal_income_jpy=None,
        total_fixed_withdrawal_income_jpy=None,
        source="app",
        by_class=by_class,
    )


def test_history_record_roundtrip_with_class_columns(tmp_path):
    key = class_history_key("jp_stock", "market_value")
    rec = _record(**{key: 60.0, class_history_key("fund", "dividend"): 0.0})
    io_csv.save_history([rec])
    loaded = io_csv.load_history()[0]
    assert loaded.by_class[key] == 60.0
    # 0.0 は空欄へ潰さない（「寄与ゼロ」と「未記録」を区別する）。
    assert loaded.by_class[class_history_key("fund", "dividend")] == 0.0
    assert loaded.by_class.get(class_history_key("us_stock", "market_value")) is None
    # 外部エクスポート → 取込でも同じ値に戻る。
    out = tmp_path / "history_export.csv"
    io_csv.export_history_csv(out, [rec])
    back = io_csv.read_history_csv(out)[0]
    assert back.by_class[key] == 60.0


def test_history_fields_include_class_columns_before_source():
    assert HISTORY_FIELDS[-1] == "source"
    assert set(CLASS_HISTORY_FIELDS) <= set(HISTORY_FIELDS)


def test_old_history_csv_without_class_columns(tmp_path):
    """列を足す前の history.csv も読める（クラス別は空）。"""
    old = tmp_path / "old_history.csv"
    with old.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["record_date", "total_market_value_jpy", "source"])
        writer.writerow(["2024-06-27", "1000", "app"])
    rec = io_csv.read_history_csv(old)[0]
    assert rec.total_market_value_jpy == 1000.0
    assert rec.by_class == {}
