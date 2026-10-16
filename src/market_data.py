from __future__ import annotations

import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
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


# 配当年額の集計窓。「今日から」ではなく「最新の権利落ち日から」数える。
# 今日起点の 370 日窓だと、権利落ち当日に前年の同じ権利落ち日も入って半期配当が
# 3 回分（四半期は 5 回分）になり、年額が約 1.5 倍に膨らむ。350 日は
# 月次（11 回前≒335 日）まで入り、前年同期の支払い（≒365 日前）が権利落ち日の
# 曜日ずれ（±数日〜2 週間）で混ざらない幅。
_DIV_WINDOW_DAYS = 350
# 配当を出さなくなった銘柄を拾い続けないための、今日からの上限。
_DIV_STALE_DAYS = 370

# 市場データへ保存する分割の遡及日数。未反映の分割を探すには直近だけあればよく
# （splits.UNSET_LOOKBACK_DAYS は60日）、反映済み日を持つ行が長く放置されても
# 拾えるよう余裕を持たせる。何年も前の分割まで持つとキャッシュが膨らむだけ。
_SPLIT_KEEP_DAYS = 800
# 分割比が 2/3〜1.5（両端を含む）の範囲では、分割前基準の配当（比率倍）と配当の
# 自然な増減（数十%）を見分けられないので補正しない。
_SPLIT_DIV_MIN_STEP = 1.5
# 分割当日の配当と比べる参照配当として、前年同期を探す幅（±日）。
_SAME_SEASON_DAYS = 45


def _parse_yfinance_splits(series) -> list[tuple[str, float]]:
    """yfinance の splits（比率=1株が何株になったか）を [(ISO 日付, 比率)] へ。"""
    if series is None or len(series) == 0:
        return []
    result: dict[str, float] = {}
    for idx, value in series.items():
        d = idx.date() if hasattr(idx, "date") else None
        try:
            ratio = float(value)
        except (TypeError, ValueError):
            continue
        if d is None or not math.isfinite(ratio) or ratio <= 0 or ratio == 1.0:
            continue
        result[d.isoformat()] = ratio
    return sorted(result.items())


def _reference_dividend(
    entries: list[tuple[date, float]], target: date, skip: int
) -> float | None:
    """分割当日の配当と比べる参照配当。前年同期を優先し、無ければ直前の配当。

    前年同期を優先するのは、中間と期末で額が大きく違う銘柄があるため（8473:
    中間 15〜20円 / 期末 70〜75円）。直前の配当だけと比べると、期末配当を「分割前基準」と
    取り違える。Yahoo は分割当日**以前**の配当を分割後基準へ調整済みなので、そのまま
    比べられる。
    """
    year_ago = target - timedelta(days=365)
    same_season = [
        (abs((d - year_ago).days), v)
        for i, (d, v) in enumerate(entries)
        if i != skip and v > 0 and abs((d - year_ago).days) <= _SAME_SEASON_DAYS
    ]
    if same_season:
        return min(same_season)[1]
    earlier = [v for i, (d, v) in enumerate(entries) if i != skip and d < target and v > 0]
    return earlier[-1] if earlier else None


