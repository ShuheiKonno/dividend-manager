"""fetch_market_data の並列集約ロジック検証（ネットワーク非依存）。

`_fetch_one` をモンキーパッチし、ThreadPoolExecutor 経路の
順序保持・エラー集約・銘柄名更新・進捗カウントを検証する。
yfinance 自体のスレッド安全性は実取得（p キー）での確認対象。
"""

from __future__ import annotations

import pytest

import src.market_data as md
from src.models import Holding, MarketData


def _holding(symbol: str, name: str = "") -> Holding:
    return Holding(
        symbol=symbol, name=name, asset_type="stock", currency="JPY",
        shares=1.0, avg_cost=1.0, account="",
        annual_div_ps_gross_override=None, annual_div_ps_net_override=None,
        div_forecast_as_of_override=None, annual_benefit_jpy=0.0,
        withdrawal_fixed_jpy=None, withdrawal_rate=None, tax_rate=None, active=True,
    )


def _fake_fetch_one(holding, fetched_at, source):
    errs = [f"err:{holding.symbol}"] if holding.symbol == "BAD" else []
    name_update = None if holding.name else (holding.symbol, f"name-{holding.symbol}")
    m = MarketData(
        symbol=holding.symbol, currency=holding.currency, last_price=None,
        price_as_of=None, annual_div_ps_gross=None, annual_div_ps_net=None,
        div_forecast_as_of=None, source=source, fetched_at=fetched_at,
    )
    return m, errs, name_update


def test_parallel_fetch_aggregation(monkeypatch):
    monkeypatch.setattr(md, "_fetch_one", _fake_fetch_one)
    holdings = [_holding(s) for s in ("AAA", "BBB", "BAD", "DDD")]
    progress: list[tuple[int, int]] = []

    res = md.fetch_market_data(
        holdings, {}, force=True, progress_cb=lambda d, t, _l: progress.append((d, t))
    )

    # (a) 入力順を保持（完了順に依存しない）
    assert [r.symbol for r in res.rows] == ["AAA", "BBB", "BAD", "DDD"]
    # (b) 全エラーを収集
    assert "err:BAD" in res.errors and len(res.errors) == 1
    # (c) 全銘柄名更新を適用
    assert res.name_updates == {s: f"name-{s}" for s in ("AAA", "BBB", "BAD", "DDD")}
    # (d) 進捗が (total, total) まで到達
    assert progress[-1] == (4, 4)


def test_cached_holdings_skip_fetch(monkeypatch):
    """当日取得済みキャッシュは _fetch_one を呼ばずそのまま返す。"""
    calls: list[str] = []

    def spy(holding, fetched_at, source):
        calls.append(holding.symbol)
        return _fake_fetch_one(holding, fetched_at, source)

    monkeypatch.setattr(md, "_fetch_one", spy)
    from src.utils import now_jst_iso

    cached = MarketData(
        symbol="AAA", currency="JPY", last_price=100.0, price_as_of="2026-05-29",
        annual_div_ps_gross=None, annual_div_ps_net=None, div_forecast_as_of=None,
        source="yfinance", fetched_at=now_jst_iso(),
    )
    res = md.fetch_market_data([_holding("AAA")], {"AAA": cached}, force=False)
    assert calls == []                       # 取得関数は呼ばれない
    assert res.rows[0].last_price == 100.0   # キャッシュ値を返す


def test_failed_fetch_keeps_existing_cache(monkeypatch):
    cached = MarketData(
        symbol="AAA", currency="JPY", last_price=100.0, price_as_of="2026-05-29",
        annual_div_ps_gross=5.0, annual_div_ps_net=None, div_forecast_as_of="2026-05-29",
        source="yfinance", fetched_at="2026-05-29T09:00:00+09:00",
    )

    def failed(holding, fetched_at, source):
        row, _, name_update = _fake_fetch_one(holding, fetched_at, source)
        return row, ["temporary failure"], name_update

    monkeypatch.setattr(md, "_fetch_one", failed)
    monkeypatch.setattr(md, "_yf", lambda: object())
    res = md.fetch_market_data([_holding("AAA")], {"AAA": cached}, force=True)
    assert res.rows == [cached]
    assert res.errors == ["temporary failure"]


