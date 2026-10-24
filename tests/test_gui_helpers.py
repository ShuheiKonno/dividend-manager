from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

import src.gui as gui
import src.io_csv as io_csv
from src.models import Holding, HistoryRecord, MarketData


def _holding(**overrides) -> Holding:
    base = dict(
        symbol="7203",
        name="トヨタ",
        asset_type="stock",
        currency="JPY",
        shares=100.0,
        avg_cost=2000.0,
        account="",
        annual_div_ps_gross_override=100.0,
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


def _window_stub(holdings, market_map):
    class WindowStub:
        dividend_mode = "net"
        fx_rate = None
        show_inactive = False
        history = []
        asset_scope = gui.DEFAULT_ASSET_SCOPE

        def _save_or_notify(self, action):
            action()
            return True

        def _refresh_trend(self):
            pass

        def _refresh_view(self):
            pass

        def _notify(self, message, error=False):
            self.notes.append((message, error))

        _record_history_snapshot = gui.DividendWindow._record_history_snapshot
        _split_notice = gui.DividendWindow._split_notice

    stub = WindowStub()
    stub.holdings = holdings
    stub.market_data_map = market_map
    stub.history = []
    stub.notes = []
    return stub


def test_parse_num_rejects_non_finite_values():
    for text in ("nan", "inf", "-inf"):
        with pytest.raises(ValueError):
            gui._parse_num(text)
    assert math.isfinite(gui._parse_num("１２３"))


def test_row_for_holding_index():
    assert gui.row_for_holding_index([2, 5, 7], 5) == 1
    assert gui.row_for_holding_index([2, 5, 7], 2) == 0
    assert gui.row_for_holding_index([2, 5, 7], 9) is None  # 削除後など対象なし
    assert gui.row_for_holding_index([], 0) is None


def test_format_cell_translates_asset_type_and_shows_account():
    row = {"account": "NISA", "asset_type": "fund", "active": True}
    assert gui._format_cell(row, "account") == ("NISA", None)
    assert gui._format_cell(row, "asset_type")[0] == "投信"
    assert gui._format_cell({"asset_type": "stock"}, "asset_type")[0] == "株式"
    # 未知の値はそのまま出す（CSV を手で編集した場合に消えないように）
    assert gui._format_cell({"asset_type": "bond"}, "asset_type")[0] == "bond"


def test_format_cell_dims_inactive_rows_over_profit_color():
    """無効銘柄は損益の赤青より優先して淡色になること。"""
    active = {"profit_jpy": 1000.0, "active": True}
    inactive = {"profit_jpy": 1000.0, "active": False}
    _, active_color = gui._format_cell(active, "profit_jpy")
    text, inactive_color = gui._format_cell(inactive, "profit_jpy")
    assert active_color is not None and active_color.name() != inactive_color.name()
    assert inactive_color.name() == gui.theme.TEXT_DISABLED
    assert text == "1,000"  # 表示文字列は淡色化で変わらない
    # active キーが無い行（無効銘柄を含めない既定パス）は従来どおり損益色
    assert gui._format_cell({"profit_jpy": 1000.0}, "profit_jpy")[1] is not None


def test_rate_and_date_validation():
    assert gui._optional_rate(0.0, "率") == 0.0
    assert gui._optional_rate(1.0, "率") == 1.0
    with pytest.raises(ValueError):
        gui._optional_rate(1.01, "率")
    assert gui._optional_iso_date("2026-05-30") == "2026-05-30"
    with pytest.raises(ValueError):
        gui._optional_iso_date("2026-02-30")


def test_history_snapshot_uses_gross_dividend(monkeypatch):
    holding = _holding()
    market = MarketData(
        symbol="7203", currency="JPY", last_price=2500.0, price_as_of="2026-05-30",
        annual_div_ps_gross=100.0, annual_div_ps_net=None,
        div_forecast_as_of="2026-05-30", source="test", fetched_at=None,
        price_fetched_at=gui.today_jst_str() + "T09:00:00+09:00",
    )
    saved = []
    monkeypatch.setattr(gui, "save_history", lambda rows: saved.extend(rows))
    stub = _window_stub([holding], {"7203": market})
    gui.DividendWindow._record_history_snapshot(stub)
    assert saved[0].total_dividend_jpy == 10_000.0


def test_history_snapshot_skips_missing_market_value(monkeypatch):
    called = False

    def save(_rows):
        nonlocal called
        called = True

    monkeypatch.setattr(gui, "save_history", save)
    stub = _window_stub([_holding()], {})
    gui.DividendWindow._record_history_snapshot(stub)
    assert called is False
    assert stub.history == []


def _snapshot_market(**overrides):
    values = dict(
        symbol="7203", currency="JPY", last_price=2500.0, price_as_of="2025-01-01",
        annual_div_ps_gross=100.0, annual_div_ps_net=None,
        div_forecast_as_of="2025-01-01", source="test", fetched_at=None,
        price_fetched_at=gui.today_jst_str() + "T09:00:00+09:00",
    )
    values.update(overrides)
    return MarketData(**values)


@pytest.mark.parametrize("price_fetched_at", [None, "2025-01-01T09:00:00+09:00"])
def test_history_snapshot_skips_stale_prices(monkeypatch, price_fetched_at):
    saved = []
    monkeypatch.setattr(gui, "save_history", lambda rows: saved.append(rows))
    market = _snapshot_market(price_fetched_at=price_fetched_at)
    # 全項目の日時が今日でも、株価の成功日時が不明・古ければ記録しない。
    market.fetched_at = gui.today_jst_str() + "T09:00:00+09:00"
    stub = _window_stub([_holding()], {"7203": market})
    previous = _history(gui.today_jst_str(), total_market_value_jpy=123.0)
    stub.history = [previous]
    notice = gui.DividendWindow._record_history_snapshot(stub)
    assert notice == "本日取得した株価が揃うまで推移を記録しません"
    assert saved == []
    assert stub.history == [previous]


def test_history_snapshot_checks_every_active_price(monkeypatch):
    saved = []
    monkeypatch.setattr(gui, "save_history", lambda rows: saved.append(rows))
    stub = _window_stub(
        [_holding(), _holding(symbol="OLD")],
        {"7203": _snapshot_market(), "OLD": _snapshot_market(
            symbol="OLD", price_fetched_at="2025-01-01",
        )},
    )
    assert gui.DividendWindow._record_history_snapshot(stub)
    assert saved == []


def test_history_snapshot_skips_pending_splits(monkeypatch):
    saved = []
    monkeypatch.setattr(gui, "save_history", lambda rows: saved.append(rows))
    market = _snapshot_market(recent_splits=[(gui.today_jst_str(), 2.0)])
    stub = _window_stub([_holding()], {"7203": market})
    notice = gui.DividendWindow._record_history_snapshot(stub)
    assert notice == "分割を反映するまで推移を記録しません"
    assert saved == []
    assert stub.history == []
    # 数値を反映済みにすれば、同じ取得結果で履歴を記録できる。
    stub.holdings[0].split_applied_through = gui.today_jst_str()
    assert gui.DividendWindow._record_history_snapshot(stub) is None
    assert len(saved) == 1


@pytest.mark.parametrize("with_cached_fund", [False, True])
def test_history_snapshot_allows_fund_book_fallback_and_inactive_prices(
    monkeypatch, with_cached_fund,
):
    saved = []
    monkeypatch.setattr(gui, "save_history", lambda rows: saved.append(rows))
    fund = _holding(symbol="FUND", asset_type="fund")
    stub = _window_stub([_holding(), fund, _holding(symbol="OLD", active=False)], {
        "7203": _snapshot_market(),
    })
    if with_cached_fund:
        stub.market_data_map["FUND"] = _snapshot_market(
            symbol="FUND", last_price=None, price_fetched_at=None,
        )
    assert gui.DividendWindow._record_history_snapshot(stub) is None
    assert saved[0][0].total_market_value_jpy == 450_000.0


def test_history_snapshot_requires_fresh_fund_market_price(monkeypatch):
    saved = []
    monkeypatch.setattr(gui, "save_history", lambda rows: saved.append(rows))
    stub = _window_stub([_holding(asset_type="fund")], {
        "7203": _snapshot_market(price_fetched_at=None),
    })
    assert gui.DividendWindow._record_history_snapshot(stub)
    assert saved == []


@pytest.mark.parametrize("pending", [False, True])
def test_market_finished_records_fresh_price_and_reports_pending_split(monkeypatch, pending):
    from types import SimpleNamespace

    saved = []
    monkeypatch.setattr(gui, "save_history", lambda rows: saved.append(rows))
    monkeypatch.setattr(gui, "save_market_data", lambda _rows: None)
    stub = _window_stub([_holding()], {"7203": _snapshot_market(price_fetched_at=None)})
    gui.DividendWindow._record_history_snapshot(stub)  # 起動時の古いキャッシュでは保存しない。
    assert saved == []
    # F5 の株価は成功、配当は失敗。終値日が古くても当日取得成功なら記録する。
    market = _snapshot_market(
        recent_splits=[(gui.today_jst_str(), 2.0)] if pending else [],
    )
    result = SimpleNamespace(
        rows=[market], source="yfinance", name_updates={}, errors=["配当取得失敗"],
    )
    gui.DividendWindow._on_market_finished(stub, gui.MarketUpdate(market=result))
    assert len(saved) == (0 if pending else 1)
    assert stub.market_data_map["7203"].price_fetched_at == market.price_fetched_at
    if pending:
        assert "分割を反映するまで推移を記録しません" in stub.notes[-1][0]
    else:
        assert saved[0][0].record_date == gui.today_jst_str()
        assert saved[0][0].total_market_value_jpy == 250_000.0


def test_market_finished_reports_stale_price_without_recording(monkeypatch):
    from types import SimpleNamespace

    saved = []
    monkeypatch.setattr(gui, "save_history", lambda rows: saved.append(rows))
    monkeypatch.setattr(gui, "save_market_data", lambda _rows: None)
    market = _snapshot_market(price_fetched_at=None)
    stub = _window_stub([_holding()], {})
    result = SimpleNamespace(
        rows=[market], source="yfinance", name_updates={}, errors=["株価取得失敗"],
    )
    gui.DividendWindow._on_market_finished(stub, gui.MarketUpdate(market=result))
    assert saved == []
    assert "本日取得した株価が揃うまで推移を記録しません" in stub.notes[-1][0]


# ---- エクスポート / 履歴取込アクション（QFileDialog をスタブ化して本体を実行） ----


def _io_stub(holdings, history):
    """エクスポート/取込アクションを unbound 呼び出しするための最小スタブ。"""
    notes: list[tuple[str, bool]] = []

    class WindowStub:
        def _notify(self, message, error=False):
            notes.append((message, error))

        def _save_or_notify(self, action):
            action()
            return True

        def _refresh_view(self):
            pass

        def _reload_all(self):
            self.reloaded = True

        _with_csv_suffix = staticmethod(gui.DividendWindow._with_csv_suffix)
        _with_md_suffix = staticmethod(gui.DividendWindow._with_md_suffix)
        _scoped = gui.DividendWindow._scoped

        # Markdown エクスポートは compute_rows を呼び直すので、表示条件を持つ。
        market_data_map: dict = {}
        fx_rate = None
        dividend_mode = "gross"
        withdrawal_mode = None
        asset_scope = gui.DEFAULT_ASSET_SCOPE
        income_benefit = "without"
        show_inactive = False
        market_fetched_at = None
        fx_fetched_at = None
        market_source = None
        fund_share_unit = gui.DEFAULT_FUND_SHARE_UNIT

    stub = WindowStub()
    stub.holdings = holdings
    stub.history = history
    stub.notes = notes
    stub.reloaded = False
    return stub


def _history(date: str, **kw) -> HistoryRecord:
    base = dict(
        record_date=date,
        total_market_value_jpy=None,
        total_book_value_jpy=None,
        total_dividend_jpy=None,
        total_benefit_jpy=None,
        total_profit_jpy=None,
        total_withdrawal_income_jpy=None,
        total_fixed_withdrawal_income_jpy=None,
        source="app",
    )
    base.update(kw)
    return HistoryRecord(**base)


def test_export_then_import_history_roundtrip(monkeypatch, tmp_path):
    """履歴をエクスポートし、別ウィンドウへ取り込んで往復できること。"""
    monkeypatch.setattr(io_csv, "DATA_DIR", tmp_path)
    monkeypatch.setattr(io_csv, "BACKUP_DIR", tmp_path / "backup")
    target = tmp_path / "history.csv"
    exporter = _io_stub([], [_history("2024-01-01", total_dividend_jpy=5.0)])
    monkeypatch.setattr(gui.QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    gui.DividendWindow.action_export_history(exporter)
    assert target.exists()

    importer = _io_stub([], [])
    monkeypatch.setattr(gui.QFileDialog, "getOpenFileName", lambda *a, **k: (str(target), ""))
    monkeypatch.setattr(gui, "save_history", lambda rows: None)
    gui.DividendWindow.action_import_history(importer)
    assert [r.record_date for r in importer.history] == ["2024-01-01"]
    assert importer.history[0].total_dividend_jpy == 5.0


def test_export_holdings_appends_csv_suffix(monkeypatch, tmp_path):
    """保存ダイアログが拡張子を返さなくても .csv を補ってから書き出すこと。"""
    captured: dict = {}
    monkeypatch.setattr(gui.QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "noext"), ""))
    monkeypatch.setattr(gui, "export_holdings_csv", lambda path, rows: captured.update(path=path))
    gui.DividendWindow.action_export_holdings(_io_stub([_holding()], []))
    assert captured["path"].endswith(".csv")


