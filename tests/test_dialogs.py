"""dialogs.py の即時バリデーション関数（純ロジック・QApplication 不要）のテスト。"""

from __future__ import annotations

from src.dialogs import (
    ACCOUNT_CHOICES,
    tax_rate_placeholder,
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


def test_tax_rate_placeholder_follows_account_and_currency():
    """空欄時に効く自動判定率をそのまま見せる（率は calc 側の一箇所が唯一の実装）。"""
    assert tax_rate_placeholder("特定", "JPY").startswith("空欄=20.315%")
    assert tax_rate_placeholder("NISA（成長）", "JPY").startswith("空欄=0%")
    assert tax_rate_placeholder("旧NISA", "USD").startswith("空欄=10%")
    # 課税口座の米国株は二段課税の合成率（浮動小数の桁が漏れないこと）。
    assert tax_rate_placeholder("特定", "USD").startswith("空欄=28.2835%")


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
