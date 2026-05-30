"""推移グラフの期間フィルタ・Y軸範囲ロジック検証（PySide6 非依存・純ロジック層）。

src/utils.py の filter_by_period / axis_bounds を対象にする。表示層
（LineChartWidget の描画）はテストしないが、これらの純関数で挙動を固定する。
"""

from __future__ import annotations

from src.utils import filter_by_period, axis_bounds, spread_labels


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


# ---- spread_labels（円グラフ引き出し線ラベルの重なり回避） ----

def test_spread_empty_and_single():
    assert spread_labels([], 20.0, 0.0, 100.0) == []
    assert spread_labels([50.0], 20.0, 0.0, 100.0) == [50.0]
    # 単一要素は範囲外なら範囲内へクランプ
    assert spread_labels([-10.0], 20.0, 0.0, 100.0) == [0.0]
    assert spread_labels([150.0], 20.0, 0.0, 100.0) == [100.0]


def test_spread_keeps_order_and_min_gap():
    out = spread_labels([10.0, 12.0, 14.0], 20.0, 0.0, 400.0)
    # 入力順を保持（昇順のまま）
    assert out == sorted(out)
    # 隣接は min_gap 以上
    for a, b in zip(out, out[1:]):
        assert b - a >= 20.0 - 1e-9


def test_spread_no_change_when_already_spaced():
    out = spread_labels([50.0, 100.0, 150.0], 20.0, 0.0, 400.0)
    assert out == [50.0, 100.0, 150.0]


def test_spread_shifts_up_when_overflowing_bottom():
    # 下端付近に密集 → 上へシフトして範囲内に収める
    out = spread_labels([390.0, 395.0, 399.0], 20.0, 0.0, 400.0)
    assert out[-1] <= 400.0 + 1e-9
    for a, b in zip(out, out[1:]):
        assert b - a >= 20.0 - 1e-9


def test_spread_overflow_distributes_within_range():
    # 6個 × min_gap 30 = 180 > 範囲100 → 均等配置（全て範囲内・単調増加）
    out = spread_labels([10.0] * 6, 30.0, 0.0, 100.0)
    assert len(out) == 6
    assert out[0] >= -1e-9 and out[-1] <= 100.0 + 1e-9
    assert out == sorted(out)
