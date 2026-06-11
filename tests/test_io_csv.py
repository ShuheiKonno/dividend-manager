from __future__ import annotations

import json
from pathlib import Path

import pytest

import src.io_csv as io_csv


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
    io_csv.save_settings(last_tab=4)     # 範囲外は拒否
    assert io_csv.load_settings()["last_tab"] == 2


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
