"""calc.compute_rows / _pick_div_ps の検証（PySide6 非依存・純ロジック層）。"""

from __future__ import annotations

import pytest

from src.calc import (
    row_yield_keys,
    scope_holdings,
    summary_yields,
    _DEFAULT_TAX_RATE,
    _US_TAXABLE_COMBINED_RATE,
    _US_WITHHOLDING_RATE,
    _pick_div_ps,
    compute_rows,
    default_tax_rate,
    latest_fx,
    format_shares,
    from_display_shares,
    fund_share_factor,
    is_nisa_account,
    to_display_shares,
)
from src.models import Holding, MarketData


def _holding(**overrides) -> Holding:
    base = dict(
        symbol="7203",
        name="トヨタ",
        asset_type="stock",
        currency="JPY",
        shares=100.0,
        avg_cost=2000.0,
        account="",
        annual_div_ps_gross_override=None,
        annual_div_ps_net_override=None,
        div_forecast_as_of_override=None,
        annual_benefit_jpy=0.0,
        withdrawal_fixed_jpy=None,
        withdrawal_rate=None,
        tax_rate=None,
        active=True,
    )
    base.update(overrides)
    return Holding(**base)


def _market(
    symbol="7203",
    currency="JPY",
    last_price=2500.0,
    gross=60.0,
    ex_month_ratios=None,
) -> MarketData:
    return MarketData(
        symbol=symbol,
        currency=currency,
        last_price=last_price,
        price_as_of="2026-05-28",
        annual_div_ps_gross=gross,
        annual_div_ps_net=None,
        div_forecast_as_of="2026-03-31",
        source="yfinance",
        fetched_at="2026-05-29T09:00:00+09:00",
        div_ex_month_ratios=ex_month_ratios,
    )


# ---- compute_rows: JPY 株 ----

def test_jpy_stock_basic():
    h = _holding(annual_div_ps_gross_override=60.0)
    rows, summary = compute_rows([h], {"7203": _market()}, 150.0, "gross")
    r = rows[0]
    assert r["book_value_jpy"] == pytest.approx(200_000)
    assert r["market_value_jpy"] == pytest.approx(250_000)
    assert r["profit_jpy"] == pytest.approx(50_000)
    assert r["profit_rate"] == pytest.approx(0.25)
    assert r["annual_dividend_jpy"] == pytest.approx(6_000)
    assert r["yield_on_cost"] == pytest.approx(0.03)
    assert r["yield_on_value"] == pytest.approx(0.024)
    assert summary.total_profit_jpy == pytest.approx(50_000)
    assert summary.yield_on_cost == pytest.approx(0.03)


# ---- compute_rows: USD 株（fx 換算） ----

def test_usd_stock_fx_conversion():
    h = _holding(
        symbol="AAPL", name="Apple", currency="USD", shares=10.0, avg_cost=100.0,
        annual_div_ps_gross_override=2.0,
    )
    market = {"AAPL": _market(symbol="AAPL", currency="USD", last_price=190.0, gross=2.0)}
    rows, _ = compute_rows([h], market, 150.0, "gross")
    r = rows[0]
    assert r["book_value_jpy"] == pytest.approx(150_000)
    assert r["market_value_jpy"] == pytest.approx(285_000)
    assert r["annual_dividend_jpy"] == pytest.approx(3_000)


def test_usd_without_fx_rate_yields_none():
    h = _holding(symbol="AAPL", currency="USD", shares=10.0, avg_cost=100.0)
    market = {"AAPL": _market(symbol="AAPL", currency="USD", last_price=190.0)}
    rows, _ = compute_rows([h], market, None, "gross")
    assert rows[0]["market_value_jpy"] is None
    assert rows[0]["book_value_jpy"] is None


def test_summary_is_none_when_any_market_value_is_missing():
    holdings = [
        _holding(symbol="KNOWN"),
        _holding(symbol="MISSING"),
    ]
    market = {"KNOWN": _market(symbol="KNOWN")}
    _, summary = compute_rows(holdings, market, 150.0, "gross")
    assert summary.total_market_value_jpy is None
    assert summary.total_profit_jpy is None
    assert summary.missing_market_value_count == 1


# ---- compute_rows: net モードの源泉税自動計算 ----

