from __future__ import annotations

import math
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


def normalize_iso_date(raw: object) -> str | None:
    """日付文字列を ISO 形式 "YYYY-MM-DD" に正規化する。解析不能なら None。

    取込 CSV を Excel で再保存すると "2024/1/1" や "2024-1-1" のように
    区切り・ゼロ埋めが崩れることがある。そのまま履歴へ保存すると
    filter_by_period の date.fromisoformat が ValueError を投げるため、
    取込時にここで ISO へ正規化し、復元不能な値は呼び出し側で除外する。
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        pass
    for sep in ("/", "-", "."):
        parts = text.split(sep)
        if len(parts) == 3:
            try:
                year, month, day = (int(p) for p in parts)
                return date(year, month, day).isoformat()
            except (ValueError, TypeError):
                continue
    return None


def parse_months(raw: object) -> list[int]:
    """ "3,9" 形式の月リストを [3, 9] へ。1〜12 以外・数値以外は捨てる。

    normalize_iso_date と同じく「壊れた入力を救済し、復元不能な要素は捨てる」方針。
    重複を除いて昇順に正規化するため、往復すると入力順は保存されない。
    """
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        parts: list[str] = [str(item) for item in raw]
    else:
        text = str(raw).strip()
        if not text:
            return []
        parts = text.replace("，", ",").split(",")
    months: set[int] = set()
    for part in parts:
        try:
            month = int(str(part).strip())
        except (TypeError, ValueError):
            continue
        if 1 <= month <= 12:
            months.add(month)
    return sorted(months)


def format_months(months: object) -> str:
    """[9, 3, 9] → "3,9"（parse_months で正規化して出力）。空/None は ""。"""
    return ",".join(str(m) for m in parse_months(months))


def parse_month_ratios(raw: object) -> dict[int, float] | None:
    """ "6:0.62,12:0.38" → {6: 0.62, 12: 0.38}。有効要素が無ければ None。

    不正な要素は捨て、残った比率の合計が 1.0 になるよう再正規化する。
    比率 0 だけが残る状態は「月が決まったのに額が乗らない」という無意味な
    結果になるため、合計が 0 以下なら None（＝月不明）を返す。
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        items = [(k, v) for k, v in raw.items()]
    else:
        text = str(raw).strip()
        if not text:
            return None
        items = []
        for chunk in text.replace("，", ",").split(","):
            key, sep, value = chunk.partition(":")
            if not sep:
                continue
            items.append((key, value))
    ratios: dict[int, float] = {}
    for key, value in items:
        try:
            month = int(str(key).strip())
        except (TypeError, ValueError):
            continue
        if not 1 <= month <= 12:
            continue
        ratio = safe_float(value)
        if ratio is None or ratio < 0:
            continue
        ratios[month] = ratios.get(month, 0.0) + ratio
    total = sum(ratios.values())
    if not ratios or total <= 0:
        return None
    return {month: ratio / total for month, ratio in sorted(ratios.items())}


def format_month_ratios(ratios: object) -> str:
    """{6: 0.62, 12: 0.38} → "6:0.6200,12:0.3800"。None/空は ""。"""
    parsed = parse_month_ratios(ratios)
    if not parsed:
        return ""
    return ",".join(f"{month}:{ratio:.4f}" for month, ratio in sorted(parsed.items()))


def parse_splits(raw: object) -> list[tuple[str, float]]:
    """ "2026-09-29:2,2025-09-29:5" → [("2025-09-29", 5.0), ("2026-09-29", 2.0)]。

    比率は「1株が何株になったか」（2:1 分割=2.0、5→1 併合=0.2）。日付昇順・同日は
    後勝ちに正規化する。日付が解析不能・比率が正の有限値でない・比率が 1.0（変化なし）
    の要素は捨てる（parse_months / parse_month_ratios と同じ「壊れた要素は捨てる」方針）。
    """
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        items = [(a, b) for a, b in raw]
    else:
        text = str(raw).strip()
        if not text:
            return []
        items = []
        for chunk in text.replace("，", ",").split(","):
            key, sep, value = chunk.strip().partition(":")
            if sep:
                items.append((key, value))
    by_date: dict[str, float] = {}
    for key, value in items:
        iso = normalize_iso_date(key)
        ratio = safe_float(value)
        if iso is None or ratio is None or not math.isfinite(ratio):
            continue
        if ratio <= 0 or ratio == 1.0:
            continue
        by_date[iso] = ratio
    return sorted(by_date.items())


