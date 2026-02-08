from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import Holding, MarketData
from .utils import safe_float


@dataclass
class Summary:
    total_book_value_jpy: float | None
    total_market_value_jpy: float | None
    total_dividend_jpy: float | None
    total_benefit_jpy: float | None
    total_numerator_jpy: float | None
    yield_on_cost: float | None
    yield_on_value: float | None
    total_profit_jpy: float | None
    profit_rate: float | None


def _pick_div_ps(holding: Holding, market: MarketData | None, mode: str) -> float | None:
    if mode == "net":
        override = holding.annual_div_ps_net_override
        market_value = market.annual_div_ps_net if market else None
    else:
        override = holding.annual_div_ps_gross_override
        market_value = market.annual_div_ps_gross if market else None
    return override if override is not None else market_value


def _to_jpy(value: float | None, currency: str, fx_rate: float | None) -> float | None:
    if value is None:
        return None
    if currency == "JPY":
        return value
    if currency == "USD" and fx_rate:
        return value * fx_rate
    return None


def compute_rows(
    holdings: Iterable[Holding],
    market_map: dict[str, MarketData],
    fx_rate: float | None,
    mode: str,
) -> tuple[list[dict[str, float | str | None]], Summary]:
    rows: list[dict[str, float | str | None]] = []
    sum_book = 0.0
    sum_market = 0.0
    sum_dividend = 0.0
    sum_benefit = 0.0
    sum_profit = 0.0

    for holding in holdings:
        if not holding.active:
            continue
        market = market_map.get(holding.symbol)
        div_ps = _pick_div_ps(holding, market, mode)
        annual_dividend = holding.shares * div_ps if div_ps is not None else None
        annual_dividend_jpy = _to_jpy(annual_dividend, holding.currency, fx_rate)

        book_value_ccy = holding.shares * holding.avg_cost
        book_value_jpy = _to_jpy(book_value_ccy, holding.currency, fx_rate)

        last_price = market.last_price if market else None
        market_value_ccy = holding.shares * last_price if last_price is not None else None
        market_value_jpy = _to_jpy(market_value_ccy, holding.currency, fx_rate)

        benefit_jpy = holding.annual_benefit_jpy
        numerator_jpy = None
        if annual_dividend_jpy is not None:
            numerator_jpy = annual_dividend_jpy + benefit_jpy
        elif benefit_jpy:
            numerator_jpy = benefit_jpy

        yield_on_cost = numerator_jpy / book_value_jpy if numerator_jpy is not None and book_value_jpy else None
        yield_on_value = numerator_jpy / market_value_jpy if numerator_jpy is not None and market_value_jpy else None
        profit_jpy = None
        profit_rate = None
        if market_value_jpy is not None and book_value_jpy is not None:
            profit_jpy = market_value_jpy - book_value_jpy
            if book_value_jpy != 0:
                profit_rate = profit_jpy / book_value_jpy

        rows.append(
            {
                "symbol": holding.symbol,
                "name": holding.name,
                "shares": holding.shares,
                "currency": holding.currency,
                "annual_div_ps": div_ps,
                "annual_dividend_jpy": annual_dividend_jpy,
                "annual_benefit_jpy": benefit_jpy,
                "book_value_jpy": book_value_jpy,
                "market_value_jpy": market_value_jpy,
                "yield_on_cost": yield_on_cost,
                "yield_on_value": yield_on_value,
                "profit_jpy": profit_jpy,
                "profit_rate": profit_rate,
                "div_forecast_as_of": holding.div_forecast_as_of_override or (market.div_forecast_as_of if market else None),
            }
        )

        if book_value_jpy is not None:
            sum_book += book_value_jpy
        if market_value_jpy is not None:
            sum_market += market_value_jpy
        if annual_dividend_jpy is not None:
            sum_dividend += annual_dividend_jpy
        sum_benefit += benefit_jpy
        if profit_jpy is not None:
            sum_profit += profit_jpy

    total_book = sum_book if sum_book > 0 else None
    total_market = sum_market if sum_market > 0 else None
    total_dividend = sum_dividend if sum_dividend > 0 else None
    total_benefit = sum_benefit if sum_benefit > 0 else None
    total_numerator = None
    if total_dividend is not None or total_benefit is not None:
        total_numerator = (total_dividend or 0.0) + (total_benefit or 0.0)
    yield_on_cost = total_numerator / total_book if total_numerator is not None and total_book else None
    yield_on_value = total_numerator / total_market if total_numerator is not None and total_market else None
    total_profit = sum_profit if sum_profit != 0 else None
    profit_rate = total_profit / total_book if total_profit is not None and total_book else None

    summary = Summary(
        total_book_value_jpy=total_book,
        total_market_value_jpy=total_market,
        total_dividend_jpy=total_dividend,
        total_benefit_jpy=total_benefit,
        total_numerator_jpy=total_numerator,
        yield_on_cost=yield_on_cost,
        yield_on_value=yield_on_value,
        total_profit_jpy=total_profit,
        profit_rate=profit_rate,
    )
    return rows, summary


def latest_fx_rate(rows: Iterable[dict[str, str]]) -> float | None:
    latest = None
    for row in rows:
        rate = safe_float(row.get("rate"))
        if rate is None:
            continue
        latest = rate
    return latest