def test_net_mode_auto_tax_default():
    h = _holding(annual_div_ps_gross_override=100.0)
    rows, _ = compute_rows([h], {}, 150.0, "net")
    expected_ps = 100.0 * (1 - _DEFAULT_TAX_RATE)
    assert rows[0]["annual_div_ps"] == pytest.approx(expected_ps)


def test_net_mode_tax_rate_zero_is_gross():
    h = _holding(annual_div_ps_gross_override=100.0, tax_rate=0.0)
    rows, _ = compute_rows([h], {}, 150.0, "net")
    assert rows[0]["annual_div_ps"] == pytest.approx(100.0)


# ---- 口座・通貨からの源泉税率の自動判定（NISA / 米国源泉10%） ----

@pytest.mark.parametrize(
    "account, expected",
    [
        ("", False),
        ("特定", False),
        ("一般", False),
        ("NISA（成長）", True),
        ("NISA（積立）", True),
        ("旧NISA", True),
        # 口座は CSV 上は自由文字列なので手編集の表記ゆれも拾う。
        ("つみたてNISA", True),
        ("nisa", True),
        ("ＮＩＳＡ", True),
    ],
)
def test_is_nisa_account(account, expected):
    assert is_nisa_account(account) is expected


def test_default_tax_rate_by_account_and_currency():
    assert default_tax_rate("特定", "JPY") == pytest.approx(_DEFAULT_TAX_RATE)
    assert default_tax_rate("", "JPY") == pytest.approx(_DEFAULT_TAX_RATE)
    # 課税口座の米国株は米国10%を引いた残額に国内20.315%＝合成で約28.2835%。
    assert default_tax_rate("特定", "USD") == pytest.approx(0.282835)
    assert default_tax_rate("一般", "USD") == pytest.approx(_US_TAXABLE_COMBINED_RATE)
    # 単純加算（30.315%）ではないことを明示する。
    assert default_tax_rate("特定", "USD") < _DEFAULT_TAX_RATE + _US_WITHHOLDING_RATE
    # NISA は国内非課税。ただし米国株は米国源泉 10% が控除できず残る。
    assert default_tax_rate("NISA（成長）", "JPY") == pytest.approx(0.0)
    assert default_tax_rate("旧NISA", "USD") == pytest.approx(_US_WITHHOLDING_RATE)


def test_net_mode_taxable_usd_is_two_stage():
    """課税口座の米国株は 米国10% → 残額に国内20.315% の順で引かれる。"""
    h = _holding(
        symbol="HDV", currency="USD", account="特定", shares=10.0, avg_cost=100.0,
        annual_div_ps_gross_override=4.0,
    )
    rows, _ = compute_rows([h], {}, 150.0, "net")
    assert rows[0]["annual_div_ps"] == pytest.approx(4.0 * 0.9 * (1 - _DEFAULT_TAX_RATE))


def test_net_mode_nisa_jpy_is_untaxed():
    h = _holding(account="NISA（成長）", annual_div_ps_gross_override=100.0)
    rows, _ = compute_rows([h], {}, 150.0, "net")
    assert rows[0]["annual_div_ps"] == pytest.approx(100.0)


def test_net_mode_nisa_usd_keeps_us_withholding():
    h = _holding(
        symbol="HDV", currency="USD", account="旧NISA", shares=10.0, avg_cost=100.0,
        annual_div_ps_gross_override=4.0,
    )
    rows, _ = compute_rows([h], {}, 150.0, "net")
    assert rows[0]["annual_div_ps"] == pytest.approx(4.0 * 0.9)
    # 円換算も 10% 引き後の額になる。
    assert rows[0]["annual_dividend_jpy"] == pytest.approx(4.0 * 0.9 * 10 * 150.0)


def test_explicit_tax_rate_wins_over_account_derivation():
    """銘柄ごとの明示指定は口座からの自動判定より優先される。"""
    h = _holding(
        currency="USD", account="NISA（成長）", annual_div_ps_gross_override=100.0,
        tax_rate=0.3,
    )
    rows, _ = compute_rows([h], {}, 150.0, "net")
    assert rows[0]["annual_div_ps"] == pytest.approx(70.0)


def test_gross_mode_ignores_account():
    """税引前モードは口座に関係なく gross をそのまま使う。"""
    h = _holding(account="NISA（成長）", annual_div_ps_gross_override=100.0)
    rows, _ = compute_rows([h], {}, 150.0, "gross")
    assert rows[0]["annual_div_ps"] == pytest.approx(100.0)


