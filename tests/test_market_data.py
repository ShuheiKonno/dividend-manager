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
    return m, errs, name_update, {"price", "dividend", "splits"}


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
        row, _, name_update, _ = _fake_fetch_one(holding, fetched_at, source)
        return row, ["temporary failure"], name_update, set()

    monkeypatch.setattr(md, "_fetch_one", failed)
    monkeypatch.setattr(md, "_yf", lambda: object())
    res = md.fetch_market_data([_holding("AAA")], {"AAA": cached}, force=True)
    assert res.rows[0].last_price == cached.last_price
    assert res.rows[0].annual_div_ps_gross == cached.annual_div_ps_gross
    assert res.rows[0].fetched_at is None
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


def _ymd(d):
    return (d.year, d.month, d.day)


def test_semiannual_dividends_on_ex_date_do_not_count_three_times():
    """権利落ち当日に前年同日の配当が窓へ残り、半期配当が3回分にならないこと。

    実例: 東京海上 105.5(前年9/29) + 112.5(3/30) + 122.5(当日9/29) を 340.5 と数えていた。
    """
    from datetime import date, timedelta

    today = date.today()
    series = _series([
        (_ymd(today - timedelta(days=365)), 105.5),
        (_ymd(today - timedelta(days=183)), 112.5),
        (_ymd(today), 122.5),
    ])
    total, latest, ratios = md._parse_yfinance_dividends(series)
    assert total == pytest.approx(235.0)
    assert latest == today.isoformat()
    assert sum(ratios.values()) == pytest.approx(1.0)


def test_semiannual_dividends_between_ex_dates_keep_two_payments():
    """通常時（最新の権利落ちから半年後）は従来どおり直近2回分になること。"""
    from datetime import date, timedelta

    today = date.today()
    series = _series([
        (_ymd(today - timedelta(days=365)), 105.5),
        (_ymd(today - timedelta(days=180)), 112.5),
    ])
    total, _, _ = md._parse_yfinance_dividends(series)
    assert total == pytest.approx(218.0)


def test_previous_year_ex_date_shifted_by_days_is_still_excluded():
    """前年の権利落ち日が今年より数日遅れていても3回分にならないこと。"""
    from datetime import date, timedelta

    today = date.today()
    series = _series([
        (_ymd(today - timedelta(days=360)), 105.5),  # 前年は5日遅かった
        (_ymd(today - timedelta(days=180)), 112.5),
        (_ymd(today), 122.5),
    ])
    total, _, _ = md._parse_yfinance_dividends(series)
    assert total == pytest.approx(235.0)


def test_quarterly_and_monthly_dividends_keep_full_year():
    from datetime import date, timedelta

    today = date.today()
    quarterly = _series(
        [(_ymd(today - timedelta(days=91 * k)), 10.0) for k in range(5)]
    )
    total, _, _ = md._parse_yfinance_dividends(quarterly)
    assert total == pytest.approx(40.0)  # 最新から 364 日前の5件目は含めない

    monthly = _series(
        [(_ymd(today - timedelta(days=30 * k)), 1.0) for k in range(13)]
    )
    total, _, _ = md._parse_yfinance_dividends(monthly)
    assert total == pytest.approx(12.0)


def test_future_dated_dividend_does_not_shift_the_window():
    from datetime import date, timedelta

    today = date.today()
    series = _series([
        (_ymd(today - timedelta(days=200)), 100.0),
        (_ymd(today - timedelta(days=20)), 100.0),
        (_ymd(today + timedelta(days=60)), 50.0),
    ])
    total, _, _ = md._parse_yfinance_dividends(series)
    assert total == pytest.approx(200.0)


def test_future_semiannual_dividend_is_excluded_from_all_outputs():
    from datetime import date, timedelta

    today = date.today()
    older = today - timedelta(days=365)
    recent = today - timedelta(days=183)
    series = _series([
        (_ymd(older), 50.0),
        (_ymd(recent), 50.0),
        (_ymd(today + timedelta(days=1)), 50.0),
    ])
    total, latest, ratios = md._parse_yfinance_dividends(series)
    assert total == pytest.approx(100.0)
    assert latest == recent.isoformat()
    assert ratios == {older.month: 0.5, recent.month: 0.5}


def test_only_future_dividends_keep_existing_fallback():
    from datetime import date, timedelta

    first = date.today() + timedelta(days=1)
    last = first + timedelta(days=180)
    total, latest, ratios = md._parse_yfinance_dividends(
        _series([(_ymd(first), 50.0), (_ymd(last), 50.0)])
    )
    assert total == pytest.approx(100.0)
    assert latest == last.isoformat()
    assert ratios == {first.month: 0.5, last.month: 0.5}


class _Close(list):
    empty = False

    def __init__(self, day):
        super().__init__([250.0])
        self.index = [_FakeStamp(day)]
        self.iloc = self

    def dropna(self):
        return self


class _EmptyClose(list):
    empty = True

    def dropna(self):
        return self