def _correct_split_day_dividends(
    entries: list[tuple[date, float]], splits: list[tuple[str, float]]
) -> list[tuple[date, float]]:
    """分割当日の権利落ち配当が分割前基準のままなら、分割後基準へ直す。

    Yahoo は分割当日以前の配当を分割後基準へ調整するが、**分割当日の配当だけは
    未調整のまま返すことがある**（2026-09-29 の 5334: 105円が正しくは 52.5円）。数日で
    調整される銘柄もあれば、5401（2025-09-29 の 60円）のように1年以上そのままの銘柄も
    ある。逆に 9101・7267・8316（2024）などは当日分も調整済みで、割ると二重に割る。

    そこで**無条件には割らず**、参照配当（前年同期／直前）と比べて、生値が「参照 × 比率」
    （未調整）と「参照 × 1」（調整済み）のどちらに対数距離で近いかで決める。Yahoo が
    後で調整すれば自動で割るのをやめる。

    既知の弱点: 参照配当（前年同期）に対する当日配当の倍率が、調整済みでも
    **√比率を超える**（比率2なら約1.4倍、比率3なら約1.7倍を超える増配）と、未調整と
    誤判定して比率で割ってしまう。参照だけでは「未調整」と「調整済みだが大幅増配」を
    原理的に見分けられない。実例: 日本郵船 9101 の 2022-09-29（3:1 分割、前年同期
    66.67→350円＝5.25倍の好況期増配。350円は分割後基準と推定されるが規則は 1/3 にする。
    集計窓の外なので現在の年額には効かない）。誤判定は年額を過小に出す向きで、
    銘柄ごとの配当上書きで直せる。参照が取れない銘柄（上場直後など）は補正せず、
    配当上書きに任せる。
    """
    by_date = {date.fromisoformat(d): r for d, r in splits}
    if not by_date:
        return entries
    ordered = sorted(entries)
    corrected: list[tuple[date, float]] = []
    for i, (d, value) in enumerate(ordered):
        ratio = by_date.get(d)
        if (
            ratio is not None
            and value > 0
            and not (1 / _SPLIT_DIV_MIN_STEP <= ratio <= _SPLIT_DIV_MIN_STEP)
        ):
            ref = _reference_dividend(ordered, d, skip=i)
            if ref is not None:
                gap = math.log(value / ref)
                if abs(gap - math.log(ratio)) < abs(gap):
                    value = value / ratio
        corrected.append((d, value))
    return corrected


