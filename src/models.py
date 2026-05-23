from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .utils import safe_float, safe_str

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
    "active",
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
]

FX_FIELDS = [
    "timestamp",
    "pair",
    "rate",
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
    active: bool

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
            active=str(row.get("active", "true")).strip().lower() != "false",
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
            "annual_div_ps_gross_override": self.annual_div_ps_gross_override or "",
            "annual_div_ps_net_override": self.annual_div_ps_net_override or "",
            "div_forecast_as_of_override": self.div_forecast_as_of_override or "",
            "annual_benefit_jpy": self.annual_benefit_jpy,
            "withdrawal_fixed_jpy": self.withdrawal_fixed_jpy or "",
            "withdrawal_rate": self.withdrawal_rate or "",
            "active": "true" if self.active else "false",
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
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "currency": self.currency,
            "last_price": self.last_price or "",
            "price_as_of": self.price_as_of or "",
            "annual_div_ps_gross": self.annual_div_ps_gross or "",
            "annual_div_ps_net": self.annual_div_ps_net or "",
            "div_forecast_as_of": self.div_forecast_as_of or "",
            "source": self.source or "",
            "fetched_at": self.fetched_at or "",
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
