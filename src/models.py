from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .utils import (
    format_month_ratios,
    format_months,
    format_splits,
    normalize_iso_date,
    parse_month_ratios,
    parse_months,
    parse_splits,
    safe_float,
    safe_str,
)


def _csv_num(value: float | None) -> float | str:
    """CSV へ書き出す数値。None のみ空文字にし、0.0 はそのまま保持する。"""
    return value if value is not None else ""


HOLDINGS_FIELDS = [
    "symbol",
    "name",
    "asset_type",
    "currency",
    "shares",
    "avg_cost",
    "account",
    "annual_div_ps_gross_override",
    "annual_div_ps_net_override",
    "div_forecast_as_of_override",
    "annual_benefit_jpy",
    "withdrawal_fixed_jpy",
    "withdrawal_rate",
    "tax_rate",
    "active",
    "dividend_months",
    "benefit_months",
    "benefit_detail",
    "split_applied_through",
]

MARKET_DATA_FIELDS = [
    "symbol",
    "currency",
    "last_price",
    "price_as_of",
    "annual_div_ps_gross",
    "annual_div_ps_net",
    "div_forecast_as_of",
    "source",
    "fetched_at",
    "div_ex_month_ratios",
    "recent_splits",
    "price_fetched_at",
]

FX_FIELDS = [
    "timestamp",
    "pair",
    "rate",
    "source",
]

# 資産・配当の日次スナップショット履歴（推移グラフ用）。
# record_date は ISO 日付 "YYYY-MM-DD"（1日1レコード）。
# total_dividend_jpy は「取崩無しの通常配当」（記録時の表示モードに依存させない）。
# total_withdrawal_income_jpy は「投信を定率取崩した収入」（fund を時価×rate に置換）。
# total_fixed_withdrawal_income_jpy は「投信を定額取崩した収入」（fund を定額に置換）。
# 取崩の 2 列は収入モード（fixed / rate）と 1 対 1 で対応する。1 列だけにして
# 両モードで使い回すと、定額を選んでいるのに定率の線が出る（値も凡例も尤もらしい
# まま間違う）。列を足したのは後からなので、それ以前の日は空＝線を引かない。
# Android バックアップ取込分は簿価・損益・取崩収入を持たない（None）。
# 資産クラス（calc.asset_class の戻り値）。並びは UI・出力・履歴列の順序。
# calc が models を import する側なので、履歴列を組むためにここへ置く
# （calc.ASSET_CLASSES はこれの再エクスポート）。
ASSET_CLASSES = ("jp_stock", "us_stock", "fund")

# 履歴へ記録する資産クラス別の指標。合計列 total_<指標>_jpy と同じ指標名を使う
# （history.scoped_history_value が名前で対応づける）。取崩は投信にしか付かない
# ——株式の「取崩込み収入」は配当と同値なので、株式2クラスは列を持たない。
CLASS_HISTORY_METRICS: dict[str, tuple[str, ...]] = {
    "jp_stock": ("market_value", "book_value", "dividend", "benefit"),
    "us_stock": ("market_value", "book_value", "dividend", "benefit"),
    "fund": (
        "market_value",
        "book_value",
        "dividend",
        "benefit",
        "withdrawal_income",
        "fixed_withdrawal_income",
    ),
}


def class_history_key(asset_class: str, metric: str) -> str:
    """資産クラス別の履歴列名（例: jp_stock_market_value_jpy）。"""
    return f"{asset_class}_{metric}_jpy"


# 列を後から足したので、それ以前に書いた CSV には無い（→ None）。
CLASS_HISTORY_FIELDS = [
    class_history_key(cls, metric)
    for cls in ASSET_CLASSES
    for metric in CLASS_HISTORY_METRICS[cls]
]

