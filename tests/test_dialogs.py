"""dialogs.py の即時バリデーション関数（純ロジック・QApplication 不要）のテスト。"""

from __future__ import annotations

from src.dialogs import (
    ACCOUNT_CHOICES,
    validate_optional_date,
    validate_optional_number,
    validate_optional_rate,
    validate_required_number,
)


def test_account_choices():
    """口座の選択肢（空=未設定を先頭に、指定の5種）。"""
    assert ACCOUNT_CHOICES == [
        "", "特定", "一般", "NISA（成長）", "NISA（積立）", "旧NISA"
    ]


def test_validate_required_number():
    assert validate_required_number("1,000", "保有株数") is None  # カンマ入力対応
    assert validate_required_number("", "保有株数") is None       # 空欄=0 扱い
    assert validate_required_number("１２３", "保有株数") is None  # 全角数字
    assert validate_required_number("-1", "保有株数") is not None
    assert validate_required_number("abc", "保有株数") is not None
    assert validate_required_number("nan", "保有株数") is not None


def test_validate_optional_number():
    assert validate_optional_number("", "配当") is None   # 空欄=未設定
    assert validate_optional_number("0", "配当") is None  # 0 は明示上書きとして有効
    assert validate_optional_number("-0.5", "配当") is not None


def test_validate_optional_rate():
    assert validate_optional_rate("", "定率取崩率") is None
    assert validate_optional_rate("0.04", "定率取崩率") is None
    assert validate_optional_rate("0", "定率取崩率") is None
    assert validate_optional_rate("1", "定率取崩率") is None
    assert validate_optional_rate("1.5", "定率取崩率") is not None


def test_validate_optional_date():
    assert validate_optional_date("") is None
    assert validate_optional_date("2026-06-11") is None
    assert validate_optional_date("2026-02-30") is not None
    assert validate_optional_date("11/06/2026") is not None