def test_export_holdings_markdown_appends_md_suffix(monkeypatch, tmp_path):
    """保存ダイアログが拡張子を返さなくても .md を補ってから書き出すこと。"""
    captured: dict = {}
    monkeypatch.setattr(
        gui.QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "noext"), "")
    )
    monkeypatch.setattr(
        gui, "write_text_export", lambda path, text: captured.update(path=path, text=text)
    )
    gui.DividendWindow.action_export_holdings_markdown(_io_stub([_holding()], []))
    assert captured["path"].endswith(".md")
    # 計算済み行が入っていること（CSV 版と違い入力値の写しではない）。
    assert "## サマリー" in captured["text"]
    assert "7203" in captured["text"]


def test_export_holdings_markdown_follows_show_inactive(monkeypatch, tmp_path):
    """無効銘柄の出力は画面の show_inactive トグルに従うこと。"""
    captured: dict = {}
    monkeypatch.setattr(
        gui.QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "a.md"), "")
    )
    monkeypatch.setattr(gui, "write_text_export", lambda path, text: captured.update(text=text))
    holdings = [_holding(), _holding(symbol="9999", name="休眠", active=False)]

    stub = _io_stub(holdings, [])
    gui.DividendWindow.action_export_holdings_markdown(stub)
    assert "9999" not in captured["text"]
    assert "無効を除く" in stub.notes[-1][0]

    stub = _io_stub(holdings, [])
    stub.show_inactive = True
    gui.DividendWindow.action_export_holdings_markdown(stub)
    assert "9999" in captured["text"]
    assert "無効を除く" not in stub.notes[-1][0]


