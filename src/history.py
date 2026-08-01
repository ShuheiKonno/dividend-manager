"""資産・配当の推移履歴ロジック（UI 非依存・純 Python）。

- 日次スナップショット（HistoryRecord）の生成・マージ
- 履歴 CSV の取込は io_csv.read_history_csv が担当（エクスポートと同一フォーマット）。

GUI からは io_csv.load_history / save_history を介して使う。表示層（gui/widgets）
には依存しないため、テスト・ヘッドレスで実行できる。
"""

from __future__ import annotations

from typing import Iterable

from .models import HistoryRecord
from .utils import safe_float


def merge_records(
    existing: Iterable[HistoryRecord],
    incoming: Iterable[HistoryRecord],
    prefer: str = "incoming",
) -> list[HistoryRecord]:
    """record_date をキーに 2 つの履歴を統合し、昇順ソートして返す。

    prefer="incoming": 同一日付は incoming で上書き（当日スナップショットの更新用）。
    prefer="existing": 同一日付は existing を保持（取込で既存記録を壊さない用）。
    """
    by_date: dict[str, HistoryRecord] = {r.record_date: r for r in existing}
    for rec in incoming:
        if not rec.record_date:
            continue
        if prefer == "existing" and rec.record_date in by_date:
            continue
        by_date[rec.record_date] = rec
    return [by_date[d] for d in sorted(by_date)]


def upsert_record(
    records: Iterable[HistoryRecord], record: HistoryRecord
) -> list[HistoryRecord]:
    """当日スナップショットを 1 件 upsert（同一日付は置換）して昇順で返す。"""
    return merge_records(records, [record], prefer="incoming")


# 収入モード → 履歴の基準列。取崩の 2 モードはそれぞれ専用の列を持つ（1 列で
# 兼ねると定額を選んでいるのに定率の値が出て、凡例まで含めて尤もらしく間違う）。
INCOME_BASE_KEYS = {
    None: "total_dividend_jpy",
    "fixed": "total_fixed_withdrawal_income_jpy",
    "rate": "total_withdrawal_income_jpy",
}


def combine_income(
    rec: dict, with_benefit: bool, withdrawal_mode: str | None
) -> float | None:
    """配当推移グラフの 1 点の表示値を、優待・収入モードに応じて合成して返す。

    withdrawal_mode=None   : 取崩無しの通常配当（total_dividend_jpy）を基準。
    withdrawal_mode="fixed": 定額取崩の収入を基準。
    withdrawal_mode="rate" : 定率取崩の収入を基準。
    with_benefit=True      : 上記へ優待（total_benefit_jpy）を加算。
    基準値が None（その列を記録する前の日＝過去の取込分など）の場合は None を
    返し、折れ線を分断する。優待が None の日は 0 として扱う。
    """
    key = INCOME_BASE_KEYS.get(withdrawal_mode, "total_dividend_jpy")
    base = safe_float(rec.get(key))
    if base is None:
        return None
    if with_benefit:
        base += safe_float(rec.get("total_benefit_jpy")) or 0.0
    return base
