"""calc.compute_rows / _pick_div_ps の検証（PySide6 非依存・純ロジック層）。"""

from __future__ import annotations

import pytest

from src.calc import _DEFAULT_TAX_RATE, _pick_div_ps, compute_rows
from src.models import Holding, MarketData


def _holding(**overrides) -> Holding:
    base = dict(
        symbol="7203",
        name="トヨタ",
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


def _market(symbol="7203", currency="JPY", last_price=2500.0, gross=60.0) -> MarketData:
    return MarketData(
        symbol=symbol,
        currency=currency,
        last_price=last_price,
        price_as_of="2026-05-28",
        annual_div_ps_gross=gross,
        annual_div_ps_net=None,
        div_forecast_as_of="2026-03-31",
        source="yfinance",
        fetched_at="2026-05-29T09:00:00+09:00",
    )


# ---- compute_rows: JPY 株 ----

def test_jpy_stock_basic():
    h = _holding(annual_div_ps_gross_override=60.0)
    rows, summary = compute_rows([h], {"7203": _market()}, 150.0, "gross")
    r = rows[0]
    assert r["book_value_jpy"] == pytest.approx(200_000)
    assert r["market_value_jpy"] == pytest.approx(250_000)
    assert r["profit_jpy"] == pytest.approx(50_000)
    assert r["profit_rate"] == pytest.approx(0.25)
    assert r["annual_dividend_jpy"] == pytest.approx(6_000)
    assert r["yield_on_cost"] == pytest.approx(0.03)
    assert r["yield_on_value"] == pytest.approx(0.024)
    assert summary.total_profit_jpy == pytest.approx(50_000)
    assert summary.yield_on_cost == pytest.approx(0.03)


# ---- compute_rows: USD 株（fx 換算） ----

def test_usd_stock_fx_conversion():
    h = _holding(
        symbol="AAPL", name="Apple", currency="USD", shares=10.0, avg_cost=100.0,
        annual_div_ps_gross_override=2.0,
    )
    market = {"AAPL": _market(symbol="AAPL", currency="USD", last_price=190.0, gross=2.0)}
    rows, _ = compute_rows([h], market, 150.0, "gross")
    r = rows[0]
    assert r["book_value_jpy"] == pytest.approx(150_000)
    assert r["market_value_jpy"] == pytest.approx(285_000)
    assert r["annual_dividend_jpy"] == pytest.approx(3_000)


def test_usd_without_fx_rate_yields_none():
    h = _holding(symbol="AAPL", currency="USD", shares=10.0, avg_cost=100.0)
    market = {"AAPL": _market(symbol="AAPL", currency="USD", last_price=190.0)}
    rows, _ = compute_rows([h], market, None, "gross")
    assert rows[0]["market_value_jpy"] is None
    assert rows[0]["book_value_jpy"] is None


# ---- compute_rows: net モードの源泉税自動計算 ----

def test_net_mode_auto_tax_default():
    h = _holding(annual_div_ps_gross_override=100.0)
    rows, _ = compute_rows([h], {}, 150.0, "net")
    expected_ps = 100.0 * (1 - _DEFAULT_TAX_RATE)
    assert rows[0]["annual_div_ps"] == pytest.approx(expected_ps)


def test_net_mode_tax_rate_zero_is_gross():
    h = _holding(annual_div_ps_gross_override=100.0, tax_rate=0.0)
    rows, _ = compute_rows([h], {}, 150.0, "net")
    assert rows[0]["annual_div_ps"] == pytest.approx(100.0)


# ---- compute_rows: ファンドの avg_cost フォールバックと無分配0 ----

def test_fund_price_fallback_to_avg_cost():
    h = _holding(symbol="JP0000", name="投信", asset_type="fund", shares=10.0, avg_cost=10_000.0)
    rows, _ = compute_rows([h], {}, 150.0, "gross")
    r = rows[0]
    assert r["last_price"] is None
    assert r["market_value_jpy"] == pytest.approx(100_000)  # avg_cost フォールバック
    assert r["annual_dividend_jpy"] == pytest.approx(0.0)   # 無分配ファンドは 0


# ---- compute_rows: 取り崩しモード ----

def test_withdrawal_fixed_overrides_dividend():
    h = _holding(symbol="JP0000", asset_type="fund", shares=10.0, avg_cost=10_000.0,
                 withdrawal_fixed_jpy=40_000.0)
    rows, _ = compute_rows([h], {}, 150.0, "gross", withdrawal_mode="fixed")
    assert rows[0]["annual_dividend_jpy"] == pytest.approx(40_000)


def test_withdrawal_rate_uses_market_value():
    h = _holding(symbol="JP0000", asset_type="fund", shares=10.0, avg_cost=10_000.0,
                 withdrawal_rate=0.04)
    rows, _ = compute_rows([h], {}, 150.0, "gross", withdrawal_mode="rate")
    # market_value=100,000（avg_cost フォールバック）× 0.04
    assert rows[0]["annual_dividend_jpy"] == pytest.approx(4_000)


def test_withdrawal_does_not_affect_stock():
    h = _holding(annual_div_ps_gross_override=60.0)
    rows, _ = compute_rows([h], {"7203": _market()}, 150.0, "gross", withdrawal_mode="fixed")
    assert rows[0]["annual_dividend_jpy"] == pytest.approx(6_000)  # 株式は不変


# ---- _pick_div_ps: 優先順位と 0 override ----

def test_pick_div_ps_override_wins_over_market():
    h = _holding(annual_div_ps_gross_override=10.0)
    assert _pick_div_ps(h, _market(gross=999.0), "gross") == 10.0


def test_pick_div_ps_zero_override_respected():
    """0 の明示上書きが market_data に上書きされないこと（旧 falsy バグの回帰防止）。"""
    h = _holding(annual_div_ps_gross_override=0.0)
    assert _pick_div_ps(h, _market(gross=50.0), "gross") == 0.0


def test_pick_div_ps_zero_override_respected_net_mode():
    h = _holding(annual_div_ps_gross_override=0.0)
    assert _pick_div_ps(h, _market(gross=50.0), "net") == pytest.approx(0.0)


def test_pick_div_ps_fund_fallback_zero():
    h = _holding(symbol="JP0000", asset_type="fund")
    assert _pick_div_ps(h, None, "gross") == 0.0


def test_pick_div_ps_stock_no_data_is_none():
    h = _holding()
    assert _pick_div_ps(h, None, "gross") is None
