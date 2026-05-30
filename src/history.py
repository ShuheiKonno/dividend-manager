"""資産・配当の推移履歴ロジック（UI 非依存・純 Python）。

- 日次スナップショット（HistoryRecord）の生成・マージ
- Android 版バックアップ JSON（BackUpRecordInfoAndroid.json）からの取込

GUI からは io_csv.load_history / save_history を介して使う。表示層（gui/widgets）
には依存しないため、テスト・ヘッドレスで実行できる。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import HistoryRecord
from .utils import safe_float


# Android バックアップの recordDate 形式（例: "Jun 27, 2024 00:00:00"）
_ANDROID_DATE_FMT = "%b %d, %Y %H:%M:%S"
# 取込対象のポートフォリオ（main 以外は未使用の空枠）
_ANDROID_MAIN_PORTFOLIO = "main"


def _android_date_to_iso(raw: str) -> str | None:
    """"Jun 27, 2024 00:00:00" → "2024-06-27"。解析不能なら None。"""
    try:
        return datetime.strptime(raw.strip(), _ANDROID_DATE_FMT).date().isoformat()
    except (ValueError, AttributeError):
        return None


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


def parse_android_backup(data: object) -> list[HistoryRecord]:
    """Android バックアップ JSON（パース済みオブジェクト）から main の履歴を抽出する。

    評価額 = japaneseStockValue + usStockValue + investmentTrustValue、
    配当 = totalHaitouKin、優待 = totalShareholderBenefit にマッピングする。
    簿価・損益は JSON に存在しないため None（推移グラフでは線が引かれない）。
    全フィールドが 0 のレコード（未入力日）は取り込まない。
    """
    if not isinstance(data, list):
        return []
    records: list[HistoryRecord] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        if entry.get("portfolioId") != _ANDROID_MAIN_PORTFOLIO:
            continue
        iso = _android_date_to_iso(str(entry.get("recordDate", "")))
        if iso is None:
            continue
        jp = safe_float(entry.get("japaneseStockValue")) or 0.0
        us = safe_float(entry.get("usStockValue")) or 0.0
        trust = safe_float(entry.get("investmentTrustValue")) or 0.0
        market = jp + us + trust
        dividend = safe_float(entry.get("totalHaitouKin")) or 0.0
        benefit = safe_float(entry.get("totalShareholderBenefit")) or 0.0
        if market <= 0 and dividend <= 0 and benefit <= 0:
            continue  # 未入力日（全 0）はスキップ
        records.append(
            HistoryRecord(
                record_date=iso,
                total_market_value_jpy=market if market > 0 else None,
                total_book_value_jpy=None,  # JSON に簿価は無い
                total_dividend_jpy=dividend if dividend > 0 else None,
                total_benefit_jpy=benefit if benefit > 0 else None,
                total_profit_jpy=None,  # JSON に損益は無い
                total_withdrawal_income_jpy=None,  # JSON に取崩収入は無い
                source="android_backup",
            )
        )
    # 同一日付が複数あれば後勝ち（通常は無いが念のため）＋昇順
    return merge_records([], records, prefer="incoming")


def load_android_backup(json_path: str | Path) -> list[HistoryRecord]:
    """Android バックアップ JSON ファイルを読み込んで履歴レコードに変換する。"""
    path = Path(json_path)
    with path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    return parse_android_backup(data)


def combine_income(
    rec: dict, with_benefit: bool, with_withdrawal: bool
) -> float | None:
    """配当推移グラフの 1 点の表示値を、優待・取崩の選択に応じて合成して返す。

    with_withdrawal=False: 取崩無しの通常配当（total_dividend_jpy）を基準。
    with_withdrawal=True : 取崩込み収入（total_withdrawal_income_jpy）を基準。
    with_benefit=True    : 上記へ優待（total_benefit_jpy）を加算。
    基準値が None（取崩込みだが取崩収入未記録＝過去の取込分など）の場合は None を
    返し、折れ線を分断する。優待が None の日は 0 として扱う。
    """
    key = "total_withdrawal_income_jpy" if with_withdrawal else "total_dividend_jpy"
    base = safe_float(rec.get(key))
    if base is None:
        return None
    if with_benefit:
        base += safe_float(rec.get("total_benefit_jpy")) or 0.0
    return base