class _Ticker:
    """history() だけを持つ偽の Ticker（dividends / splits を読んだら失敗させる）。

    failed: "price"=通信の例外 / "no_close"=終値が空 / "dividend"=Dividends 列なし /
    "splits"=Stock Splits 列なし。events の値は pandas の列と同じく、イベントの無い
    営業日にも 0.0 の行が入る形で渡す。
    """

    def __init__(self, day, failed, dividends=None, splits=None, close_day=None):
        self.day = day
        self.failed = failed
        self.close_day = close_day or day
        self._dividends = [(day, 12.0)] if dividends is None else dividends
        self._splits = [] if splits is None else splits
        self.history_calls = 0

    def history(self, **kwargs):
        from datetime import date as _date

        assert kwargs == {"period": md._HISTORY_PERIOD, "auto_adjust": False}
        self.history_calls += 1
        if self.failed == "price":
            raise OSError("株価エラー")
        frame = {
            "Close": (
                _EmptyClose() if self.failed == "no_close" else _Close(self.close_day)
            )
        }
        # 営業日の 0.0 行を混ぜる（イベント行の前後）。
        zero = _FakeStamp(_date.today())
        if self.failed != "dividend":
            frame["Dividends"] = _FakeSeries(
                [(zero, 0.0)]
                + [(_FakeStamp(d), v) for d, v in self._dividends]
                + [(zero, 0.0)]
            )
        if self.failed != "splits":
            frame["Stock Splits"] = _FakeSeries(
                [(zero, 0.0)] + [(_FakeStamp(d), v) for d, v in self._splits]
            )
        return frame

    @property
    def dividends(self):
        raise AssertionError("ticker.dividends は読まない（通信が倍になる）")

    @property
    def splits(self):
        raise AssertionError("ticker.splits は読まない（通信が倍になる）")


def _cached_row(cached_splits, old_stamp):
    return MarketData(
        symbol="AAA", currency="JPY", last_price=100.0, price_as_of="2025-01-01",
        annual_div_ps_gross=5.0, annual_div_ps_net=4.0,
        div_forecast_as_of="2025-01-01", source="yfinance", fetched_at=old_stamp,
        div_ex_month_ratios={1: 1.0}, recent_splits=cached_splits,
        price_fetched_at=old_stamp,
    )


@pytest.mark.parametrize("failed", ["price", "no_close", "dividend", "splits", None])
@pytest.mark.parametrize("cached_splits", [None, [], [("2026-09-29", 2.0)]])
def test_partial_fetch_merges_each_item(monkeypatch, failed, cached_splits):
    """取得失敗と取得成功で空を区別し、成功項目だけをキャッシュへ反映する。"""
    from datetime import date, timedelta
    from types import SimpleNamespace
    from src.utils import now_jst_iso

    day = date.today() - timedelta(days=3)  # 休日でも取得成功日時は今日
    ticker = _Ticker(day, failed)
    monkeypatch.setattr(md, "_yf", lambda: SimpleNamespace(Ticker=lambda _: ticker))
    stamp = now_jst_iso()
    monkeypatch.setattr(md, "now_jst_iso", lambda: stamp)
    old_stamp = "2025-01-01T09:00:00+09:00"
    existing = {}
    if cached_splits is not None:
        existing["AAA"] = _cached_row(cached_splits, old_stamp)
    result = md.fetch_market_data([_holding("AAA", name="銘柄")], existing, force=True)
    row = result.rows[0]
    cached = existing.get("AAA")
    # 通信の例外は 3 項目とも失敗。終値が空なら株価だけ失敗（配当・分割は列があれば成功）。
    price_failed = failed in ("price", "no_close")
    dividend_failed = failed in ("price", "dividend")
    splits_failed = failed in ("price", "splits")
    if price_failed:
        assert row.last_price == (100.0 if cached else None)
        assert row.price_as_of == ("2025-01-01" if cached else None)
        assert row.price_fetched_at == (old_stamp if cached else None)
    else:
        assert row.last_price == 250.0
        assert row.price_as_of == day.isoformat()
        assert row.price_fetched_at == stamp
    if dividend_failed:
        assert row.annual_div_ps_gross == (5.0 if cached else None)
        assert row.annual_div_ps_net == (4.0 if cached else None)
        assert row.div_forecast_as_of == ("2025-01-01" if cached else None)
        assert row.div_ex_month_ratios == ({1: 1.0} if cached else None)
    else:
        assert row.annual_div_ps_gross == 12.0
        assert row.annual_div_ps_net is None
        assert row.div_forecast_as_of == day.isoformat()
        assert row.div_ex_month_ratios == {day.month: 1.0}
    assert row.recent_splits == (cached_splits if splits_failed and cached else [])
    assert row.fetched_at == (None if failed else stamp)
    # 通信の例外は同じ原因なので 1 件だけ。それ以外は失敗した項目ごとに 1 件。
    assert len(result.errors) == (1 if failed else 0)
    assert ticker.history_calls == 1  # 1 銘柄 1 リクエスト
    if failed:
        # 部分失敗を当日取得済みとしてスキップしない。
        retried = md.fetch_market_data(
            [_holding("AAA", name="銘柄")], {"AAA": row}, force=False,
        )
        assert len(retried.errors) == 1


