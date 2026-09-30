"""分割の反映済み日・直近の分割の CSV 往復と、compute_rows の旗（未反映の分割）の検証。"""

from __future__ import annotations

from datetime import date, timedelta

import src.io_csv as io_csv
from src.calc import compute_rows
from src.models import HOLDINGS_FIELDS, MARKET_DATA_FIELDS, Holding, MarketData
from tests.test_io_csv import _configure_tmp_dirs


def _holding(symbol="5334", **overrides) -> Holding:
    base = dict(
        symbol=symbol, name="日特殊陶", asset_type="stock", currency="JPY",
        shares=300.0, avg_cost=2503.0, account="特定",
        annual_div_ps_gross_override=None, annual_div_ps_net_override=None,
        div_forecast_as_of_override=None, annual_benefit_jpy=0.0,
        withdrawal_fixed_jpy=None, withdrawal_rate=None, tax_rate=None, active=True,
    )
    base.update(overrides)
    return Holding(**base)


def _market(symbol="5334", recent_splits=None, price=3737.0) -> MarketData:
    return MarketData(
        symbol=symbol, currency="JPY", last_price=price, price_as_of="2026-09-30",
        annual_div_ps_gross=108.5, annual_div_ps_net=None, div_forecast_as_of=None,
        source="yfinance", fetched_at=None, recent_splits=recent_splits or [],
    )


def _recent_split_date(days_ago: int = 5) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


# ---- CSV の往復 ----

def test_holdings_csv_roundtrips_split_applied_through(monkeypatch, tmp_path):
    _configure_tmp_dirs(monkeypatch, tmp_path)
    holdings = [
        _holding("5334", split_applied_through="2026-09-29"),
        _holding("8316", split_applied_through=""),
    ]
    target = tmp_path / "holdings.csv"
    io_csv.export_holdings_csv(target, holdings)
    assert "split_applied_through" in target.read_text(encoding="utf-8-sig").splitlines()[0]
    assert io_csv.read_holdings_csv(target) == holdings


def test_legacy_holdings_csv_without_column_loads_and_gains_it_on_save(monkeypatch, tmp_path):
    """列を足す前の holdings.csv も読め、次の保存で列が付く（空＝記録なし）。"""
    _configure_tmp_dirs(monkeypatch, tmp_path)
    fields = [f for f in HOLDINGS_FIELDS if f != "split_applied_through"]
    legacy = {k: v for k, v in _holding().to_dict().items() if k in fields}
    io_csv._atomic_write(io_csv.HOLDINGS_PATH, [legacy], fields, backup=False)

    loaded = io_csv.load_holdings()
    assert [h.split_applied_through for h in loaded] == [""]

    io_csv.save_holdings(loaded)
    header = io_csv.HOLDINGS_PATH.read_text(encoding="utf-8").splitlines()[0]
    assert header.split(",")[-1] == "split_applied_through"


def test_market_data_csv_roundtrips_recent_splits(monkeypatch, tmp_path):
    _configure_tmp_dirs(monkeypatch, tmp_path)
    rows = [
        _market("5334", [("2026-09-29", 2.0)]),
        _market("8316", [("2024-09-27", 3.0), ("2026-09-29", 2.0)]),
        _market("7203"),
    ]
    io_csv.save_market_data(rows)
    loaded = io_csv.load_market_data()
    assert [m.recent_splits for m in loaded] == [m.recent_splits for m in rows]


def test_legacy_market_data_csv_without_column_loads_empty(monkeypatch, tmp_path):
    _configure_tmp_dirs(monkeypatch, tmp_path)
    fields = [f for f in MARKET_DATA_FIELDS if f != "recent_splits"]
    legacy = {k: v for k, v in _market().to_dict().items() if k in fields}
    io_csv._atomic_write(io_csv.MARKET_DATA_PATH, [legacy], fields, backup=False)
    assert [m.recent_splits for m in io_csv.load_market_data()] == [[]]


# ---- compute_rows の旗 ----

def test_row_carries_pending_splits_and_leaves_numbers_untouched():
    day = _recent_split_date()
    holding = _holding()
    market = _market(recent_splits=[(day, 2.0)])
    rows, _ = compute_rows([holding], {"5334": market}, None, "gross")
    assert rows[0]["pending_splits"] == [(day, 2.0)]
    # 旗を立てるだけ。株数・単価・時価は補正前のまま（補正は利用者が押したときだけ）。
    assert rows[0]["shares"] == 300.0
    assert rows[0]["avg_cost"] == 2503.0
    assert rows[0]["market_value_jpy"] == 300.0 * 3737.0


def test_row_has_no_flag_after_baseline_or_without_market():
    day = _recent_split_date()
    market = _market(recent_splits=[(day, 2.0)])
    done = _holding(split_applied_through=day)
    rows, _ = compute_rows([done], {"5334": market}, None, "gross")
    assert rows[0]["pending_splits"] == []
    rows, _ = compute_rows([_holding()], {}, None, "gross")
    assert rows[0]["pending_splits"] == []


def test_row_carries_applied_splits_after_reflecting():
    """反映済みにした行は pending が空になり、applied_splits に直近の分割が載る。"""
    day = _recent_split_date()
    market = _market(recent_splits=[(day, 2.0)])
    done = _holding(split_applied_through=day)
    rows, _ = compute_rows([done], {"5334": market}, None, "gross")
    assert rows[0]["pending_splits"] == []
    assert rows[0]["applied_splits"] == [(day, 2.0)]
    # 数値は動かさない（完了表示は見た目だけ）。
    assert rows[0]["shares"] == 300.0


def test_pending_flag_does_not_change_the_summary():
    """旗の有無で集計は変わらない（補正しない限り数値は動かさない）。"""
    day = _recent_split_date()
    with_split = compute_rows(
        [_holding()], {"5334": _market(recent_splits=[(day, 2.0)])}, None, "gross"
    )[1]
    without = compute_rows([_holding()], {"5334": _market()}, None, "gross")[1]
    assert with_split == without
