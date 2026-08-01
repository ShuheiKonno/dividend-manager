from __future__ import annotations

import json
from pathlib import Path

import pytest

import src.io_csv as io_csv
from src.models import (
    HOLDINGS_FIELDS,
    HISTORY_FIELDS,
    Holding,
    HistoryRecord,
)


def _configure_tmp_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr(io_csv, "DATA_DIR", tmp_path)
    monkeypatch.setattr(io_csv, "BACKUP_DIR", tmp_path / "backup")
    # SETTINGS_PATH はモジュールロード時に DATA_DIR から確定済みのため個別に差し替える。
    monkeypatch.setattr(io_csv, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(io_csv.time, "sleep", lambda _seconds: None)


def test_atomic_write_retries_permission_error(monkeypatch, tmp_path):
    _configure_tmp_dirs(monkeypatch, tmp_path)
    target = tmp_path / "sample.csv"
    original_replace = Path.replace
    attempts = 0

    def flaky_replace(self, destination):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("locked")
        return original_replace(self, destination)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    io_csv._atomic_write(target, [{"value": "1"}], ["value"], backup=False)
    assert attempts == 3
    assert target.read_text(encoding="utf-8").splitlines() == ["value", "1"]


def test_atomic_write_reports_file_lock(monkeypatch, tmp_path):
    _configure_tmp_dirs(monkeypatch, tmp_path)
    target = tmp_path / "sample.csv"
    monkeypatch.setattr(Path, "replace", lambda *_args: (_ for _ in ()).throw(PermissionError("locked")))
    with pytest.raises(PermissionError, match="Excel"):
        io_csv._atomic_write(target, [{"value": "1"}], ["value"], backup=False)


def test_atomic_write_prunes_old_backups(monkeypatch, tmp_path):
    _configure_tmp_dirs(monkeypatch, tmp_path)
    target = tmp_path / "sample.csv"
    target.write_text("value\nold\n", encoding="utf-8")
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    for index in range(io_csv._BACKUP_KEEP_PER_FILE):
        (backup_dir / f"sample_20260530T000000{index:06d}+0900.csv").write_text(
            "old", encoding="utf-8"
        )
    io_csv._atomic_write(target, [{"value": "new"}], ["value"])
    assert len(list(backup_dir.glob("sample_*.csv"))) == io_csv._BACKUP_KEEP_PER_FILE


# ---- エクスポート / インポート（外部パス・BOM 付き UTF-8・往復可能） ----


def _history(date: str, **kw) -> HistoryRecord:
    base = dict(
        record_date=date,
        total_market_value_jpy=None,
        total_book_value_jpy=None,
        total_dividend_jpy=None,
        total_benefit_jpy=None,
        total_profit_jpy=None,
        total_withdrawal_income_jpy=None,
        source="app",
    )
    base.update(kw)
    return HistoryRecord(**base)


def _holding(symbol: str, **kw) -> Holding:
    base = dict(
        symbol=symbol,
        name="日本株サンプル",  # Excel での BOM 確認用に日本語を含める
        asset_type="stock",
        currency="JPY",
        shares=100.0,
        avg_cost=1500.0,
        account="特定",
        annual_div_ps_gross_override=None,
        annual_div_ps_net_override=None,
        div_forecast_as_of_override=None,
        annual_benefit_jpy=0.0,
        withdrawal_fixed_jpy=None,
        withdrawal_rate=None,
        tax_rate=None,
        active=True,
    )
    base.update(kw)
    return Holding(**base)


def test_export_history_csv_is_sorted_and_roundtrips(monkeypatch, tmp_path):
    _configure_tmp_dirs(monkeypatch, tmp_path)
    target = tmp_path / "export" / "history.csv"
    target.parent.mkdir()
    records = [
        _history("2024-12-01", total_market_value_jpy=200.0),
        _history("2024-01-01", total_market_value_jpy=100.0, total_benefit_jpy=0.0),
    ]
    io_csv.export_history_csv(target, records)
    # 昇順で書き出され、read_history_csv で同値に読み戻せる（0.0 も保持）。
    loaded = io_csv.read_history_csv(target)
    assert [r.record_date for r in loaded] == ["2024-01-01", "2024-12-01"]
    assert loaded[0].total_market_value_jpy == 100.0
    assert loaded[0].total_benefit_jpy == 0.0  # 明示 0 が空に潰れない
    assert loaded[1].total_market_value_jpy == 200.0


def test_export_csv_writes_bom_and_canonical_headers(monkeypatch, tmp_path):
    _configure_tmp_dirs(monkeypatch, tmp_path)
    hist_path = tmp_path / "h.csv"
    hold_path = tmp_path / "p.csv"
    io_csv.export_history_csv(hist_path, [_history("2024-01-01")])
    io_csv.export_holdings_csv(hold_path, [_holding("7203")])
    # Excel 文字化け回避のため BOM 付き UTF-8 で書き出す。
    assert hist_path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert hold_path.read_bytes().startswith(b"\xef\xbb\xbf")
    # ヘッダーは内部正規フォーマットと同一（往復可能）。
    hist_header = hist_path.read_text(encoding="utf-8-sig").splitlines()[0]
    hold_header = hold_path.read_text(encoding="utf-8-sig").splitlines()[0]
    assert hist_header == ",".join(HISTORY_FIELDS)
    assert hold_header == ",".join(HOLDINGS_FIELDS)


def test_export_holdings_csv_roundtrips(monkeypatch, tmp_path):
    _configure_tmp_dirs(monkeypatch, tmp_path)
    target = tmp_path / "holdings.csv"
    holdings = [
        # 月リストはカンマを含むため、csv のクォート往復もここで確認する。
        _holding("7203", dividend_months=[6, 12], benefit_months=[3]),
        _holding("AAPL", currency="USD", account="NISA"),
    ]
    io_csv.export_holdings_csv(target, holdings)
    assert '"6,12"' in target.read_text(encoding="utf-8-sig")
    rows = io_csv._read_csv(target)
    restored = [Holding.from_dict(r) for r in rows]
    assert restored == holdings


def test_read_history_csv_skips_blank_record_date(monkeypatch, tmp_path):
    _configure_tmp_dirs(monkeypatch, tmp_path)
    target = tmp_path / "history.csv"
    io_csv.export_history_csv(
        target,
        [_history("2024-01-01", total_market_value_jpy=100.0), _history("")],
    )
    loaded = io_csv.read_history_csv(target)
    assert [r.record_date for r in loaded] == ["2024-01-01"]


def test_read_history_csv_normalizes_and_rejects_dates(monkeypatch, tmp_path):
    """Excel 再保存の "2024/1/1" 等を ISO へ正規化し、不正日付の行は除外する。

    正規化を怠ると後段の filter_by_period が date.fromisoformat で ValueError。
    """
    _configure_tmp_dirs(monkeypatch, tmp_path)
    target = tmp_path / "history.csv"
    rows = [
        {"record_date": "2024/1/1", "total_market_value_jpy": "100.0"},
        {"record_date": "2024-2-3", "total_market_value_jpy": "200.0"},
        {"record_date": "abc", "total_market_value_jpy": "300.0"},  # 不正→除外
    ]
    io_csv._atomic_write(target, rows, HISTORY_FIELDS, backup=False)
    loaded = io_csv.read_history_csv(target)
    assert [r.record_date for r in loaded] == ["2024-01-01", "2024-02-03"]
    # 取り込んだ全日付が ISO として解釈可能であること（後段クラッシュ防止）。
    from datetime import date
    for r in loaded:
        date.fromisoformat(r.record_date)


def test_read_history_csv_wrong_file_yields_empty(monkeypatch, tmp_path):
    """履歴ではないファイル（保有 CSV 等）を選んでも空を返し履歴を壊さない。"""
    _configure_tmp_dirs(monkeypatch, tmp_path)
    target = tmp_path / "holdings.csv"
    io_csv.export_holdings_csv(target, [_holding("7203")])
    assert io_csv.read_history_csv(target) == []


def test_read_holdings_csv_roundtrips(monkeypatch, tmp_path):
    _configure_tmp_dirs(monkeypatch, tmp_path)
    target = tmp_path / "holdings.csv"
    holdings = [_holding("7203"), _holding("AAPL", currency="USD", account="NISA")]
    io_csv.export_holdings_csv(target, holdings)
    assert io_csv.read_holdings_csv(target) == holdings


def test_read_holdings_csv_skips_blank_symbol(monkeypatch, tmp_path):
    """symbol 空の行は除外する（誤った履歴 CSV を選んでも空を返す）。"""
    _configure_tmp_dirs(monkeypatch, tmp_path)
    target = tmp_path / "holdings.csv"
    io_csv.export_holdings_csv(target, [_holding("7203"), _holding("")])
    loaded = io_csv.read_holdings_csv(target)
    assert [h.symbol for h in loaded] == ["7203"]


def test_read_holdings_csv_wrong_file_yields_empty(monkeypatch, tmp_path):
    """保有ではないファイル（履歴 CSV）を選んでも空を返し保有を壊さない。"""
    _configure_tmp_dirs(monkeypatch, tmp_path)
    target = tmp_path / "history.csv"
    io_csv.export_history_csv(target, [_history("2024-01-01")])
    assert io_csv.read_holdings_csv(target) == []


# ---- settings.json（集合キー＝従来互換 / callable キー＝ウィンドウ状態） ----


def test_settings_existing_set_keys_roundtrip(monkeypatch, tmp_path):
    """既存の集合キーは従来どおり保存・読込でき、不正値は黙って捨てられる。"""
    _configure_tmp_dirs(monkeypatch, tmp_path)
    io_csv.save_settings(dividend_mode="net", asset_trend_period="1y")
    io_csv.save_settings(dividend_mode="bogus")  # 不正値は上書きされない
    loaded = io_csv.load_settings()
    assert loaded["dividend_mode"] == "net"
    assert loaded["asset_trend_period"] == "1y"


def test_settings_window_geometry_roundtrip(monkeypatch, tmp_path):
    _configure_tmp_dirs(monkeypatch, tmp_path)
    io_csv.save_settings(window_geometry=[-100, 50, 1180, 760])
    assert io_csv.load_settings()["window_geometry"] == [-100, 50, 1180, 760]


@pytest.mark.parametrize(
    "value",
    [
        [0, 0, 1180],            # 長さ不正
        [0, 0, 1180.0, 760],     # float 混入
        [0, 0, True, 760],       # bool 混入（int のサブクラス）
        [0, 0, 100, 760],        # 幅が下限未満
        [0, 0, 1180, 20000],     # 高さが上限超過
        "0,0,1180,760",          # 型不正
    ],
)
def test_settings_window_geometry_rejects_invalid(monkeypatch, tmp_path, value):
    _configure_tmp_dirs(monkeypatch, tmp_path)
    io_csv.save_settings(window_geometry=value)
    assert "window_geometry" not in io_csv.load_settings()


def test_settings_last_tab_rejects_bool_and_out_of_range(monkeypatch, tmp_path):
    _configure_tmp_dirs(monkeypatch, tmp_path)
    io_csv.save_settings(last_tab=2)
    assert io_csv.load_settings()["last_tab"] == 2
    io_csv.save_settings(last_tab=True)  # bool は拒否（int のサブクラス）
    io_csv.save_settings(last_tab=5)     # 範囲外は拒否（タブは 0〜4 の5枚）
    assert io_csv.load_settings()["last_tab"] == 2
    io_csv.save_settings(last_tab=4)     # 月別配当タブは受理する
    assert io_csv.load_settings()["last_tab"] == 4


def test_settings_monthly_sort_roundtrip(monkeypatch, tmp_path):
    _configure_tmp_dirs(monkeypatch, tmp_path)
    io_csv.save_settings(monthly_sort="asc")
    assert io_csv.load_settings()["monthly_sort"] == "asc"
    io_csv.save_settings(monthly_sort="sideways")  # 未知の値は拒否
    assert io_csv.load_settings()["monthly_sort"] == "asc"


def test_settings_show_inactive_roundtrip(monkeypatch, tmp_path):
    """無効銘柄の表示トグルが bool として往復し、非 bool は拒否されること。"""
    _configure_tmp_dirs(monkeypatch, tmp_path)
    io_csv.save_settings(show_inactive=True)
    assert io_csv.load_settings()["show_inactive"] is True
    io_csv.save_settings(show_inactive=False)
    assert io_csv.load_settings()["show_inactive"] is False
    io_csv.save_settings(show_inactive="yes")  # 文字列は拒否
    assert io_csv.load_settings()["show_inactive"] is False


def test_settings_pie_threshold_and_column_widths(monkeypatch, tmp_path):
    _configure_tmp_dirs(monkeypatch, tmp_path)
    io_csv.save_settings(
        pie_threshold_asset=2.5,
        pie_threshold_income=0,
        column_widths_price=[60, 180, 70],
    )
    loaded = io_csv.load_settings()
    assert loaded["pie_threshold_asset"] == 2.5
    assert loaded["pie_threshold_income"] == 0
    assert loaded["column_widths_price"] == [60, 180, 70]
    io_csv.save_settings(pie_threshold_asset=25)      # 上限超過は拒否
    io_csv.save_settings(column_widths_price=[])      # 空リストは拒否
    io_csv.save_settings(column_widths_income=[5000])  # 上限超過は拒否
    loaded = io_csv.load_settings()
    assert loaded["pie_threshold_asset"] == 2.5
    assert loaded["column_widths_price"] == [60, 180, 70]
    assert "column_widths_income" not in loaded


def test_settings_load_drops_invalid_values(monkeypatch, tmp_path):
    """JSON を直接編集して不正値が紛れても、妥当なキーだけ採用される。"""
    _configure_tmp_dirs(monkeypatch, tmp_path)
    (tmp_path / "settings.json").write_text(
        json.dumps({
            "dividend_mode": "net",
            "window_geometry": [0, 0, 1180, 760],
            "last_tab": "2",                # 文字列は拒否
            "asset_chart_type": ["pie"],    # 非ハッシュ値でも全体を壊さない
            "unknown_key": 123,             # 未知キーは無視
        }),
        encoding="utf-8",
    )
    loaded = io_csv.load_settings()
    assert loaded == {"dividend_mode": "net", "window_geometry": [0, 0, 1180, 760]}


def test_settings_unknown_key_not_saved(monkeypatch, tmp_path):
    _configure_tmp_dirs(monkeypatch, tmp_path)
    io_csv.save_settings(unknown_key="x", dividend_mode="gross")
    raw = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert "unknown_key" not in raw
    assert raw["dividend_mode"] == "gross"