def test_export_markdown_follows_asset_scope_but_csv_exports_all(monkeypatch, tmp_path):
    """Markdown は表示対象の資産に従い、CSV は全件（取込が全置換のため）を出す。"""
    captured: dict = {}
    monkeypatch.setattr(
        gui.QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "a.md"), "")
    )
    monkeypatch.setattr(gui, "write_text_export", lambda path, text: captured.update(text=text))
    holdings = [_holding(), _holding(symbol="AAPL", name="アップル", currency="USD")]
    stub = _io_stub(holdings, [])
    stub.asset_scope = ("jp_stock",)
    gui.DividendWindow.action_export_holdings_markdown(stub)
    assert "AAPL" not in captured["text"]
    assert "7203" in captured["text"]
    assert "対象: 日本株" in stub.notes[-1][0]

    csv_path = tmp_path / "holdings.csv"
    monkeypatch.setattr(
        gui.QFileDialog, "getSaveFileName", lambda *a, **k: (str(csv_path), "")
    )
    gui.DividendWindow.action_export_holdings(stub)
    exported = csv_path.read_text(encoding="utf-8-sig")
    assert "AAPL" in exported and "7203" in exported


def test_snapshot_notice_names_out_of_scope_split_holdings(monkeypatch):
    """表示対象外の銘柄の分割で記録が止まるとき、その銘柄を通知文に書く。"""
    stub = _window_stub([_holding(), _holding(symbol="AAPL", currency="USD")], {})
    stub.asset_scope = ("jp_stock",)
    monkeypatch.setattr(
        gui.splits, "pending_holdings", lambda *a, **k: [(1, [("2026-10-01", 2.0)])]
    )
    notice = gui.DividendWindow._record_history_snapshot(stub)
    assert "分割を反映するまで推移を記録しません" in notice
    assert "表示対象外の銘柄: AAPL" in notice


