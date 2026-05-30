"""PySide6 用のカスタムウィジェットと描画ヘルパー。

- squarified treemap アルゴリズム（commit 3f4bb81 の純Python実装を移植・依存追加なし）
- 損益に応じた配色ヘルパー（日本式: プラス=赤 / マイナス=青、3段階濃淡）
- TreemapWidget: 面積比例ツリーマップ（面積 ∝ 指定額[既定=評価額]、色 = 損益率）
- PieChartWidget: 円グラフ（面積 ∝ 指定額[既定=評価額]、色 = 損益率）
- KpiCard: サマリー指標カード
"""

from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import Qt, QRectF, QPoint
from PySide6.QtGui import QColor, QPainter, QPen, QFont
from PySide6.QtWidgets import QWidget, QFrame, QLabel, QVBoxLayout, QToolTip

from .utils import fmt_number, fmt_percent, safe_float, filter_by_period, axis_bounds

# 株価未取得時などチャートが空のときに中央表示する既定メッセージ
_EMPTY_MESSAGE = "株価未取得 — 'p' で株価・配当を取得してください"


def _fmt_compact(v: float) -> str:
    """評価額を万/億単位で簡略表示する。"""
    if v >= 1e8:
        return f"{v / 1e8:.1f}億円"
    if v >= 1e4:
        return f"{v / 1e4:.0f}万円"
    return f"{v:.0f}円"


def _categorical_colors(tiles: list[dict]) -> list[QColor]:
    """銘柄ごとに色相を等間隔に割り当てたカテゴリカル配色リストを返す。
    「その他」はニュートラルグレー固定。"""
    regular_count = sum(1 for t in tiles if t.get("symbol") != "その他")
    colors: list[QColor] = []
    reg_idx = 0
    for tile in tiles:
        if tile.get("symbol") == "その他":
            colors.append(QColor("#757575"))
        else:
            hue = (reg_idx * 360 / max(regular_count, 1)) % 360
            colors.append(QColor.fromHsvF(hue / 360.0, 0.55, 0.85))
            reg_idx += 1
    return colors


def _text_color_for_bg(color: QColor) -> QColor:
    """背景色の輝度に応じて読みやすいテキスト色（暗/白）を返す。"""
    lum = 0.299 * color.redF() + 0.587 * color.greenF() + 0.114 * color.blueF()
    return QColor("#1c1c1c") if lum > 0.55 else QColor("#f5f5f5")


# ---- スクエアファイド・ツリーマップ（Bruls/Huizing/van Wijk 2000） ----
# 面積を構成比に比例させ、各矩形がなるべく正方形になるよう配置する。
# 戻り値の矩形は入力 sizes と同じ順序。入力 sizes の総和が dx*dy になるよう
# 呼び出し側で正規化しておくこと（領域がちょうど埋まる）。


def _tm_layout(sizes: list[float], x: float, y: float, dx: float, dy: float):
    rects = []
    covered = sum(sizes)
    if dx >= dy:  # 行（横長領域）に縦積み
        width = covered / dy if dy else 0.0
        cy = y
        for s in sizes:
            h = s / width if width else 0.0
            rects.append((x, cy, width, h))
            cy += h
    else:  # 列（縦長領域）に横並び
        height = covered / dx if dx else 0.0
        cx = x
        for s in sizes:
            w = s / height if height else 0.0
            rects.append((cx, y, w, height))
            cx += w
    return rects


def _tm_leftover(sizes: list[float], x: float, y: float, dx: float, dy: float):
    covered = sum(sizes)
    if dx >= dy:
        width = covered / dy if dy else 0.0
        return (x + width, y, dx - width, dy)
    height = covered / dx if dx else 0.0
    return (x, y + height, dx, dy - height)


def _tm_worst(sizes: list[float], dx: float, dy: float) -> float:
    rects = _tm_layout(sizes, 0.0, 0.0, dx, dy)
    ratios = [max(w / h, h / w) for (_, _, w, h) in rects if w > 0 and h > 0]
    return max(ratios) if ratios else float("inf")