# ---- compute_rows: ファンドの avg_cost フォールバックと無分配0 ----

def test_fund_price_fallback_to_avg_cost():
    h = _holding(symbol="JP0000", name="投信", asset_type="fund", shares=10.0, avg_cost=10_000.0)
    rows, _ = compute_rows([h], {}, 150.0, "gross")
    r = rows[0]
    assert r["last_price"] is None
    assert r["market_value_jpy"] == pytest.approx(100_000)  # avg_cost フォールバック
    assert r["annual_dividend_jpy"] == pytest.approx(0.0)   # 無分配ファンドは 0


# ---- compute_rows: 取り崩しモード ----

def test_withdrawal_fixed_overrides_dividend():
    h = _holding(symbol="JP0000", asset_type="fund", shares=10.0, avg_cost=10_000.0,
                 withdrawal_fixed_jpy=40_000.0)
    rows, _ = compute_rows([h], {}, 150.0, "gross", withdrawal_mode="fixed")
    assert rows[0]["annual_dividend_jpy"] == pytest.approx(40_000)


def test_withdrawal_rate_uses_market_value():
    h = _holding(symbol="JP0000", asset_type="fund", shares=10.0, avg_cost=10_000.0,
                 withdrawal_rate=0.04)
    rows, _ = compute_rows([h], {}, 150.0, "gross", withdrawal_mode="rate")
    # market_value=100,000（avg_cost フォールバック）× 0.04
    assert rows[0]["annual_dividend_jpy"] == pytest.approx(4_000)


def test_withdrawal_does_not_affect_stock():
    h = _holding(annual_div_ps_gross_override=60.0)
    rows, _ = compute_rows([h], {"7203": _market()}, 150.0, "gross", withdrawal_mode="fixed")
    assert rows[0]["annual_dividend_jpy"] == pytest.approx(6_000)  # 株式は不変


# ---- compute_rows: 配当＋優待の合算列（配当タブ構成グラフ用） ----

def test_dividend_with_benefit_sums_both():
    h = _holding(annual_div_ps_gross_override=60.0, annual_benefit_jpy=3_000.0)
    rows, summary = compute_rows([h], {"7203": _market()}, 150.0, "gross")
    r = rows[0]
    assert r["annual_dividend_jpy"] == pytest.approx(6_000)
    assert r["annual_dividend_with_benefit_jpy"] == pytest.approx(9_000)
    # Summary.total_numerator_jpy の行版（KPI「合計」と構成グラフを一致させる前提）
    assert summary.total_numerator_jpy == pytest.approx(9_000)


def test_dividend_with_benefit_benefit_only_holding():
    """配当が N/A でも優待があれば合算列には額が入る（優待込みでは行が現れる）。"""
    h = _holding(symbol="4755", name="楽天G", annual_benefit_jpy=5_000.0)
    rows, _ = compute_rows([h], {}, 150.0, "gross")
    r = rows[0]
    assert r["annual_dividend_jpy"] is None
    assert r["annual_dividend_with_benefit_jpy"] == pytest.approx(5_000)


def test_dividend_with_benefit_none_when_both_missing():
    h = _holding()
    rows, _ = compute_rows([h], {}, 150.0, "gross")
    assert rows[0]["annual_dividend_with_benefit_jpy"] is None


def test_dividend_with_benefit_follows_withdrawal_mode():
    """取崩モードでは合算列も取崩収入＋優待になる（配当額のままにしない）。"""
    h = _holding(symbol="JP0000", asset_type="fund", shares=10.0, avg_cost=10_000.0,
                 withdrawal_fixed_jpy=40_000.0, annual_benefit_jpy=1_000.0)
    rows, _ = compute_rows([h], {}, 150.0, "gross", withdrawal_mode="fixed")
    assert rows[0]["annual_dividend_with_benefit_jpy"] == pytest.approx(41_000)


# ---- _pick_div_ps: 優先順位と 0 override ----

def test_pick_div_ps_override_wins_over_market():
    h = _holding(annual_div_ps_gross_override=10.0)
    assert _pick_div_ps(h, _market(gross=999.0), "gross") == 10.0