def test_out_of_scope_note():
    stub = SimpleNamespace(asset_scope=("jp_stock",))
    note = gui.DividendWindow._out_of_scope_note
    assert note(stub, _holding()) == ""
    assert "米国株は表示対象外" in note(stub, _holding(symbol="AAPL", currency="USD"))
    assert "投信は表示対象外" in note(stub, _holding(asset_type="fund"))


def test_export_holdings_markdown_empty_guard_skips_dialog(monkeypatch):
    """保有が空ならダイアログを開かずエラー通知して早期 return すること。"""
    opened = {"dialog": False}

    def fake_dialog(*_a, **_k):
        opened["dialog"] = True
        return ("", "")

    monkeypatch.setattr(gui.QFileDialog, "getSaveFileName", fake_dialog)
    stub = _io_stub([], [])
    gui.DividendWindow.action_export_holdings_markdown(stub)
    assert opened["dialog"] is False
    assert stub.notes and stub.notes[-1][1] is True


def test_export_history_empty_guard_skips_dialog(monkeypatch):
    """履歴が空ならダイアログを開かずエラー通知して早期 return すること。"""
    opened = {"dialog": False}

    def fake_dialog(*_a, **_k):
        opened["dialog"] = True
        return ("", "")

    monkeypatch.setattr(gui.QFileDialog, "getSaveFileName", fake_dialog)
    stub = _io_stub([], [])
    gui.DividendWindow.action_export_history(stub)
    assert opened["dialog"] is False
    assert stub.notes and stub.notes[-1][1] is True  # error 通知で終わる


def test_import_holdings_replaces_after_confirm(monkeypatch, tmp_path):
    """確認 Yes で現在の保有を取込内容に全置換し、保存・再読込すること。"""
    monkeypatch.setattr(io_csv, "DATA_DIR", tmp_path)
    monkeypatch.setattr(io_csv, "BACKUP_DIR", tmp_path / "backup")
    target = tmp_path / "holdings.csv"
    io_csv.export_holdings_csv(target, [_holding(symbol="7203"), _holding(symbol="AAPL")])

    stub = _io_stub([_holding(symbol="9999")], [])  # 既存1件
    monkeypatch.setattr(gui.QFileDialog, "getOpenFileName", lambda *a, **k: (str(target), ""))
    monkeypatch.setattr(
        gui.QMessageBox, "question", lambda *a, **k: gui.QMessageBox.StandardButton.Yes
    )
    saved: dict = {}
    monkeypatch.setattr(gui, "save_holdings", lambda rows: saved.update(rows=rows))
    gui.DividendWindow.action_import_holdings(stub)
    assert [h.symbol for h in saved["rows"]] == ["7203", "AAPL"]  # 全置換
    assert stub.reloaded is True


def test_import_holdings_declined_keeps_current(monkeypatch, tmp_path):
    """確認 No なら保存も再読込もせず現在の保有を維持すること。"""
    monkeypatch.setattr(io_csv, "DATA_DIR", tmp_path)
    monkeypatch.setattr(io_csv, "BACKUP_DIR", tmp_path / "backup")
    target = tmp_path / "holdings.csv"
    io_csv.export_holdings_csv(target, [_holding(symbol="7203")])

    stub = _io_stub([_holding(symbol="9999")], [])
    monkeypatch.setattr(gui.QFileDialog, "getOpenFileName", lambda *a, **k: (str(target), ""))
    monkeypatch.setattr(
        gui.QMessageBox, "question", lambda *a, **k: gui.QMessageBox.StandardButton.No
    )
    saved = {"called": False}
    monkeypatch.setattr(gui, "save_holdings", lambda rows: saved.update(called=True))
    gui.DividendWindow.action_import_holdings(stub)
    assert saved["called"] is False
    assert stub.reloaded is False


