from __future__ import annotations

import math

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
        history = []

        def _save_or_notify(self, action):
            action()
            return True

        def _refresh_trend(self):
            pass

    stub = WindowStub()
    stub.holdings = holdings
    stub.market_data_map = market_map
    stub.history = []
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
        div_forecast_as_of="2026-05-30", source="test", fetched_at="x",
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

        # Markdown エクスポートは compute_rows を呼び直すので、表示条件を持つ。
        market_data_map: dict = {}
        fx_rate = None
        dividend_mode = "gross"
        withdrawal_mode = None
        show_inactive = False
        market_fetched_at = None
        market_source = None

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
