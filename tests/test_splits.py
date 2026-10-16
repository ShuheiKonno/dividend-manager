"""splits.py（未反映の分割の検知・補正）と分割の CSV 表現の検証。"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from src import splits
from src.models import Holding, MarketData
from src.utils import format_splits, parse_splits

TODAY = date(2026, 9, 30)


def _holding(**overrides) -> Holding:
    base = dict(
        symbol="5334", name="日特殊陶", asset_type="stock", currency="JPY",
        shares=300.0, avg_cost=2503.0, account="特定",
        annual_div_ps_gross_override=None, annual_div_ps_net_override=None,
        div_forecast_as_of_override=None, annual_benefit_jpy=0.0,
        withdrawal_fixed_jpy=None, withdrawal_rate=None, tax_rate=None, active=True,
    )
    base.update(overrides)
    return Holding(**base)


def _market(splits_=(("2026-09-29", 2.0),)) -> MarketData:
    return MarketData(
        symbol="5334", currency="JPY", last_price=3737.0, price_as_of="2026-09-30",
        annual_div_ps_gross=108.5, annual_div_ps_net=None, div_forecast_as_of=None,
        source="yfinance", fetched_at=None, recent_splits=list(splits_),
    )


# ---- CSV 表現 ----

def test_parse_and_format_splits_roundtrip():
    text = "2025-09-29:5,2026-09-29:2"
    assert parse_splits(text) == [("2025-09-29", 5.0), ("2026-09-29", 2.0)]
    assert format_splits(parse_splits(text)) == text


def test_parse_splits_drops_broken_and_neutral_entries():
    # 日付が壊れている / 比率が 0・負・非数・1.0（変化なし）の要素は捨てる。
    raw = "bad:2,2026-09-29:0,2026-09-29:-2,2026-09-29:x,2026-09-29:1,2026-09-28:3"
    assert parse_splits(raw) == [("2026-09-28", 3.0)]


def test_parse_splits_sorts_and_normalizes_dates():
    assert parse_splits("2026/9/29:2,2024-01-05:3") == [
        ("2024-01-05", 3.0), ("2026-09-29", 2.0),
    ]


def test_parse_splits_empty_inputs():
    assert parse_splits(None) == []
    assert parse_splits("") == []
    assert format_splits([]) == ""


# ---- 比率の表示 ----

@pytest.mark.parametrize(
    "ratio,label",
    [(2.0, "2:1"), (15.0, "15:1"), (1.5, "3:2"), (0.2, "1:5"), (0.5, "1:2")],
)
def test_format_ratio(ratio, label):
    assert splits.format_ratio(ratio) == label


def test_flag_text_is_short_and_empty_when_no_split():
    assert splits.flag_text([]) == ""
    assert splits.flag_text([("2026-09-29", 2.0)]) == "⚑ 2:1(9/29)"
    assert splits.flag_text([("2026-03-30", 4.0), ("2026-09-29", 2.0)]) == (
        "⚑ 4:1(3/30) / 2:1(9/29)"
    )


# ---- 未反映の検知 ----

def test_split_after_baseline_is_pending():
    h = _holding(split_applied_through="2026-09-01")
    assert splits.pending_splits(h, _market(), TODAY) == [("2026-09-29", 2.0)]


def test_split_on_or_before_baseline_is_not_pending():
    # 反映済み日は「その日を含む」。当日の分割は反映済みとして扱う。
    for baseline in ("2026-09-29", "2026-10-15"):
        h = _holding(split_applied_through=baseline)
        assert splits.pending_splits(h, _market(), TODAY) == []


def test_blank_baseline_only_flags_recent_splits():
    old = _market((("2025-11-27", 2.0), ("2026-09-29", 2.0)))
    assert splits.pending_splits(_holding(), old, TODAY) == [("2026-09-29", 2.0)]


def test_blank_baseline_lookback_boundary():
    edge = (TODAY.replace(day=1).isoformat(), 2.0)  # 2026-09-01（29日前）
    assert splits.pending_splits(_holding(), _market((edge,)), TODAY) == [edge]
    stale = ("2026-07-31", 2.0)  # 61日前
    assert splits.pending_splits(_holding(), _market((stale,)), TODAY) == []


def test_fund_and_missing_market_are_never_pending():
    assert splits.pending_splits(_holding(asset_type="fund"), _market(), TODAY) == []
    assert splits.pending_splits(_holding(), None, TODAY) == []
    assert splits.pending_splits(_holding(), _market(()), TODAY) == []


def test_future_dated_split_is_not_pending():
    assert splits.pending_splits(_holding(), _market((("2026-10-05", 2.0),)), TODAY) == []


def test_pending_is_per_row_not_per_symbol():
    # 同じ銘柄を2口座で持つとき、片方だけ反映済みでも他方の旗は消えない。
    done = _holding(account="NISA", split_applied_through="2026-09-29")
    todo = _holding(account="特定")
    found = splits.pending_holdings([done, todo], {"5334": _market()}, TODAY)
    assert [i for i, _ in found] == [1]


def test_pending_holdings_excludes_inactive_unless_requested():
    """無効銘柄（active=False）は既定で除外し、include_inactive=True のときのみ含める。"""
    active_row = _holding(account="特定", active=True)
    inactive_row = _holding(account="旧NISA", active=False)
    market_map = {"5334": _market()}
    assert [i for i, _ in splits.pending_holdings([active_row, inactive_row], market_map, TODAY)] == [0]
    assert [
        i
        for i, _ in splits.pending_holdings(
            [active_row, inactive_row], market_map, TODAY, include_inactive=True
        )
    ] == [0, 1]


# ---- 補正 ----

def test_apply_correction_scales_shares_cost_and_overrides():
    h = _holding(
        annual_div_ps_gross_override=108.0, annual_div_ps_net_override=86.0,
        annual_benefit_jpy=1000.0, withdrawal_fixed_jpy=5000.0,
    )
    out = splits.apply_correction(h, [("2026-09-29", 2.0)])
    assert out.shares == 600.0
    assert out.avg_cost == 1251.5
    assert out.annual_div_ps_gross_override == 54.0
    assert out.annual_div_ps_net_override == 43.0
    assert out.split_applied_through == "2026-09-29"
    # 優待額・取崩額は1株あたりでも株数比例でもないので触らない。
    assert out.annual_benefit_jpy == 1000.0
    assert out.withdrawal_fixed_jpy == 5000.0
    # 元は変更しない（編集キャンセルで元データが変わらない）。
    assert h.shares == 300.0 and h.split_applied_through == ""


def test_apply_correction_keeps_book_value_and_unset_overrides():
    h = _holding()
    out = splits.apply_correction(h, [("2026-09-29", 2.0)])
    assert out.shares * out.avg_cost == pytest.approx(h.shares * h.avg_cost)
    assert out.annual_div_ps_gross_override is None
    assert out.annual_div_ps_net_override is None


def test_apply_correction_reverse_split():
    h = _holding(shares=1000.0, avg_cost=50.0, annual_div_ps_gross_override=1.0)
    out = splits.apply_correction(h, [("2026-09-29", 0.2)])  # 5→1 併合
    assert out.shares == 200.0
    assert out.avg_cost == 250.0
    assert out.annual_div_ps_gross_override == 5.0


def test_apply_correction_combines_multiple_pending_splits():
    h = _holding(shares=100.0, avg_cost=6000.0)
    out = splits.apply_correction(h, [("2026-03-30", 4.0), ("2026-09-29", 2.0)])
    assert out.shares == 800.0
    assert out.avg_cost == 750.0
    assert out.split_applied_through == "2026-09-29"


def test_apply_correction_snaps_non_terminating_ratio():
    # 3:1 併合の 1/3 を float のまま掛けると 300 株が 99.9999... 株になる。
    h = _holding(shares=300.0, avg_cost=100.0)
    out = splits.apply_correction(h, [("2026-09-29", 1 / 3)])
    assert out.shares == 100.0
    assert out.avg_cost == 300.0


def test_applying_then_recomputing_leaves_nothing_pending():
    # 補正後は旗が消える（二度押しても二重適用にならない）。
    h = _holding()
    out = splits.apply_correction(h, [("2026-09-29", 2.0)])
    assert splits.pending_splits(out, _market(), TODAY) == []


def test_mark_applied_changes_only_the_baseline():
    h = _holding(annual_div_ps_gross_override=108.0)
    out = splits.mark_applied(h, [("2026-09-29", 2.0)])
    assert out == replace(h, split_applied_through="2026-09-29")
    assert splits.pending_splits(out, _market(), TODAY) == []


def test_correction_lines_show_before_after_and_price():
    h = _holding(annual_div_ps_gross_override=108.0)
    text = "\n".join(splits.correction_lines(h, [("2026-09-29", 2.0)], 3737.0))
    assert "2026-09-29 に 2:1" in text
    assert "株数: 300 → 600" in text
    assert "購入単価: 2,503 → 1,251.5" in text
    assert "配当/株の上書き（税引前）: 108 → 54" in text
    assert "現在株価: 3,737" in text
    assert "税引後" not in text  # 未設定の上書きは出さない


# ---- モデル・CSV の往復 ----

def test_holding_roundtrips_split_applied_through():
    h = _holding(split_applied_through="2026-09-29")
    assert Holding.from_dict(h.to_dict()) == h


def test_holding_from_legacy_row_without_column_is_blank():
    row = _holding().to_dict()
    del row["split_applied_through"]
    assert Holding.from_dict(row).split_applied_through == ""


def test_holding_normalizes_or_drops_broken_baseline():
    row = _holding().to_dict()
    row["split_applied_through"] = "2026/9/29"
    assert Holding.from_dict(row).split_applied_through == "2026-09-29"
    row["split_applied_through"] = "昨日"
    assert Holding.from_dict(row).split_applied_through == ""


def test_market_data_roundtrips_recent_splits():
    m = _market((("2025-09-29", 5.0), ("2026-09-29", 2.0)))
    assert MarketData.from_dict(m.to_dict()).recent_splits == m.recent_splits


def test_market_data_from_legacy_row_without_column_is_empty():
    row = _market().to_dict()
    del row["recent_splits"]
    assert MarketData.from_dict(row).recent_splits == []


# ---- 反映済みの分割（「完了」表示で残す） ----

def test_done_flag_text():
    assert splits.done_flag_text([]) == ""
    assert splits.done_flag_text([("2026-09-29", 2.0)]) == "✓ 2:1(9/29)"


def test_applying_turns_the_flag_into_a_done_mark():
    """補正した行は旗が消えるのではなく「反映済み」として残る。"""
    h = _holding()
    market = _market()
    assert splits.pending_splits(h, market, TODAY) == [("2026-09-29", 2.0)]
    assert splits.applied_splits(h, market, TODAY) == []

    corrected = splits.apply_correction(h, [("2026-09-29", 2.0)])
    assert splits.pending_splits(corrected, market, TODAY) == []
    assert splits.applied_splits(corrected, market, TODAY) == [("2026-09-29", 2.0)]


def test_marking_as_applied_also_shows_the_done_mark():
    h = _holding()
    out = splits.mark_applied(h, [("2026-09-29", 2.0)])
    assert splits.applied_splits(out, _market(), TODAY) == [("2026-09-29", 2.0)]


def test_done_mark_disappears_after_the_display_period():
    h = _holding(split_applied_through="2026-09-29")
    edge = date(2026, 9, 29) + timedelta(days=splits.APPLIED_DISPLAY_DAYS)
    assert splits.applied_splits(h, _market(), edge) == [("2026-09-29", 2.0)]
    assert splits.applied_splits(h, _market(), edge + timedelta(days=1)) == []


def test_done_mark_only_for_the_split_that_was_applied():
    # 新規登録の行（反映済み日＝登録日）に、登録前の分割まで「完了」として並べない。
    h = _holding(split_applied_through="2026-09-30")
    assert splits.applied_splits(h, _market(), TODAY) == []
    # 反映済み日が無ければ完了表示も出さない。
    assert splits.applied_splits(_holding(), _market(), TODAY) == []


def test_done_mark_is_never_shown_for_funds_or_without_market():
    h = _holding(asset_type="fund", split_applied_through="2026-09-29")
    assert splits.applied_splits(h, _market(), TODAY) == []
    assert splits.applied_splits(_holding(split_applied_through="2026-09-29"), None, TODAY) == []