def test_pick_div_ps_zero_override_respected():
    """0 の明示上書きが market_data に上書きされないこと（旧 falsy バグの回帰防止）。"""
    h = _holding(annual_div_ps_gross_override=0.0)
    assert _pick_div_ps(h, _market(gross=50.0), "gross") == 0.0


def test_pick_div_ps_zero_override_respected_net_mode():
    h = _holding(annual_div_ps_gross_override=0.0)
    assert _pick_div_ps(h, _market(gross=50.0), "net") == pytest.approx(0.0)


def test_pick_div_ps_fund_fallback_zero():
    h = _holding(symbol="JP0000", asset_type="fund")
    assert _pick_div_ps(h, None, "gross") == 0.0


def test_pick_div_ps_stock_no_data_is_none():
    h = _holding()
    assert _pick_div_ps(h, None, "gross") is None


def test_summary_preserves_known_zero_values():
    h = _holding(avg_cost=2500.0, annual_div_ps_gross_override=0.0)
    _, summary = compute_rows([h], {"7203": _market(last_price=2500.0)}, 150.0, "gross")
    assert summary.total_dividend_jpy == 0.0
    assert summary.total_profit_jpy == 0.0
    assert summary.profit_rate == 0.0


# ---- compute_rows: include_inactive（無効銘柄の表示） ----

def _mixed_holdings() -> list[Holding]:
    """有効2件・無効1件（順序を跨いで配置）。"""
    return [
        _holding(symbol="7203", annual_div_ps_gross_override=60.0),
        _holding(symbol="9999", name="休眠", active=False, avg_cost=100.0),
        _holding(symbol="8058", name="三菱商事", annual_div_ps_gross_override=70.0),
    ]


def _mixed_market() -> dict[str, MarketData]:
    return {
        "7203": _market(last_price=2500.0),
        "9999": _market(symbol="9999", last_price=500.0),
        "8058": _market(symbol="8058", last_price=3000.0),
    }


def test_include_inactive_defaults_to_excluding_inactive():
    rows, _ = compute_rows(_mixed_holdings(), _mixed_market(), 150.0, "gross")
    assert [r["symbol"] for r in rows] == ["7203", "8058"]


def test_include_inactive_adds_row_preserving_order():
    rows, _ = compute_rows(
        _mixed_holdings(), _mixed_market(), 150.0, "gross", include_inactive=True
    )
    assert [r["symbol"] for r in rows] == ["7203", "9999", "8058"]
    assert [r["active"] for r in rows] == [True, False, True]


def test_include_inactive_leaves_summary_bit_identical():
    """無効銘柄を行に足しても Summary は 1 フィールドたりとも変わらないこと。"""
    holdings, market = _mixed_holdings(), _mixed_market()
    _, without = compute_rows(holdings, market, 150.0, "gross")
    _, with_inactive = compute_rows(
        holdings, market, 150.0, "gross", include_inactive=True
    )
    assert with_inactive == without


def test_include_inactive_does_not_shift_missing_market_value_count():
    """無効銘柄に株価が無くても missing_market_value_count は増えないこと。"""
    holdings = _mixed_holdings()
    market = {"7203": _market(last_price=2500.0), "8058": _market(symbol="8058")}
    _, summary = compute_rows(
        holdings, market, 150.0, "gross", include_inactive=True
    )
    assert summary.missing_market_value_count == 0


def test_rows_expose_account_and_asset_type():
    """同一 symbol の重複行を表示側が判別できるキーを持つこと。"""
    holdings = [
        _holding(symbol="PFF", account="特定", asset_type="stock"),
        _holding(symbol="PFF", account="NISA", asset_type="stock"),
    ]
    rows, _ = compute_rows(holdings, {}, 150.0, "gross")
    assert [r["account"] for r in rows] == ["特定", "NISA"]
    assert [r["asset_type"] for r in rows] == ["stock", "stock"]


# ---- 月別ビュー用のキー ----

def test_rows_expose_month_keys_from_market():
    """market の権利落ち月が支払月へシフトされて row に載ること。"""
    h = _holding(annual_div_ps_gross_override=60.0)
    rows, _ = compute_rows(
        [h], {"7203": _market(ex_month_ratios={3: 0.6, 9: 0.4})}, 150.0, "gross"
    )
    assert rows[0]["dividend_month_ratios"] == {6: 0.6, 12: 0.4}
    assert rows[0]["dividend_months_manual"] is False
    assert rows[0]["benefit_months"] == []


