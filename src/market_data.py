from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import yfinance as yf

from .models import Holding, MarketData, FXRate
from .utils import now_jst_iso, today_jst_str


@dataclass
class MarketFetchResult:
    rows: list[MarketData]
    fetched_at: str | None
    source: str | None
    errors: list[str]
    name_updates: dict[str, str]


def _yfinance_company_name(symbol: str) -> str | None:
    try:
        ticker = yf.Ticker(symbol)
        info = {}
        try:
            info = ticker.info or {}
        except Exception:
            info = {}
        for key in ("longName", "shortName", "name"):
            value = info.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    except Exception:
        return None
    return None


def _parse_yfinance_dividends(series) -> tuple[float | None, str | None]:
    if series is None or len(series) == 0:
        return None, None
    cutoff = date.today() - timedelta(days=370)
    total = 0.0
    latest_date = None
    for idx, value in series.items():
        d = idx.date() if hasattr(idx, "date") else None
        if d is None or d < cutoff:
            continue
        try:
            val = float(value)
        except (TypeError, ValueError):
            continue
        total += val
        if latest_date is None or d.isoformat() > latest_date:
            latest_date = d.isoformat()
    return (total if total > 0 else None), latest_date


def _yfinance_symbol(holding: Holding) -> str:
    symbol = holding.symbol
    if holding.currency == "JPY" and "." not in symbol:
        return f"{symbol}.T"
    return symbol


def fetch_market_data(
    holdings: list[Holding],
    existing: dict[str, MarketData],
    force: bool = False,
) -> MarketFetchResult:
    fetched_at = now_jst_iso()
    source = "yfinance"
    errors: list[str] = []
    results: list[MarketData] = []
    name_updates: dict[str, str] = {}

    for holding in holdings:
        if not holding.active:
            continue
        cached = existing.get(holding.symbol)
        if not force and cached and cached.fetched_at and cached.fetched_at.startswith(today_jst_str()):
            results.append(cached)
            continue

        yf_symbol = _yfinance_symbol(holding)
        price, price_date = None, None
        div_ps, div_date = None, None

        try:
            ticker = yf.Ticker(yf_symbol)
            history = ticker.history(period="10d")
            if not history.empty:
                price = float(history["Close"].iloc[-1])
                price_date = history.index[-1].date().isoformat()
        except Exception as exc:
            errors.append(f"株価取得失敗: {holding.symbol} ({exc})")

        try:
            series = ticker.dividends
            div_ps, div_date = _parse_yfinance_dividends(series)
        except Exception as exc:
            errors.append(f"配当取得失敗: {holding.symbol} ({exc})")

        if not holding.name:
            name = _yfinance_company_name(yf_symbol)
            if name:
                name_updates[holding.symbol] = name

        results.append(
            MarketData(
                symbol=holding.symbol,
                currency=holding.currency,
                last_price=price,
                price_as_of=price_date,
                annual_div_ps_gross=div_ps,
                annual_div_ps_net=None,
                div_forecast_as_of=div_date,
                source=source,
                fetched_at=fetched_at,
            )
        )

    return MarketFetchResult(
        rows=results,
        fetched_at=fetched_at,
        source=source,
        errors=errors,
        name_updates=name_updates,
    )


def fetch_usdjpy() -> FXRate | None:
    try:
        ticker = yf.Ticker("USDJPY=X")
        history = ticker.history(period="10d")
        if history.empty:
            return None
        rate = float(history["Close"].iloc[-1])
        return FXRate(timestamp=now_jst_iso(), pair="USDJPY", rate=rate, source="yfinance")
    except Exception:
        return None
