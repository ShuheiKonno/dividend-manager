"""models のラウンドトリップ検証（PySide6 非依存・純ロジック層）。

特に `0.0` の明示上書きと `tax_rate=0`（NISA）が CSV 往復で保持されることを確認する。
"""

from __future__ import annotations

from src.models import Holding, MarketData


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