def test_import_holdings_shows_preview_without_fund_unit_conversion(
    monkeypatch, tmp_path
):
    """確認ダイアログにコード・銘柄名・株数・簿価のプレビューが出て、投信口数を変換しないこと。"""
    monkeypatch.setattr(io_csv, "DATA_DIR", tmp_path)
    monkeypatch.setattr(io_csv, "BACKUP_DIR", tmp_path / "backup")
    target = tmp_path / "holdings.csv"
    io_csv.export_holdings_csv(
        target,
        [
            _holding(symbol="7203", name="トヨタ", shares=100.0, avg_cost=2000.0),
            _holding(
                symbol="0331418A",
                name="eMAXIS Slim",
                asset_type="fund",
                shares=206.7284,
                avg_cost=10000.0,
            ),
        ],
    )
    stub = _io_stub([_holding(symbol="9999")], [])
    stub.fund_share_unit = "kuchi"  # 1口表示中でもプレビューは CSV 生値（206.7284）のまま
    monkeypatch.setattr(
        gui.QFileDialog, "getOpenFileName", lambda *a, **k: (str(target), "")
    )
    captured_prompt: list[str] = []

    def fake_question(_parent, _title, text, *_args):
        captured_prompt.append(text)
        return gui.QMessageBox.StandardButton.No

    monkeypatch.setattr(gui.QMessageBox, "question", fake_question)
    gui.DividendWindow.action_import_holdings(stub)
    assert len(captured_prompt) == 1
    prompt = captured_prompt[0]
    assert "7203" in prompt and "トヨタ" in prompt
    assert "株数 100" in prompt and "¥200,000" in prompt
    assert "0331418A" in prompt and "株数 206.7284" in prompt
    assert "株数 2,067,284" not in prompt  # 1万倍の口数変換をしない


def test_import_holdings_rejects_market_csv_before_confirm(monkeypatch, tmp_path):
    """市場データ CSV や不正数値 CSV を選んだら確認ダイアログを出さずエラー通知で中止すること。"""
    market_csv = tmp_path / "market_data.csv"
    market_csv.write_text(
        "symbol,currency,last_price\n7203,JPY,3000\n", encoding="utf-8"
    )
    stub = _io_stub([_holding(symbol="9999")], [])
    monkeypatch.setattr(
        gui.QFileDialog, "getOpenFileName", lambda *a, **k: (str(market_csv), "")
    )
    asked = {"called": False}
    monkeypatch.setattr(
        gui.QMessageBox,
        "question",
        lambda *a, **k: asked.update(called=True) or gui.QMessageBox.StandardButton.Yes,
    )
    saved = {"called": False}
    monkeypatch.setattr(gui, "save_holdings", lambda rows: saved.update(called=True))

    gui.DividendWindow.action_import_holdings(stub)
    assert asked["called"] is False
    assert saved["called"] is False
    assert stub.reloaded is False
    assert stub.notes and stub.notes[-1][1] is True
    assert "shares" in stub.notes[-1][0]



# ---- データフォルダの変更（QFileDialog / QMessageBox をスタブ化して本体を実行） ----


def _data_dir_stub(monkeypatch, tmp_path, holdings=(), history=()):
    """action_change_data_dir を unbound 呼び出しするための土台を用意する。

    現在のデータフォルダ・set_data_dir・copy_data_files・init_empty_files を
    すべて差し替え、実ユーザーの %APPDATA% と data/ を触らせない。
    """
    current = tmp_path / "current"
    current.mkdir()
    monkeypatch.setattr(io_csv, "DATA_DIR", current)
    calls: dict = {"set": [], "copied": [], "init": 0}
    monkeypatch.setattr(gui, "set_data_dir", lambda p: calls["set"].append(p))
    monkeypatch.setattr(gui, "copy_data_files", lambda s, d: calls["copied"].append((s, d)))
    monkeypatch.setattr(gui, "init_empty_files", lambda: calls.update(init=calls["init"] + 1))
    stub = _io_stub(list(holdings), list(history))
    stub._busy = False
    return stub, current, calls


def _answer(monkeypatch, button):
    monkeypatch.setattr(gui.QMessageBox, "question", lambda *a, **k: button)


def test_change_data_dir_copies_into_empty_folder(monkeypatch, tmp_path):
    """空フォルダを選び「はい」なら、現在のデータをコピーしてから切り替えること。"""
    stub, current, calls = _data_dir_stub(monkeypatch, tmp_path, [_holding()])
    target = tmp_path / "empty"
    target.mkdir()
    monkeypatch.setattr(gui.QFileDialog, "getExistingDirectory", lambda *a, **k: str(target))
    _answer(monkeypatch, gui.QMessageBox.StandardButton.Yes)

    gui.DividendWindow.action_change_data_dir(stub)
    assert calls["copied"] == [(current, target)]
    assert calls["set"] == [target]
    assert calls["init"] == 1
    assert stub.reloaded is True


def test_change_data_dir_without_copy_starts_empty(monkeypatch, tmp_path):
    """空フォルダで「いいえ」なら、コピーせず切り替えること（空で開始）。"""
    stub, _current, calls = _data_dir_stub(monkeypatch, tmp_path, [_holding()])
    target = tmp_path / "empty"
    target.mkdir()
    monkeypatch.setattr(gui.QFileDialog, "getExistingDirectory", lambda *a, **k: str(target))
    _answer(monkeypatch, gui.QMessageBox.StandardButton.No)

    gui.DividendWindow.action_change_data_dir(stub)
    assert calls["copied"] == []
    assert calls["set"] == [target]
    assert stub.reloaded is True