def _fetch_with(monkeypatch, ticker, existing=None):
    from types import SimpleNamespace

    monkeypatch.setattr(md, "_yf", lambda: SimpleNamespace(Ticker=lambda _: ticker))
    return md.fetch_market_data(
        [_holding("AAA", name="銘柄")], existing or {}, force=True
    )


def test_zero_rows_in_dividends_do_not_move_the_anchor(monkeypatch):
    """Dividends 列の 0.0 行（イベントの無い営業日）を落としてから集計する。

    落とさないと最新の権利落ち日が「最新の営業日」になり、div_forecast_as_of が
    今日付近になる（エラーは出ない）。ゼロ行を除いた入力と同じ結果になること。
    """
    from datetime import date, timedelta

    ex = date.today() - timedelta(days=40)
    ticker = _Ticker(date.today() - timedelta(days=1), None, dividends=[(ex, 12.0)])
    row = _fetch_with(monkeypatch, ticker).rows[0]
    expected = md._parse_yfinance_dividends(_series([(_ymd(ex), 12.0)]))
    assert (
        row.annual_div_ps_gross, row.div_forecast_as_of, row.div_ex_month_ratios
    ) == expected
    assert row.div_forecast_as_of == ex.isoformat()


def test_all_zero_dividends_column_clears_cached_dividends(monkeypatch):
    """列があって全部 0 は「配当なし」＝成功で空（古い配当を消す）。"""
    from datetime import date, timedelta

    ticker = _Ticker(date.today() - timedelta(days=1), None, dividends=[])
    existing = {"AAA": _cached_row([], "2025-01-01T09:00:00+09:00")}
    result = _fetch_with(monkeypatch, ticker, existing)
    row = result.rows[0]
    assert row.annual_div_ps_gross is None
    assert row.div_forecast_as_of is None
    assert row.div_ex_month_ratios is None
    assert result.errors == []


def test_missing_dividends_column_keeps_cache_and_reports(monkeypatch):
    """列が無い（存在しない銘柄など）は失敗＝キャッシュを保持してエラーを 1 件積む。"""
    from datetime import date, timedelta

    ticker = _Ticker(date.today() - timedelta(days=1), "dividend")
    existing = {"AAA": _cached_row([], "2025-01-01T09:00:00+09:00")}
    result = _fetch_with(monkeypatch, ticker, existing)
    assert result.rows[0].annual_div_ps_gross == 5.0
    assert len(result.errors) == 1 and "配当取得失敗" in result.errors[0]


@pytest.mark.parametrize("age, ok", [(14, True), (15, False)])
def test_stale_close_is_not_a_successful_price_fetch(monkeypatch, age, ok):
    """5 年ぶん取るので、売買停止銘柄の古い終値を当日取得として記録しない。"""
    from datetime import date, timedelta

    close_day = date.today() - timedelta(days=age)
    ticker = _Ticker(date.today(), None, close_day=close_day)
    existing = {"AAA": _cached_row([], "2025-01-01T09:00:00+09:00")}
    result = _fetch_with(monkeypatch, ticker, existing)
    row = result.rows[0]
    if ok:
        assert row.last_price == 250.0 and not result.errors
    else:
        assert row.last_price == 100.0  # キャッシュ保持
        assert row.price_fetched_at == "2025-01-01T09:00:00+09:00"
        assert result.errors == ["株価取得失敗: AAA (終値データなし)"]


def test_dividend_after_last_close_does_not_shift_the_window(monkeypatch):
    """最後の終値より後ろの行にある先日付の権利落ちは、今日以前の配当があれば集計に入らない。"""
    from datetime import date, timedelta

    past = date.today() - timedelta(days=60)
    future = date.today() + timedelta(days=20)
    ticker = _Ticker(
        date.today(), None, dividends=[(past, 10.0), (future, 99.0)],
        close_day=date.today() - timedelta(days=1),
    )
    row = _fetch_with(monkeypatch, ticker).rows[0]
    assert row.annual_div_ps_gross == pytest.approx(10.0)
    assert row.div_forecast_as_of == past.isoformat()


def test_unexpected_fetch_failure_preserves_all_cached_items(monkeypatch):
    def crash(*_args):
        raise OSError("予期しない失敗")

    monkeypatch.setattr(md, "_fetch_one", crash)
    monkeypatch.setattr(md, "_yf", lambda: object())
    cached = MarketData(
        "AAA", "JPY", 100.0, "2025-01-01", 5.0, None, "2025-01-01",
        "yfinance", "2025-01-01", recent_splits=[("2025-01-01", 2.0)],
        price_fetched_at="2025-01-01",
    )
    result = md.fetch_market_data([_holding("AAA")], {"AAA": cached}, force=True)
    row = result.rows[0]
    assert row.last_price == cached.last_price
    assert row.price_fetched_at == cached.price_fetched_at
    assert row.recent_splits == cached.recent_splits
    assert row.fetched_at is None
    assert "予期しない失敗" in result.errors[0]