# 資産クラスごとの「優待のある銘柄」だけの評価額・簿価。ツールバーで資産を絞り
# 優待を選んだとき、選んでいない資産の優待銘柄も一覧・KPI に入る（配当は数えない）
# ので、推移の評価額・簿価をその定義で組み直すのに使う（history.scope_history_record）。
# 合計列の内訳ではない（優待銘柄は上のクラス別の列にも入っている）——Σ を取る
# 突合へ混ぜないこと。列を後から足したので、それ以前の日は空（線を切る）。
BENEFIT_HOLDER_METRICS = ("market_value", "book_value")


def benefit_holder_history_key(asset_class: str, metric: str) -> str:
    """資産クラス別・優待銘柄だけの履歴列名（例: jp_stock_benefit_holder_market_value_jpy）。"""
    return f"{asset_class}_benefit_holder_{metric}_jpy"


BENEFIT_HOLDER_HISTORY_FIELDS = [
    benefit_holder_history_key(cls, metric)
    for cls in ASSET_CLASSES
    for metric in BENEFIT_HOLDER_METRICS
]
# HistoryRecord.by_class が持つ列（クラス別の内訳＋優待銘柄の評価額・簿価）。
BY_CLASS_HISTORY_FIELDS = [*CLASS_HISTORY_FIELDS, *BENEFIT_HOLDER_HISTORY_FIELDS]

HISTORY_FIELDS = [
    "record_date",
    "total_market_value_jpy",
    "total_book_value_jpy",
    "total_dividend_jpy",
    "total_benefit_jpy",
    "total_profit_jpy",
    "total_withdrawal_income_jpy",
    "total_fixed_withdrawal_income_jpy",
    *CLASS_HISTORY_FIELDS,
    *BENEFIT_HOLDER_HISTORY_FIELDS,
    "source",
]


@dataclass
class Holding:
    symbol: str
    name: str
    asset_type: str
    currency: str
    shares: float
    avg_cost: float
    account: str
    annual_div_ps_gross_override: float | None
    annual_div_ps_net_override: float | None
    div_forecast_as_of_override: str | None
    annual_benefit_jpy: float
    withdrawal_fixed_jpy: float | None
    withdrawal_rate: float | None
    tax_rate: float | None
    active: bool
    # 配当の支払月（1〜12・手動指定）。空なら market_data の権利落ち実績から自動判定する。
    # 源泉税率と同じく「空欄＝自動に従う」が既定運用で、自動値を書き写さない。
    dividend_months: list[int] = field(default_factory=list)
    # 優待の受取月（1〜12）。優待には自動判定の材料が無いため常に手動。
    benefit_months: list[int] = field(default_factory=list)
    # 優待内容の自由記述（例「クオカード1,000円分」）。金額（annual_benefit_jpy）を
    # 見ただけでは何の優待か分からないため、表示専用のメモとして持つ。
    # 未設定は None ではなく "" で表す（表示層で毎回 None を潰さずに済む）。
    benefit_detail: str = ""
    # 「この日（含む）までの株式分割は株数・取得単価へ反映済み」を表す ISO 日付。
    # 行ごとに持つ（同じ銘柄を複数口座で持てるので銘柄コード単位では足りない）。
    # 空は「記録なし」＝直近の分割だけを未反映候補として旗を立てる（splits.pending_splits）。
    # 自動で書き換えず、補正ボタン・「反映済みにする」・新規登録でだけ書く
    # （証券会社の反映時期とアプリの更新時期はずれるため、自動適用は二重適用になる）。
    split_applied_through: str = ""

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "Holding":
        return cls(
            symbol=str(row.get("symbol", "")).strip(),
            name=str(row.get("name", "")).strip(),
            asset_type=str(row.get("asset_type", "stock")).strip() or "stock",
            currency=str(row.get("currency", "JPY")).strip() or "JPY",
            shares=safe_float(row.get("shares")) or 0.0,
            avg_cost=safe_float(row.get("avg_cost")) or 0.0,
            account=str(row.get("account", "")).strip(),
            annual_div_ps_gross_override=safe_float(
                row.get("annual_div_ps_gross_override")
            ),
            annual_div_ps_net_override=safe_float(
                row.get("annual_div_ps_net_override")
            ),
            div_forecast_as_of_override=safe_str(
                row.get("div_forecast_as_of_override")
            ),
            annual_benefit_jpy=safe_float(row.get("annual_benefit_jpy")) or 0.0,
            withdrawal_fixed_jpy=safe_float(row.get("withdrawal_fixed_jpy")),
            withdrawal_rate=safe_float(row.get("withdrawal_rate")),
            tax_rate=safe_float(row.get("tax_rate")),
            active=str(row.get("active", "true")).strip().lower() != "false",
            dividend_months=parse_months(row.get("dividend_months")),
            benefit_months=parse_months(row.get("benefit_months")),
            # safe_str は未設定を None にするが、この項目は "" を空とする
            # （表示層・CSV の双方で None 判定を増やさないため）。
            benefit_detail=str(row.get("benefit_detail") or "").strip(),
            # 列を後から足したので旧 CSV には無い（→ ""）。解析不能な値も "" へ落とす。
            split_applied_through=normalize_iso_date(row.get("split_applied_through"))
            or "",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "asset_type": self.asset_type,
            "currency": self.currency,
            "shares": self.shares,
            "avg_cost": self.avg_cost,
            "account": self.account,
            # 0.0 を空文字に潰さないよう None 判定で書き出す（0 の明示上書きを保持）。
            "annual_div_ps_gross_override": _csv_num(self.annual_div_ps_gross_override),
            "annual_div_ps_net_override": _csv_num(self.annual_div_ps_net_override),
            "div_forecast_as_of_override": self.div_forecast_as_of_override or "",
            "annual_benefit_jpy": self.annual_benefit_jpy,
            "withdrawal_fixed_jpy": _csv_num(self.withdrawal_fixed_jpy),
            "withdrawal_rate": _csv_num(self.withdrawal_rate),
            "tax_rate": _csv_num(self.tax_rate),
            "active": "true" if self.active else "false",
            "dividend_months": format_months(self.dividend_months),
            "benefit_months": format_months(self.benefit_months),
            "benefit_detail": self.benefit_detail,
            "split_applied_through": self.split_applied_through,
        }


