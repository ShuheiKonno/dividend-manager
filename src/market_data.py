from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Optional

from .models import Holding, MarketData, FXRate
from .utils import now_jst_iso, today_jst_str

# yfinance / ToushinReader は import に数秒かかる重量級依存。実際に取得を行う関数が
# 呼ばれるまでロードしないことで、GUI/TUI の起動を即時化する（取得は QThread ワーカー上）。
_yf_module = None
_ToushinFund = None
_TOUSHIN_AVAILABLE: bool | None = None  # None = 未プローブ


def _yf():
    global _yf_module
    if _yf_module is None:
        import yfinance as yf
        _yf_module = yf
    return _yf_module


def _toushin_fund_cls():
    """ToushinReader.core.Fund を遅延ロード。未インストールなら None を返す。"""
    global _ToushinFund, _TOUSHIN_AVAILABLE
    if _TOUSHIN_AVAILABLE is None:
        try:
            from ToushinReader.core import Fund
            _ToushinFund = Fund
            _TOUSHIN_AVAILABLE = True
        except Exception:
            _TOUSHIN_AVAILABLE = False
    return _ToushinFund if _TOUSHIN_AVAILABLE else None


ProgressCallback = Callable[[int, int, str], None]


@dataclass
class MarketFetchResult:
    rows: list[MarketData]
    fetched_at: str | None
    source: str | None
    errors: list[str]
    name_updates: dict[str, str]


def _yfinance_company_name(symbol: str) -> str | None:
    try:
        ticker = _yf().Ticker(symbol)
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


def _parse_yfinance_dividends(
    series,
) -> tuple[float | None, str | None, dict[int, float] | None]:
    """直近約370日の配当合計・最新権利落ち日・権利落ち月別の構成比を返す。

    比率は支払月ではなく**権利落ち月**ベース（yfinance のインデックスは ex-date）。
    支払月への変換は calc.shift_ex_month_to_payment が担う——ここで焼き込むと、
    取得結果キャッシュである market_data.csv が推定値の入れ物に化けてしまい、
    推定ルールを変えるたび全銘柄の再取得が要る。

    中間配当と期末配当は額が違う（例 1723: 期末24.75円 / 中間15.25円）ため、
    月数で等分せずこの実績比率で按分する。
    """
    if series is None or len(series) == 0:
        return None, None, None
    cutoff = date.today() - timedelta(days=370)
    total = 0.0
    latest_date = None
    by_month: dict[int, float] = {}
    for idx, value in series.items():
        d = idx.date() if hasattr(idx, "date") else None
        if d is None or d < cutoff:
            continue
        try:
            val = float(value)
        except (TypeError, ValueError):
            continue
        total += val
        by_month[d.month] = by_month.get(d.month, 0.0) + val
        if latest_date is None or d.isoformat() > latest_date:
            latest_date = d.isoformat()
    if total <= 0:
        return None, latest_date, None
    ratios = {month: amount / total for month, amount in sorted(by_month.items())}
    return total, latest_date, ratios


def _is_isin(symbol: str) -> bool:
    """12 文字・先頭 2 文字英字・残り英数字の ISIN コードかどうか判定"""
    return bool(re.fullmatch(r"[A-Z]{2}[A-Z0-9]{10}", symbol))


def _fetch_toushin(isin: str) -> tuple[float | None, str | None]:
    """ToushinReader で基準価額と日付を取得。(price, date_iso) を返す。"""
    fund_cls = _toushin_fund_cls()
    if fund_cls is None:
        return None, None
    try:
        fund = fund_cls(isin)
        price_str = fund.basic_price
        date_str = fund.as_of_date
        price = float(price_str.replace(",", "").replace("，", ""))
        m = re.match(r"(\d{4})年(\d{2})月(\d{2})日", date_str)
        date_iso = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None
        return price, date_iso
    except Exception:
        return None, None


def _yfinance_symbol(holding: Holding) -> str:
    symbol = holding.symbol
    if holding.currency == "JPY" and "." not in symbol:
        return f"{symbol}.T"
    return symbol


def _fetch_one(
    holding: Holding, fetched_at: str | None, source: str
) -> tuple[MarketData, list[str], tuple[str, str] | None]:
    """1 銘柄分の取得を行う。(MarketData, エラー一覧, 銘柄名更新|None) を返す。

    純粋に取得のみを行い共有状態を変更しないため、スレッドプールから安全に呼べる。
    """
    errors: list[str] = []
    name_update: tuple[str, str] | None = None
    price, price_date = None, None
    div_ps, div_date, div_ratios = None, None, None

    if holding.asset_type == "fund" and _is_isin(holding.symbol):
        # 投資信託 ISIN → ToushinReader で基準価額を取得
        price, price_date = _fetch_toushin(holding.symbol)
        if price is None:
            errors.append(f"基準価額取得失敗: {holding.symbol} (ToushinReader)")
    else:
        yf_symbol = _yfinance_symbol(holding)
        ticker = None
        try:
            ticker = _yf().Ticker(yf_symbol)
            history = ticker.history(period="10d")
            # 最終行の Close が NaN（最新バー未確定）の銘柄があるため、
            # 最後の非 NaN 終値を採用する。
            close = history["Close"].dropna()
            if not close.empty:
                price = float(close.iloc[-1])
                price_date = close.index[-1].date().isoformat()
            else:
                errors.append(f"株価取得失敗: {holding.symbol} (終値データなし)")
        except Exception as exc:
            errors.append(f"株価取得失敗: {holding.symbol} ({exc})")

        if ticker is not None:
            try:
                series = ticker.dividends
                div_ps, div_date, div_ratios = _parse_yfinance_dividends(series)
            except Exception as exc:
                errors.append(f"配当取得失敗: {holding.symbol} ({exc})")

        if not holding.name:
            name = _yfinance_company_name(yf_symbol)
            if name:
                name_update = (holding.symbol, name)

    md = MarketData(
        symbol=holding.symbol,
        currency=holding.currency,
        last_price=price,
        price_as_of=price_date,
        annual_div_ps_gross=div_ps,
        annual_div_ps_net=None,
        div_forecast_as_of=div_date,
        source=source,
        fetched_at=fetched_at,
        div_ex_month_ratios=div_ratios,
    )
    return md, errors, name_update


