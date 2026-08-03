"""models のラウンドトリップ検証（PySide6 非依存・純ロジック層）。

特に `0.0` の明示上書きと `tax_rate=0`（NISA）が CSV 往復で保持されることを確認する。
"""

from __future__ import annotations

from src.models import HOLDINGS_FIELDS, MARKET_DATA_FIELDS, Holding, MarketData


def _full_holding(**overrides) -> Holding:
    base = dict(
        symbol="7203",
        name="トヨタ",
        asset_type="stock",
        currency="JPY",
        shares=100.0,
        avg_cost=2000.0,
        account="特定",
        annual_div_ps_gross_override=60.0,
        annual_div_ps_net_override=None,
        div_forecast_as_of_override="2026-03-31",
        annual_benefit_jpy=0.0,
        withdrawal_fixed_jpy=None,
        withdrawal_rate=None,
        tax_rate=None,
        active=True,
        dividend_months=[6, 12],
        benefit_months=[3],
    )
    base.update(overrides)
    return Holding(**base)


def test_holding_roundtrip_preserves_values():
    h = _full_holding()
    restored = Holding.from_dict(h.to_dict())
    assert restored == h


def test_zero_override_is_preserved():
    """配当0を明示上書きした銘柄が、再読込で None に戻らないこと（旧 falsy バグの回帰防止）。"""
    h = _full_holding(annual_div_ps_gross_override=0.0)
    restored = Holding.from_dict(h.to_dict())
    assert restored.annual_div_ps_gross_override == 0.0


def test_zero_withdrawal_values_are_preserved():
    h = _full_holding(withdrawal_fixed_jpy=0.0, withdrawal_rate=0.0)
    restored = Holding.from_dict(h.to_dict())
    assert restored.withdrawal_fixed_jpy == 0.0
    assert restored.withdrawal_rate == 0.0


def test_tax_rate_zero_for_nisa_is_preserved():
    h = _full_holding(tax_rate=0.0)
    restored = Holding.from_dict(h.to_dict())
    assert restored.tax_rate == 0.0


def test_none_override_stays_none():
    h = _full_holding(annual_div_ps_gross_override=None, tax_rate=None)
    restored = Holding.from_dict(h.to_dict())
    assert restored.annual_div_ps_gross_override is None
    assert restored.tax_rate is None


def test_account_roundtrip():
    h = _full_holding(account="NISA成長投資枠")
    restored = Holding.from_dict(h.to_dict())
    assert restored.account == "NISA成長投資枠"


def test_market_data_roundtrip():
    m = MarketData(
        symbol="AAPL",
        currency="USD",
        last_price=190.5,
        price_as_of="2026-05-28",
        annual_div_ps_gross=1.0,
        annual_div_ps_net=None,
        div_forecast_as_of="2026-05-01",
        source="yfinance",
        fetched_at="2026-05-29T09:00:00+09:00",
        div_ex_month_ratios={2: 0.25, 5: 0.25, 8: 0.25, 11: 0.25},
    )
    restored = MarketData.from_dict(m.to_dict())
    assert restored == m


def test_market_data_zero_values_are_preserved():
    m = MarketData(
        symbol="ZERO",
        currency="JPY",
        last_price=0.0,
        price_as_of="2026-05-30",
        annual_div_ps_gross=0.0,
        annual_div_ps_net=0.0,
        div_forecast_as_of="2026-05-30",
        source="test",
        fetched_at="2026-05-30T09:00:00+09:00",
    )
    restored = MarketData.from_dict(m.to_dict())
    assert restored == m


def test_month_fields_roundtrip():
    """月リストがカンマ区切りで往復すること（csv のクォート往復は test_io_csv 側）。"""
    h = _full_holding(dividend_months=[6, 12], benefit_months=[])
    assert h.to_dict()["dividend_months"] == "6,12"
    assert h.to_dict()["benefit_months"] == ""
    restored = Holding.from_dict(h.to_dict())
    assert restored == h


def test_benefit_detail_roundtrips_and_strips():
    """優待内容が往復し、前後の空白だけ落ちること（中身は加工しない）。"""
    h = _full_holding(benefit_detail="  クオカード1,000円分  ")
    restored = Holding.from_dict(h.to_dict())
    assert restored.benefit_detail == "クオカード1,000円分"


def test_month_fields_are_normalized_on_write():
    """順不同・重複・範囲外を含む入力が昇順の正規形へ寄ること。"""
    h = _full_holding(dividend_months=[12, 6, 6, 0, 13])
    assert h.to_dict()["dividend_months"] == "6,12"


def test_legacy_rows_without_month_columns():
    """月列を持たない旧 CSV 行が読めること（新列は空扱い）。"""
    row = _full_holding().to_dict()
    del row["dividend_months"]
    del row["benefit_months"]
    del row["benefit_detail"]
    restored = Holding.from_dict(row)
    assert restored.dividend_months == []
    assert restored.benefit_months == []
    assert restored.benefit_detail == ""  # None ではなく空文字（表示層の None 判定を増やさない）

    market_row = MarketData(
        symbol="7203",
        currency="JPY",
        last_price=3000.0,
        price_as_of="2026-05-28",
        annual_div_ps_gross=95.0,
        annual_div_ps_net=None,
        div_forecast_as_of=None,
        source="yfinance",
        fetched_at="2026-05-29T09:00:00+09:00",
    ).to_dict()
    del market_row["div_ex_month_ratios"]
    assert MarketData.from_dict(market_row).div_ex_month_ratios is None


def test_to_dict_keys_match_csv_fields():
    """to_dict のキーが FIELDS と一致すること。

    csv.DictWriter の extrasaction は既定 'raise' なので、片方だけ更新すると
    保存のたびに ValueError で落ちる。
    """
    assert list(_full_holding().to_dict()) == HOLDINGS_FIELDS
    market = MarketData(
        symbol="7203",
        currency="JPY",
        last_price=3000.0,
        price_as_of="2026-05-28",
        annual_div_ps_gross=95.0,
        annual_div_ps_net=None,
        div_forecast_as_of=None,
        source="yfinance",
        fetched_at="2026-05-29T09:00:00+09:00",
    )
    assert list(market.to_dict()) == MARKET_DATA_FIELDS
