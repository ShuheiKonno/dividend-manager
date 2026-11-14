"""PySide6 用のカスタムウィジェットと描画ヘルパー。

- squarified treemap アルゴリズム（commit 3f4bb81 の純Python実装を移植・依存追加なし）
- 損益に応じた配色ヘルパー（日本式: プラス=赤 / マイナス=青、3段階濃淡）
- TreemapWidget: 面積比例ツリーマップ（面積 ∝ 指定額[既定=評価額]、色 = 損益率）
- PieChartWidget: 円グラフ（面積 ∝ 指定額[既定=評価額]、色 = 損益率）
- BarChartWidget: 月別の積み上げ棒グラフ（クリックで月を選択）
- KpiCard: サマリー指標カード
"""

from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import Qt, QRectF, QPoint, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QFont
from PySide6.QtWidgets import (
    QWidget,
    QFrame,
    QLabel,
    QVBoxLayout,
    QSizePolicy,
    QToolTip,
)

# 配色はテーマ切替で張り替わる「現在値」なので、値で import せず描画時に
# theme.X で読む（値 import すると切替後も旧テーマの色を描き続ける）。
from . import theme
from .utils import (
    fmt_number,
    fmt_percent,
    safe_float,
    filter_by_period,
    axis_bounds,
    spread_labels,
    date_x_positions,
    nearest_x_index,
    date_axis_ticks,
)

# チャートの最小サイズ。ウィンドウの最小高さ（900x600）に収まる値にしてある
# （実フォントで全体の最小の高さが 597px。170→180 / 160→170 にすると 607px で溢れる）。
# 大きくすると、縮めたウィンドウでチャートの下側が切れ、月別タブの注記が棒に重なる。
_CHART_MIN_SIZE = (320, 170)
_BAR_CHART_MIN_SIZE = (360, 160)

# 株価未取得時などチャートが空のときに中央表示する既定メッセージ
_EMPTY_MESSAGE = "株価未取得 — F5 で株価・配当を取得してください"


def _fmt_compact(v: float) -> str:
    """評価額を万/億単位で簡略表示する。"""
    if v >= 1e8:
        return f"{v / 1e8:.1f}億円"
    if v >= 1e4:
        return f"{v / 1e4:.0f}万円"
    return f"{v:.0f}円"


def _label_text(tile: dict) -> str:
    """円グラフの引き出し線ラベル1行分（「銘柄名(コード)」／集約行は「その他(N銘柄)」）。"""
    symbol = str(tile.get("symbol", ""))
    name = str(tile.get("name") or "")
    if tile.get("is_other"):
        return f"{symbol}{name}"
    if not name:
        return symbol
    return f"{name}({symbol})"


def _categorical_colors(tiles: list[dict]) -> list[QColor]:
    """銘柄ごとに色相を等間隔に割り当てたカテゴリカル配色リストを返す。
    「その他」はニュートラルグレー固定。"""
    regular_count = sum(1 for t in tiles if t.get("symbol") != "その他")
    colors: list[QColor] = []
    reg_idx = 0
    for tile in tiles:
        if tile.get("symbol") == "その他":
            colors.append(QColor(theme.CATEGORICAL_OTHER))
        else:
            hue = (reg_idx * 360 / max(regular_count, 1)) % 360
            colors.append(QColor.fromHsvF(hue / 360.0, 0.55, 0.85))
            reg_idx += 1
    return colors


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
# QColor はテーマ切替のたびに作り直す必要があるため、モジュール定数として
# 持たず必ず関数の中で theme から作る。


def _readable_text_color(bg: QColor) -> QColor:
    """塗りの上に文字を重ねるとき、背景色の明るさで明/暗を切り替えて返す。

    カテゴリカル配色は色相を一周するため、白固定にすると黄〜シアンのスライスで
    構成比の数字が読めなくなる（実測: 黄 0.80 / シアン 0.71 / 赤 0.52）。
    ライトテーマの淡いタイル（TILE_*_MID / TILE_NEUTRAL_BG）もこの判定で
    暗い文字へ切り替わる。
    """
    luminance = 0.299 * bg.redF() + 0.587 * bg.greenF() + 0.114 * bg.blueF()
    return QColor(theme.ON_FILL_DARK if luminance > 0.62 else theme.ON_FILL_LIGHT)


