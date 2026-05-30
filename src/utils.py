from __future__ import annotations

from datetime import date, datetime, timezone, timedelta
from typing import Any

JST = timezone(timedelta(hours=9))

# 推移グラフの表示期間（最新日付からの遡及年数）。"all" は全期間。
PERIOD_YEARS = {"10y": 10, "5y": 5, "3y": 3, "1y": 1}


def now_jst() -> datetime:
    return datetime.now(JST)


def now_jst_iso() -> str:
    return now_jst().isoformat(timespec="seconds")


def today_jst_str() -> str:
    return now_jst().date().isoformat()


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, float):
        return value
    if isinstance(value, int):
        return float(value)
    text = str(value).strip()
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def safe_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text != "" else None


def fmt_number(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{value:,.{digits}f}"


def fmt_percent(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:,.{digits}f}%"


def rjust(text: str, width: int) -> str:
    if len(text) >= width:
        return text
    return " " * (width - len(text)) + text


def filter_by_period(
    records: list[dict], period_key: str | None, date_key: str = "date"
) -> list[dict]:
    """推移グラフ用に、最新日付から指定年数ぶんのレコードへ絞り込む。

    period_key が "all"/None/未知のキーなら全件をそのまま返す。
    日付は ISO 文字列（"YYYY-MM-DD"、辞書順=日付順）で比較し、
    cutoff（最新日付の N 年前）以降（含む）を残す。閏日 2/29 は 2/28 に丸める。
    """
    years = PERIOD_YEARS.get(period_key) if period_key else None
    if years is None or not records:
        return list(records)
    dates = [r.get(date_key) for r in records if r.get(date_key)]
    if not dates:
        return list(records)
    latest = date.fromisoformat(max(dates))
    try:
        cutoff = date(latest.year - years, latest.month, latest.day)
    except ValueError:  # 2/29 → 2/28
        cutoff = date(latest.year - years, latest.month, 28)
    cutoff_s = cutoff.isoformat()
    return [r for r in records if r.get(date_key) and r.get(date_key) >= cutoff_s]


def spread_labels(
    ideal_ys: list[float], min_gap: float, top: float, bottom: float
) -> list[float]:
    """円グラフ引き出し線ラベルの y 位置を、重ならないよう最小限ずらして返す。

    ideal_ys は同一サイド（左 or 右）のラベルの理想 y を昇順で渡す前提。出力は
    入力と同じ順序（＝引き出し線が同サイド内で交差しない）。隣接は min_gap 以上
    離し、全体は [top, bottom] に収める。全ラベルが min_gap 間隔で収まらない場合は
    範囲内へ均等配置する（重なりは許容するが画面外には出さない）。
    """
    n = len(ideal_ys)
    if n == 0:
        return []
    if n == 1:
        return [min(max(ideal_ys[0], top), bottom)]
    # min_gap 間隔で全部は収まらない → 範囲内に均等配置
    if (n - 1) * min_gap >= (bottom - top):
        step = (bottom - top) / (n - 1)
        return [top + i * step for i in range(n)]
    out = list(ideal_ys)
    # 下方向パス: 前要素と min_gap を確保
    for i in range(1, n):
        if out[i] < out[i - 1] + min_gap:
            out[i] = out[i - 1] + min_gap
    # 下端超過なら全体を上へシフト
    if out[-1] > bottom:
        out = [y - (out[-1] - bottom) for y in out]
    # 上端割れを下方向へ再解決（収まる前提なので bottom は超えない）
    if out[0] < top:
        out[0] = top
        for i in range(1, n):
            if out[i] < out[i - 1] + min_gap:
                out[i] = out[i - 1] + min_gap
    return out


def axis_bounds(
    values: list[float | None], baseline_mode: str
) -> tuple[float, float]:
    """推移グラフ Y 軸の (下限, 上限) を返す。

    baseline_mode="zero": 0 〜 最大値×1.08（ゼロ基準）。
    baseline_mode="min" : 最小値×0.9 〜 最大値×1.05（最低値基準で変動を拡大）。
    金額系列を想定し下限は 0 未満にクランプしない。値が無ければ (0, 1)。
    """
    vals = [v for v in values if v is not None]
    if not vals:
        return 0.0, 1.0
    vmax = max(vals)
    if baseline_mode == "min":
        vmin = min(vals)
        floor = vmin * 0.9
        if floor < 0:
            floor = 0.0
        top = vmax * 1.05
        if top <= floor:  # 全点同値などの退避
            top = floor + (abs(floor) * 0.1 or 1.0)
        return floor, top
    # zero 基準
    return 0.0, vmax * 1.08 if vmax > 0 else 1.0
