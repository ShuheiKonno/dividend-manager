from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .utils import (
    format_month_ratios,
    format_months,
    parse_month_ratios,
    parse_months,
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
HISTORY_FIELDS = [
    "record_date",
    "total_market_value_jpy",
    "total_book_value_jpy",
    "total_dividend_jpy",
    "total_benefit_jpy",
    "total_profit_jpy",
    "total_withdrawal_income_jpy",
    "total_fixed_withdrawal_income_jpy",
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