def format_splits(splits: object) -> str:
    """[("2026-09-29", 2.0)] → "2026-09-29:2"。空/None は ""（parse_splits で正規化）。"""
    return ",".join(f"{d}:{r:g}" for d, r in parse_splits(splits))


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if isinstance(value, (float, int)):
            number = float(value)
        else:
            text = str(value).strip()
            if text == "":
                return None
            number = float(text)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


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


def _ordinals_or_none(dates: list[str]) -> list[int] | None:
    """ISO 日付列を toordinal() のリストへ変換する。1件でも解釈不能なら None。"""
    ords: list[int] = []
    for raw in dates:
        iso = normalize_iso_date(raw)
        if iso is None:
            return None
        ords.append(date.fromisoformat(iso).toordinal())
    return ords


def date_x_positions(dates: list[str], left: float, width: float) -> list[float]:
    """推移グラフの各レコード日付を、経過日数（ordinal）に比例した X 座標へ変換する。

    レコード件数で等間隔に並べると、月1回しか更新しない期間と毎日更新した期間が
    同じ横幅になり時間軸が歪むため、実日付の差分で按分する。単一点または全点同日は
    中央に置く。日付が解釈不能な要素が含まれる場合のみインデックス等間隔へ退避する。
    """
    n = len(dates)
    if n == 0:
        return []
    if n == 1 or width <= 0:
        return [left + width / 2.0] * n
    ords = _ordinals_or_none(dates)
    if ords is None:
        return [left + width * i / (n - 1) for i in range(n)]
    min_ord = min(ords)
    span = max(ords) - min_ord
    if span <= 0:
        return [left + width / 2.0] * n
    return [left + width * (o - min_ord) / span for o in ords]


def nearest_x_index(
    xs: list[float],
    px: float,
    left: float,
    right: float,
    margin: float = 12.0,
) -> int | None:
    """描画と同じ X 座標配列 xs から、マウスの px に最も近い点のインデックスを返す。

    プロット領域の左右 margin 外なら None。同距離なら先のインデックスを返す。
    """
    if not xs or px < left - margin or px > right + margin:
        return None
    return min(range(len(xs)), key=lambda i: abs(xs[i] - px))


def date_axis_ticks(
    dates: list[str], left: float, width: float, max_ticks: int = 5
) -> list[tuple[float, str]]:
    """推移グラフ X 軸の目盛 `(x座標, "YYYY-MM-DD")` を日数基準の等間隔で返す。

    データ点が偏っていても目盛は期間の両端を基準に暦日で等分し、date_x_positions と
    同じ比率で配置する。期間が max_ticks 未満の日数しか無い場合は日数を上限にして
    同じ日付ラベルの重複を防ぐ。
    """
    n = len(dates)
    if n == 0 or max_ticks <= 0:
        return []
    ords = _ordinals_or_none(dates)
    if ords is None:
        count = min(max_ticks, n)
        xs = date_x_positions(dates, left, width)
        if count == 1:
            return [(xs[0], str(dates[0]))]
        indices = [round(j * (n - 1) / (count - 1)) for j in range(count)]
        return [(xs[idx], str(dates[idx])) for idx in indices]
    min_ord = min(ords)
    max_ord = max(ords)
    span = max_ord - min_ord
    if span <= 0 or max_ticks == 1 or width <= 0:
        return [(left + width / 2.0, date.fromordinal(min_ord).isoformat())]
    count = min(max_ticks, span + 1)
    ticks: list[tuple[float, str]] = []
    for j in range(count):
        ord_j = round(min_ord + j * span / (count - 1))
        x_j = left + width * (ord_j - min_ord) / span
        ticks.append((x_j, date.fromordinal(ord_j).isoformat()))
    return ticks

