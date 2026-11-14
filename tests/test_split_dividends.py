"""分割当日の配当補正（market_data._correct_split_day_dividends ほか）の検証。

Yahoo は分割当日**以前**の配当を分割後基準へ調整するが、**当日の配当は未調整のことも
あれば調整済みのこともある**。無条件に割ると調整済みの銘柄を二重に割るので、参照配当
（前年同期／直前）と比べて判別する。値は実データ（2026-09-30 時点の yfinance）そのまま。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

import src.market_data as md
from tests.test_market_data import _FakeStamp, _holding, _series, _ymd


def _entries(pairs):
    return [(date.fromisoformat(day), value) for day, value in pairs]


def _corrected(pairs, split_list):
    fixed = md._correct_split_day_dividends(_entries(pairs), split_list)
    return {d.isoformat(): v for d, v in fixed}


# ---- 判別規則（実データの値） ----


def test_unadjusted_split_day_dividend_is_divided_by_ratio():
    # 5334: 2026-09-29 に 2:1 分割。当日の 105 は分割前基準（正しくは 52.5）。
    out = _corrected(
        [
            ("2024-09-27", 44.0),
            ("2025-03-28", 45.0),
            ("2025-09-29", 46.5),
            ("2026-03-30", 56.0),
            ("2026-09-29", 105.0),
        ],
        [("2026-09-29", 2.0)],
    )
    assert out["2026-09-29"] == pytest.approx(52.5)
    # 当日以外は触らない。
    assert out["2026-03-30"] == 56.0 and out["2025-09-29"] == 46.5


def test_unadjusted_split_day_dividend_large_ratio():
    # 8766: 15:1 分割。122.5 は分割前基準（正しくは 8.1667）。
    out = _corrected(
        [
            ("2025-03-28", 6.0667),
            ("2025-09-29", 7.0333),
            ("2026-03-30", 7.5),
            ("2026-09-29", 122.5),
        ],
        [("2026-09-29", 15.0)],
    )
    assert out["2026-09-29"] == pytest.approx(122.5 / 15)


def test_unadjusted_dividend_left_for_a_year_is_still_corrected():
    # 5401: 2025-09-29 の 60 円（正しくは 12）は1年後も未調整のまま。
    out = _corrected(
        [
            ("2024-09-27", 16.0),
            ("2025-03-28", 16.0),
            ("2025-09-29", 60.0),
            ("2026-03-30", 12.0),
            ("2026-09-29", 12.0),
        ],
        [("2025-09-29", 5.0)],
    )
    assert out["2025-09-29"] == pytest.approx(12.0)
    assert out["2026-09-29"] == 12.0


def test_already_adjusted_split_day_dividend_is_left_alone():
    # 8316: 2024-09-27 の 3:1 分割は当日分も調整済み（30 のまま。割ると 10 で二重に割る）。
    out = _corrected(
        [("2024-03-28", 22.5), ("2024-09-27", 30.0), ("2025-03-28", 31.0)],
        [("2024-09-27", 3.0)],
    )
    assert out["2024-09-27"] == 30.0


def test_adjusted_dividend_is_not_divided_again_after_yahoo_fixes_it():
    # 5334 の当日分を Yahoo が後から 52.5 へ調整したら、補正は自動でやめる。
    out = _corrected(
        [("2025-09-29", 46.5), ("2026-03-30", 56.0), ("2026-09-29", 52.5)],
        [("2026-09-29", 2.0)],
    )
    assert out["2026-09-29"] == 52.5


def test_reference_prefers_same_season_over_previous_dividend():
    # 中間 15〜20 / 期末 70〜75 の銘柄（8473 型）。期末の分割当日 75 を直前の中間 20 と
    # 比べると 3.75 倍で「未調整」と誤判定するが、前年同期の期末 70 と比べれば調整済み。
    out = _corrected(
        [
            ("2024-09-27", 15.0),
            ("2025-03-28", 70.0),
            ("2025-09-29", 20.0),
            ("2026-03-30", 75.0),
        ],
        [("2026-03-30", 2.0)],
    )
    assert out["2026-03-30"] == 75.0


def test_known_limitation_large_dividend_growth_is_mistaken_for_unadjusted():
    """既知の弱点の固定（規則を改善したらこのテストを更新する）。

    日本郵船 9101 の 2022-09-29（3:1 分割）。350円は好況期の実際の増配で分割後基準と
    推定されるが（前年同期 66.67円の5.25倍）、前年同期との比較だけでは「未調整（×3）」と
    「調整済みだが大幅増配」を見分けられず、規則は 1/3 にしてしまう。
    分割後基準かは会社の一次情報では未確認。集計窓の外の配当なので年額には効かない。
    """
    out = _corrected(
        [("2021-03-30", 36.6667), ("2021-09-29", 66.6667), ("2022-03-30", 416.6667),
         ("2022-09-29", 350.0), ("2023-03-30", 170.0)],
        [("2022-09-29", 3.0)],
    )
    assert out["2022-09-29"] == pytest.approx(350.0 / 3)


def test_no_reference_dividend_means_no_correction():
    out = _corrected([("2026-09-29", 105.0)], [("2026-09-29", 2.0)])
    assert out["2026-09-29"] == 105.0


def test_ratios_too_close_to_one_are_not_corrected():
    # 3:2 分割（1.5）は「分割前基準の配当」と「数十%の増配」を見分けられない。
    out = _corrected(
        [("2025-09-29", 45.0), ("2026-03-30", 46.0), ("2026-09-29", 70.0)],
        [("2026-09-29", 1.5)],
    )
    assert out["2026-09-29"] == 70.0


def test_unadjusted_dividend_on_reverse_split_is_corrected():
    # 5→1 併合（0.2）。新基準の配当は 5 倍になり、未調整の当日分は 0.2 倍で出る。
    out = _corrected(
        [("2025-09-29", 10.0), ("2026-03-30", 10.0), ("2026-09-29", 2.0)],
        [("2026-09-29", 0.2)],
    )
    assert out["2026-09-29"] == pytest.approx(10.0)


def test_no_splits_or_no_split_day_dividend_is_a_noop():
    pairs = [("2025-09-29", 46.5), ("2026-03-30", 56.0)]
    assert _corrected(pairs, []) == dict(pairs)
    assert _corrected(pairs, [("2026-01-15", 2.0)]) == dict(pairs)


# ---- 年額への反映 ----


def test_parse_dividends_applies_split_day_correction_to_annual_total():
    today = date.today()
    ex = today - timedelta(days=1)
    series = _series(
        [
            (_ymd(ex - timedelta(days=365)), 46.5),
            (_ymd(ex - timedelta(days=183)), 56.0),
            (_ymd(ex), 105.0),
        ]
    )
    plain, _, _ = md._parse_yfinance_dividends(series)
    fixed, latest, ratios = md._parse_yfinance_dividends(
        series, [(ex.isoformat(), 2.0)]
    )
    assert plain == pytest.approx(161.0)  # 補正なし（従来の挙動）
    assert fixed == pytest.approx(108.5)  # 56 + 52.5
    assert latest == ex.isoformat()
    assert ratios[ex.month] == pytest.approx(52.5 / 108.5)


def test_reference_dividend_older_than_stale_cutoff_is_still_used():
    # 参照は古い配当を捨てる前に探す。250日前の分割当日は、前年同期が615日前で
    # 370日の足切りの外だが、補正の参照には使える。
    ex = date.today() - timedelta(days=250)
    series = _series(
        [
            (_ymd(ex - timedelta(days=365)), 46.5),
            (_ymd(ex), 105.0),
        ]
    )
    fixed, _, _ = md._parse_yfinance_dividends(series, [(ex.isoformat(), 2.0)])
    assert fixed == pytest.approx(52.5)


def test_parse_yfinance_splits_filters_neutral_and_broken():
    series = _series(
        [
            ((2025, 9, 29), 5.0),
            ((2026, 9, 29), 2.0),
            ((2026, 1, 1), 1.0),
            ((2026, 2, 1), 0.0),
            ((2026, 3, 1), float("nan")),
        ]
    )
    assert md._parse_yfinance_splits(series) == [
        ("2025-09-29", 5.0),
        ("2026-09-29", 2.0),
    ]
    assert md._parse_yfinance_splits(None) == []


# ---- _fetch_one が分割を取得して配当補正へ渡す ----


class _FakeClose:
    def __init__(self, day, price):
        self._day, self._price = day, price
        self.empty = False

    def dropna(self):
        return self

    @property
    def iloc(self):
        price = self._price

        class _Iloc:
            def __getitem__(self, _i):
                return price

        return _Iloc()

    @property
    def index(self):
        return [_FakeStamp(self._day)]


class _FakeHistory:
    """history() の戻り値。Dividends / Stock Splits 列はイベントの無い日の 0.0 行を含む。"""

    def __init__(self, day, price, dividends, splits):
        self._columns = {"Close": _FakeClose(day, price)}
        zero = [(_FakeStamp(day), 0.0)]
        if dividends is not None:
            self._columns["Dividends"] = _Events(zero + list(dividends.items()))
        if splits is not None:
            self._columns["Stock Splits"] = _Events(zero + list(splits.items()))

    def __contains__(self, key):
        return key in self._columns

    def __getitem__(self, key):
        return self._columns[key]


class _Events:
    def __init__(self, pairs):
        self._pairs = pairs

    def __len__(self):
        return len(self._pairs)

    def items(self):
        return iter(self._pairs)


class _FakeTicker:
    """splits=None は Stock Splits 列が無い（＝分割の取得失敗）ことを表す。"""

    def __init__(self, dividends, splits):
        self._dividends = dividends
        self._splits = splits

    def history(self, **_kwargs):
        return _FakeHistory(date.today(), 3737.0, self._dividends, self._splits)


class _FakeYf:
    def __init__(self, ticker):
        self._ticker = ticker

    def Ticker(self, _symbol):  # noqa: N802 (yfinance API)
        return self._ticker


def _fetch_with(monkeypatch, ticker):
    monkeypatch.setattr(md, "_yf", lambda: _FakeYf(ticker))
    return md._fetch_one(
        _holding("5334", name="日特殊陶"), "2026-09-30T00:00:00+09:00", "yfinance"
    )


def test_fetch_one_stores_recent_splits_and_corrects_dividend(monkeypatch):
    ex = date.today() - timedelta(days=1)
    dividends = _series(
        [
            (_ymd(ex - timedelta(days=365)), 46.5),
            (_ymd(ex - timedelta(days=183)), 56.0),
            (_ymd(ex), 105.0),
        ]
    )
    splits = _series([((2009, 1, 4), 100.0), (_ymd(ex), 2.0)])
    row, errors, _, succeeded = _fetch_with(monkeypatch, _FakeTicker(dividends, splits))
    assert errors == []
    assert succeeded == {"price", "dividend", "splits"}
    assert row.annual_div_ps_gross == pytest.approx(108.5)
    # 何年も前の分割は保存しない（直近だけ）。
    assert row.recent_splits == [(ex.isoformat(), 2.0)]


def test_fetch_one_survives_splits_failure(monkeypatch):
    ex = date.today() - timedelta(days=1)
    dividends = _series([(_ymd(ex - timedelta(days=183)), 56.0), (_ymd(ex), 105.0)])
    row, errors, _, succeeded = _fetch_with(monkeypatch, _FakeTicker(dividends, None))
    # 株価・配当は取得を続け、分割だけ失敗として集約側へ通知する。
    assert errors == ["分割取得失敗: 5334 (Stock Splits 列なし)"]
    assert succeeded == {"price", "dividend"}
    assert row.last_price == 3737.0
    assert row.annual_div_ps_gross == pytest.approx(161.0)
    assert row.recent_splits == []
