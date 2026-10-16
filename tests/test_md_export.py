from __future__ import annotations

from src.calc import Summary
from src.md_export import build_holdings_markdown


def _summary(**kw) -> Summary:
    base = dict(
        total_book_value_jpy=200000.0,
        total_market_value_jpy=250000.0,
        total_dividend_jpy=10000.0,
        total_benefit_jpy=2000.0,
        total_numerator_jpy=12000.0,
        yield_on_cost=0.06,
        yield_on_value=0.048,
        total_profit_jpy=50000.0,
        profit_rate=0.25,
        missing_market_value_count=0,
        total_book_value_ex_fund_jpy=100000.0,
        total_market_value_ex_fund_jpy=120000.0,
        total_dividend_ex_fund_jpy=8000.0,
        total_benefit_ex_fund_jpy=2000.0,
        total_numerator_ex_fund_jpy=10000.0,
        yield_on_cost_ex_fund=0.10,
        yield_on_value_ex_fund=0.0833,
    )
    base.update(kw)
    return Summary(**base)


def _row(**kw) -> dict:
    base = dict(
        symbol="7203",
        name="トヨタ",
        account="特定",
        asset_type="stock",
        active=True,
        shares=100.0,
        currency="JPY",
        avg_cost=2000.0,
        last_price=2500.0,
        annual_div_ps=100.0,
        annual_dividend_jpy=10000.0,
        annual_benefit_jpy=2000.0,
        annual_dividend_with_benefit_jpy=12000.0,
        book_value_jpy=200000.0,
        market_value_jpy=250000.0,
        yield_on_cost=0.06,
        yield_on_value=0.048,
        yield_on_cost_div_only=0.05,
        yield_on_value_div_only=0.04,
        profit_jpy=50000.0,
        profit_rate=0.25,
        div_forecast_as_of="2026-06-30",
        benefit_detail="",
    )
    base.update(kw)
    return base


def _build(rows, summary=None, **kw) -> str:
    params = dict(
        dividend_mode="gross",
        withdrawal_mode=None,
        fx_rate=155.1234,
        market_fetched_at="2026-08-08T09:12:00+09:00",
        market_source="yfinance",
        generated_at="2026-08-08T21:30:00+09:00",
    )
    params.update(kw)
    return build_holdings_markdown(rows, summary or _summary(), **params)


def _table_lines(text: str, heading: str) -> list[str]:
    """指定した見出し直下のテーブル行（`|` 始まり）だけを返す。"""
    lines = text.splitlines()
    start = lines.index(f"## {heading}")
    out = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        if line.startswith("|"):
            out.append(line)
    return out


def _cells(line: str) -> list[str]:
    """テーブル行をセルへ分解する（エスケープ済みの `\\|` は区切りにしない）。"""
    body = line.strip().strip("|")
    cells, current, escaped = [], "", False
    for ch in body:
        if escaped:
            current += ch
            escaped = False
        elif ch == "\\":
            current += ch
            escaped = True
        elif ch == "|":
            cells.append(current.strip())
            current = ""
        else:
            current += ch
    cells.append(current.strip())
    return cells


def test_escapes_pipe_in_free_text_without_breaking_columns():
    """自由記述の `|` がエスケープされ、列数が保たれること。"""
    text = _build([_row(name="A|B 株式会社", benefit_detail="お米|1kg")])
    lines = _table_lines(text, "銘柄一覧")
    header, _sep, data = lines[0], lines[1], lines[2]
    assert "A\\|B 株式会社" in data
    assert "お米\\|1kg" in data
    assert len(_cells(data)) == len(_cells(header))


def test_collapses_newlines_into_single_row():
    """改行入りの値（旧 CSV 由来）がセルの外へ飛び出さないこと。"""
    text = _build([_row(benefit_detail="クオカード\n1,000円分")])
    lines = _table_lines(text, "銘柄一覧")
    assert len(lines) == 3  # ヘッダ・区切り・データ1行のみ
    assert "クオカード 1,000円分" in lines[2]


