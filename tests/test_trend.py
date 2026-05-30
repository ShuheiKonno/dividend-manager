"""推移グラフの期間フィルタ・Y軸範囲ロジック検証（PySide6 非依存・純ロジック層）。

src/utils.py の filter_by_period / axis_bounds を対象にする。表示層
（LineChartWidget の描画）はテストしないが、これらの純関数で挙動を固定する。
"""

from __future__ import annotations

from src.utils import filter_by_period, axis_bounds


def _recs(*dates: str) -> list[dict]:
    return [{"date": d, "v": 1.0} for d in dates]


# ---- filter_by_period ----


def test_period_all_returns_everything():
    recs = _recs("2020-01-01", "2024-06-01", "2026-05-30")
    assert filter_by_period(recs, "all") == recs
    assert filter_by_period(recs, None) == recs


def test_period_1y_cutoff_inclusive():
    recs = _recs(
        "2025-05-29",  # 1年より前（除外）
        "2025-05-30",  # ちょうど1年前（含む・境界）
        "2025-12-31",
        "2026-05-30",  # 最新
    )
    out = [r["date"] for r in filter_by_period(recs, "1y")]
    assert out == ["2025-05-30", "2025-12-31", "2026-05-30"]


def test_period_3y_and_5y():
    recs = _recs("2020-05-30", "2022-05-30", "2024-05-30", "2026-05-30")
    assert [r["date"] for r in filter_by_period(recs, "3y")] == [
        "2024-05-30",
        "2026-05-30",
    ]
    assert [r["date"] for r in filter_by_period(recs, "5y")] == [
        "2022-05-30",
        "2024-05-30",
        "2026-05-30",
    ]


def test_period_longer_than_data_returns_all():
    recs = _recs("2024-06-27", "2025-01-01", "2026-05-30")  # 約2年分
    assert filter_by_period(recs, "10y") == recs


def test_period_leap_day_cutoff():
    # 最新が閏日。1年前 2027-02-29 は存在しない → 2/28 に丸める。
    recs = _recs("2027-02-27", "2027-02-28", "2028-02-29")
    out = [r["date"] for r in filter_by_period(recs, "1y")]
    assert out == ["2027-02-28", "2028-02-29"]


def test_period_empty():
    assert filter_by_period([], "1y") == []


# ---- axis_bounds ----


def test_axis_zero_mode():
    floor, top = axis_bounds([100.0, 200.0, 150.0], "zero")
    assert floor == 0.0
    assert top == 200.0 * 1.08


def test_axis_min_mode_floor_is_min_times_0_9():
    floor, top = axis_bounds([100.0, 200.0, 150.0], "min")
    assert floor == 100.0 * 0.9
    assert top == 200.0 * 1.05
    assert floor > 0  # 変動の拡大（ゼロ基準より底が持ち上がる）


def test_axis_min_skips_none_values():
    # None（簿価未記録など）は無視して min/max を取る。
    floor, top = axis_bounds([None, 100.0, None, 200.0], "min")
    assert floor == 100.0 * 0.9
    assert top == 200.0 * 1.05


def test_axis_min_single_value_has_positive_span():
    floor, top = axis_bounds([100.0], "min")
    assert floor == 90.0
    assert top > floor  # 単一点でも上限>下限（ゼロ割回避）


def test_axis_no_values():
    assert axis_bounds([], "zero") == (0.0, 1.0)
    assert axis_bounds([None, None], "min") == (0.0, 1.0)
