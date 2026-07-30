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
    missing_market_value_count: int


_DEFAULT_TAX_RATE = 0.20315  # 日本の源泉税率（所得税15.315% + 住民税5%）
# 米国株の配当は日米租税条約により米国側で 10% 源泉徴収される。NISA は国内が
# 非課税＝外国税額控除に充てる国内税額が無いため、この 10% は引かれたまま残る。
# よって NISA でも USD 建て（米国上場）銘柄の税引後は 0% ではなくこの率を使う。
_US_WITHHOLDING_RATE = 0.10
# 課税口座（特定・一般）の米国株は、米国 10% を引いた「残額」に国内 20.315% が
# 課される二段構えなので、合成税率は単純加算（30.315%）ではなく
# 1-(1-0.10)(1-0.20315) ≒ 28.2835%。確定申告の外国税額控除で米国分の一部を
# 取り戻せる場合があるが、還付額は他の所得に依存して一意に決まらないため、
# ここでは「取り戻さない場合」＝源泉徴収されたままの額を税引後として表示する。
_US_TAXABLE_COMBINED_RATE = 1.0 - (1.0 - _US_WITHHOLDING_RATE) * (1.0 - _DEFAULT_TAX_RATE)

# 口座名の全角英字を半角へ畳むための変換表（"ＮＩＳＡ" → "NISA"）。
_NISA_TRANS = str.maketrans("ＮＩＳＡｎｉｓａ", "NISANISA")


def is_nisa_account(account: str | None) -> bool:
    """口座名が NISA 口座を指すかを判定する。

    account はモデル・CSV 上は自由文字列のまま（手編集した値も保持する方針）
    なので、選択肢の "NISA（成長）" / "NISA（積立）" / "旧NISA" だけでなく
    "つみたてNISA" や全角 "ＮＩＳＡ"、小文字 "nisa" も拾えるよう、全角を畳んで
    大文字化した部分一致で判定する。
    """
    if not account:
        return False
    return "NISA" in account.translate(_NISA_TRANS).upper()


def default_tax_rate(account: str | None, currency: str) -> float:
    """口座と通貨から税引後計算の既定源泉税率を導く。

    JPY: NISA=0% / 課税口座=20.315%。
    USD: NISA=10%（米国源泉のみ）/ 課税口座=約28.2835%（米国10%＋国内20.315%の
    二段課税。定数のコメント参照）。
    """
    if currency == "USD":
        return (
            _US_WITHHOLDING_RATE
            if is_nisa_account(account)
            else _US_TAXABLE_COMBINED_RATE
        )
    return 0.0 if is_nisa_account(account) else _DEFAULT_TAX_RATE


def resolve_tax_rate(holding: Holding) -> float:
    """銘柄に適用する源泉税率を決める（銘柄ごとの明示指定 > 口座・通貨の自動判定）。"""
    if holding.tax_rate is not None:
        return holding.tax_rate
    return default_tax_rate(holding.account, holding.currency)