def profit_tile_colors(profit_rate: float | None) -> tuple[QColor, QColor]:
    """損益率に応じたタイルの (背景色, 文字色) を3段階で返す（日本式: +赤 / -青）。

    文字色は背景の明るさから決める（_readable_text_color）。塗りの濃さが
    テーマで反転するため、白固定にするとライトで読めなくなる。
    """
    rate = safe_float(profit_rate)
    if rate is None or rate == 0:
        return _with_readable_text(theme.TILE_NEUTRAL_BG)
    magnitude = abs(rate)
    if rate > 0:
        if magnitude >= 0.05:
            return _with_readable_text(theme.TILE_UP_STRONG)
        if magnitude >= 0.02:
            return _with_readable_text(theme.TILE_UP_MID)
        return QColor(theme.TILE_WEAK_BG), QColor(theme.PROFIT_UP)
    if magnitude >= 0.05:
        return _with_readable_text(theme.TILE_DOWN_STRONG)
    if magnitude >= 0.02:
        return _with_readable_text(theme.TILE_DOWN_MID)
    return QColor(theme.TILE_WEAK_BG), QColor(theme.PROFIT_DOWN)


def _with_readable_text(bg_token: str) -> tuple[QColor, QColor]:
    bg = QColor(bg_token)
    return bg, _readable_text_color(bg)


def profit_text_qcolor(value: float | None) -> Optional[QColor]:
    """損益値に応じたテキスト色（プラス=赤 / マイナス=青 / それ以外=既定色 None）。

    treemap のタイル文字色と同じ theme トークンを使う（従来は表だけ濃い
    #d32f2f / #1976d2 で、ダーク背景に対するコントラストが不足していた）。
    """
    num = safe_float(value)
    if num is None or num == 0:
        return None
    return QColor(theme.PROFIT_UP if num > 0 else theme.PROFIT_DOWN)


