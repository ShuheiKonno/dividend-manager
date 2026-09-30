"""Markdown エクスポートの「前提・注意」に、未反映の分割の注記が入ること。"""

from __future__ import annotations

from tests.test_md_export import _build, _row, _summary


def test_note_lists_symbols_with_unreflected_splits():
    rows = [
        _row(symbol="5334", pending_splits=[("2026-09-29", 2.0)]),
        _row(symbol="7203", pending_splits=[]),
    ]
    text = _build(rows, _summary())
    notes = text.split("## 前提・注意")[1]
    assert "未反映" in notes
    assert "5334（2:1(9/29)）" in notes
    # 反映済みの銘柄は挙げない。
    assert "7203（" not in notes
    # 旗を「値下がり」と読ませない説明が入っている。
    assert "値下がりしたわけではない" in notes


def test_note_is_absent_when_no_split_is_pending():
    text = _build([_row(pending_splits=[]), _row(symbol="AAPL")], _summary())
    assert "未反映" not in text.split("## 前提・注意")[1]