@dataclass
class MarketData:
    symbol: str
    currency: str
    last_price: float | None
    price_as_of: str | None
    annual_div_ps_gross: float | None
    annual_div_ps_net: float | None
    div_forecast_as_of: str | None
    source: str | None
    fetched_at: str | None
    # 直近約1年の各回配当から算出した「権利落ち月」別の構成比（合計 1.0）。
    # 支払月ではない——ここは取得結果のキャッシュなので観測値をそのまま保持し、
    # 支払月への変換は calc.shift_ex_month_to_payment が担う。
    div_ex_month_ratios: dict[int, float] | None = None
    # 直近の株式分割 [(ISO 日付, 比率)]（比率=1株が何株になったか。2:1 分割=2.0）。
    # 取得結果のキャッシュなので観測値をそのまま持ち、未反映かどうかの判定は保有行側
    # （Holding.split_applied_through）と突き合わせる splits.pending_splits が担う。
    recent_splits: list[tuple[str, float]] = field(default_factory=list)
    # 株価の取得成功日時。配当等の部分失敗や、休日の終値日付とは独立して鮮度を判定する。
    price_fetched_at: str | None = None

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "MarketData":
        return cls(
            symbol=str(row.get("symbol", "")).strip(),
            currency=str(row.get("currency", "")).strip(),
            last_price=safe_float(row.get("last_price")),
            price_as_of=safe_str(row.get("price_as_of")),
            annual_div_ps_gross=safe_float(row.get("annual_div_ps_gross")),
            annual_div_ps_net=safe_float(row.get("annual_div_ps_net")),
            div_forecast_as_of=safe_str(row.get("div_forecast_as_of")),
            source=safe_str(row.get("source")),
            fetched_at=safe_str(row.get("fetched_at")),
            div_ex_month_ratios=parse_month_ratios(row.get("div_ex_month_ratios")),
            recent_splits=parse_splits(row.get("recent_splits")),
            price_fetched_at=safe_str(row.get("price_fetched_at")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "currency": self.currency,
            # 0.0 と未取得（None）を区別する。
            "last_price": _csv_num(self.last_price),
            "price_as_of": self.price_as_of or "",
            "annual_div_ps_gross": _csv_num(self.annual_div_ps_gross),
            "annual_div_ps_net": _csv_num(self.annual_div_ps_net),
            "div_forecast_as_of": self.div_forecast_as_of or "",
            "source": self.source or "",
            "fetched_at": self.fetched_at or "",
            "div_ex_month_ratios": format_month_ratios(self.div_ex_month_ratios),
            "recent_splits": format_splits(self.recent_splits),
            "price_fetched_at": self.price_fetched_at or "",
        }


@dataclass
class HistoryRecord:
    """資産・配当の日次スナップショット（推移グラフのデータ点）。"""

    record_date: str  # ISO 日付 "YYYY-MM-DD"
    total_market_value_jpy: float | None
    total_book_value_jpy: float | None
    total_dividend_jpy: float | None
    total_benefit_jpy: float | None
    total_profit_jpy: float | None
    total_withdrawal_income_jpy: float | None  # 定率取崩
    total_fixed_withdrawal_income_jpy: float | None  # 定額取崩
    source: str | None
    # 資産クラス別の内訳（キーは BY_CLASS_HISTORY_FIELDS）。表示対象を絞ったときの
    # 推移グラフ用。評価額の列が空＝記録前の日（過去分は合計しか無いので復元しない）。
    # 記録した日の配当・取崩の空欄は「不明」（history.class_breakdown 参照）。
    by_class: dict[str, float | None] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "HistoryRecord":
        return cls(
            record_date=str(row.get("record_date", "")).strip(),
            total_market_value_jpy=safe_float(row.get("total_market_value_jpy")),
            total_book_value_jpy=safe_float(row.get("total_book_value_jpy")),
            total_dividend_jpy=safe_float(row.get("total_dividend_jpy")),
            total_benefit_jpy=safe_float(row.get("total_benefit_jpy")),
            total_profit_jpy=safe_float(row.get("total_profit_jpy")),
            total_withdrawal_income_jpy=safe_float(
                row.get("total_withdrawal_income_jpy")
            ),
            # 列を後から足したので、それ以前に書いた CSV には無い（→ None）。
            total_fixed_withdrawal_income_jpy=safe_float(
                row.get("total_fixed_withdrawal_income_jpy")
            ),
            source=safe_str(row.get("source")),
            # 空欄（記録前）はキーごと持たない＝ by_class.get(key) が None。
            by_class={
                key: value
                for key in BY_CLASS_HISTORY_FIELDS
                if (value := safe_float(row.get(key))) is not None
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_date": self.record_date,
            # 0.0 を空文字に潰さないよう None 判定で書き出す。
            "total_market_value_jpy": _csv_num(self.total_market_value_jpy),
            "total_book_value_jpy": _csv_num(self.total_book_value_jpy),
            "total_dividend_jpy": _csv_num(self.total_dividend_jpy),
            "total_benefit_jpy": _csv_num(self.total_benefit_jpy),
            "total_profit_jpy": _csv_num(self.total_profit_jpy),
            "total_withdrawal_income_jpy": _csv_num(self.total_withdrawal_income_jpy),
            "total_fixed_withdrawal_income_jpy": _csv_num(
                self.total_fixed_withdrawal_income_jpy
            ),
            **{
                key: _csv_num(self.by_class.get(key))
                for key in BY_CLASS_HISTORY_FIELDS
            },
            "source": self.source or "",
        }


@dataclass
class FXRate:
    timestamp: str
    pair: str
    rate: float
    source: str

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "FXRate":
        return cls(
            timestamp=str(row.get("timestamp", "")).strip(),
            pair=str(row.get("pair", "")).strip(),
            rate=safe_float(row.get("rate")) or 0.0,
            source=str(row.get("source", "")).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "pair": self.pair,
            "rate": self.rate,
            "source": self.source,
        }