class TreemapWidget(QWidget):
    """面積比例ツリーマップ。面積 ∝ value_key（既定=market_value_jpy）、色 = profit_rate。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tiles: list[dict] = []
        self._total: float = 0.0
        self._rects: list[tuple[QRectF, dict]] = []
        self._hover_tile: dict | None = None  # マウスホバー中のタイル（強調表示用）
        self._value_key: str = "market_value_jpy"
        self._value_label: str = "評価額"
        self._empty_message: str = _EMPTY_MESSAGE
        self.setMinimumSize(*_CHART_MIN_SIZE)
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
        self._hover_tile = None  # データ差し替え時に古いタイル参照を残さない
        self.update()

    # ---- 描画 ----
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        rect = self.rect()
        painter.fillRect(rect, QColor(theme.CHART_BG))

        if not self._tiles or self._total <= 0:
            painter.setPen(QPen(QColor(theme.CHART_AXIS_TEXT)))
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
        hover_rect: QRectF | None = None
        small_font = QFont(self.font())
        for tile, (rx, ry, rw, rh) in zip(self._tiles, layout):
            if rw <= 0 or rh <= 0:
                continue
            qrect = QRectF(rx, ry, rw, rh)
            self._rects.append((qrect, tile))

            bg, fg = profit_tile_colors(tile.get("profit_rate"))
            # ホバー中のタイルは背景をわずかに明るく（色決定はヘルパー経由を維持）。
            if tile is self._hover_tile:
                bg = bg.lighter(115)
                hover_rect = qrect
            painter.fillRect(qrect, bg)
            painter.setPen(QPen(QColor(theme.TILE_BORDER), 1))
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
        # ホバー枠は隣接タイルの枠線に潰されないよう、全タイル描画後に上描きする。
        if hover_rect is not None:
            painter.setPen(QPen(QColor(theme.TILE_HOVER_OUTLINE), 2))
            painter.drawRect(hover_rect.adjusted(1, 1, -1, -1))
        painter.end()

    # ---- ホバー: タイル強調＋ツールチップで詳細表示 ----
    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt API)
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        tile = self._tile_at(pos)
        if tile is not self._hover_tile:
            self._hover_tile = tile
            self.update()
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

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if self._hover_tile is not None:
            self._hover_tile = None
            self.update()
        QToolTip.hideText()
        super().leaveEvent(event)

    def _tile_at(self, pos: QPoint) -> dict | None:
        for qrect, tile in self._rects:
            if qrect.contains(pos):
                return tile
        return None


class PieChartWidget(QWidget):
    """構成比のドーナツグラフ。面積 ∝ value_key（既定=market_value_jpy）、色 = 銘柄ごと固有色（時計回り）。

    中央の穴には合計（評価額ベースなら損益も）を出す。構成比(%)はスライス内、
    銘柄名は引き出し線で円外に置く。
    """

    _LABEL_BAND = 190.0
    _MIN_RADIUS = 30.0
    _RADIUS_SCALE = 0.92
    _HOLE_RATIO = 0.60  # 中央に合計を置くための内径比（外半径に対する割合）

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tiles: list[dict] = []
        self._total: float = 0.0
        self._threshold_pct: float = 2.0  # この%未満のセグメントを「その他」に集約
        self._segments: list[tuple[float, float, dict]] = []  # (start_deg, span_deg, tile)
        self._value_key: str = "market_value_jpy"
        self._value_label: str = "評価額"
        self._empty_message: str = _EMPTY_MESSAGE
        self.setMinimumSize(*_CHART_MIN_SIZE)
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
                # 集約行はラベル表記が「銘柄名(コード)」ではなく「その他(N銘柄)」。
                # profit_jpy / book_value_jpy は持たないため、中央の損益は
                # この表示用リストではなく生の self._tiles から集計すること。
                "is_other": True,
            })
        return main

    def _geometry(self) -> tuple[float, float, float, float]:
        """(cx, cy, 外半径, 内半径) を返す。描画とヒットテストで同じ式を共有する。"""
        w = float(self.rect().width())
        h = float(self.rect().height())
        base_r = min(w / 2.0 - self._LABEL_BAND, h / 2.0 - 16.0)
        r = max(self._MIN_RADIUS, base_r * self._RADIUS_SCALE)
        return w / 2.0, h / 2.0, r, r * self._HOLE_RATIO

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect()
        painter.fillRect(rect, QColor(theme.CHART_BG))

        if not self._tiles or self._total <= 0:
            painter.setPen(QPen(QColor(theme.CHART_AXIS_TEXT)))
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
        # ラベルを扇外に引き出すため左右に帯を確保しつつ、円は大きめに描く
        # （_RADIUS_SCALE）。帯は銘柄名を表示できるよう広めに取る。
        cx, cy, r, r_in = self._geometry()
        pie_rect = QRectF(cx - r, cy - r, r * 2, r * 2)

        # 扇形描画: 90°（12時）から時計回り、銘柄ごとカテゴリカル配色
        colors = _categorical_colors(display_tiles)
        self._segments = []
        current = 90.0
        for i, tile in enumerate(display_tiles):
            span = float(tile[self._value_key]) / self._total * 360.0
            painter.setBrush(colors[i])
            painter.setPen(QPen(QColor(theme.CHART_BG), 1.5))
            painter.drawPie(pie_rect, int(current * 16), -int(span * 16))  # 負値 = 時計回り
            self._segments.append((current, span, tile))
            current -= span  # 時計回り = 角度を減算

        # ドーナツの穴: 扇を描いた上から背景色で抜く（中央に合計を置くため）。
        # 扇の区切り線は中心へ向かうので、この円で一緒に隠れる。
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.CHART_BG))
        painter.drawEllipse(QRectF(cx - r_in, cy - r_in, r_in * 2, r_in * 2))

        self._draw_slice_percents(painter, cx, cy, r, r_in, colors)
        self._draw_center(painter, cx, cy, r_in)

        # ラベル描画: 扇の中点から引き出し線を伸ばし、円外（左右）にテキストを置く。
        # 縦の重なりは spread_labels で回避する（同サイド内は引き出し線が交差しない）。
        # 銘柄名(コード)の1行だけにして行ピッチを詰め、多数のスライスでも
        # spread_labels の均等配置（＝重なり許容）へ落ちにくくする。
        lbl_font = QFont(self.font())
        lbl_font.setPointSize(max(7, self.font().pointSize() - 1))
        painter.setFont(lbl_font)
        row_h = float(painter.fontMetrics().height())
        min_gap = row_h + 2.0
        # 円の外側に実際に空いている幅（肘＋余白を除く）。_LABEL_BAND を直に使うと
        # 高さ律速で円が小さいときに余白を捨てることになる。上限は付ける
        # （横長・低背のペインでは r が縮んで幅が余り、ラベルが端まで伸びる）。
        label_w = min(260.0, max(60.0, w / 2.0 - (r + 46.0)))

        # 各扇の中点から円周上の起点と左右サイドを求める
        entries = []  # (i, tile, x_in, y_in, side)
        for i, (start, span, tile) in enumerate(self._segments):
            if span <= 0.8:  # 0.8°未満の極小スライスはラベル省略
                continue
            mid_rad = math.radians(start - span / 2.0)
            cosm = math.cos(mid_rad)
            sinm = math.sin(mid_rad)
            x_in = cx + r * cosm
            y_in = cy - r * sinm
            side = "right" if cosm >= 0 else "left"
            entries.append((i, tile, x_in, y_in, side))

        for side in ("left", "right"):
            grp = [e for e in entries if e[4] == side]
            grp.sort(key=lambda e: e[3])  # 円周上の y 昇順（交差防止）
            # y_lbl は行の中心。上下にはみ出さないよう、配置範囲は中心が
            # 取り得る上下端（= 行の半分＋余白）に絞る。
            placed = spread_labels(
                [e[3] for e in grp],
                min_gap,
                top=row_h / 2.0 + 2.0,
                bottom=h - row_h / 2.0 - 2.0,
            )
            sign = 1.0 if side == "right" else -1.0
            elbow_x = cx + sign * (r + 34.0)
            text_x = elbow_x + sign * 8.0
            for (i, tile, x_in, y_in, _side), y_lbl in zip(grp, placed):
                color = colors[i]
                # 引き出し線（扇色）: 円周点 → 肘 → 水平
                painter.setPen(QPen(color, 1.2))
                painter.drawLine(int(x_in), int(y_in), int(elbow_x), int(y_lbl))
                painter.drawLine(int(elbow_x), int(y_lbl), int(text_x), int(y_lbl))

                if side == "right":
                    box_x = text_x + 2.0
                    align = Qt.AlignmentFlag.AlignLeft
                else:
                    box_x = text_x - 2.0 - label_w
                    align = Qt.AlignmentFlag.AlignRight
                # 「銘柄名(コード)」の1行。幅に収まらなければ末尾省略（金額と
                # 構成比はスライス内の % とツールチップで読める）。
                painter.setFont(lbl_font)
                painter.setPen(QPen(QColor(theme.TEXT_PRIMARY)))
                text = painter.fontMetrics().elidedText(
                    _label_text(tile), Qt.TextElideMode.ElideRight, int(label_w)
                )
                painter.drawText(
                    QRectF(box_x, y_lbl - row_h / 2.0, label_w, row_h),
                    align | Qt.AlignmentFlag.AlignVCenter,
                    text,
                )

        painter.end()

    def _draw_slice_percents(
        self,
        painter: QPainter,
        cx: float,
        cy: float,
        r: float,
        r_in: float,
        colors: list[QColor],
    ) -> None:
        """構成比(%)をスライスの帯の中に描く。

        帯の厚みか弦の長さに収まらないスライスは飛ばす（細いスライスに書くと
        隣のスライスへはみ出して、どの銘柄の数字か読めなくなる）。省いた分は
        ツールチップで読める。
        """
        font = QFont(self.font())
        font.setPointSize(max(7, self.font().pointSize() - 1))
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()
        text_h = float(fm.height())
        band = r - r_in
        r_mid = (r + r_in) / 2.0
        if band < text_h + 2.0:
            return
        for i, (start, span, tile) in enumerate(self._segments):
            text = f"{float(tile[self._value_key]) / self._total * 100.0:.1f}"
            text_w = float(fm.horizontalAdvance(text))
            chord = 2.0 * r_mid * math.sin(math.radians(span) / 2.0)
            if chord < text_w + 2.0:
                continue
            mid_rad = math.radians(start - span / 2.0)
            px = cx + r_mid * math.cos(mid_rad)
            py = cy - r_mid * math.sin(mid_rad)
            painter.setPen(QPen(_readable_text_color(colors[i])))
            painter.drawText(
                QRectF(px - text_w / 2.0 - 4.0, py - text_h / 2.0, text_w + 8.0, text_h),
                Qt.AlignmentFlag.AlignCenter,
                text,
            )

    def _center_profit_text(self) -> tuple[str, QColor] | None:
        """中央に添える損益の (文字列, 色) を返す。出せないときは None。

        集計は集約前の self._tiles から行う。_build_display_tiles が作る「その他」
        には profit_jpy が無く、表示用リストから合計すると黙って過少表示になる。
        1件でも欠けたら出さない（calc.Summary の total_profit と同じ all-or-nothing）。
        """
        if self._value_key != "market_value_jpy":
            return None  # 配当ベースの構成比に損益を出しても意味が対応しない
        profit = 0.0
        book = 0.0
        for tile in self._tiles:
            p = safe_float(tile.get("profit_jpy"))
            b = safe_float(tile.get("book_value_jpy"))
            if p is None or b is None:
                return None
            profit += p
            book += b
        if not book:
            return None
        sign = "+" if profit > 0 else ""
        text = f"{sign}{fmt_number(profit, 0)}円 ({sign}{fmt_percent(profit / book, 1)})"
        return text, profit_text_qcolor(profit) or QColor(theme.TEXT_SECONDARY)

    def _draw_center(
        self, painter: QPainter, cx: float, cy: float, r_in: float
    ) -> None:
        """ドーナツ中央に合計（評価額ベースなら損益も）を描く。

        穴に収まらないまま描くとスライスへ重なって読めなくなるため、フォントを
        段階的に縮め → 簡略表記（億/万）へ落とし → それでも入らなければ何も
        描かない。最小サイズのウィジェットでは中央表示が消える。
        """
        # 円の内側で横一列に使える幅（内接する矩形の目安）。
        avail_w = r_in * 1.55
        # 「年間配当＋優待合計」は 年間配当＋(優待合計) と読めるため、複合ラベルの
        # ときだけ「の」を挟む（単独なら「評価額合計」のまま）。
        title = (
            f"{self._value_label}の合計" if "＋" in self._value_label
            else f"{self._value_label}合計"
        )
        # 金額は必ず正確な値を出す。_fmt_compact（1.4億円）へ落とすと画面で
        # 最も目立つ数字が黙って1%以上ずれるため、中央では使わない。
        value_text = f"¥{fmt_number(self._total, 0)}"
        profit = self._center_profit_text()
        base = max(7, self.font().pointSize())

        # 1周目は見出し・損益まで全部入る組み合わせを探し、見つからなければ
        # 2周目で入る行だけを描く。1周で済ませると「大きい字で合計だけ」が
        # 先に成立して損益が黙って消える。
        for require_all in (True, False):
            if self._fit_center(
                painter, cx, cy, r_in, avail_w, title, value_text,
                profit, base, require_all,
            ):
                return

    def _fit_center(
        self,
        painter: QPainter,
        cx: float,
        cy: float,
        r_in: float,
        avail_w: float,
        title: str,
        value_text: str,
        profit: tuple[str, QColor] | None,
        base: int,
        require_all: bool,
    ) -> bool:
        """中央ブロックを描けたら True。require_all=True なら全行入るときのみ描く。"""
        for shrink in range(0, 4):
            value_font = QFont(self.font())
            value_font.setPointSize(max(8, base + 3 - shrink * 2))
            value_font.setBold(True)
            small_font = QFont(self.font())
            small_font.setPointSize(max(7, base - 1 - shrink))
            painter.setFont(value_font)
            fm_value = painter.fontMetrics()
            value_w = float(fm_value.horizontalAdvance(value_text))
            value_h = float(fm_value.height())
            painter.setFont(small_font)
            fm_small = painter.fontMetrics()
            small_h = float(fm_small.height())
            has_title = fm_small.horizontalAdvance(title) <= avail_w
            has_profit = (
                profit is not None
                and fm_small.horizontalAdvance(profit[0]) <= avail_w
            )
            if require_all and (
                not has_title or (profit is not None and not has_profit)
            ):
                continue
            rows: list[tuple[str, QFont, QColor, float]] = []
            if has_title:
                rows.append((title, small_font, QColor(theme.TEXT_SECONDARY), small_h))
            rows.append((value_text, value_font, QColor(theme.TEXT_BRIGHT), value_h))
            if profit is not None and has_profit:
                rows.append((profit[0], small_font, profit[1], small_h))
            block_h = sum(h for _, _, _, h in rows)
            if value_w > avail_w or block_h > r_in * 1.5:
                continue
            y = cy - block_h / 2.0
            for text, font, color, height in rows:
                painter.setFont(font)
                painter.setPen(QPen(color))
                painter.drawText(
                    QRectF(cx - avail_w / 2.0, y, avail_w, height),
                    Qt.AlignmentFlag.AlignCenter,
                    text,
                )
                y += height
            return True
        return False

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
        cx, cy, r, r_in = self._geometry()

        dx = pos.x() - cx
        dy = -(pos.y() - cy)  # Qt y軸反転
        dist = math.sqrt(dx * dx + dy * dy)
        # 中央の穴は合計表示なのでスライス扱いしない（穴の上でツールチップが
        # 出ると、背後のスライスの値だと誤読される）。
        if dist > r or dist < r_in:
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
        self.setMinimumSize(*_CHART_MIN_SIZE)
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

    def _x_positions(self, plot: QRectF) -> list[float]:
        """表示対象の日付から X 座標配列を作る（描画とホバー判定で共有）。"""
        dates = [str(rec.get("date", "")) for rec in self._visible]
        return date_x_positions(dates, plot.left(), plot.width())

    def _x_for(self, idx: int, plot: QRectF) -> float:
        xs = self._x_positions(plot)
        if not xs:
            return plot.left() + plot.width() / 2.0
        return xs[max(0, min(len(xs) - 1, idx))]

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
        painter.fillRect(rect, QColor(theme.CHART_BG))

        if not self._visible or not self._series or self._top <= self._floor:
            painter.setPen(QPen(QColor(theme.CHART_AXIS_TEXT)))
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
            painter.setPen(QPen(QColor(theme.CHART_GRID), 1))
            painter.drawLine(int(plot.left()), int(y), int(plot.right()), int(y))
            painter.setPen(QPen(QColor(theme.CHART_AXIS_TEXT)))
            painter.drawText(
                QRectF(0, y - 8, self._MARGIN_LEFT - 6, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                _fmt_compact(self._floor + (self._top - self._floor) * frac),
            )

        # X 軸ラベル（経過日数に比例した最大 5 点の日付目盛）
        n = len(self._visible)
        xs = self._x_positions(plot)
        dates = [str(rec.get("date", "")) for rec in self._visible]
        painter.setPen(QPen(QColor(theme.CHART_AXIS_TEXT)))
        for x, date_label in date_axis_ticks(dates, plot.left(), plot.width()):
            painter.drawText(
                QRectF(x - 44, plot.bottom() + 4, 88, 16),
                Qt.AlignmentFlag.AlignCenter,
                date_label,
            )

        # 各系列の折れ線（None 点で分断）。前後が None の孤立点はマーカーで可視化
        # （例: 取崩収入は当初 1 点のみで線が引けないため点を描く）。
        for label, key, color in self._series:
            painter.setPen(QPen(color, 2))
            vals = [safe_float(rec.get(key)) for rec in self._visible]
            prev: tuple[float, float] | None = None
            for idx, v in enumerate(vals):
                if v is None:
                    prev = None
                    continue
                x = xs[idx]
                y = self._y_for(v, plot)
                if prev is not None:
                    painter.drawLine(int(prev[0]), int(prev[1]), int(x), int(y))
                else:
                    nxt = vals[idx + 1] if idx + 1 < len(vals) else None
                    if nxt is None:  # 前後とも繋がらない孤立点
                        painter.setBrush(color)
                        painter.drawEllipse(QRectF(x - 2.5, y - 2.5, 5, 5))
                prev = (x, y)

        # ホバー: 縦カーソル線 + 該当点マーカー
        if self._hover_idx is not None and 0 <= self._hover_idx < n:
            hx = xs[self._hover_idx]
            painter.setPen(QPen(QColor(theme.CHART_CURSOR), 1, Qt.PenStyle.DashLine))
            painter.drawLine(int(hx), int(plot.top()), int(hx), int(plot.bottom()))
            rec = self._visible[self._hover_idx]
            for _label, key, color in self._series:
                v = safe_float(rec.get(key))
                if v is None:
                    continue
                hy = self._y_for(v, plot)
                painter.setBrush(color)
                painter.setPen(QPen(QColor(theme.CHART_BG), 1))
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
            painter.setPen(QPen(QColor(theme.CHART_LABEL_TEXT)))
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
        return nearest_x_index(
            self._x_positions(plot), px, plot.left(), plot.right()
        )


class BarChartWidget(QWidget):
    """月別の積み上げ棒グラフ（純 QPainter・依存追加なし）。

    LineChartWidget と同じく外部グラフライブラリを使わず、起動パスに重量級 import を
    持ち込まない方針に従う。

    set_data(buckets, series):
      buckets … 12件の dict。各要素に "month"（1〜12）と各系列の数値キーを持つ。
      series  … (ラベル, キー, 塗り色 QColor) のタプルのリスト。下から順に積む。

    バーの色は損益ではなく系列（資産クラス別の配当/優待）の区別なので、日本式の損益カラー
    （profit_text_qcolor / profit_tile_colors）は通さず呼び出し側から受け取る。
    """

    month_clicked = Signal(int)

    _MARGIN_LEFT = 64
    _MARGIN_RIGHT = 16
    # 上部に「月平均」の行を出すぶん、折れ線グラフより広く取る。
    _MARGIN_TOP = 44
    _MARGIN_BOTTOM = 34
    _BAND_FILL = 0.62  # 1ヶ月ぶんの帯幅に対する棒の幅

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buckets: list[dict] = []
        self._series: list[tuple[str, str, QColor]] = []
        self._empty_message: str = "配当データがありません（F5 で株価・配当を取得してください）"
        self._average: float | None = None
        self._selected: int | None = None
        self._hover_month: int | None = None
        self._top: float = 1.0
        self.setMinimumSize(*_BAR_CHART_MIN_SIZE)
        self.setMouseTracking(True)
        self.setAutoFillBackground(True)

    def set_data(
        self,
        buckets: list[dict],
        series: list[tuple[str, str, QColor]],
        average: float | None = None,
        empty_message: str | None = None,
    ) -> None:
        self._buckets = list(buckets)
        self._series = list(series)
        self._average = average
        if empty_message is not None:
            self._empty_message = empty_message
        self._hover_month = None
        self._recompute()
        self.update()

    def set_selected_month(self, month: int | None) -> None:
        if month == self._selected:
            return
        self._selected = month
        self.update()

    def _recompute(self) -> None:
        totals = [self._total_of(b) for b in self._buckets]
        # 棒グラフは常にゼロ基準（途中から始めると高さの比が嘘になる）。
        _floor, self._top = axis_bounds(totals, "zero")

    def _total_of(self, bucket: dict) -> float:
        return sum(safe_float(bucket.get(key)) or 0.0 for _label, key, _color in self._series)

    # ---- 座標変換 ----
    def _plot_rect(self) -> QRectF:
        rect = self.rect()
        return QRectF(
            self._MARGIN_LEFT,
            self._MARGIN_TOP,
            max(1.0, rect.width() - self._MARGIN_LEFT - self._MARGIN_RIGHT),
            max(1.0, rect.height() - self._MARGIN_TOP - self._MARGIN_BOTTOM),
        )

    def _band_width(self, plot: QRectF) -> float:
        return plot.width() / max(1, len(self._buckets))

    def _band_left(self, idx: int, plot: QRectF) -> float:
        return plot.left() + self._band_width(plot) * idx

    def _y_for(self, value: float, plot: QRectF) -> float:
        if self._top <= 0:
            return plot.bottom()
        return plot.bottom() - plot.height() * (value / self._top)

    # ---- 描画 ----
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect()
        painter.fillRect(rect, QColor(theme.CHART_BG))

        totals = [self._total_of(b) for b in self._buckets]
        if not self._buckets or not self._series or sum(totals) <= 0:
            painter.setPen(QPen(QColor(theme.CHART_AXIS_TEXT)))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._empty_message)
            painter.end()
            return

        plot = self._plot_rect()
        band = self._band_width(plot)
        bar_w = band * self._BAND_FILL
        small_font = QFont(self.font())
        small_font.setPointSize(max(7, self.font().pointSize() - 1))

        # 選択中の月の帯を淡く塗る（どの月の内訳を見ているか分かるように）
        if self._selected is not None:
            for idx, bucket in enumerate(self._buckets):
                if bucket.get("month") != self._selected:
                    continue
                painter.fillRect(
                    QRectF(self._band_left(idx, plot), plot.top(), band, plot.height()),
                    QColor(theme.CHART_SELECTION_BG),
                )

        # 横グリッド線 + Y 軸ラベル（0 〜 上限を 4 分割）
        painter.setFont(small_font)
        for i in range(5):
            frac = i / 4.0
            y = plot.bottom() - plot.height() * frac
            painter.setPen(QPen(QColor(theme.CHART_GRID), 1))
            painter.drawLine(int(plot.left()), int(y), int(plot.right()), int(y))
            painter.setPen(QPen(QColor(theme.CHART_AXIS_TEXT)))
            painter.drawText(
                QRectF(0, y - 8, self._MARGIN_LEFT - 6, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                _fmt_compact(self._top * frac),
            )

        # 積み上げ棒（series の順に下から積む）
        painter.setPen(Qt.PenStyle.NoPen)
        for idx, bucket in enumerate(self._buckets):
            month = bucket.get("month")
            bx = self._band_left(idx, plot) + (band - bar_w) / 2.0
            baseline = 0.0
            for _label, key, color in self._series:
                value = safe_float(bucket.get(key)) or 0.0
                if value <= 0:
                    continue
                y_top = self._y_for(baseline + value, plot)
                y_bottom = self._y_for(baseline, plot)
                fill = color.lighter(115) if month == self._hover_month else color
                painter.fillRect(
                    QRectF(bx, y_top, bar_w, max(1.0, y_bottom - y_top)), fill
                )
                baseline += value

        # 各棒の上に金額（万/億丸めだと月額の差が潰れるので実数のまま）
        painter.setPen(QPen(QColor(theme.CHART_LABEL_TEXT)))
        for idx, (bucket, total) in enumerate(zip(self._buckets, totals)):
            if total <= 0:
                continue
            y = self._y_for(total, plot)
            painter.drawText(
                QRectF(self._band_left(idx, plot), y - 16, band, 14),
                Qt.AlignmentFlag.AlignCenter,
                fmt_number(total, 0),
            )

        # X 軸ラベル（1月〜12月）
        painter.setPen(QPen(QColor(theme.CHART_AXIS_TEXT)))
        for idx, bucket in enumerate(self._buckets):
            painter.drawText(
                QRectF(self._band_left(idx, plot), plot.bottom() + 4, band, 16),
                Qt.AlignmentFlag.AlignCenter,
                f"{bucket.get('month', idx + 1)}月",
            )

        # 月平均の水平線（按分できた年額 ÷ 12）
        if self._average and self._average > 0:
            ay = self._y_for(self._average, plot)
            painter.setPen(QPen(QColor(theme.CHART_CURSOR), 1, Qt.PenStyle.DashLine))
            painter.drawLine(int(plot.left()), int(ay), int(plot.right()), int(ay))
            painter.setPen(QPen(QColor(theme.CHART_AXIS_TEXT)))
            painter.drawText(
                QRectF(plot.left(), 6, plot.width() / 2.0, 16),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"月平均 ¥{fmt_number(self._average, 0)}",
            )

        # 凡例（右上）。系列と同じ左→右の順に並べるため、先に総幅を測って左端を決める。
        sw = 14  # 色見本の幅
        gap = 12  # 系列間の間隔
        widths = [
            sw + 4 + painter.fontMetrics().horizontalAdvance(label)
            for label, _key, _color in self._series
        ]
        legend_x = plot.right() - 8 - sum(widths) - gap * (len(widths) - 1)
        legend_y = 6
        for (label, _key, color), row_w in zip(self._series, widths):
            painter.fillRect(QRectF(legend_x, legend_y + 3, sw, 10), color)
            painter.setPen(QPen(QColor(theme.CHART_LABEL_TEXT)))
            painter.drawText(
                QRectF(legend_x + sw + 4, legend_y, row_w - sw, 16),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                label,
            )
            legend_x += row_w + gap

        painter.end()

    # ---- 対話 ----
    def _month_at(self, px: float) -> int | None:
        """x 座標を月に対応させる。棒の外側でも帯の内側なら拾う（押しやすさ優先）。"""
        if not self._buckets:
            return None
        plot = self._plot_rect()
        if px < plot.left() or px > plot.right():
            return None
        idx = int((px - plot.left()) / self._band_width(plot))
        idx = max(0, min(len(self._buckets) - 1, idx))
        month = self._buckets[idx].get("month")
        return int(month) if month is not None else None

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        month = self._month_at(pos.x())
        if month is not None and month != self._selected:
            self._selected = month
            self.update()
            self.month_clicked.emit(month)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt API)
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        month = self._month_at(pos.x())
        if month != self._hover_month:
            self._hover_month = month
            self.update()
        if month is None:
            QToolTip.hideText()
            super().mouseMoveEvent(event)
            return
        bucket = next((b for b in self._buckets if b.get("month") == month), None)
        if bucket is not None:
            lines = [f"{month}月"]
            for label, key, _color in self._series:
                value = safe_float(bucket.get(key)) or 0.0
                lines.append(f"{label}: {fmt_number(value, 0)} 円")
            if len(self._series) > 1:
                lines.append(f"合計: {fmt_number(self._total_of(bucket), 0)} 円")
            QToolTip.showText(event.globalPosition().toPoint(), "\n".join(lines), self)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if self._hover_month is not None:
            self._hover_month = None
            self.update()
        QToolTip.hideText()
        super().leaveEvent(event)


class KpiCard(QFrame):
    """サマリー指標を1つ表示する小カード（タイトル＋値）。"""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # 見た目はグローバル QSS（theme.build_app_qss の #kpiCard 系セレクタ）に集約。
        self.setObjectName("kpiCard")
        # 横はウィンドウ幅に合わせて伸縮、縦は内容ぶんに固定する。最小幅を持たせて
        # ウィンドウを狭めたときに値が読めなくなるまで潰れるのを防ぐ。
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(120)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)
        self._title = QLabel(title)
        self._title.setObjectName("kpiTitle")
        self._value = QLabel("—")
        self._value.setObjectName("kpiValue")
        layout.addWidget(self._title)
        layout.addWidget(self._value)

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def set_value(self, text: str, color: QColor | None = None) -> None:
        self._value.setText(text)
        # 損益などの動的色のみインラインで上書きし、既定色はグローバル QSS に任せる。
        self._value.setStyleSheet(f"color: {color.name()};" if color is not None else "")
