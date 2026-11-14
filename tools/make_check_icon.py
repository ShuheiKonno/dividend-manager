"""チェックボックスのチェックマーク画像（assets/check.png）の生成スクリプト。

実行: py -3 tools/make_check_icon.py

QSS でチェックマークを出すには画像が要る（`QCheckBox::indicator:checked { image: url(...) }`）。
矢印などのサブコントロールを QSS で定義しない方針（画像リソース不要化）の**例外として、
このチェックマーク 1 枚だけ**を持つ。理由: ACCENT 塗りだけだと、ツールバーの「対象」が
青い四角の並びになり、月別グラフの凡例の色見本と見分けられなかった。

方針（tools/make_app_icon.py と同じ）:
- 依存追加なし。PySide6 の QPainter だけで描く。
- 生成物 assets/check.png はリポジトリへコミット済み。実行時に生成しないこと
  （起動パスへ処理を足さない方針。theme.build_app_qss は存在すれば URL を埋めるだけ）。
- 15px のインジケータへ縮小して使うので、125% 表示などの拡大でもぼやけないよう
  60px で描く。線は白（ACCENT 塗り・無効時の灰色塗りのどちらの上でも読める）。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen, QPolygonF

SIZE = 60
# 折れ線の 3 点（一辺に対する比）。
POINTS = ((0.22, 0.53), (0.42, 0.72), (0.78, 0.30))


def draw_check(size: int = SIZE) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor("#ffffff"), size * 0.14)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.drawPolyline(QPolygonF([QPointF(x * size, y * size) for x, y in POINTS]))
    painter.end()
    return image


def main() -> None:
    QGuiApplication(sys.argv)  # QPainter / QImage に QGuiApplication が要る
    out = Path(__file__).resolve().parents[1] / "assets" / "check.png"
    if not draw_check().save(str(out), "PNG"):
        raise SystemExit(f"保存に失敗しました: {out}")
    print(f"wrote {out} ({out.stat().st_size} bytes, {SIZE}x{SIZE})")


if __name__ == "__main__":
    main()