def test_missing_values_are_blank_not_na():
    """値が無いセルは空欄で、"N/A" を出さないこと。"""
    text = _build([_row(last_price=None, market_value_jpy=None, profit_jpy=None,
                        profit_rate=None, annual_dividend_jpy=None)],
                  _summary(total_market_value_jpy=None, total_profit_jpy=None,
                           profit_rate=None))
    assert "N/A" not in text
    data = _cells(_table_lines(text, "銘柄一覧")[2])
    header = _cells(_table_lines(text, "銘柄一覧")[0])
    assert data[header.index("現在株価")] == ""
    assert data[header.index("損益率(%)")] == ""


def test_numbers_have_no_thousands_separator():
    """LLM が再計算しやすいよう桁区切りカンマを入れないこと。"""
    text = _build([_row(book_value_jpy=1234567.0)])
    assert "1,234,567" not in text
    assert "1234567" in text


def test_rates_are_scaled_to_percent_with_unit_in_header():
    """率は ×100 した数値で出し、単位はヘッダの (%) が持つこと。"""
    text = _build([_row(profit_rate=0.1644, yield_on_cost=0.0355)])
    header = _cells(_table_lines(text, "銘柄一覧")[0])
    data = _cells(_table_lines(text, "銘柄一覧")[2])
    assert data[header.index("損益率(%)")] == "16.44"
    assert data[header.index("簿価利回り＋優待(%)")] == "3.55"


def test_income_header_follows_withdrawal_mode():
    """収入モードでは画面と同じく「年間配当」→「年間収入」へ入れ替わること。"""
    plain = _build([_row()])
    assert "年間配当(円)" in plain and "年間収入(円)" not in plain

    withdrawal = _build([_row()], withdrawal_mode="fixed")
    assert "年間収入(円)" in withdrawal and "年間配当(円)" not in withdrawal
    # サマリー側のラベルも揃う（片方だけ残ると同じ数字に2つの名前が付く）。
    assert any("年間収入(円)" in line for line in _table_lines(withdrawal, "サマリー"))


def test_every_data_row_matches_header_cell_count():
    """列を足したときの取りこぼしを検出する（ヘッダとデータのセル数一致）。"""
    rows = [_row(), _row(symbol="AAPL", currency="USD", benefit_detail="")]
    lines = _table_lines(_build(rows), "銘柄一覧")
    width = len(_cells(lines[0]))
    assert len(lines) == 2 + len(rows)
    for line in lines[2:]:
        assert len(_cells(line)) == width


def test_status_column_appears_only_with_inactive_rows():
    """無効銘柄が無ければ「状態」列を出さないこと（意味の無い列を常設しない）。"""
    active_only = _build([_row()])
    assert "状態" not in _table_lines(active_only, "銘柄一覧")[0]

    mixed = _build([_row(), _row(symbol="9999", active=False)])
    header = _cells(_table_lines(mixed, "銘柄一覧")[0])
    assert header[-1] == "状態"
    rows = _table_lines(mixed, "銘柄一覧")[2:]
    assert _cells(rows[0])[-1] == "有効"
    assert _cells(rows[1])[-1] == "無効"
    # 集計に入っていないことを注記で明示する（行を足してもサマリーと合わない）。
    assert "サマリーの各合計には一切含まれていない" in mixed


def test_holding_count_reports_inactive_share():
    text = _build([_row(), _row(symbol="9999", active=False)])
    assert "2（うち無効 1）" in text


def test_asset_type_is_localized():
    text = _build([_row(asset_type="fund")])
    header = _cells(_table_lines(text, "銘柄一覧")[0])
    assert _cells(_table_lines(text, "銘柄一覧")[2])[header.index("種別")] == "投信"


def test_notes_explain_tax_rules_only_in_net_mode():
    """税率の注記は税引後モードのときだけ出す（税引前で読むと誤解を生む）。"""
    net = _build([_row()], dividend_mode="net")
    assert "28.28%" in net and "外国税額控除" in net
    gross = _build([_row()], dividend_mode="gross")
    assert "28.28%" not in gross
    assert "源泉徴収前" in gross