def test_change_data_dir_cancel_keeps_current(monkeypatch, tmp_path):
    """確認をキャンセルしたら、コピーも切替も再読込もしないこと。"""
    stub, _current, calls = _data_dir_stub(monkeypatch, tmp_path, [_holding()])
    target = tmp_path / "empty"
    target.mkdir()
    monkeypatch.setattr(gui.QFileDialog, "getExistingDirectory", lambda *a, **k: str(target))
    _answer(monkeypatch, gui.QMessageBox.StandardButton.Cancel)

    gui.DividendWindow.action_change_data_dir(stub)
    assert calls == {"set": [], "copied": [], "init": 0}
    assert stub.reloaded is False


def test_change_data_dir_existing_data_is_not_overwritten(monkeypatch, tmp_path):
    """データがあるフォルダを選んだ場合はコピーせず、そのデータを読み込むこと。"""
    stub, _current, calls = _data_dir_stub(monkeypatch, tmp_path, [_holding()])
    target = tmp_path / "existing"
    target.mkdir()
    (target / "holdings.csv").write_text("symbol\n7203\n", encoding="utf-8")
    monkeypatch.setattr(gui.QFileDialog, "getExistingDirectory", lambda *a, **k: str(target))
    _answer(monkeypatch, gui.QMessageBox.StandardButton.Yes)

    gui.DividendWindow.action_change_data_dir(stub)
    assert calls["copied"] == [], "既存データのあるフォルダへコピーしている"
    assert calls["set"] == [target]


def test_change_data_dir_same_folder_is_noop(monkeypatch, tmp_path):
    """現在と同じフォルダを選んだら切り替えず、通知だけして戻ること。"""
    stub, current, calls = _data_dir_stub(monkeypatch, tmp_path)
    monkeypatch.setattr(gui.QFileDialog, "getExistingDirectory", lambda *a, **k: str(current))
    gui.DividendWindow.action_change_data_dir(stub)
    assert calls["set"] == []
    assert stub.reloaded is False
    assert stub.notes and "同じフォルダ" in stub.notes[-1][0]


def test_change_data_dir_blocked_while_busy(monkeypatch, tmp_path):
    """取得中はフォルダ選択ダイアログすら開かないこと（旧フォルダへ書き戻す）。"""
    stub, _current, calls = _data_dir_stub(monkeypatch, tmp_path)
    stub._busy = True
    opened = {"dialog": False}

    def fake_dialog(*_a, **_k):
        opened["dialog"] = True
        return ""

    monkeypatch.setattr(gui.QFileDialog, "getExistingDirectory", fake_dialog)
    gui.DividendWindow.action_change_data_dir(stub)
    assert opened["dialog"] is False
    assert calls["set"] == []
    assert stub.notes and stub.notes[-1][1] is True


def test_change_data_dir_reports_failure(monkeypatch, tmp_path):
    """切替に失敗（保存不可等）したらエラー通知し、再読込しないこと。"""
    stub, _current, calls = _data_dir_stub(monkeypatch, tmp_path)
    target = tmp_path / "empty"
    target.mkdir()
    monkeypatch.setattr(gui.QFileDialog, "getExistingDirectory", lambda *a, **k: str(target))
    _answer(monkeypatch, gui.QMessageBox.StandardButton.No)
    monkeypatch.setattr(
        gui, "set_data_dir",
        lambda _p: (_ for _ in ()).throw(OSError("書き込めません")),
    )
    gui.DividendWindow.action_change_data_dir(stub)
    assert calls["init"] == 0
    assert stub.reloaded is False
    assert stub.notes and stub.notes[-1][1] is True


# ---- F5 で為替も取る（MarketWorker / _on_market_finished） ----

def _fx_rate(rate: float = 150.5, timestamp: str = "2026-10-17T09:00:00+09:00"):
    from src.models import FXRate

    return FXRate(timestamp=timestamp, pair="USDJPY", rate=rate, source="test")


def _fetch_result(**kw):
    from src.market_data import MarketFetchResult

    base = dict(rows=[], fetched_at=None, source="yfinance", errors=[], name_updates={})
    base.update(kw)
    return MarketFetchResult(**base)


def _run_worker(monkeypatch, fetch_fx: bool, fx_outcome):
    """MarketWorker.run を同期実行し、finished / error で受けた値を返す。"""
    import src.market_data as md

    calls: list[str] = []
    monkeypatch.setattr(md, "fetch_market_data", lambda *a, **k: _fetch_result())

    def fake_fx():
        calls.append("fx")
        if isinstance(fx_outcome, Exception):
            raise fx_outcome
        return fx_outcome

    monkeypatch.setattr(md, "fetch_usdjpy", fake_fx)
    worker = gui.MarketWorker([], {}, fetch_fx=fetch_fx)
    got: dict = {}
    worker.finished.connect(lambda update: got.update(update=update))
    worker.error.connect(lambda message: got.update(error=message))
    worker.run()
    return got, calls


def test_market_worker_fetches_fx_when_requested(monkeypatch):
    got, calls = _run_worker(monkeypatch, True, _fx_rate())
    assert calls == ["fx"]
    assert got["update"].fx.rate == 150.5 and got["update"].fx_error is None


def test_market_worker_keeps_market_result_when_fx_fails(monkeypatch):
    """為替の失敗で株価・配当の結果を捨てない（エラーに積むだけ）。"""
    got, _ = _run_worker(monkeypatch, True, RuntimeError("timeout"))
    assert "error" not in got, "為替の失敗で取得全体が失敗扱いになった"
    assert got["update"].market is not None
    assert got["update"].fx is None and "timeout" in got["update"].fx_error


