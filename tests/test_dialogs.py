"""dialogs.py の即時バリデーション関数（純ロジック・QApplication 不要）のテスト。"""

from __future__ import annotations

from src.dialogs import (
    ACCOUNT_CHOICES,
    auto_dividend_months,
    validate_optional_date,
    validate_optional_number,
    validate_optional_rate,
    validate_required_number,
)
from src.models import Holding, MarketData


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


# ---- 支払月の自動判定 ----

def test_auto_dividend_months_ignores_manual_setting():
    """手動指定済みでも「自動ならこうなる」を返すこと（プレビュー表示に使う）。"""
    holding = Holding(
        symbol="7203", name="トヨタ", asset_type="stock", currency="JPY",
        shares=100.0, avg_cost=2000.0, account="特定",
        annual_div_ps_gross_override=None, annual_div_ps_net_override=None,
        div_forecast_as_of_override=None, annual_benefit_jpy=0.0,
        withdrawal_fixed_jpy=None, withdrawal_rate=None, tax_rate=None, active=True,
        dividend_months=[1, 2],
    )
    market = MarketData(
        symbol="7203", currency="JPY", last_price=3000.0, price_as_of=None,
        annual_div_ps_gross=95.0, annual_div_ps_net=None, div_forecast_as_of=None,
        source="yfinance", fetched_at=None, div_ex_month_ratios={3: 0.5, 9: 0.5},
    )
    # 権利落ち 3月/9月 → 支払 6月/12月。手動の [1, 2] には引きずられない。
    assert auto_dividend_months(holding, market) == [6, 12]
    assert auto_dividend_months(holding, None) == []