def test_notes_explain_withdrawal_scope():
    """取崩モード時、影響が投信に限られることを注記すること。"""
    text = _build([_row()], withdrawal_mode="rate")
    assert "定率取崩" in text
    assert "株式・ETF の行は配当のまま" in text


def test_shares_follow_fund_share_unit():
    """投信の株数が単位設定に追随し、株式は影響を受けないこと。"""
    rows = [
        _row(symbol="JP90C000H1T1", name="投信", asset_type="fund", shares=206.7284),
        _row(),  # 株式（100株）
    ]
    per10k = _build(rows)
    header = _cells(_table_lines(per10k, "銘柄一覧")[0])
    assert "株数(投信:万口)" in header, header
    body = [_cells(line) for line in _table_lines(per10k, "銘柄一覧")[2:]]
    col = header.index("株数(投信:万口)")
    assert body[0][col] == "206.73" and body[1][col] == "100.00"

    per1 = _build(rows, fund_share_unit="per1")
    header1 = _cells(_table_lines(per1, "銘柄一覧")[0])
    assert "株数(投信:口数)" in header1, header1
    body1 = [_cells(line) for line in _table_lines(per1, "銘柄一覧")[2:]]
    col1 = header1.index("株数(投信:口数)")
    # 投信だけ 1口単位（× 10,000）。株式の株数は動かさない。
    assert body1[0][col1] == "2067284.00"
    assert body1[1][col1] == "100.00"


def test_fund_share_unit_appears_in_conditions_and_notes():
    """単位は表の数字を変えるので「出力条件」に出し、前提の文言も入れ替わること。"""
    per10k = _build([_row(asset_type="fund")])
    assert "1万口単位（口数÷10,000）" in per10k
    assert "口数 ÷ 10,000" in per10k

    per1 = _build([_row(asset_type="fund")], fund_share_unit="per1")
    conditions = "\n".join(_table_lines(per1, "出力条件"))
    assert "投信の口数単位" in conditions and "1口単位" in conditions
    assert "口数 ÷ 10,000" not in per1, "1口単位モードで旧い前提が残っている"
    assert "口数そのもの（1口単位）" in per1


# ---- 利回りの集計対象（yield_scope） ----

def test_yield_scope_appears_in_conditions():
    """サマリーの数値そのものが変わる設定なので「出力条件」に出す。"""
    conditions = "\n".join(_table_lines(_build([_row()]), "出力条件"))
    assert "利回りの対象" in conditions and "投信込み" in conditions
    conditions = "\n".join(
        _table_lines(_build([_row()], yield_scope="ex_fund"), "出力条件")
    )
    assert "投信除く" in conditions


def test_fx_fetched_at_appears_in_conditions():
    """為替の取得日時は株価と別に書く（為替だけ古いことがある）。"""
    conditions = "\n".join(_table_lines(
        _build([_row()], fx_fetched_at="2026-08-01T15:00:00+09:00"), "出力条件"
    ))
    assert "| USDJPYの取得日時 | 2026-08-01T15:00:00+09:00 |" in conditions
    # 未取得は空欄（N/A を出さない方針に揃える）
    unset = "\n".join(_table_lines(_build([_row()]), "出力条件"))
    assert "| USDJPYの取得日時 |  |" in unset


def test_yield_scope_switches_summary_values_and_labels():
    summary = "\n".join(_table_lines(_build([_row()]), "サマリー"))
    assert "| 簿価利回り＋優待(%) | 6.00 |" in summary
    assert "| 時価利回り＋優待(%) | 4.80 |" in summary
    summary = "\n".join(_table_lines(_build([_row()], yield_scope="ex_fund"), "サマリー"))
    assert "| 簿価利回り＋優待(投信除く)(%) | 10.00 |" in summary
    assert "| 時価利回り＋優待(投信除く)(%) | 8.33 |" in summary


def test_yield_scope_does_not_touch_the_holdings_table():
    """銘柄一覧の利回り列は集計対象の切替と無関係（1銘柄それぞれの自己利回り）。"""
    rows = _table_lines(_build([_row()], yield_scope="ex_fund"), "銘柄一覧")
    assert "簿価利回り＋優待(%)" in rows[0]
    assert "投信除く" not in rows[0]
    assert "| 6.00 |" in rows[-1]