def test_market_worker_skips_fx_without_usd_holdings(monkeypatch):
    got, calls = _run_worker(monkeypatch, False, _fx_rate())
    assert calls == [], "USD 銘柄が無いのに為替を取りに行った"
    assert got["update"].fx is None


def _finish_stub():
    """_on_market_finished の呼び出し順と、その時点の為替レートを記録するスタブ。"""

    class WindowStub:
        _store_fx = gui.DividendWindow._store_fx

        def __init__(self):
            self.holdings = []
            self.market_data_map = {}
            self.fx_rate = 100.0
            self.fx_fetched_at = "old"
            self.events: list[tuple] = []

        def _save_or_notify(self, action):
            action()
            return True

        def _refresh_view(self):
            self.events.append(("refresh", self.fx_rate))

        def _record_history_snapshot(self):
            self.events.append(("snapshot", self.fx_rate))

        def _split_notice(self):
            return ""

        def _notify(self, message, error=False):
            self.events.append(("notify", message, error))

    return WindowStub()


def test_on_market_finished_applies_fx_before_snapshot():
    """スナップショットは新しい株価×新しいレートで記録する（古いレートで記録しない）。"""
    stub = _finish_stub()
    update = gui.MarketUpdate(market=_fetch_result(), fx=_fx_rate(151.25, "new"))
    gui.DividendWindow._on_market_finished(stub, update)
    assert stub.events[:2] == [("refresh", 151.25), ("snapshot", 151.25)], stub.events
    assert stub.fx_fetched_at == "new"
    assert [r.rate for r in io_csv.load_fx()] == [151.25], "fx.csv へ追記されていない"
    assert stub.events[-1][2] is False, stub.events[-1]


def test_on_market_finished_reports_fx_error_but_keeps_market_update():
    stub = _finish_stub()
    update = gui.MarketUpdate(market=_fetch_result(), fx_error="USDJPY更新に失敗しました: x")
    gui.DividendWindow._on_market_finished(stub, update)
    assert ("snapshot", 100.0) in stub.events, "為替の失敗で株価の反映・記録が止まった"
    message, error = stub.events[-1][1:]
    assert error is True and "USDJPY" in message
    assert io_csv.load_fx() == []


def test_store_fx_rejects_unusable_rate():
    """0・nan のレートは保存も反映もしない（latest_fx と同じ基準）。"""
    stub = _finish_stub()
    assert gui.DividendWindow._store_fx(stub, _fx_rate(float("nan"))) is False
    assert stub.fx_rate == 100.0 and io_csv.load_fx() == []


def test_income_trend_label_marks_gross_history_in_net_mode():
    """履歴は税引前で記録しているので、税引後モードの凡例にだけ（税引前）を付ける。"""
    label = gui.DividendWindow._income_trend_label
    assert label(False, None) == "配当"
    assert label(True, "fixed", dividend_mode="net") == "定額取崩収入＋優待（税引前）"
    assert label(False, None, dividend_mode="gross") == "配当"


def _real_switch_stub(monkeypatch, tmp_path):
    """実 set_data_dir を使いつつ、app_config 保存先だけメモリへ隔離したスタブを作る。"""
    current = tmp_path / "current"
    target = tmp_path / "target"
    current.mkdir()
    target.mkdir()
    io_csv.set_data_dir(current, persist=False)
    persisted = {"dir": current}
    monkeypatch.setattr(
        io_csv.app_config,
        "save_data_dir",
        lambda p: persisted.update(dir=gui.Path(p)),
    )
    stub = _io_stub([_holding(symbol="7203")], [_history("2026-01-01")])
    stub._busy = False
    monkeypatch.setattr(
        gui.QFileDialog, "getExistingDirectory", lambda *a, **k: str(target)
    )
    _answer(monkeypatch, gui.QMessageBox.StandardButton.No)
    return stub, current, target, persisted


def test_change_data_dir_rolls_back_when_init_empty_files_fails(monkeypatch, tmp_path):
    """init_empty_files が OSError を投げたら、io_csv パス・app_config・表示すべて旧フォルダへ戻ること。"""
    stub, current, _target, persisted = _real_switch_stub(monkeypatch, tmp_path)
    monkeypatch.setattr(
        gui,
        "init_empty_files",
        lambda: (_ for _ in ()).throw(OSError("backup フォルダを作成できません")),
    )

    gui.DividendWindow.action_change_data_dir(stub)
    assert io_csv.DATA_DIR == current
    for name in io_csv._DERIVED_PATHS:
        assert getattr(io_csv, name).parent == current
    assert persisted["dir"] == current
    assert [h.symbol for h in stub.holdings] == ["7203"]
    assert [r.record_date for r in stub.history] == ["2026-01-01"]
    assert stub.reloaded is False
    assert stub.notes and stub.notes[-1][1] is True