def test_rows_prefer_manual_months_over_market():
    h = _holding(
        annual_div_ps_gross_override=60.0,
        dividend_months=[3, 9],
        benefit_months=[12],
    )
    rows, _ = compute_rows(
        [h], {"7203": _market(ex_month_ratios={3: 1.0})}, 150.0, "gross"
    )
    assert rows[0]["dividend_month_ratios"] == {3: 0.5, 9: 0.5}
    assert rows[0]["dividend_months_manual"] is True
    assert rows[0]["benefit_months"] == [12]


def test_rows_expose_benefit_detail():
    """優待内容（自由記述）が加工されず row へ載ること。"""
    h = _holding(annual_benefit_jpy=3_000.0, benefit_detail="クオカード3,000円分")
    rows, _ = compute_rows([h], {}, 150.0, "gross")
    assert rows[0]["benefit_detail"] == "クオカード3,000円分"
    # 未設定は None ではなく空文字（表示層で毎回 None を潰さずに済む）
    rows2, _ = compute_rows([_holding()], {}, 150.0, "gross")
    assert rows2[0]["benefit_detail"] == ""


def test_rows_month_ratios_none_when_unknown():
    h = _holding(annual_div_ps_gross_override=60.0)
    rows, _ = compute_rows([h], {"7203": _market()}, 150.0, "gross")
    assert rows[0]["dividend_month_ratios"] is None


# ---- 投信の口数単位（表示・入力の変換のみ） ----

def test_fund_share_factor_only_scales_funds_in_per1_mode():
    assert fund_share_factor("fund", "per1") == 10000.0
    assert fund_share_factor("fund", "per10k") == 1.0
    # 株式・ETF は単位設定に関わらず素通し（口数の概念が無い）。
    assert fund_share_factor("stock", "per1") == 1.0
    assert fund_share_factor(None, "per1") == 1.0


def test_display_shares_round_trip_is_exact():
    """開いて保存しただけで CSV の数字が変わらないこと（往復が恒等）。"""
    # 6桁に収まらない値・極小値も丸めない（保存で保有数が減らないこと）。
    for shares in (206.7284, 0.0, 1.0, 12345.6789, 0.0001, 1e-7, 1234.56789012):
        for unit in ("per10k", "per1"):
            shown = to_display_shares(shares, "fund", unit)
            assert from_display_shares(shown, "fund", unit) == shares
    # 表示側も浮動小数の誤差を出さない（2067283.9999999998 にならない）。
    assert to_display_shares(206.7284, "fund", "per1") == 2067284.0
    assert format_shares(206.7284, "fund", "per1") == "2067284"
    assert format_shares(206.7284, "fund", "per10k") == "206.7284"
    assert format_shares(100.0, "stock", "per1") == "100"
    assert to_display_shares(None, "fund", "per1") is None


def test_compute_rows_ignores_fund_share_unit():
    """計算は保存値（万口単位）だけを見る＝単位設定で時価・簿価が動かないこと。"""
    h = _holding(asset_type="fund", symbol="JP90C000H1T1", shares=206.7284, avg_cost=10_000.0)
    rows, summary = compute_rows([h], {}, 150.0, "gross")
    # 価格未取得のファンドは簿価を時価の代わりに使う（既存仕様）。
    assert rows[0]["book_value_jpy"] == pytest.approx(2_067_284.0)
    assert rows[0]["shares"] == 206.7284  # row は常に保存値のまま
    assert summary.total_market_value_jpy == pytest.approx(2_067_284.0)


# ---- compute_rows: 表示対象の資産クラスで絞った集計 ----
#
# 旧「利回り: 投信除く」は表示対象の資産（scope_holdings）に吸収した。絞りは
# compute_rows へ渡す保有の側で掛けるので、分子（配当＋優待）と分母（簿価／時価）
# の両方から自動的に外れる。

_STOCKS = ("jp_stock", "us_stock")


def _stock_and_fund():
    """配当のある株式1件（簿価20万・配当6千）と無分配ファンド1件（簿価10万）。"""
    stock = _holding(annual_div_ps_gross_override=60.0)
    fund = _holding(
        symbol="JP0000", name="投信", asset_type="fund", shares=10.0, avg_cost=10_000.0
    )
    return [stock, fund]


