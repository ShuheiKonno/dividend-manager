"""資産・配当の推移履歴ロジック（UI 非依存・純 Python）。

- 日次スナップショット（HistoryRecord）の生成・マージ
- 履歴 CSV の取込は io_csv.read_history_csv が担当（エクスポートと同一フォーマット）。

GUI からは io_csv.load_history / save_history を介して使う。表示層（gui/widgets）
には依存しないため、テスト・ヘッドレスで実行できる。
"""

from __future__ import annotations

from typing import Iterable

from .calc import compute_rows, scope_holdings
from .models import (
    ASSET_CLASSES,
    CLASS_HISTORY_METRICS,
    Holding,
    HistoryRecord,
    MarketData,
    class_history_key,
)
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


def class_breakdown(
    holdings: Iterable[Holding],
    market_map: dict[str, MarketData],
    fx_rate: float | None,
) -> dict[str, float]:
    """当日スナップショット用の資産クラス別内訳（キーは CLASS_HISTORY_FIELDS）。

    合計列と同じく表示モードに依存させない（税引前・有効銘柄のみ）。株式2クラスは
    取崩の影響を受けないので取崩なしの1回、投信は取崩なし・定率・定額の3回計算する。

    値は 0.0 で埋める（そのクラスの銘柄が無い・配当が1件も無い＝寄与ゼロ）。None を
    残すと「その日は記録していない」と区別できず、該当クラスを含む選択の線が
    記録後も切れたままになる。呼び出し側は時価の欠損が無いことを確かめてから
    呼ぶ（_record_history_snapshot が先に弾く）ので、0 埋めで合計と食い違わない。
    """
    holdings = list(holdings)
    result: dict[str, float] = {}
    for cls in ASSET_CLASSES:
        subset = [h for _, h in scope_holdings(holdings, (cls,))]
        _, base = compute_rows(subset, market_map, fx_rate, "gross", None)
        values = {
            "market_value": base.total_market_value_jpy,
            "book_value": base.total_book_value_jpy,
            "dividend": base.total_dividend_jpy,
            "benefit": base.total_benefit_jpy,
        }
        metrics = CLASS_HISTORY_METRICS[cls]
        if "withdrawal_income" in metrics:
            _, rate = compute_rows(subset, market_map, fx_rate, "gross", "rate")
            values["withdrawal_income"] = rate.total_dividend_jpy
        if "fixed_withdrawal_income" in metrics:
            _, fixed = compute_rows(subset, market_map, fx_rate, "gross", "fixed")
            values["fixed_withdrawal_income"] = fixed.total_dividend_jpy
        for metric in metrics:
            result[class_history_key(cls, metric)] = values[metric] or 0.0
    return result


# 合計列 total_<指標>_jpy のうち、資産クラス別の列から組み直せる指標。
_SCOPED_METRICS = (
    "market_value",
    "book_value",
    "dividend",
    "benefit",
    "withdrawal_income",
    "fixed_withdrawal_income",
)


def scoped_history_value(rec: dict, metric: str, scope) -> float | None:
    """選んだ資産クラスの列の和。1つでも未記録（空欄）なら None（線を切る）。

    取崩の列を持たない株式クラスは、取崩込み収入＝配当として足す（取崩は投信に
    しか付かない）。
    """
    total = 0.0
    for cls in ASSET_CLASSES:
        if cls not in scope:
            continue
        own = metric if metric in CLASS_HISTORY_METRICS[cls] else "dividend"
        value = safe_float(rec.get(class_history_key(cls, own)))
        if value is None:
            return None
        total += value
    return total


def scope_history_record(rec: dict, scope) -> dict:
    """推移グラフ用の1点を、表示対象の資産クラスで絞った値へ差し替えて返す。

    全クラスを選んでいるときは合計列をそのまま使う（クラス別の列は途中から
    足したので、和で描くと記録前の期間の線が消える）。一部だけのときは合計列の
    キー（total_<指標>_jpy）をクラス別の和で上書きする——グラフ側（系列定義・
    combine_income・取崩の記録開始日の注記）は同じキーを読むだけで済む。
    過去分は合計しか残っていないので推定で埋めない（記録前は None で線が切れる）。
    """
    if set(scope) >= set(ASSET_CLASSES):
        return rec
    out = dict(rec)
    for metric in _SCOPED_METRICS:
        out[f"total_{metric}_jpy"] = scoped_history_value(rec, metric, scope)
    market = out["total_market_value_jpy"]
    book = out["total_book_value_jpy"]
    out["total_profit_jpy"] = (
        market - book if market is not None and book is not None else None
    )
    return out


def scoped_history_start(records: Iterable[dict], scope) -> str | None:
    """一部の資産クラスに絞ったとき、線を引ける最初の日（記録が無ければ None）。"""
    for rec in records:
        if scoped_history_value(rec, "market_value", scope) is not None:
            return rec.get("date")
    return None
