"""株式分割の未反映検知と補正（Qt 非依存のロジック層）。

株式分割が起きても、証券会社の口座の株数・取得単価は各社のタイミングで書き換わり、
このアプリの holdings.csv は人が書き換えるまで分割前のままになる。株価だけが分割後に
なるので、放置すると時価が合わず（株数が足りない）、履歴にも凹みが残る。ここでは

- 保有行ごとに「まだ株数・単価へ反映していない分割」を見つける（pending_splits）
- 補正（株数×比率・単価÷比率・配当の上書き値÷比率）を計画・適用する
- 「手で直したので補正しない」を記録する（mark_applied）

までを扱う。**自動では書き換えない**——証券会社の反映時期とアプリの更新時期はずれ、
利用者が先に手で直していることも多い。自動適用は二重適用（株数が4倍）になり、
エラーも出ないまま時価だけが狂う。書き換えは利用者が補正ボタンを押したときだけ。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from fractions import Fraction

from .models import Holding, MarketData
from .utils import today_jst_str

# 「(ISO 日付, 比率)」。比率は 1 株が何株になったか（2:1 分割=2.0、5→1 併合=0.2）。
Split = tuple[str, float]

# 反映済み日（Holding.split_applied_through）が空の保有行に旗を立てる対象期間。
# 記録が無い行は「いつまでの分割を反映済みか」が分からないので、古い分割まで拾うと
# 何年も前に反映済みの分割へ旗が立つ。直近の分割だけを候補にし、外れたときは
# 「反映済みにする」で1回だけ消せる。
UNSET_LOOKBACK_DAYS = 60

# 比率の分数化の分母上限。3:1 の逆数など、float で 0.333333 になる値を元の 1/3 へ戻す
# （そのまま掛けると 300 株が 99.9999 株になる）。
_RATIO_MAX_DENOMINATOR = 1000
_LABEL_MAX_DENOMINATOR = 100


def _today() -> date:
    return date.fromisoformat(today_jst_str())


def format_ratio(ratio: float) -> str:
    """2.0 → "2:1"、0.2 → "1:5"、1.5 → "3:2"（1 株が何株になったか、新:旧）。"""
    frac = Fraction(ratio).limit_denominator(_LABEL_MAX_DENOMINATOR)
    return f"{frac.numerator}:{frac.denominator}"


def _short_date(iso: str) -> str:
    """"2026-09-29" → "9/29"（旗のセルは狭いので年を省く。全文はツールチップ）。"""
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{d.month}/{d.day}"


def flag_text(events: list[Split]) -> str:
    """一覧の旗セルに出す短い文字列。分割が無ければ ""。例: "⚑ 2:1(9/29)"。"""
    if not events:
        return ""
    return "⚑ " + " / ".join(f"{format_ratio(r)}({_short_date(d)})" for d, r in events)


# 反映済みの分割を「完了」として一覧に残す日数。旗が反映の瞬間に消えると、押したのが
# 効いたのか（別の行を押したのではないか）が画面から読み取れない。一定期間だけ色を変えて
# 残し、以降は消す（いつまでも残すと、何年も前の分割が全行に並ぶ）。
APPLIED_DISPLAY_DAYS = 60


def done_flag_text(events: list[Split]) -> str:
    """反映済みの分割の短い文字列。無ければ ""。例: "✓ 2:1(9/29)"。"""
    if not events:
        return ""
    return "✓ " + " / ".join(f"{format_ratio(r)}({_short_date(d)})" for d, r in events)


def pending_splits(
    holding: Holding, market: MarketData | None, today: date | None = None
) -> list[Split]:
    """この保有行に未反映の分割（日付昇順）。無ければ空。

    - 投信は対象外（基準価額の単位が分割で変わらない。取得も yfinance ではない）。
    - 反映済み日があれば、それより後の分割すべて。
    - 反映済み日が空なら、今日から UNSET_LOOKBACK_DAYS 日以内の分割だけ。
    """
    if holding.asset_type == "fund" or market is None or not market.recent_splits:
        return []
    today = today or _today()
    today_iso = today.isoformat()
    baseline = holding.split_applied_through
    if baseline:
        low, inclusive = baseline, False
    else:
        low, inclusive = (today - timedelta(days=UNSET_LOOKBACK_DAYS)).isoformat(), True
    return sorted(
        (d, r)
        for d, r in market.recent_splits
        if d <= today_iso and (d >= low if inclusive else d > low)
    )


def applied_splits(
    holding: Holding, market: MarketData | None, today: date | None = None
) -> list[Split]:
    """直近に反映済みにした分割（一覧へ「完了」表示で残すもの）。無ければ空。

    補正・「反映済みにする」は反映済み日をその分割の日付にするので、**反映済み日と同じ
    日付の分割**だけを対象にする。反映済み日以前の分割を全部拾うと、新規登録した行
    （反映済み日＝今日）に、登録前の分割まで「完了」として並ぶ。表示は
    APPLIED_DISPLAY_DAYS 日で消える。投信は対象外。
    """
    baseline = holding.split_applied_through
    if not baseline or holding.asset_type == "fund" or market is None:
        return []
    today = today or _today()
    since = (today - timedelta(days=APPLIED_DISPLAY_DAYS)).isoformat()
    return sorted(
        (d, r) for d, r in market.recent_splits if d == baseline and d >= since
    )


def pending_holdings(
    holdings: list[Holding],
    market_map: dict[str, MarketData],
    today: date | None = None,
) -> list[tuple[int, list[Split]]]:
    """未反映の分割を持つ保有行の (holdings インデックス, 分割) を、行順で返す。"""
    today = today or _today()
    result: list[tuple[int, list[Split]]] = []
    for index, holding in enumerate(holdings):
        events = pending_splits(holding, market_map.get(holding.symbol), today)
        if events:
            result.append((index, events))
    return result


def _combined_ratio(events: list[Split]) -> Fraction:
    ratio = Fraction(1)
    for _, r in events:
        ratio *= Fraction(r).limit_denominator(_RATIO_MAX_DENOMINATOR)
    return ratio


def _scaled(value: float, factor: Fraction) -> float:
    # 小数 6 桁で丸める。丸めないと 1252.0 / 3 のような値が CSV に長い小数で残る
    # （簿価 = 株数 × 単価への影響は株数 × 5e-7 円以下）。
    return round(float(Fraction(value) * factor), 6)


@dataclass(frozen=True)
class SplitCorrection:
    """補正の前後（確認ダイアログとツールチップの表示、適用の計画に使う）。"""

    events: tuple[Split, ...]
    ratio: float
    applied_through: str
    shares: tuple[float, float]
    avg_cost: tuple[float, float]
    gross_override: tuple[float, float] | None
    net_override: tuple[float, float] | None


def plan_correction(holding: Holding, events: list[Split]) -> SplitCorrection:
    """未反映の分割を株数・単価・配当の上書き値へ反映したときの前後を計算する。

    株数は比率倍、購入単価と1株あたり配当の上書き値は比率で割る（1株が2株になれば
    1株あたりの額は半分）。優待額（円）・取崩額・支払月は1株あたりでも株数比例でもない
    ので触らない。簿価（株数×単価）は変わらない。
    """
    ratio = _combined_ratio(events)
    inverse = 1 / ratio

    def pair(value: float | None, factor: Fraction) -> tuple[float, float] | None:
        if value is None:
            return None
        return value, _scaled(value, factor)

    return SplitCorrection(
        events=tuple(events),
        ratio=float(ratio),
        applied_through=max(d for d, _ in events),
        shares=(holding.shares, _scaled(holding.shares, ratio)),
        avg_cost=(holding.avg_cost, _scaled(holding.avg_cost, inverse)),
        gross_override=pair(holding.annual_div_ps_gross_override, inverse),
        net_override=pair(holding.annual_div_ps_net_override, inverse),
    )


def apply_correction(holding: Holding, events: list[Split]) -> Holding:
    """未反映の分割を反映した新しい Holding を返す（元は変更しない）。"""
    plan = plan_correction(holding, events)
    return replace(
        holding,
        shares=plan.shares[1],
        avg_cost=plan.avg_cost[1],
        annual_div_ps_gross_override=(
            plan.gross_override[1] if plan.gross_override else None
        ),
        annual_div_ps_net_override=plan.net_override[1] if plan.net_override else None,
        split_applied_through=plan.applied_through,
    )


def mark_applied(holding: Holding, events: list[Split]) -> Holding:
    """数値は変えずに「この分割は反映済み」とだけ記録した Holding を返す。

    利用者が証券会社の値に合わせて手で直した銘柄に補正をかけると二重適用になる。
    そのための逃げ道（旗を消すだけで株数・単価は触らない）。
    """
    return replace(holding, split_applied_through=max(d for d, _ in events))


def _num(value: float) -> str:
    return f"{value:,.6f}".rstrip("0").rstrip(".")


def correction_lines(
    holding: Holding, events: list[Split], last_price: float | None = None
) -> list[str]:
    """補正前後を人が読む形にする（確認ダイアログ・ツールチップ共用）。

    現在株価を添える。株価と取得単価の桁を見比べれば、分割前後どちらの基準で
    株数・単価を持っているかが分かり、反映済みの銘柄へ誤って補正をかけるのを防げる。
    """
    plan = plan_correction(holding, events)
    lines = [
        "分割: "
        + "、".join(f"{d} に {format_ratio(r)}" for d, r in events),
        f"株数: {_num(plan.shares[0])} → {_num(plan.shares[1])}",
        f"購入単価: {_num(plan.avg_cost[0])} → {_num(plan.avg_cost[1])}",
    ]
    if plan.gross_override:
        lines.append(
            f"配当/株の上書き（税引前）: {_num(plan.gross_override[0])} → "
            f"{_num(plan.gross_override[1])}"
        )
    if plan.net_override:
        lines.append(
            f"配当/株の上書き（税引後）: {_num(plan.net_override[0])} → "
            f"{_num(plan.net_override[1])}"
        )
    if last_price is not None:
        lines.append(f"現在株価: {_num(last_price)}")
    return lines
