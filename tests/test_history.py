"""履歴ロジック（src/history.py）と HistoryRecord の検証（PySide6 非依存）。

日付キーのマージ/upsert、配当系列の合成、CSV ラウンドトリップを確認する。
履歴 CSV の取込（read_history_csv）は tests/test_io_csv.py で検証する。
"""

from __future__ import annotations

from src.history import (
    merge_records,
    upsert_record,
    combine_income,
)
from src.models import HistoryRecord


def _rec(date: str, **kw) -> HistoryRecord:
    base = dict(
        record_date=date,
        total_market_value_jpy=None,
        total_book_value_jpy=None,
        total_dividend_jpy=None,
        total_benefit_jpy=None,
        total_profit_jpy=None,
        total_withdrawal_income_jpy=None,
        source="app",
    )
    base.update(kw)
    return HistoryRecord(**base)


# ---- マージ / upsert ----

def test_upsert_replaces_same_date():
    existing = [_rec("2024-06-27", total_market_value_jpy=100.0)]
    updated = _rec("2024-06-27", total_market_value_jpy=200.0)
    result = upsert_record(existing, updated)
    assert len(result) == 1
    assert result[0].total_market_value_jpy == 200.0


def test_merge_prefer_existing_keeps_original():
    existing = [_rec("2024-06-27", total_market_value_jpy=100.0, source="app")]
    incoming = [
        _rec("2024-06-27", total_market_value_jpy=999.0, source="csv"),
        _rec("2024-06-26", total_market_value_jpy=50.0, source="csv"),
    ]
    result = merge_records(existing, incoming, prefer="existing")
    by_date = {r.record_date: r for r in result}
    # 既存日付はアプリ記録を保持
    assert by_date["2024-06-27"].source == "app"
    assert by_date["2024-06-27"].total_market_value_jpy == 100.0
    # 既存に無い日付は取込分を追加
    assert by_date["2024-06-26"].total_market_value_jpy == 50.0


def test_merge_sorts_ascending():
    records = [_rec("2024-12-01"), _rec("2024-01-01"), _rec("2024-06-15")]
    result = merge_records([], records, prefer="incoming")
    assert [r.record_date for r in result] == [
        "2024-01-01", "2024-06-15", "2024-12-01",
    ]


# ---- CSV ラウンドトリップ ----

def test_history_record_roundtrip():
    r = _rec(
        "2024-06-27",
        total_market_value_jpy=85204416.6486,
        total_book_value_jpy=60000000.0,
        total_dividend_jpy=2835193.0,
        total_benefit_jpy=0.0,  # 0.0 の明示値が保持されること
        total_profit_jpy=-123.0,
        total_withdrawal_income_jpy=4880000.0,
        source="app",
    )
    restored = HistoryRecord.from_dict(r.to_dict())
    assert restored == r


def test_history_record_roundtrip_missing_new_column():
    """新列を持たない旧 CSV 行（DictReader で欠損→None）も読み込めること。"""
    row = {
        "record_date": "2024-06-27",
        "total_market_value_jpy": "100.0",
        "total_dividend_jpy": "5.0",
        "source": "android_backup",
        # total_withdrawal_income_jpy は欠損
    }
    rec = HistoryRecord.from_dict(row)
    assert rec.total_withdrawal_income_jpy is None
    assert rec.total_dividend_jpy == 5.0


# ---- combine_income（配当推移グラフの系列合成） ----

def _income_rec(div, benefit, withdrawal) -> dict:
    return {
        "total_dividend_jpy": div,
        "total_benefit_jpy": benefit,
        "total_withdrawal_income_jpy": withdrawal,
    }


def test_combine_income_dividend_only():
    rec = _income_rec(1000.0, 200.0, 1500.0)
    assert combine_income(rec, with_benefit=False, with_withdrawal=False) == 1000.0


def test_combine_income_with_benefit():
    rec = _income_rec(1000.0, 200.0, 1500.0)
    assert combine_income(rec, with_benefit=True, with_withdrawal=False) == 1200.0


def test_combine_income_withdrawal():
    rec = _income_rec(1000.0, 200.0, 1500.0)
    assert combine_income(rec, with_benefit=False, with_withdrawal=True) == 1500.0
    assert combine_income(rec, with_benefit=True, with_withdrawal=True) == 1700.0


def test_combine_income_withdrawal_missing_returns_none():
    """取崩収入が未記録（過去の取込分など）なら取崩込みは None で線を分断。"""
    rec = _income_rec(1000.0, 200.0, None)
    assert combine_income(rec, with_benefit=False, with_withdrawal=True) is None
    assert combine_income(rec, with_benefit=True, with_withdrawal=True) is None
    # 取崩無しは通常配当で描ける
    assert combine_income(rec, with_benefit=False, with_withdrawal=False) == 1000.0


def test_combine_income_none_benefit_treated_as_zero():
    rec = _income_rec(1000.0, None, None)
    assert combine_income(rec, with_benefit=True, with_withdrawal=False) == 1000.0