def _squarify(sizes: list[float], x: float, y: float, dx: float, dy: float):
    sizes = [float(s) for s in sizes]
    if not sizes:
        return []
    if len(sizes) == 1:
        return _tm_layout(sizes, x, y, dx, dy)
    i = 1
    while i < len(sizes) and _tm_worst(sizes[:i], dx, dy) >= _tm_worst(sizes[: i + 1], dx, dy):
        i += 1
    current, remaining = sizes[:i], sizes[i:]
    return _tm_layout(current, x, y, dx, dy) + _squarify(
        remaining, *_tm_leftover(current, x, y, dx, dy)
    )


# ---- 配色ヘルパー（既存 DividendApp._heatmap_style / profit_style を移植） ----

_BORDER = QColor("#7f7f7f")          # grey50 相当（タイル枠線）
_NEUTRAL_BG = QColor("#3a3a3a")      # grey23 相当
_WEAK_BG = QColor("#262626")         # grey15 相当
_RED_TEXT = QColor("#e53935")
_BLUE_TEXT = QColor("#42a5f5")
_WHITE = QColor("#f5f5f5")


def profit_tile_colors(profit_rate: float | None) -> tuple[QColor, QColor]:
    """損益率に応じたタイルの (背景色, 文字色) を3段階で返す（日本式: +赤 / -青）。"""
    rate = safe_float(profit_rate)
    if rate is None or rate == 0:
        return _NEUTRAL_BG, _WHITE
    magnitude = abs(rate)
    if rate > 0:
        if magnitude >= 0.05:
            return QColor("#c62828"), _WHITE
        if magnitude >= 0.02:
            return QColor("#8e2424"), _WHITE
        return _WEAK_BG, _RED_TEXT
    if magnitude >= 0.05:
        return QColor("#1565c0"), _WHITE
    if magnitude >= 0.02:
        return QColor("#1a3a6b"), _WHITE
    return _WEAK_BG, _BLUE_TEXT


def profit_text_qcolor(value: float | None) -> Optional[QColor]:
    """損益値に応じたテキスト色（プラス=赤 / マイナス=青 / それ以外=既定色 None）。"""
    num = safe_float(value)
    if num is None or num == 0:
        return None
    return QColor("#d32f2f") if num > 0 else QColor("#1976d2")