def test_duplicate_symbols_fetch_once(monkeypatch):
    calls: list[str] = []

    def spy(holding, fetched_at, source):
        calls.append(holding.symbol)
        return _fake_fetch_one(holding, fetched_at, source)

    monkeypatch.setattr(md, "_fetch_one", spy)
    monkeypatch.setattr(md, "_yf", lambda: object())
    res = md.fetch_market_data([_holding("AAA"), _holding("AAA")], {}, force=True)
    assert calls == ["AAA"]
    assert [row.symbol for row in res.rows] == ["AAA"]


def test_inactive_existing_cache_is_preserved(monkeypatch):
    inactive = _holding("OLD")
    inactive.active = False
    cached = MarketData(
        symbol="OLD", currency="JPY", last_price=100.0, price_as_of="2026-05-29",
        annual_div_ps_gross=5.0, annual_div_ps_net=None, div_forecast_as_of="2026-05-29",
        source="yfinance", fetched_at="2026-05-29T09:00:00+09:00",
    )
    res = md.fetch_market_data([inactive], {"OLD": cached}, force=True)
    assert res.rows == [cached]


def test_fetch_usdjpy_reports_failure_reason(monkeypatch):
    class BrokenYFinance:
        @staticmethod
        def Ticker(_symbol):
            raise OSError("cache unavailable")

    monkeypatch.setattr(md, "_yf", lambda: BrokenYFinance())
    with pytest.raises(RuntimeError, match="cache unavailable"):
        md.fetch_usdjpy()


# ---- 権利落ち月の構成比 ----

class _FakeSeries:
    """yfinance の dividends（DatetimeIndex 付き Series）の最小スタブ。"""

    def __init__(self, pairs):
        self._pairs = list(pairs)

    def __len__(self):
        return len(self._pairs)

    def items(self):
        return iter(self._pairs)


class _FakeStamp:
    def __init__(self, d):
        self._d = d

    def date(self):
        return self._d


def _series(pairs):
    from datetime import date as _date

    return _FakeSeries([(_FakeStamp(_date(*ymd)), value) for ymd, value in pairs])


def test_dividend_month_ratios_use_actual_amounts():
    """中間と期末で額が違う日本株が、月数の等分ではなく実績比率で按分されること。"""
    from datetime import date, timedelta

    recent = date.today() - timedelta(days=30)
    older = date.today() - timedelta(days=200)
    series = _series([
        ((older.year, older.month, older.day), 15.25),
        ((recent.year, recent.month, recent.day), 24.75),
    ])
    total, latest, ratios = md._parse_yfinance_dividends(series)
    assert total == pytest.approx(40.0)
    assert latest == recent.isoformat()
    assert ratios is not None
    assert abs(sum(ratios.values()) - 1.0) < 1e-9
    assert ratios[recent.month] == pytest.approx(24.75 / 40.0)


def test_dividends_outside_window_are_ignored():
    from datetime import date, timedelta

    recent = date.today() - timedelta(days=10)
    stale = date.today() - timedelta(days=500)
    series = _series([
        ((stale.year, stale.month, stale.day), 100.0),
        ((recent.year, recent.month, recent.day), 50.0),
    ])
    total, _, ratios = md._parse_yfinance_dividends(series)
    assert total == pytest.approx(50.0)
    assert ratios == {recent.month: pytest.approx(1.0)}


def test_no_dividends_returns_all_none():
    assert md._parse_yfinance_dividends(None) == (None, None, None)
    assert md._parse_yfinance_dividends(_series([])) == (None, None, None)


def test_only_stale_dividends_yield_no_ratios():
    from datetime import date, timedelta

    stale = date.today() - timedelta(days=500)
    total, _, ratios = md._parse_yfinance_dividends(
        _series([((stale.year, stale.month, stale.day), 100.0)])
    )
    assert total is None
    assert ratios is None