def _pick_div_ps(
    holding: Holding, market: MarketData | None, mode: str
) -> float | None:
    if mode == "net":
        override = holding.annual_div_ps_net_override
        market_value = market.annual_div_ps_net if market else None
        result = override if override is not None else market_value
        # override も market_data もない場合は gross から源泉税率で自動計算
        if result is None:
            # 0.0 の明示上書きを尊重するため None 判定でフォールバックする。
            gross = holding.annual_div_ps_gross_override
            if gross is None:
                gross = market.annual_div_ps_gross if market else None
            if gross is not None:
                result = gross * (1.0 - resolve_tax_rate(holding))
    else:
        override = holding.annual_div_ps_gross_override
        market_value = market.annual_div_ps_gross if market else None
        result = override if override is not None else market_value
    # 無分配ファンドは N/A ではなく 0 として扱う
    if result is None and holding.asset_type == "fund":
        return 0.0
    return result


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
    withdrawal_mode: str | None = None,
    include_inactive: bool = False,
) -> tuple[list[dict[str, float | str | None]], Summary]:
    """保有銘柄ごとの表示用 dict 列と、集計済み Summary を返す。

    include_inactive=True のとき active=False の銘柄も rows へ（元の順序のまま）
    含めるが、Summary の集計には一切加算しない（無効銘柄は集計対象外という
    意味論を保つ）。既定の False では従来どおり無効銘柄を完全に読み飛ばすため、
    既存の呼び出し側（legacy_tui / 履歴スナップショット）の結果は変わらない。
    """
    rows: list[dict[str, float | str | None]] = []
    sum_book = 0.0
    sum_market = 0.0
    sum_dividend = 0.0
    sum_benefit = 0.0
    sum_profit = 0.0
    active_count = 0
    known_book_count = 0
    known_market_count = 0
    known_dividend_count = 0
    known_profit_count = 0

    for holding in holdings:
        if not holding.active and not include_inactive:
            continue
        is_active = holding.active
        if is_active:
            active_count += 1
        market = market_map.get(holding.symbol)
        div_ps = _pick_div_ps(holding, market, mode)
        annual_dividend = holding.shares * div_ps if div_ps is not None else None
        annual_dividend_jpy = _to_jpy(annual_dividend, holding.currency, fx_rate)

        book_value_ccy = holding.shares * holding.avg_cost
        book_value_jpy = _to_jpy(book_value_ccy, holding.currency, fx_rate)

        last_price = market.last_price if market else None
        market_value_ccy = (
            holding.shares * last_price if last_price is not None else None
        )
        market_value_jpy = _to_jpy(market_value_ccy, holding.currency, fx_rate)

        is_fund = holding.asset_type == "fund"
        # yfinance で価格が取れないファンドは取得単価をフォールバック価格として使用
        if is_fund and last_price is None:
            market_value_jpy = _to_jpy(
                holding.shares * holding.avg_cost, holding.currency, fx_rate
            )

        # 取り崩しモード：ファンド銘柄の annual_dividend_jpy を取り崩し額で上書き
        if is_fund and withdrawal_mode == "fixed":
            annual_dividend_jpy = holding.withdrawal_fixed_jpy or 0.0
        elif is_fund and withdrawal_mode == "rate":
            if market_value_jpy is not None and holding.withdrawal_rate is not None:
                annual_dividend_jpy = market_value_jpy * holding.withdrawal_rate
            else:
                annual_dividend_jpy = None

        benefit_jpy = holding.annual_benefit_jpy
        numerator_jpy = None
        if annual_dividend_jpy is not None:
            numerator_jpy = annual_dividend_jpy + benefit_jpy
        elif benefit_jpy:
            numerator_jpy = benefit_jpy

        yield_on_cost = (
            numerator_jpy / book_value_jpy
            if numerator_jpy is not None and book_value_jpy
            else None
        )
        yield_on_value = (
            numerator_jpy / market_value_jpy
            if numerator_jpy is not None and market_value_jpy
            else None
        )
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
                # 同一 symbol が複数行（複数口座・取得単価）になり得るため、
                # 表示側が行を判別できるよう口座・資産種別も返す。
                "account": holding.account,
                "asset_type": holding.asset_type,
                "active": is_active,
                "shares": holding.shares,
                "currency": holding.currency,
                "avg_cost": holding.avg_cost,
                "last_price": last_price,
                "annual_div_ps": div_ps,
                "annual_dividend_jpy": annual_dividend_jpy,
                "annual_benefit_jpy": benefit_jpy,
                "book_value_jpy": book_value_jpy,
                "market_value_jpy": market_value_jpy,
                "yield_on_cost": yield_on_cost,
                "yield_on_value": yield_on_value,
                "profit_jpy": profit_jpy,
                "profit_rate": profit_rate,
                "div_forecast_as_of": holding.div_forecast_as_of_override
                or (market.div_forecast_as_of if market else None),
            }
        )

        # 無効銘柄は行として返しても集計には一切加算しない（集計対象外の意味論）。
        if not is_active:
            continue
        if book_value_jpy is not None:
            sum_book += book_value_jpy
            known_book_count += 1
        if market_value_jpy is not None:
            sum_market += market_value_jpy
            known_market_count += 1
        if annual_dividend_jpy is not None:
            sum_dividend += annual_dividend_jpy
            known_dividend_count += 1
        sum_benefit += benefit_jpy
        if profit_jpy is not None:
            sum_profit += profit_jpy
            known_profit_count += 1

    total_book = (
        sum_book if active_count > 0 and known_book_count == active_count else None
    )
    total_market = (
        sum_market if active_count > 0 and known_market_count == active_count else None
    )
    total_dividend = sum_dividend if known_dividend_count > 0 else None
    total_benefit = sum_benefit if sum_benefit > 0 else None
    total_numerator = None
    if total_dividend is not None or total_benefit is not None:
        total_numerator = (total_dividend or 0.0) + (total_benefit or 0.0)
    yield_on_cost = (
        total_numerator / total_book
        if total_numerator is not None and total_book
        else None
    )
    yield_on_value = (
        total_numerator / total_market
        if total_numerator is not None and total_market
        else None
    )
    total_profit = (
        sum_profit if active_count > 0 and known_profit_count == active_count else None
    )
    profit_rate = (
        total_profit / total_book if total_profit is not None and total_book else None
    )

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
        missing_market_value_count=active_count - known_market_count,
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