class TreemapWidget(QWidget):
    """面積比例ツリーマップ。面積 ∝ value_key（既定=market_value_jpy）、色 = profit_rate。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tiles: list[dict] = []
        self._total: float = 0.0
        self._rects: list[tuple[QRectF, dict]] = []
        self._value_key: str = "market_value_jpy"
        self._value_label: str = "評価額"
        self._empty_message: str = _EMPTY_MESSAGE
        self.setMinimumSize(420, 320)
        self.setMouseTracking(True)
        self.setAutoFillBackground(True)

    def set_data(
        self,
        rows: list[dict],
        value_key: str = "market_value_jpy",
        value_label: str = "評価額",
        empty_message: str | None = None,
    ) -> None:
        """compute_rows の行から value_key が正の銘柄を抽出して保持する。

        面積は value_key（既定=評価額）に比例。色は損益率（profit_rate）。
        """
        self._value_key = value_key
        self._value_label = value_label
        self._empty_message = empty_message or _EMPTY_MESSAGE
        tiles = []
        for r in rows:
            v = safe_float(r.get(value_key))
            if v is not None and v > 0:
                tiles.append(r)
        tiles.sort(key=lambda r: float(r[value_key]), reverse=True)
        self._tiles = tiles
        self._total = sum(float(t[value_key]) for t in tiles)
        self._rects = []
        self.update()

    # ---- 描画 ----
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        rect = self.rect()
        painter.fillRect(rect, QColor("#1c1c1c"))

        if not self._tiles or self._total <= 0:
            painter.setPen(QPen(QColor("#9e9e9e")))
            painter.drawText(
                rect,
                Qt.AlignmentFlag.AlignCenter,
                self._empty_message,
            )
            painter.end()
            return

        w = float(rect.width())
        h = float(rect.height())
        area = w * h
        scaled = [float(t[self._value_key]) / self._total * area for t in self._tiles]
        layout = _squarify(scaled, 0.0, 0.0, w, h)

        self._rects = []
        small_font = QFont(self.font())
        for tile, (rx, ry, rw, rh) in zip(self._tiles, layout):
            if rw <= 0 or rh <= 0:
                continue
            qrect = QRectF(rx, ry, rw, rh)
            self._rects.append((qrect, tile))

            bg, fg = profit_tile_colors(tile.get("profit_rate"))
            painter.fillRect(qrect, bg)
            painter.setPen(QPen(_BORDER, 1))
            painter.drawRect(qrect.adjusted(0.5, 0.5, -0.5, -0.5))

            if rw >= 56 and rh >= 34:  # ラベルが収まる程度の矩形だけ描画
                symbol = str(tile.get("symbol", ""))
                name = str(tile.get("name") or "")
                mv_str = _fmt_compact(float(tile[self._value_key]))
                painter.setPen(QPen(fg))
                bold = QFont(small_font)
                bold.setBold(True)
                if rh >= 60 and name:
                    # 3行: シンボル / 銘柄名 / 評価額
                    third = rh / 3.0
                    painter.setFont(bold)
                    painter.drawText(
                        qrect.adjusted(2, 2, -2, -(rh * 2.0 / 3.0)),
                        Qt.AlignmentFlag.AlignCenter,
                        symbol,
                    )
                    painter.setFont(small_font)
                    name_rect = qrect.adjusted(2, third, -2, -third)
                    # タイル幅に収まれば全文表示、超える場合のみ末尾を「…」省略する
                    elided_name = painter.fontMetrics().elidedText(
                        name, Qt.TextElideMode.ElideRight, int(name_rect.width())
                    )
                    painter.drawText(
                        name_rect, Qt.AlignmentFlag.AlignCenter, elided_name
                    )
                    painter.drawText(
                        qrect.adjusted(2, rh * 2.0 / 3.0, -2, -2),
                        Qt.AlignmentFlag.AlignCenter,
                        mv_str,
                    )
                else:
                    # 2行: シンボル / 評価額
                    painter.setFont(bold)
                    painter.drawText(
                        qrect.adjusted(2, 2, -2, -rh / 2),
                        Qt.AlignmentFlag.AlignCenter,
                        symbol,
                    )
                    painter.setFont(small_font)
                    painter.drawText(
                        qrect.adjusted(2, rh / 2, -2, -2),
                        Qt.AlignmentFlag.AlignCenter,
                        mv_str,
                    )
        painter.end()

    # ---- ホバー: ツールチップで詳細表示 ----
    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt API)
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        tile = self._tile_at(pos)
        if tile is None:
            QToolTip.hideText()
        else:
            name = tile.get("name") or ""
            mv = fmt_number(tile.get(self._value_key), 0)
            pr = fmt_percent(tile.get("profit_rate"), 2)
            text = (
                f"{tile.get('symbol', '')}  {name}\n"
                f"{self._value_label}: {mv} 円\n"
                f"損益率: {pr}"
            )
            QToolTip.showText(event.globalPosition().toPoint(), text, self)
        super().mouseMoveEvent(event)

    def _tile_at(self, pos: QPoint) -> dict | None:
        for qrect, tile in self._rects:
            if qrect.contains(pos):
                return tile
        return None


class PieChartWidget(QWidget):
    """構成比の円グラフ。面積 ∝ value_key（既定=market_value_jpy）、色 = 銘柄ごと固有色（時計回り）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tiles: list[dict] = []
        self._total: float = 0.0
        self._threshold_pct: float = 2.0  # この%未満のセグメントを「その他」に集約
        self._segments: list[tuple[float, float, dict]] = []  # (start_deg, span_deg, tile)
        self._value_key: str = "market_value_jpy"
        self._value_label: str = "評価額"
        self._empty_message: str = _EMPTY_MESSAGE
        self.setMinimumSize(420, 320)
        self.setMouseTracking(True)
        self.setAutoFillBackground(True)

    def set_data(
        self,
        rows: list[dict],
        value_key: str = "market_value_jpy",
        value_label: str = "評価額",
        empty_message: str | None = None,
    ) -> None:
        self._value_key = value_key
        self._value_label = value_label
        self._empty_message = empty_message or _EMPTY_MESSAGE
        tiles = []
        for r in rows:
            v = safe_float(r.get(value_key))
            if v is not None and v > 0:
                tiles.append(r)
        tiles.sort(key=lambda r: float(r[value_key]), reverse=True)
        self._tiles = tiles
        self._total = sum(float(t[value_key]) for t in tiles)
        self._segments = []
        self.update()

    def set_threshold(self, pct: float) -> None:
        """その他にまとめる閾値（%）を設定して再描画する。"""
        self._threshold_pct = max(0.0, pct)
        self.update()

    def _build_display_tiles(self) -> list[dict]:
        """閾値未満のセグメントを「その他」に集約した描画用リストを返す。"""
        if self._total <= 0:
            return list(self._tiles)
        main: list[dict] = []
        others: list[dict] = []
        for tile in self._tiles:
            pct = float(tile[self._value_key]) / self._total * 100.0
            if self._threshold_pct > 0 and pct < self._threshold_pct:
                others.append(tile)
            else:
                main.append(tile)
        if others:
            other_mv = sum(float(t[self._value_key]) for t in others)
            pr_sum = sum(
                float(t[self._value_key]) * (safe_float(t.get("profit_rate")) or 0.0)
                for t in others
            )
            other_pr = pr_sum / other_mv if other_mv > 0 else None
            main.append({
                "symbol": "その他",
                "name": f"({len(others)}銘柄)",
                self._value_key: other_mv,
                "profit_rate": other_pr,
            })
        return main

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect()
        painter.fillRect(rect, QColor("#1c1c1c"))

        if not self._tiles or self._total <= 0:
            painter.setPen(QPen(QColor("#9e9e9e")))
            painter.drawText(
                rect,
                Qt.AlignmentFlag.AlignCenter,
                self._empty_message,
            )
            painter.end()
            return

        display_tiles = self._build_display_tiles()
        w = float(rect.width())
        h = float(rect.height())
        r = min(w, h) / 2.0 - 20.0
        cx = w / 2.0
        cy = h / 2.0
        pie_rect = QRectF(cx - r, cy - r, r * 2, r * 2)

        small_font = QFont(self.font())
        small_font.setPointSize(max(7, self.font().pointSize() - 1))
        bold_font = QFont(small_font)
        bold_font.setBold(True)

        # 扇形描画: 90°（12時）から時計回り、銘柄ごとカテゴリカル配色
        colors = _categorical_colors(display_tiles)
        self._segments = []
        current = 90.0
        for i, tile in enumerate(display_tiles):
            span = float(tile[self._value_key]) / self._total * 360.0
            painter.setBrush(colors[i])
            painter.setPen(QPen(QColor("#1c1c1c"), 1.5))
            painter.drawPie(pie_rect, int(current * 16), -int(span * 16))  # 負値 = 時計回り
            self._segments.append((current, span, tile))
            current -= span  # 時計回り = 角度を減算

        # ラベル描画
        for i, (start, span, tile) in enumerate(self._segments):
            if span < 5.0:
                continue
            mid_rad = math.radians(start - span / 2.0)  # 時計回りの中点
            label_r = r * 0.65
            lx = cx + label_r * math.cos(mid_rad)
            ly = cy - label_r * math.sin(mid_rad)

            fg = _text_color_for_bg(colors[i])
            painter.setPen(QPen(fg))

            symbol = str(tile.get("symbol", ""))
            name = str(tile.get("name") or "")
            mv_str = _fmt_compact(float(tile[self._value_key]))

            if span >= 20.0:
                painter.setFont(bold_font)
                painter.drawText(
                    QRectF(lx - 50, ly - 19, 100, 14),
                    Qt.AlignmentFlag.AlignCenter,
                    symbol,
                )
                painter.setFont(small_font)
                name_rect = QRectF(lx - 50, ly - 6, 100, 13)
                # ラベル枠に収まれば全文表示、超える場合のみ末尾を「…」省略する
                elided_name = painter.fontMetrics().elidedText(
                    name, Qt.TextElideMode.ElideRight, int(name_rect.width())
                )
                painter.drawText(name_rect, Qt.AlignmentFlag.AlignCenter, elided_name)
                painter.drawText(
                    QRectF(lx - 50, ly + 7, 100, 13),
                    Qt.AlignmentFlag.AlignCenter,
                    mv_str,
                )
            elif span >= 10.0:
                painter.setFont(bold_font)
                painter.drawText(
                    QRectF(lx - 35, ly - 10, 70, 13),
                    Qt.AlignmentFlag.AlignCenter,
                    symbol,
                )
                painter.setFont(small_font)
                painter.drawText(
                    QRectF(lx - 35, ly + 3, 70, 13),
                    Qt.AlignmentFlag.AlignCenter,
                    mv_str,
                )
            else:
                painter.setFont(small_font)
                painter.drawText(
                    QRectF(lx - 30, ly - 8, 60, 16),
                    Qt.AlignmentFlag.AlignCenter,
                    symbol,
                )

        painter.end()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        tile = self._tile_at(pos)
        if tile is None:
            QToolTip.hideText()
        else:
            name = tile.get("name") or ""
            mv = fmt_number(tile.get(self._value_key), 0)
            weight = float(tile[self._value_key]) / self._total * 100.0 if self._total else 0
            text = (
                f"{tile.get('symbol', '')}  {name}\n"
                f"{self._value_label}: {mv} 円（{weight:.1f}%）"
            )
            QToolTip.showText(event.globalPosition().toPoint(), text, self)
        super().mouseMoveEvent(event)

    def _tile_at(self, pos: QPoint) -> dict | None:
        if not self._segments:
            return None
        w = float(self.rect().width())
        h = float(self.rect().height())
        r = min(w, h) / 2.0 - 20.0
        cx = w / 2.0
        cy = h / 2.0

        dx = pos.x() - cx
        dy = -(pos.y() - cy)  # Qt y軸反転
        if math.sqrt(dx * dx + dy * dy) > r:
            return None

        angle = math.degrees(math.atan2(dy, dx))
        if angle < 0:
            angle += 360.0

        for start, span, tile in self._segments:
            s = start % 360.0           # 扇形の開始角（時計回り起点）
            e = (start - span) % 360.0  # 扇形の終了角（時計回り終点）
            if e <= s:  # 通常ケース: 0°/360°をまたがない
                if e <= angle <= s:
                    return tile
            else:  # e > s: 0°/360°をまたぐ扇形
                if angle >= e or angle <= s:
                    return tile
        return None


