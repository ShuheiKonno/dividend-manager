"""資産・配当の推移履歴ロジック（UI 非依存・純 Python）。

- 日次スナップショット（HistoryRecord）の生成・マージ
- 履歴 CSV の取込は io_csv.read_history_csv が担当（エクスポートと同一フォーマット）。

GUI からは io_csv.load_history / save_history を介して使う。表示層（gui/widgets）
には依存しないため、テスト・ヘッドレスで実行できる。
"""

from __future__ import annotations

from typing import Iterable

from .calc import compute_rows, has_benefit, scope_holdings
from .models import (
    ASSET_CLASSES,
    BENEFIT_HOLDER_METRICS,
    CLASS_HISTORY_METRICS,
    Holding,
    HistoryRecord,
    MarketData,
    benefit_holder_history_key,
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

    ただし資産別の列を記録した日（_recorded_by_class）の基準値 None は「列が無い」
    ではなく「配当（取崩）が不明」。優待を含めるときは KPI の「合計」
    （calc._numerator: 片方だけ既知ならその値）と同じく既知の優待だけで描く——
    None を返すと、合計カードに出ている優待の額が推移からだけ消える。
    """
    key = INCOME_BASE_KEYS.get(withdrawal_mode, "total_dividend_jpy")
    base = safe_float(rec.get(key))
    benefit = safe_float(rec.get("total_benefit_jpy")) if with_benefit else None
    if base is None:
        if benefit is not None and _recorded_by_class(rec):
            return benefit
        return None
    return base + (benefit or 0.0)


def _recorded_by_class(rec: dict) -> bool:
    """資産別の列を記録した日か（全クラスの評価額の列がある＝記録日の印）。

    記録日の評価額は必ず数値で入る（欠損があると記録自体が止まる）ので、この列の
    有無で「列を足す前の日」と「記録したが値が不明」を区別できる。取崩の列も
    資産別の列と同じ日から揃って記録しているので、収入モードに関係なく使える。
    """
    return all(
        safe_float(rec.get(class_history_key(cls, "market_value"))) is not None
        for cls in ASSET_CLASSES
    )


def class_breakdown(
    holdings: Iterable[Holding],
    market_map: dict[str, MarketData],
    fx_rate: float | None,
) -> dict[str, float | None]:
    """当日スナップショット用の資産クラス別内訳（キーは CLASS_HISTORY_FIELDS）。

    合計列と同じく表示モードに依存させない（税引前・有効銘柄のみ）。株式2クラスは
    取崩の影響を受けないので取崩なしの1回、投信は取崩なし・定率・定額の3回計算する。

    「寄与ゼロ」と「不明」を区別して残す（同じ対象の KPI と食い違わせない）:
    - そのクラスの銘柄が無い → 全指標 0.0
    - 優待 → 0.0（Summary の None は「優待なし」の意味で、不明ではない）
    - 配当・取崩収入が不明（未取得の配当だけ・取崩率未設定の投信だけ等）→ None
      （KPI はこのとき N/A。0 で残すと推移にだけ誤ったゼロが描かれる）
    評価額は呼び出し側が欠損なしを確かめてから呼ぶ（_record_history_snapshot が
    先に弾く）ので、記録した日には必ず数値が入る——scoped_history_value はこれを
    「その日は資産別を記録した」印に使う。
    """
    holdings = list(holdings)
    result: dict[str, float | None] = {}
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
            value = values[metric]
            if not subset or metric == "benefit":
                value = value or 0.0
            result[class_history_key(cls, metric)] = value
    return result


def benefit_holder_breakdown(
    holdings: Iterable[Holding],
    market_map: dict[str, MarketData],
    fx_rate: float | None,
) -> dict[str, float | None]:
    """当日スナップショット用の、資産クラスごとの優待銘柄だけの評価額・簿価
    （キーは BENEFIT_HOLDER_HISTORY_FIELDS）。

    資産を絞って優待を選んだとき、選んでいない資産の優待銘柄も一覧・KPI の
    評価額・簿価に入る。推移をその定義で組み直すための列（scope_history_record）。
    優待の判定は scope_holdings と同じ calc.has_benefit（記録日時点の優待額）。
    優待銘柄が無いクラスは 0.0。
    """
    holdings = list(holdings)
    result: dict[str, float | None] = {}
    for cls in ASSET_CLASSES:
        subset = [h for _, h in scope_holdings(holdings, (cls,)) if has_benefit(h)]
        _, base = compute_rows(subset, market_map, fx_rate, "gross", None)
        values = {
            "market_value": base.total_market_value_jpy,
            "book_value": base.total_book_value_jpy,
        }
        for metric in BENEFIT_HOLDER_METRICS:
            value = values[metric]
            result[benefit_holder_history_key(cls, metric)] = (
                0.0 if not subset else value
            )
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
    """選んだ資産クラスの列の和（推移グラフの1点）。

    - 選んだクラスのどれかが**未記録**（評価額の列が空＝資産別の列を足す前の日）
      なら None（線を切る。合計から按分しない）。
    - 記録済みの日は、**既知の値だけ**を足す。選んだクラスがすべて不明なら None。
      Summary の配当（既知の銘柄だけの和・1件も無ければ None）と同じ数え方なので、
      同じ対象の KPI と推移の右端が食い違わない。
    取崩の列を持たない株式クラスは、取崩込み収入＝配当として足す（取崩は投信に
    しか付かない）。
    """
    known: list[float] = []
    for cls in ASSET_CLASSES:
        if cls not in scope:
            continue
        if safe_float(rec.get(class_history_key(cls, "market_value"))) is None:
            return None
        own = metric if metric in CLASS_HISTORY_METRICS[cls] else "dividend"
        value = safe_float(rec.get(class_history_key(cls, own)))
        if value is not None:
            known.append(value)
    return sum(known) if known else None


def scope_history_record(rec: dict, scope, with_benefit: bool = False) -> dict:
    """推移グラフ用の1点を、表示対象の資産クラスで絞った値へ差し替えて返す。

    with_benefit=True（ツールバーの「優待」）で資産を絞っているときは、画面の
    KPI と同じ定義で組み直す（_scope_with_benefit）。

    全クラスを選んでいるときは合計列をそのまま使う（クラス別の列は途中から
    足したので、和で描くと記録前の期間の線が消える）。一部だけのときは合計列の
    キー（total_<指標>_jpy）をクラス別の和で上書きする——グラフ側（系列定義・
    combine_income・取崩の記録開始日の注記）は同じキーを読むだけで済む。
    過去分は合計しか残っていないので推定で埋めない（記録前は None で線が切れる）。
    """
    if set(scope) >= set(ASSET_CLASSES):
        return rec
    if with_benefit:
        return _without_empty_selection(_scope_with_benefit(rec, scope))
    out = dict(rec)
    for metric in _SCOPED_METRICS:
        out[f"total_{metric}_jpy"] = scoped_history_value(rec, metric, scope)
    market = out["total_market_value_jpy"]
    book = out["total_book_value_jpy"]
    out["total_profit_jpy"] = (
        market - book if market is not None and book is not None else None
    )
    return _without_empty_selection(out)


def _without_empty_selection(out: dict) -> dict:
    """選んだ対象に銘柄が1つも無い日（評価額・簿価とも 0）は全指標を None にする。

    銘柄の無いクラスの列は 0.0 で記録している（寄与ゼロ）ので、和を取ると 0 の線が
    描かれる。一方、同じ選択の KPI は表示行が 0 件で N/A（compute_rows の
    all-or-nothing）。0 のまま出すと推移にだけ「評価額 0 円」が描かれて食い違う。
    評価額と簿価がともに 0 の銘柄は現実には無い（保有していれば簿価か時価が立つ）
    ので、これを「銘柄が無い」の印に使う。
    """
    if out.get("total_market_value_jpy") == 0 and out.get("total_book_value_jpy") == 0:
        out = dict(out)
        for metric in _SCOPED_METRICS:
            out[f"total_{metric}_jpy"] = None
        out["total_profit_jpy"] = None
    return out


def _scope_with_benefit(rec: dict, scope) -> dict:
    """資産を絞って優待を選んだときの1点（画面の KPI と同じ定義）。

    - 評価額・簿価 = 選んだクラスの列 ＋ 選んでいないクラスの優待銘柄の列
      （どれか1つでも空＝記録前の日なら、全指標 None で線を切る）
    - 配当・取崩収入 = 選んだクラスだけ（選んでいない資産の優待銘柄の配当は
      数えない）。資産を1つも選んでいない（優待のみ）なら 0.0——None にすると
      combine_income が優待の線まで消す。
    - 優待 = 全クラスの和
    """
    selected = [cls for cls in ASSET_CLASSES if cls in set(scope)]
    others = [cls for cls in ASSET_CLASSES if cls not in set(scope)]

    def _value(metric: str) -> float | None:
        keys = [class_history_key(cls, metric) for cls in selected] + [
            benefit_holder_history_key(cls, metric) for cls in others
        ]
        values = [safe_float(rec.get(key)) for key in keys]
        if any(v is None for v in values):
            return None
        return sum(values)

    out = dict(rec)
    market = _value("market_value")
    book = _value("book_value")
    if market is None or book is None:
        for metric in _SCOPED_METRICS:
            out[f"total_{metric}_jpy"] = None
        out["total_profit_jpy"] = None
        return out
    out["total_market_value_jpy"] = market
    out["total_book_value_jpy"] = book
    for metric in ("dividend", "withdrawal_income", "fixed_withdrawal_income"):
        out[f"total_{metric}_jpy"] = (
            scoped_history_value(rec, metric, selected) if selected else 0.0
        )
    out["total_benefit_jpy"] = scoped_history_value(rec, "benefit", ASSET_CLASSES)
    out["total_profit_jpy"] = market - book
    return out


def scoped_history_start(
    records: Iterable[dict], scope, with_benefit: bool = False
) -> str | None:
    """資産を絞ったとき、線を引ける最初の日（記録が無ければ None）。"""
    for rec in records:
        scoped = scope_history_record(rec, scope, with_benefit)
        if safe_float(scoped.get("total_market_value_jpy")) is not None:
            return rec.get("date")
    return None