def _scoped_summary(holdings, market, scope, **kwargs):
    picked = [h for _, h in scope_holdings(holdings, scope)]
    return compute_rows(picked, market, 150.0, "gross", **kwargs)[1]


def test_scope_without_fund_excludes_it_from_both_sides():
    _, summary = compute_rows(_stock_and_fund(), {"7203": _market()}, 150.0, "gross")
    # 全体: 分子 6,000 / 簿価 300,000 / 時価 350,000
    assert summary.total_book_value_jpy == pytest.approx(300_000)
    assert summary.yield_on_cost == pytest.approx(6_000 / 300_000)
    # 株式だけ: 分子 6,000 / 簿価 200,000 / 時価 250,000（分母から投信が消える）
    stocks = _scoped_summary(_stock_and_fund(), {"7203": _market()}, _STOCKS)
    assert stocks.total_book_value_jpy == pytest.approx(200_000)
    assert stocks.total_market_value_jpy == pytest.approx(250_000)
    assert stocks.total_numerator_jpy == pytest.approx(6_000)
    assert summary_yields(stocks) == (
        pytest.approx(6_000 / 200_000),
        pytest.approx(6_000 / 250_000),
    )


def test_scope_drops_fund_income_and_benefit_from_numerator():
    stock = _holding(annual_div_ps_gross_override=60.0, annual_benefit_jpy=1_000.0)
    fund = _holding(
        symbol="JP0000", asset_type="fund", shares=10.0, avg_cost=10_000.0,
        annual_div_ps_gross_override=500.0,  # 10 × 500 = 5,000 の分配
        annual_benefit_jpy=2_000.0,
    )
    stocks = _scoped_summary([stock, fund], {"7203": _market()}, _STOCKS)
    assert stocks.total_dividend_jpy == pytest.approx(6_000)
    assert stocks.total_benefit_jpy == pytest.approx(1_000)
    assert stocks.total_numerator_jpy == pytest.approx(7_000)


def test_scope_with_no_holdings_gives_none_not_zero():
    """対象の銘柄が無ければ分母が無いので None（0% にも例外にもしない）。"""
    fund = _holding(symbol="JP0000", asset_type="fund", shares=10.0, avg_cost=10_000.0)
    stocks = _scoped_summary([fund], {}, _STOCKS)
    assert stocks.total_book_value_jpy is None
    assert stocks.total_market_value_jpy is None
    assert summary_yields(stocks) == (None, None)


def test_scope_all_or_nothing_counts_only_in_scope():
    """時価の欠けた銘柄が対象外なら、対象の時価計は欠けない（母数は対象内の件数）。"""
    stock_ok = _holding(annual_div_ps_gross_override=60.0)
    us_ng = _holding(symbol="AAPL", currency="USD")  # market 無し＝時価が None
    holdings = [stock_ok, us_ng]
    market = {"7203": _market()}
    _, total = compute_rows(holdings, market, 150.0, "gross")
    assert total.total_market_value_jpy is None
    jp = _scoped_summary(holdings, market, ("jp_stock",))
    assert jp.total_market_value_jpy == pytest.approx(250_000)
    assert jp.missing_market_value_count == 0


def test_scope_drops_withdrawal_income_with_the_fund():
    stock = _holding(annual_div_ps_gross_override=60.0)
    fund = _holding(
        symbol="JP0000", asset_type="fund", shares=10.0, avg_cost=10_000.0,
        withdrawal_fixed_jpy=40_000.0,
    )
    holdings = [stock, fund]
    _, total = compute_rows(
        holdings, {"7203": _market()}, 150.0, "gross", withdrawal_mode="fixed"
    )
    assert total.total_numerator_jpy == pytest.approx(46_000)
    stocks = _scoped_summary(
        holdings, {"7203": _market()}, _STOCKS, withdrawal_mode="fixed"
    )
    assert stocks.total_numerator_jpy == pytest.approx(6_000)


def test_scope_does_not_change_row_values():
    """行の値は絞っても同じ（1銘柄それぞれの値）。"""
    rows, _ = compute_rows(_stock_and_fund(), {"7203": _market()}, 150.0, "gross")
    picked = [h for _, h in scope_holdings(_stock_and_fund(), _STOCKS)]
    scoped_rows, _ = compute_rows(picked, {"7203": _market()}, 150.0, "gross")
    assert scoped_rows == rows[:1]


