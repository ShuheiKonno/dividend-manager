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

from .utils import fmt_number, fmt_percent, safe_float

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