def test_change_data_dir_rolls_back_when_reload_fails_midway(monkeypatch, tmp_path):
    """_reload_all の途中で読込例外が出ても、self の状態・io_csv パス・app_config が旧フォルダで揃うこと。"""
    stub, current, target, persisted = _real_switch_stub(monkeypatch, tmp_path)
    # 新フォルダには別銘柄の holdings.csv があり、history.csv の読込だけ失敗させる。
    io_csv.export_holdings_csv(target / "holdings.csv", [_holding(symbol="AAPL")])
    _answer(monkeypatch, gui.QMessageBox.StandardButton.Yes)
    stub._reload_all = lambda: gui.DividendWindow._reload_all(stub)
    monkeypatch.setattr(
        gui,
        "load_history",
        lambda: (_ for _ in ()).throw(OSError("history.csv が破損しています")),
    )

    gui.DividendWindow.action_change_data_dir(stub)
    assert io_csv.DATA_DIR == current
    for name in io_csv._DERIVED_PATHS:
        assert getattr(io_csv, name).parent == current
    assert persisted["dir"] == current
    # 先に読めたはずの AAPL で self.holdings が半端に上書きされていないこと
    assert [h.symbol for h in stub.holdings] == ["7203"]
    assert [r.record_date for r in stub.history] == ["2026-01-01"]
    assert stub.notes and stub.notes[-1][1] is True


def test_change_data_dir_keeps_state_when_config_save_fails(monkeypatch, tmp_path):
    """app_config.save_data_dir が失敗したら、パスも表示も旧フォルダのまま保たれること。"""
    stub, current, _target, persisted = _real_switch_stub(monkeypatch, tmp_path)
    monkeypatch.setattr(
        io_csv.app_config,
        "save_data_dir",
        lambda _p: (_ for _ in ()).throw(OSError("config.json に書き込めません")),
    )

    gui.DividendWindow.action_change_data_dir(stub)
    assert io_csv.DATA_DIR == current
    for name in io_csv._DERIVED_PATHS:
        assert getattr(io_csv, name).parent == current
    assert persisted["dir"] == current
    assert [h.symbol for h in stub.holdings] == ["7203"]
    assert stub.reloaded is False
    assert stub.notes and stub.notes[-1][1] is True


# ---- 月別内訳テーブルの「優待内容」列幅（A5） ----


def _monthly_stub(monthly):
    gui.QApplication.instance() or gui.QApplication([])

    class MonthlyStub:
        income_benefit = "with"
        monthly_month = 6
        _monthly_label = "年間配当"
        _monthly_sorted_once = False
        _monthly_columns_sized = False
        _monthly_benefit_detail_sized = False
        _make_breakdown_table = gui.DividendWindow._make_breakdown_table
        _apply_monthly_sort = gui.DividendWindow._apply_monthly_sort
        _resize_monthly_benefit_detail_column = (
            gui.DividendWindow._resize_monthly_benefit_detail_column
        )

    stub = MonthlyStub()
    stub._monthly = monthly
    stub._monthly_head = gui.QLabel("")
    stub._monthly_view, stub._monthly_model, stub._monthly_proxy = (
        stub._make_breakdown_table()
    )
    return stub


def test_monthly_benefit_detail_column_expands_on_first_non_empty_render():
    """配当のみの月で初回描画したあと、優待のある月を選んだら優待内容列だけ広がること。"""
    from src.calc import monthly_breakdown

    monthly = monthly_breakdown([
        dict(
            symbol="7203",
            name="トヨタ",
            account="特定",
            active=True,
            annual_dividend_jpy=1200.0,
            annual_benefit_jpy=3000.0,
            dividend_month_ratios={6: 1.0},
            dividend_months_manual=False,
            benefit_months=[3],
            benefit_detail="株主優待お買物券10,000円分（長期保有特典含む・全国の店舗で利用可）",
        ),
    ])
    stub = _monthly_stub(monthly)
    col = gui.MONTHLY_COL_BENEFIT_DETAIL

    # 6月（配当のみ・優待内容は空）で初回描画: 列幅は見出し幅で確定する。
    stub.monthly_month = 6
    gui.DividendWindow._render_monthly_breakdown(stub)
    assert stub._monthly_columns_sized is True
    assert stub._monthly_benefit_detail_sized is False
    header_only_width = stub._monthly_view.columnWidth(col)

    # 3月（優待内容あり）へ切替: 優待内容列が片方向で広がり、上限で頭打ちになる。
    stub.monthly_month = 3
    gui.DividendWindow._render_monthly_breakdown(stub)
    assert stub._monthly_benefit_detail_sized is True
    expanded_width = stub._monthly_view.columnWidth(col)
    assert expanded_width > header_only_width
    assert expanded_width <= gui.FREE_TEXT_COLUMN_MAX_WIDTH


def test_monthly_benefit_detail_column_never_shrinks_user_widened_width():
    """ユーザーが手動で広げた優待内容列の幅を、初回の優待月描画で縮めないこと。"""
    from src.calc import monthly_breakdown

    monthly = monthly_breakdown([
        dict(
            symbol="7203",
            name="トヨタ",
            account="特定",
            active=True,
            annual_dividend_jpy=1200.0,
            annual_benefit_jpy=3000.0,
            dividend_month_ratios={6: 1.0},
            dividend_months_manual=False,
            benefit_months=[3],
            benefit_detail="クオカード1,000円分",
        ),
    ])
    stub = _monthly_stub(monthly)
    col = gui.MONTHLY_COL_BENEFIT_DETAIL

    stub.monthly_month = 6
    gui.DividendWindow._render_monthly_breakdown(stub)
    # ユーザーが上限を超えて 360px に広げた状態を再現
    stub._monthly_view.setColumnWidth(col, 360)

    stub.monthly_month = 3
    gui.DividendWindow._render_monthly_breakdown(stub)
    assert stub._monthly_view.columnWidth(col) == 360