class LineChartWidget(QWidget):
    """日次スナップショットの推移を描く折れ線グラフ（純 QPainter・依存追加なし）。

    面積比例チャート群（treemap/pie）と同じく外部ライブラリを使わず、
    起動パスに重量級 import を持ち込まない方針に従う。

    set_data(records, series):
      records … 時系列順の dict のリスト。各要素に "date"（"YYYY-MM-DD"）と
                各系列の数値キーを持つ。値が None の点は線を分断する。
      series  … (ラベル, キー, 線色 QColor) のタプルのリスト。
    """

    _MARGIN_LEFT = 64
    _MARGIN_RIGHT = 16
    _MARGIN_TOP = 28
    _MARGIN_BOTTOM = 34

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._records: list[dict] = []      # 全期間の元データ
        self._series: list[tuple[str, str, QColor]] = []
        self._empty_message: str = "履歴データがありません（株価を更新すると記録されます）"
        self._period: str = "all"           # 表示期間 all/10y/5y/3y/1y
        self._baseline: str = "zero"        # Y 軸下限 zero/min
        self._visible: list[dict] = []      # 期間フィルタ後の描画対象
        self._floor: float = 0.0            # Y 軸下限値
        self._top: float = 1.0              # Y 軸上限値
        self._hover_idx: int | None = None
        self.setMinimumSize(420, 320)
        self.setMouseTracking(True)
        self.setAutoFillBackground(True)

    def set_data(
        self,
        records: list[dict],
        series: list[tuple[str, str, QColor]],
        empty_message: str | None = None,
    ) -> None:
        # 期間・底辺モードは保持し、データのみ差し替える（再描画時に再計算）。
        self._records = list(records)
        self._series = list(series)
        if empty_message is not None:
            self._empty_message = empty_message
        self._hover_idx = None
        self._recompute()
        self.update()

    def set_period(self, period_key: str) -> None:
        """表示期間（all/10y/5y/3y/1y）を設定して再描画する。"""
        self._period = period_key
        self._hover_idx = None
        self._recompute()
        self.update()

    def set_baseline(self, mode: str) -> None:
        """Y 軸下限モード（zero/min）を設定して再描画する。"""
        self._baseline = mode
        self._recompute()
        self.update()

    def _recompute(self) -> None:
        """期間フィルタと Y 軸範囲（_visible / _floor / _top）を再計算する。"""
        self._visible = filter_by_period(self._records, self._period)
        values: list[float | None] = []
        for rec in self._visible:
            for _label, key, _color in self._series:
                values.append(safe_float(rec.get(key)))
        self._floor, self._top = axis_bounds(values, self._baseline)
        if self._hover_idx is not None and self._hover_idx >= len(self._visible):
            self._hover_idx = None

    # ---- 座標変換 ----
    def _plot_rect(self) -> QRectF:
        rect = self.rect()
        return QRectF(
            self._MARGIN_LEFT,
            self._MARGIN_TOP,
            max(1.0, rect.width() - self._MARGIN_LEFT - self._MARGIN_RIGHT),
            max(1.0, rect.height() - self._MARGIN_TOP - self._MARGIN_BOTTOM),
        )

    def _x_for(self, idx: int, plot: QRectF) -> float:
        n = len(self._visible)
        if n <= 1:
            return plot.left() + plot.width() / 2.0
        return plot.left() + plot.width() * idx / (n - 1)

    def _y_for(self, value: float, plot: QRectF) -> float:
        span = self._top - self._floor
        if span <= 0:
            return plot.bottom()
        return plot.bottom() - plot.height() * ((value - self._floor) / span)

    # ---- 描画 ----
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect()
        painter.fillRect(rect, QColor("#1c1c1c"))

        if not self._visible or not self._series or self._top <= self._floor:
            painter.setPen(QPen(QColor("#9e9e9e")))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._empty_message)
            painter.end()
            return

        plot = self._plot_rect()
        small_font = QFont(self.font())
        small_font.setPointSize(max(7, self.font().pointSize() - 1))

        # 横グリッド線 + Y 軸ラベル（下限 〜 上限を 4 分割）
        painter.setFont(small_font)
        for i in range(5):
            frac = i / 4.0
            y = plot.bottom() - plot.height() * frac
            painter.setPen(QPen(QColor("#2f2f2f"), 1))
            painter.drawLine(int(plot.left()), int(y), int(plot.right()), int(y))
            painter.setPen(QPen(QColor("#9e9e9e")))
            painter.drawText(
                QRectF(0, y - 8, self._MARGIN_LEFT - 6, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                _fmt_compact(self._floor + (self._top - self._floor) * frac),
            )

        # X 軸ラベル（最大 5 点の日付を等間隔に）
        n = len(self._visible)
        painter.setPen(QPen(QColor("#9e9e9e")))
        label_count = min(5, n)
        for j in range(label_count):
            idx = 0 if label_count == 1 else round(j * (n - 1) / (label_count - 1))
            x = self._x_for(idx, plot)
            date = str(self._visible[idx].get("date", ""))
            painter.drawText(
                QRectF(x - 44, plot.bottom() + 4, 88, 16),
                Qt.AlignmentFlag.AlignCenter,
                date,
            )

        # 各系列の折れ線（None 点で分断）
        for label, key, color in self._series:
            pen = QPen(color, 2)
            painter.setPen(pen)
            prev: tuple[float, float] | None = None
            for idx, rec in enumerate(self._visible):
                v = safe_float(rec.get(key))
                if v is None:
                    prev = None
                    continue
                x = self._x_for(idx, plot)
                y = self._y_for(v, plot)
                if prev is not None:
                    painter.drawLine(int(prev[0]), int(prev[1]), int(x), int(y))
                prev = (x, y)

        # ホバー: 縦カーソル線 + 該当点マーカー
        if self._hover_idx is not None and 0 <= self._hover_idx < n:
            hx = self._x_for(self._hover_idx, plot)
            painter.setPen(QPen(QColor("#6f6f6f"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(int(hx), int(plot.top()), int(hx), int(plot.bottom()))
            rec = self._visible[self._hover_idx]
            for _label, key, color in self._series:
                v = safe_float(rec.get(key))
                if v is None:
                    continue
                hy = self._y_for(v, plot)
                painter.setBrush(color)
                painter.setPen(QPen(QColor("#1c1c1c"), 1))
                painter.drawEllipse(QRectF(hx - 3.5, hy - 3.5, 7, 7))

        # 凡例（右上）
        legend_x = plot.right() - 8
        legend_y = plot.top() + 4
        painter.setFont(small_font)
        for label, _key, color in self._series:
            text_w = painter.fontMetrics().horizontalAdvance(label)
            sw = 16  # 色見本の幅
            row_w = sw + 4 + text_w
            lx = legend_x - row_w
            painter.setPen(QPen(color, 2))
            painter.drawLine(int(lx), int(legend_y + 7), int(lx + sw), int(legend_y + 7))
            painter.setPen(QPen(QColor("#d0d0d0")))
            painter.drawText(
                QRectF(lx + sw + 4, legend_y, text_w + 4, 14),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                label,
            )
            legend_y += 16

        painter.end()

    # ---- ホバー: 最寄りの日付の値をツールチップ表示 ----
    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt API)
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        idx = self._index_at(pos.x())
        if idx is None:
            if self._hover_idx is not None:
                self._hover_idx = None
                self.update()
            QToolTip.hideText()
            super().mouseMoveEvent(event)
            return
        if idx != self._hover_idx:
            self._hover_idx = idx
            self.update()
        rec = self._visible[idx]
        lines = [str(rec.get("date", ""))]
        for label, key, _color in self._series:
            v = safe_float(rec.get(key))
            if v is not None:
                lines.append(f"{label}: {fmt_number(v, 0)} 円")
        QToolTip.showText(event.globalPosition().toPoint(), "\n".join(lines), self)
        super().mouseMoveEvent(event)

    def _index_at(self, px: float) -> int | None:
        if not self._visible:
            return None
        plot = self._plot_rect()
        if px < plot.left() - 12 or px > plot.right() + 12:
            return None
        n = len(self._visible)
        if n <= 1:
            return 0
        frac = (px - plot.left()) / plot.width()
        idx = round(frac * (n - 1))
        return max(0, min(n - 1, idx))


class KpiCard(QFrame):
    """サマリー指標を1つ表示する小カード（タイトル＋値）。"""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("kpiCard")
        self.setStyleSheet(
            "#kpiCard { background: #2a2a2a; border: 1px solid #3f3f3f; "
            "border-radius: 6px; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(2)
        self._title = QLabel(title)
        self._title.setStyleSheet("color: #9e9e9e; font-size: 11px;")
        self._value = QLabel("—")
        self._value.setStyleSheet("color: #f5f5f5; font-size: 16px; font-weight: bold;")
        layout.addWidget(self._title)
        layout.addWidget(self._value)

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def set_value(self, text: str, color: QColor | None = None) -> None:
        self._value.setText(text)
        hex_color = color.name() if color is not None else "#f5f5f5"
        self._value.setStyleSheet(
            f"color: {hex_color}; font-size: 16px; font-weight: bold;"
        )