def fetch_market_data(
    holdings: list[Holding],
    existing: dict[str, MarketData],
    force: bool = False,
    progress_cb: Optional[ProgressCallback] = None,
) -> MarketFetchResult:
    fetched_at = now_jst_iso()
    source = "yfinance"
    errors: list[str] = []
    name_updates: dict[str, str] = {}

    # 複数口座で同じ銘柄を保有していても、市場データは symbol 単位で一度だけ取得する。
    active_by_symbol: dict[str, Holding] = {}
    for holding in holdings:
        if holding.active and holding.symbol not in active_by_symbol:
            active_by_symbol[holding.symbol] = holding
    active = list(active_by_symbol.values())
    total = len(active)
    result_by_symbol = dict(existing)

    # 当日取得済みキャッシュと要取得を分離。結果配列は入力順を保持する。
    to_fetch: list[Holding] = []
    for holding in active:
        cached = existing.get(holding.symbol)
        if not force and cached and cached.fetched_at and cached.fetched_at.startswith(today_jst_str()):
            result_by_symbol[holding.symbol] = cached
        else:
            to_fetch.append(holding)

    done = total - len(to_fetch)  # キャッシュ済みは完了済みとして算入
    if progress_cb:
        progress_cb(done, total, "取得中..." if to_fetch else "完了")

    if to_fetch:
        # yfinance を事前ロードし、各プールスレッドでの初回 import 競合を避ける。
        try:
            _yf()
        except Exception:
            pass
        # 取得は I/O 待ちが支配的なため ThreadPoolExecutor で並列化（最大8並列）。
        # プールスレッドは _fetch_one（共有状態に触れない）のみ実行し、結果集約と
        # progress_cb 呼び出しはこの呼び出し元スレッド（QThread ワーカー）で行う。
        with ThreadPoolExecutor(max_workers=min(8, len(to_fetch))) as executor:
            future_map = {
                executor.submit(_fetch_one, holding, fetched_at, source): holding
                for holding in to_fetch
            }
            for future in as_completed(future_map):
                holding = future_map[future]
                try:
                    md, errs, name_update = future.result()
                except Exception as exc:  # noqa: BLE001 — 1銘柄の失敗で全体を止めない
                    md = MarketData(
                        symbol=holding.symbol,
                        currency=holding.currency,
                        last_price=None,
                        price_as_of=None,
                        annual_div_ps_gross=None,
                        annual_div_ps_net=None,
                        div_forecast_as_of=None,
                        source=source,
                        fetched_at=fetched_at,
                        div_ex_month_ratios=None,
                    )
                    errs = [f"取得失敗: {holding.symbol} ({exc})"]
                    name_update = None
                # 一時的な通信失敗で直前の正常キャッシュを消さない。
                cached = existing.get(holding.symbol)
                if errs and cached:
                    result_by_symbol[holding.symbol] = cached
                else:
                    if errs:
                        md.fetched_at = None  # force=False の次回更新で再取得できるようにする。
                    result_by_symbol[holding.symbol] = md
                errors.extend(errs)
                if name_update:
                    name_updates[name_update[0]] = name_update[1]
                done += 1
                if progress_cb:
                    progress_cb(done, total, f"取得中: {holding.symbol} ({done}/{total})")

    if progress_cb:
        progress_cb(total, total, "完了")

    active_symbols = set(active_by_symbol)
    rows = [result_by_symbol[h.symbol] for h in active if h.symbol in result_by_symbol]
    rows.extend(row for symbol, row in result_by_symbol.items() if symbol not in active_symbols)
    return MarketFetchResult(
        rows=rows,
        fetched_at=fetched_at,
        source=source,
        errors=errors,
        name_updates=name_updates,
    )


def fetch_usdjpy() -> FXRate:
    try:
        ticker = _yf().Ticker("USDJPY=X")
        history = ticker.history(period="10d")
        close = history["Close"].dropna()
        if close.empty:
            raise RuntimeError("終値データなし")
        rate = float(close.iloc[-1])
        return FXRate(timestamp=now_jst_iso(), pair="USDJPY", rate=rate, source="yfinance")
    except Exception as exc:
        raise RuntimeError(f"USDJPY取得失敗: {exc}") from exc
