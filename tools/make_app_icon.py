"""アプリアイコン（assets/app.ico）の生成スクリプト。

実行: py -3 tools/make_app_icon.py

デザインは「濃紺の角丸 ＋ ドーナツリング（青＋赤セグメント）＋ ¥」。
アプリ内の構成円グラフ（ドーナツ）と損益カラー（プラス=赤）に合わせてある。

方針:
- 依存追加なし。PySide6 の QPainter だけで描き、.ico の器も struct で自前に組む
  （Pillow を requirements へ足さないため。Qt の ICO ハンドラは書き出しに使わない）。
- ¥ はフォントではなくベクタの線で描く（環境のフォントに依存せず、16px でも形が残る）。
- 各サイズは 256px を縮小するのではなくネイティブに描き直す（小サイズの潰れ防止）。
- 生成物 assets/app.ico はリポジトリへコミット済み。実行時に生成しないこと
  （起動パスへ処理を足さない方針。gui._ensure_app は読み込むだけ）。
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)

# 下地の濃紺は theme.ACCENT（#1565c0）系。ウィンドウ背景（#1e1e1e）を使うと
# ダークなタスクバーに沈んで見えなくなるため、あえて明るい紺を選んでいる。
NAVY_TOP = "#1c5fb4"
NAVY_BOTTOM = "#0d2c56"
WHITE = "#f7fbff"
LIGHT = "#bcd8ff"
RED = "#e53935"  # theme.PROFIT_UP（日本式のプラス）

ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def _field(p: QPainter, S: int) -> None:
    """角丸の下地（濃紺の縦グラデーション）。"""
    g = QLinearGradient(0, 0, 0, S)
    g.setColorAt(0.0, QColor(NAVY_TOP))
    g.setColorAt(1.0, QColor(NAVY_BOTTOM))
    m = S * 0.02
    path = QPainterPath()
    path.addRoundedRect(QRectF(m, m, S - 2 * m, S - 2 * m), S * 0.22, S * 0.22)
    p.fillPath(path, QBrush(g))


def _yen(p: QPainter, cx: float, cy: float, h: float, color: str, weight: float) -> None:
    """¥ を線で描く（フォント非依存＝小サイズでも形が崩れない）。"""
    w = h * 0.88
    pen = QPen(QColor(color), max(1.0, h * weight))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    top = cy - h / 2
    junction = cy - h * 0.06
    p.drawLine(QPointF(cx - w / 2, top), QPointF(cx, junction))
    p.drawLine(QPointF(cx + w / 2, top), QPointF(cx, junction))
    p.drawLine(QPointF(cx, junction), QPointF(cx, cy + h / 2))
    for frac in (0.16, 0.36):  # ¥ の2本の横棒
        y = junction + h * frac
        p.drawLine(QPointF(cx - w * 0.36, y), QPointF(cx + w * 0.36, y))
    p.setPen(Qt.PenStyle.NoPen)


def draw_icon(p: QPainter, S: int) -> None:
    """1辺 S px のアイコンを描く（座標はすべて S 比で持つ＝任意サイズで同形）。"""
    _field(p, S)
    cx = cy = S / 2
    outer = S * 0.44
    width = S * 0.10
    rect = QRectF(
        cx - outer + width / 2, cy - outer + width / 2,
        2 * (outer - width / 2), 2 * (outer - width / 2),
    )
    pen = QPen(QColor(LIGHT), width)
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    p.setPen(pen)
    p.drawArc(rect, 0, 360 * 16)          # リング全体
    pen.setColor(QColor(RED))
    p.setPen(pen)
    p.drawArc(rect, 90 * 16, -118 * 16)   # 構成比のセグメント（12時から時計回り）
    p.setPen(Qt.PenStyle.NoPen)
    _yen(p, cx, cy + S * 0.005, S * 0.42, WHITE, weight=0.19)


def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    draw_icon(p, size)
    p.end()
    return img


def _png_bytes(img: QImage) -> bytes:
    ba = QByteArray()  # QBuffer は参照を保持しないので一時オブジェクトにしない
    buf = QBuffer(ba)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


def write_ico(path: Path) -> None:
    """PNG 埋め込み形式の .ico を組み立てて書き出す（Windows Vista 以降が対応）。"""
    blobs = [_png_bytes(render(s)) for s in ICO_SIZES]
    offset = 6 + 16 * len(blobs)  # ICONDIR + ICONDIRENTRY × n
    entries, payload = b"", b""
    for size, blob in zip(ICO_SIZES, blobs):
        dim = size if size < 256 else 0  # 256 は 0 で表す（1バイトに収まらないため）
        entries += struct.pack(
            "<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset
        )
        payload += blob
        offset += len(blob)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<HHH", 0, 1, len(blobs)) + entries + payload)


def main() -> None:
    QGuiApplication(sys.argv)  # QPainter / QImage に QGuiApplication が要る
    out = Path(__file__).resolve().parents[1] / "assets" / "app.ico"
    write_ico(out)
    print(f"wrote {out} ({out.stat().st_size} bytes, sizes={ICO_SIZES})")


if __name__ == "__main__":
    main()