def _parse_yfinance_dividends(
    series, splits: list[tuple[str, float]] | None = None
) -> tuple[float | None, str | None, dict[int, float] | None]:
    """直近1年分の配当合計・最新権利落ち日・権利落ち月別の構成比を返す。

    窓は最新の権利落ち日から `_DIV_WINDOW_DAYS` 日（今日から
    `_DIV_STALE_DAYS` 日より古い配当は除外）。

    splits を渡すと、分割当日の配当が分割前基準のままなら分割後基準へ直す
    （_correct_split_day_dividends）。参照配当に前年の配当が要るため、補正は古い配当を
    捨てる前に行う。

    比率は支払月ではなく**権利落ち月**ベース（yfinance のインデックスは ex-date）。
    支払月への変換は calc.shift_ex_month_to_payment が担う——ここで焼き込むと、
    取得結果キャッシュである market_data.csv が推定値の入れ物に化けてしまい、
    推定ルールを変えるたび全銘柄の再取得が要る。

    中間配当と期末配当は額が違う（例 1723: 期末24.75円 / 中間15.25円）ため、
    月数で等分せずこの実績比率で按分する。
    """
    if series is None or len(series) == 0:
        return None, None, None
    today = date.today()
    stale_cutoff = today - timedelta(days=_DIV_STALE_DAYS)
    raw: list[tuple[date, float]] = []
    for idx, value in series.items():
        d = idx.date() if hasattr(idx, "date") else None
        if d is None:
            continue
        try:
            raw.append((d, float(value)))
        except (TypeError, ValueError):
            continue
    if splits:
        raw = _correct_split_day_dividends(raw, splits)
    entries = [(d, v) for d, v in raw if d >= stale_cutoff]
    if not entries:
        return None, None, None
    # 起点は今日以前の最新の権利落ち日（先日付が混ざっても窓を未来へずらさない）。
    past = [d for d, _ in entries if d <= today]
    anchor = max(past) if past else max(d for d, _ in entries)
    window_start = anchor - timedelta(days=_DIV_WINDOW_DAYS)
    total = 0.0
    latest_date = None
    by_month: dict[int, float] = {}
    for d, val in entries:
        if d <= window_start or d > anchor:
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
) -> tuple[MarketData, list[str], tuple[str, str] | None, set[str]]:
    """1 銘柄分の市場データ・エラー・銘柄名更新・取得成功項目を返す。

    純粋に取得のみを行い共有状態を変更しないため、スレッドプールから安全に呼べる。
    """
    errors: list[str] = []
    succeeded: set[str] = set()
    name_update: tuple[str, str] | None = None
    price, price_date = None, None
    div_ps, div_date, div_ratios = None, None, None
    recent_splits: list[tuple[str, float]] = []

    if holding.asset_type == "fund" and _is_isin(holding.symbol):
        # 投資信託 ISIN → ToushinReader で基準価額を取得
        price, price_date = _fetch_toushin(holding.symbol)
        if price is None:
            errors.append(f"基準価額取得失敗: {holding.symbol} (ToushinReader)")
        else:
            succeeded.add("price")
        # 投信の配当・分割はこの取得元の対象外（既存どおり空の結果とする）。
        succeeded.update(("dividend", "splits"))
    else:
        yf_symbol = _yfinance_symbol(holding)
        ticker = None
        try:
            ticker = _yf().Ticker(yf_symbol)
            # 最新の終値だけ使うので配当・分割の調整は不要。既定の auto_adjust=True だと、
            # 最新バーの後ろに未来の権利落ち日の配当があるとき最新バーまで調整され
            # 市場価格より安い値が入る（分割直後は分割前基準の配当で -23% になった）。
            history = ticker.history(period="10d", auto_adjust=False)
            # 最終行の Close が NaN（最新バー未確定）の銘柄があるため、
            # 最後の非 NaN 終値を採用する。
            close = history["Close"].dropna()
            if not close.empty:
                price = float(close.iloc[-1])
                price_date = close.index[-1].date().isoformat()
                succeeded.add("price")
            else:
                errors.append(f"株価取得失敗: {holding.symbol} (終値データなし)")
        except Exception as exc:
            errors.append(f"株価取得失敗: {holding.symbol} ({exc})")

        if ticker is not None:
            # 分割の失敗は独立して扱い、集約時に既存の分割情報を保持する。
            # 配当と同じ履歴から取れるので、通常は追加の通信は発生しない。
            all_splits: list[tuple[str, float]] = []
            try:
                all_splits = _parse_yfinance_splits(ticker.splits)
                succeeded.add("splits")
            except Exception as exc:
                errors.append(f"分割取得失敗: {holding.symbol} ({exc})")
            keep_since = (date.today() - timedelta(days=_SPLIT_KEEP_DAYS)).isoformat()
            recent_splits = [(d, r) for d, r in all_splits if d >= keep_since]
            try:
                series = ticker.dividends
                div_ps, div_date, div_ratios = _parse_yfinance_dividends(
                    series, all_splits
                )
                succeeded.add("dividend")
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
        recent_splits=recent_splits,
        price_fetched_at=fetched_at if "price" in succeeded else None,
    )
    return md, errors, name_update, succeeded


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
                    md, errs, name_update, succeeded = future.result()
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
                    succeeded = set()
                # 失敗した項目だけキャッシュを引き継ぐ。取得成功で空の場合は更新する。
                cached = existing.get(holding.symbol)
                if cached:
                    fields_by_item = {
                        "price": ("last_price", "price_as_of", "price_fetched_at"),
                        "dividend": (
                            "annual_div_ps_gross", "annual_div_ps_net",
                            "div_forecast_as_of", "div_ex_month_ratios",
                        ),
                        "splits": ("recent_splits",),
                    }
                    md = replace(md, **{
                        field: getattr(cached, field)
                        for item, fields in fields_by_item.items()
                        if item not in succeeded
                        for field in fields
                    })
                if errs:
                    # 全項目の取得完了日時とは分離し、次回は部分失敗を再取得する。
                    md.fetched_at = None
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
