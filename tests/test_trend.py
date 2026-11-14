"""推移グラフの期間フィルタ・Y軸範囲ロジック検証（PySide6 非依存・純ロジック層）。

src/utils.py の filter_by_period / axis_bounds を対象にする。表示層
（LineChartWidget の描画）はテストしないが、これらの純関数で挙動を固定する。
"""

from __future__ import annotations

from src.utils import (
    filter_by_period,
    axis_bounds,
    spread_labels,
    date_x_positions,
    nearest_x_index,
    date_axis_ticks,
    clamp_label_left,
)


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


# ---- 推移グラフ X 軸座標変換・最近傍・目盛（X5） ----


def test_date_x_positions_empty_and_single():
    assert date_x_positions([], 10.0, 200.0) == []
    # 単一点はプロット領域の中央に配置
    assert date_x_positions(["2026-01-01"], 10.0, 200.0) == [110.0]
    # 全点同日もゼロ割せず中央に配置
    assert date_x_positions(["2026-01-01", "2026-01-01"], 10.0, 200.0) == [110.0, 110.0]


def test_date_x_positions_consecutive_days():
    xs = date_x_positions(
        ["2026-01-01", "2026-01-02", "2026-01-03"], left=0.0, width=100.0
    )
    assert xs == [0.0, 50.0, 100.0]


def test_date_x_positions_uneven_intervals_proportional_to_days():
    """不等間隔の日付で、件数等分ではなく経過日数に比例した X 座標になること。"""
    # 2026-01-01 → 2026-01-11（+10日）→ 2026-04-11（+100日）
    xs = date_x_positions(
        ["2026-01-01", "2026-01-11", "2026-04-11"], left=50.0, width=400.0
    )
    assert xs[0] == 50.0
    assert abs(xs[1] - (50.0 + 400.0 * 0.1)) < 1e-9  # 件数等分なら 250.0 になる箇所
    assert xs[2] == 450.0


def test_nearest_x_index_uses_same_coordinate_array():
    """不等間隔の X 座標配列に対して、実際の最近傍インデックスを返すこと。"""
    xs = date_x_positions(
        ["2026-01-01", "2026-01-11", "2026-04-11"], left=0.0, width=1000.0
    )
    # xs == [0.0, 100.0, 1000.0]
    assert nearest_x_index(xs, -20.0, 0.0, 1000.0) is None
    assert nearest_x_index(xs, 1020.0, 0.0, 1000.0) is None
    assert nearest_x_index(xs, -10.0, 0.0, 1000.0) == 0  # margin=12 の内側
    assert nearest_x_index(xs, 40.0, 0.0, 1000.0) == 0
    assert nearest_x_index(xs, 60.0, 0.0, 1000.0) == 1
    # 中央 (500.0) は x=100 の点 (idx=1) の方が x=1000 (idx=2) より近い
    assert nearest_x_index(xs, 500.0, 0.0, 1000.0) == 1
    assert nearest_x_index(xs, 600.0, 0.0, 1000.0) == 2


def test_date_axis_ticks_proportional_to_calendar_days():
    """データ点が偏っていても、X 軸目盛は暦日基準で等間隔になること。"""
    # 前半に点が密集し後半が空いている 100 日間（2026-01-01 〜 2026-04-11）
    dates = ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-04-11"]
    ticks = date_axis_ticks(dates, left=0.0, width=400.0, max_ticks=5)
    assert len(ticks) == 5
    assert ticks[0] == (0.0, "2026-01-01")
    assert ticks[1] == (100.0, "2026-01-26")  # +25日
    assert ticks[2] == (200.0, "2026-02-20")  # +50日
    assert ticks[3] == (300.0, "2026-03-17")  # +75日
    assert ticks[4] == (400.0, "2026-04-11")  # +100日

    # 単一点・短期間（2日間）でも重複ラベルを出さない
    assert date_axis_ticks(["2026-01-01"], 0.0, 400.0) == [(200.0, "2026-01-01")]
    two_days = date_axis_ticks(["2026-01-01", "2026-01-02"], 0.0, 400.0, max_ticks=5)
    assert two_days == [(0.0, "2026-01-01"), (400.0, "2026-01-02")]


# ---- clamp_label_left（X 軸ラベルをウィジェット内へ収める） ----


def test_clamp_label_left_keeps_center_ticks_centered():
    # 中央の目盛は中心揃えのまま（幅 88 → 左端は中心 − 44）。
    assert clamp_label_left(300.0, 88.0, 0.0, 600.0) == 256.0


def test_clamp_label_left_pulls_right_edge_tick_inside():
    # プロット右端（右余白 16px → x=584）のラベルは右へはみ出すので内側へ寄せる。
    left = clamp_label_left(584.0, 88.0, 0.0, 600.0)
    assert left == 600.0 - 88.0
    assert left + 88.0 <= 600.0


def test_clamp_label_left_pulls_left_edge_tick_inside():
    assert clamp_label_left(10.0, 88.0, 0.0, 600.0) == 0.0


def test_clamp_label_left_prefers_left_when_label_wider_than_range():
    assert clamp_label_left(50.0, 88.0, 0.0, 60.0) == 0.0