def test_notes_explain_fund_exclusion_only_when_excluding():
    text = _build([_row()], yield_scope="ex_fund")
    assert "投資信託を除いた値" in text
    assert "分子（配当＋優待）と分母（簿価／時価）の**両方**" in text
    assert "投信除外の影響を受けない" in text
    assert "投資信託を除いた値" not in _build([_row()])


def test_fund_exclusion_note_follows_the_benefit_switch():
    """投信除外の注記が分子を「配当＋優待」で決め打ちしないこと。

    優待を外した出力の中で注記だけが別の計算条件を説明すると、LLM は分子を
    取り違えたまま尤もらしい分析を返す（数字自体は正しいので気付けない）。
    """
    text = _build([_row()], yield_scope="ex_fund", with_benefit=False)
    assert "分子（配当のみ）と分母（簿価／時価）の**両方**" in text
    assert "分子（配当＋優待）" not in text
    # 銘柄一覧側の列名も実際に出しているヘッダと揃っていること
    assert "`簿価利回り(%)`" in text
    with_benefit = _build([_row()], yield_scope="ex_fund")
    assert "`簿価利回り＋優待(%)`" in with_benefit


def test_notes_warn_withdrawal_income_is_dropped_when_excluding_funds():
    """取崩モード × 投信除外は「取崩収入ゼロの利回り」になる旨を出す。"""
    text = _build([_row()], withdrawal_mode="fixed", yield_scope="ex_fund")
    assert "取崩収入をゼロとした値" in text
    # 投信を含める側では出さない（含めているのに除外の説明が付くと誤読する）
    assert "取崩収入をゼロとした値" not in _build([_row()], withdrawal_mode="fixed")


# ---- 優待スイッチ（with_benefit） ----

def test_benefit_switch_appears_in_conditions():
    conditions = "\n".join(_table_lines(_build([_row()]), "出力条件"))
    assert "| 利回りの優待 | 含む |" in conditions
    conditions = "\n".join(
        _table_lines(_build([_row()], with_benefit=False), "出力条件")
    )
    assert "| 利回りの優待 | 含まない |" in conditions


def test_benefit_switch_changes_summary_yield_values_and_labels():
    summary = "\n".join(
        _table_lines(_build([_row()], with_benefit=False), "サマリー")
    )
    # 分子が配当だけ（10,000）になる: 10,000 / 200,000 = 5.00%
    assert "| 簿価利回り(%) | 5.00 |" in summary
    assert "| 時価利回り(%) | 4.00 |" in summary
    assert "＋優待" not in summary


def test_benefit_switch_swaps_the_row_yield_columns():
    """画面と同じく銘柄一覧の利回り列も配当のみへ差し替わること。"""
    text = _build([_row()], with_benefit=False)
    header = _cells(_table_lines(text, "銘柄一覧")[0])
    data = _cells(_table_lines(text, "銘柄一覧")[2])
    assert "簿価利回り(%)" in header and "簿価利回り＋優待(%)" not in header
    assert data[header.index("簿価利回り(%)")] == "5.00"
    assert data[header.index("時価利回り(%)")] == "4.00"


def test_notes_state_the_yield_numerator():
    assert "利回りの分子は「配当＋優待」。" in _build([_row()])
    without = _build([_row()], with_benefit=False)
    assert "**配当のみ**（優待を含めない）" in without
    assert "利回りの分子とは一致しない" in without


def test_benefit_and_scope_suffixes_combine():
    summary = "\n".join(
        _table_lines(_build([_row()], yield_scope="ex_fund"), "サマリー")
    )
    assert "| 簿価利回り＋優待(投信除く)(%) | 10.00 |" in summary
    summary = "\n".join(
        _table_lines(
            _build([_row()], yield_scope="ex_fund", with_benefit=False), "サマリー"
        )
    )
    # 投信除外の配当のみ: 8,000 / 100,000 = 8.00%
    assert "| 簿価利回り(投信除く)(%) | 8.00 |" in summary
