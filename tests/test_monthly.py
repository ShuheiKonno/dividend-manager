"""月別配当の按分ロジック（PySide6 非依存・純ロジック層）。

表示層（BarChartWidget の描画）はテストしないが、按分・月不明の扱い・突合は
ここで固定する。グラフに出る数字の根拠はすべてこのファイルが担保する。
"""

from __future__ import annotations

from src.calc import (
    monthly_breakdown,
    resolve_benefit_months,
    resolve_payment_month_ratios,
    shift_ex_month_to_payment,
)
from src.models import Holding, MarketData
from src.utils import format_month_ratios, format_months, parse_month_ratios, parse_months


def _holding(**overrides) -> Holding:
    base = dict(
        symbol="7203",
        name="トヨタ",
        asset_type="stock",
        currency="JPY",
        shares=100.0,
        avg_cost=2000.0,
        account="特定",
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


def _market(**overrides) -> MarketData:
    base = dict(
        symbol="7203",
        currency="JPY",
        last_price=3000.0,
        price_as_of="2026-07-31",
        annual_div_ps_gross=95.0,
        annual_div_ps_net=None,
        div_forecast_as_of=None,
        source="yfinance",
        fetched_at="2026-08-01T09:00:00+09:00",
    )
    base.update(overrides)
    return MarketData(**base)


def _row(**overrides) -> dict:
    base = dict(
        symbol="7203",
        name="トヨタ",
        account="特定",
        active=True,
        annual_dividend_jpy=1200.0,
        annual_benefit_jpy=0.0,
        dividend_month_ratios={6: 0.5, 12: 0.5},
        dividend_months_manual=False,
        benefit_months=[],
    )
    base.update(overrides)
    return base


# ---- 権利落ち月 → 支払月 ----

def test_shift_jpy_adds_three_months():
    assert shift_ex_month_to_payment(3, "JPY") == 6
    assert shift_ex_month_to_payment(9, "JPY") == 12


def test_shift_jpy_wraps_at_year_end():
    """12月を越えたら翌年へ回り込む（14月を作らない）。"""
    assert shift_ex_month_to_payment(10, "JPY") == 1
    assert shift_ex_month_to_payment(11, "JPY") == 2
    assert shift_ex_month_to_payment(12, "JPY") == 3


def test_shift_usd_is_same_month():
    for month in range(1, 13):
        assert shift_ex_month_to_payment(month, "USD") == month


# ---- 月比率の解決 ----

def test_manual_months_are_not_shifted():
    """手動指定は既に支払月なのでシフトしない（3ヶ月ずれた誤グラフの回帰防止）。"""
    holding = _holding(dividend_months=[3, 9])
    ratios = resolve_payment_month_ratios(holding, _market(div_ex_month_ratios={6: 1.0}))
    assert ratios == {3: 0.5, 9: 0.5}


def test_manual_months_split_evenly():
    holding = _holding(dividend_months=[1, 4, 7, 10])
    ratios = resolve_payment_month_ratios(holding, None)
    assert ratios == {1: 0.25, 4: 0.25, 7: 0.25, 10: 0.25}


def test_market_ratios_are_shifted_for_jpy():
    holding = _holding()
    market = _market(div_ex_month_ratios={3: 0.6, 9: 0.4})
    ratios = resolve_payment_month_ratios(holding, market)
    assert ratios is not None
    assert sorted(ratios) == [6, 12]
    assert ratios[6] == 0.6
    assert ratios[12] == 0.4


def test_market_ratios_merge_when_shift_collides():
    """シフトで同じ支払月に落ちた比率が合算され、合計 1.0 に保たれること。"""
    holding = _holding()
    # 12月権利落ち → 3月支払、3月権利落ち → 6月支払。両方 3月権利があると衝突する。
    market = _market(div_ex_month_ratios={12: 0.5, 3: 0.3, 9: 0.2})
    ratios = resolve_payment_month_ratios(holding, market)
    assert ratios is not None
    assert abs(sum(ratios.values()) - 1.0) < 1e-9
    assert ratios[3] == 0.5  # 12月権利落ち分


def test_no_months_and_no_market_returns_none():
    assert resolve_payment_month_ratios(_holding(), None) is None
    assert resolve_payment_month_ratios(_holding(), _market()) is None


def test_benefit_months_are_manual_only():
    assert resolve_benefit_months(_holding()) == []
    assert resolve_benefit_months(_holding(benefit_months=[3, 9])) == [3, 9]


# ---- 月別集計 ----

def test_buckets_always_cover_twelve_months():
    result = monthly_breakdown([])
    assert [b.month for b in result.buckets] == list(range(1, 13))
    assert result.known_total_jpy == 0.0
    assert result.monthly_average_jpy == 0.0


def test_dividend_is_split_by_ratio():
    result = monthly_breakdown([_row(dividend_month_ratios={6: 0.7, 12: 0.3})])
    assert result.buckets[5].dividend_jpy == 840.0  # 6月
    assert result.buckets[11].dividend_jpy == 360.0  # 12月
    assert result.known_dividend_jpy == 1200.0
    assert result.monthly_average_jpy == 100.0


def test_benefit_is_split_evenly():
    result = monthly_breakdown([
        _row(annual_dividend_jpy=None, annual_benefit_jpy=6000.0, benefit_months=[3, 9])
    ])
    assert result.buckets[2].benefit_jpy == 3000.0
    assert result.buckets[8].benefit_jpy == 3000.0
    assert result.known_benefit_jpy == 6000.0


def test_dividend_and_benefit_in_same_month_share_one_item():
    result = monthly_breakdown([
        _row(
            annual_dividend_jpy=1000.0,
            dividend_month_ratios={6: 1.0},
            annual_benefit_jpy=500.0,
            benefit_months=[6],
        )
    ])
    june = result.buckets[5]
    assert len(june.items) == 1
    assert june.items[0].dividend_jpy == 1000.0
    assert june.items[0].benefit_jpy == 500.0
    assert june.items[0].total_jpy == 1500.0
    assert june.total_jpy == 1500.0


def test_na_dividend_is_not_counted_as_zero():
    """配当額そのものが不明な銘柄は、どのバケットにも積まず na_symbols へ回す。"""
    result = monthly_breakdown([_row(symbol="JP90C000J569", annual_dividend_jpy=None)])
    assert result.na_symbols == ["JP90C000J569"]
    assert result.known_dividend_jpy == 0.0
    assert all(b.dividend_jpy == 0.0 for b in result.buckets)


def test_amount_without_months_goes_to_unknown():
    result = monthly_breakdown([
        _row(symbol="1489", annual_dividend_jpy=5000.0, dividend_month_ratios=None)
    ])
    assert result.unknown_dividend_jpy == 5000.0
    assert result.unknown_symbols == ["1489"]
    assert result.known_dividend_jpy == 0.0


def test_benefit_without_months_goes_to_unknown():
    result = monthly_breakdown([
        _row(annual_dividend_jpy=None, annual_benefit_jpy=3000.0, benefit_months=[])
    ])
    assert result.unknown_benefit_jpy == 3000.0
    assert result.unknown_symbols == ["7203"]


def test_unknown_symbol_is_listed_once_per_symbol():
    """配当と優待の両方が月不明でも銘柄名は1度だけ数える。"""
    result = monthly_breakdown([
        _row(
            annual_dividend_jpy=1000.0,
            dividend_month_ratios=None,
            annual_benefit_jpy=500.0,
            benefit_months=[],
        )
    ])
    assert result.unknown_symbols == ["7203"]


def test_inactive_rows_are_skipped():
    result = monthly_breakdown([_row(active=False)])
    assert result.known_dividend_jpy == 0.0
    assert all(b.total_jpy == 0.0 for b in result.buckets)


def test_totals_reconcile_with_input():
    """按分できた額＋月不明の額が、入力の年額合計と一致すること。

    ここが崩れると Σ(棒グラフ) と KPI「合計」が食い違い、その差が画面のどこにも
    現れなくなる。
    """
    rows = [
        _row(symbol="A", annual_dividend_jpy=1200.0, dividend_month_ratios={6: 0.5, 12: 0.5}),
        _row(symbol="B", annual_dividend_jpy=800.0, dividend_month_ratios=None),
        _row(symbol="C", annual_dividend_jpy=None),
        _row(symbol="D", annual_dividend_jpy=0.0, annual_benefit_jpy=3000.0, benefit_months=[3]),
        _row(symbol="E", annual_dividend_jpy=100.0, dividend_month_ratios={9: 1.0},
             annual_benefit_jpy=500.0, benefit_months=[]),
    ]
    result = monthly_breakdown(rows)
    expected = sum(
        (r["annual_dividend_jpy"] or 0.0) + (r["annual_benefit_jpy"] or 0.0) for r in rows
    )
    actual = (
        result.known_total_jpy + result.unknown_dividend_jpy + result.unknown_benefit_jpy
    )
    assert abs(actual - expected) < 1e-9
    # バケットの合計は「按分できた額」と一致する。
    assert abs(sum(b.total_jpy for b in result.buckets) - result.known_total_jpy) < 1e-9


def test_estimated_flag_follows_manual_setting():
    auto = monthly_breakdown([_row(dividend_months_manual=False)])
    manual = monthly_breakdown([_row(dividend_months_manual=True)])
    assert auto.buckets[5].items[0].estimated is True
    assert manual.buckets[5].items[0].estimated is False


# ---- 月文字列パーサ ----

def test_parse_months_normalizes():
    assert parse_months("3,9") == [3, 9]
    assert parse_months("9,3,9") == [3, 9]
    assert parse_months(" 3 , 9 ") == [3, 9]
    assert parse_months("3，9") == [3, 9]  # 全角カンマ
    assert parse_months("0,13,abc,") == []
    assert parse_months(None) == []
    assert parse_months("") == []


def test_format_months_roundtrip():
    assert format_months([12, 6, 6]) == "6,12"
    assert format_months([]) == ""
    assert parse_months(format_months([6, 12])) == [6, 12]


def test_parse_month_ratios_normalizes_to_one():
    ratios = parse_month_ratios("6:0.62,12:0.38")
    assert ratios is not None
    assert abs(sum(ratios.values()) - 1.0) < 1e-9
    assert abs(ratios[6] - 0.62) < 1e-9


def test_parse_month_ratios_renormalizes_after_dropping_bad_parts():
    ratios = parse_month_ratios("6:1,13:5,abc,12:1")
    assert ratios == {6: 0.5, 12: 0.5}


def test_parse_month_ratios_returns_none_when_empty():
    assert parse_month_ratios("") is None
    assert parse_month_ratios(None) is None
    assert parse_month_ratios("abc") is None
    assert parse_month_ratios("6:0") is None  # 比率 0 だけなら月不明と同じ


def test_format_month_ratios_roundtrip():
    assert format_month_ratios({6: 0.62, 12: 0.38}) == "6:0.6200,12:0.3800"
    assert format_month_ratios(None) == ""
    restored = parse_month_ratios(format_month_ratios({6: 0.62, 12: 0.38}))
    assert restored is not None
    assert abs(restored[6] - 0.62) < 1e-4


def test_month_ratios_survive_rounding_on_write():
    """CSV は小数4桁で丸めるが、読み戻しの再正規化で合計 1.0 に戻ること。

    1/3 のように4桁で表せない比率（年3回配当）だと 0.3333×3 = 0.9999 になり、
    そのままだと按分額が年額に届かない。
    """
    thirds = {1: 1 / 3, 5: 1 / 3, 9: 1 / 3}
    restored = parse_month_ratios(format_month_ratios(thirds))
    assert restored is not None
    assert abs(sum(restored.values()) - 1.0) < 1e-12
    assert all(abs(v - 1 / 3) < 1e-9 for v in restored.values())
