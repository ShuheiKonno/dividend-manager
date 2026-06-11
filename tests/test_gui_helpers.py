from __future__ import annotations

import math

import pytest

import src.gui as gui
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
        annual_div_ps_gross_override=100.0,
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


def _window_stub(holdings, market_map):
    class WindowStub:
        dividend_mode = "net"
        fx_rate = None
        history = []

        def _save_or_notify(self, action):
            action()
            return True

        def _refresh_trend(self):
            pass

    stub = WindowStub()
    stub.holdings = holdings
    stub.market_data_map = market_map
    stub.history = []
    return stub


def test_parse_num_rejects_non_finite_values():
    for text in ("nan", "inf", "-inf"):
        with pytest.raises(ValueError):
            gui._parse_num(text)
    assert math.isfinite(gui._parse_num("１２３"))


def test_row_for_holding_index():
    assert gui.row_for_holding_index([2, 5, 7], 5) == 1
    assert gui.row_for_holding_index([2, 5, 7], 2) == 0
    assert gui.row_for_holding_index([2, 5, 7], 9) is None  # 削除後など対象なし
    assert gui.row_for_holding_index([], 0) is None


def test_rate_and_date_validation():
    assert gui._optional_rate(0.0, "率") == 0.0
    assert gui._optional_rate(1.0, "率") == 1.0
    with pytest.raises(ValueError):
        gui._optional_rate(1.01, "率")
    assert gui._optional_iso_date("2026-05-30") == "2026-05-30"
    with pytest.raises(ValueError):
        gui._optional_iso_date("2026-02-30")


def test_history_snapshot_uses_gross_dividend(monkeypatch):
    holding = _holding()
    market = MarketData(
        symbol="7203", currency="JPY", last_price=2500.0, price_as_of="2026-05-30",
        annual_div_ps_gross=100.0, annual_div_ps_net=None,
        div_forecast_as_of="2026-05-30", source="test", fetched_at="x",
    )
    saved = []
    monkeypatch.setattr(gui, "save_history", lambda rows: saved.extend(rows))
    stub = _window_stub([holding], {"7203": market})
    gui.DividendWindow._record_history_snapshot(stub)
    assert saved[0].total_dividend_jpy == 10_000.0


def test_history_snapshot_skips_missing_market_value(monkeypatch):
    called = False

    def save(_rows):
        nonlocal called
        called = True

    monkeypatch.setattr(gui, "save_history", save)
    stub = _window_stub([_holding()], {})
    gui.DividendWindow._record_history_snapshot(stub)
    assert called is False
    assert stub.history == []