def test_summary_yields_matches_summary_fields():
    _, summary = compute_rows(_stock_and_fund(), {"7203": _market()}, 150.0, "gross")
    assert summary_yields(summary) == (summary.yield_on_cost, summary.yield_on_value)


# ---- 利回りの分子（優待を含む / 含まない） ----
#
# 優待スイッチは KPI カード・配当優待詳細タブの利回り列・Markdown の3箇所に効く。
# 行は両方の値を載せ、表示側は row_yield_keys でキーを選ぶだけにする。

def test_row_carries_both_yield_definitions():
    h = _holding(annual_div_ps_gross_override=60.0, annual_benefit_jpy=2_000.0)
    rows, _ = compute_rows([h], {"7203": _market()}, 150.0, "gross")
    r = rows[0]
    # 簿価 200,000 / 時価 250,000 / 配当 6,000 / 優待 2,000
    assert r["yield_on_cost"] == pytest.approx(8_000 / 200_000)
    assert r["yield_on_value"] == pytest.approx(8_000 / 250_000)
    assert r["yield_on_cost_div_only"] == pytest.approx(6_000 / 200_000)
    assert r["yield_on_value_div_only"] == pytest.approx(6_000 / 250_000)


def test_row_div_only_yield_is_none_for_benefit_only_holding():
    """配当が N/A で優待だけの銘柄は、配当のみの利回りが空（0% にしない）。"""
    h = _holding(annual_benefit_jpy=3_000.0)  # market も override も無い＝配当 N/A
    rows, _ = compute_rows([h], {}, 150.0, "gross")
    r = rows[0]
    assert r["yield_on_cost"] == pytest.approx(3_000 / 200_000)  # 優待込みなら出る
    assert r["yield_on_cost_div_only"] is None
    assert r["yield_on_value_div_only"] is None


def test_row_yield_keys_maps_both_definitions():
    assert row_yield_keys(True) == ("yield_on_cost", "yield_on_value")
    assert row_yield_keys(False) == (
        "yield_on_cost_div_only",
        "yield_on_value_div_only",
    )


def test_summary_yields_drops_benefit_from_the_numerator():
    h = _holding(annual_div_ps_gross_override=60.0, annual_benefit_jpy=2_000.0)
    _, summary = compute_rows([h], {"7203": _market()}, 150.0, "gross")
    assert summary_yields(summary, True) == (
        pytest.approx(8_000 / 200_000),
        pytest.approx(8_000 / 250_000),
    )
    assert summary_yields(summary, False) == (
        pytest.approx(6_000 / 200_000),
        pytest.approx(6_000 / 250_000),
    )
    # 既定は優待込み（従来の Summary.yield_on_cost と一致する）
    assert summary_yields(summary) == (
        summary.yield_on_cost,
        summary.yield_on_value,
    )


def test_summary_yields_none_when_no_dividend_and_benefit_excluded():
    """優待だけの銘柄しか無ければ、配当のみの利回りは None。"""
    h = _holding(annual_benefit_jpy=3_000.0)
    _, summary = compute_rows([h], {}, 150.0, "gross")
    assert summary_yields(summary, True)[0] is not None
    assert summary_yields(summary, False) == (None, None)


def _fx(rate, timestamp="2026-10-01T09:00:00+09:00"):
    from src.models import FXRate

    return FXRate(timestamp=timestamp, pair="USDJPY", rate=rate, source="test")


def test_latest_fx_takes_last_usable_row():
    rows = [_fx(150.0, "a"), _fx(151.0, "b")]
    assert latest_fx(rows).timestamp == "b"


def test_latest_fx_skips_trailing_zero_nan_and_negative_rows():
    """手編集の空欄（from_dict で 0.0）や nan の最終行で有効なレートを失わない。"""
    rows = [_fx(150.0, "ok"), _fx(0.0, "zero"), _fx(float("nan"), "nan"), _fx(-1.0, "neg")]
    assert latest_fx(rows).timestamp == "ok"


def test_latest_fx_returns_none_without_usable_rows():
    assert latest_fx([]) is None
    assert latest_fx([_fx(0.0), _fx(float("inf"))]) is None

