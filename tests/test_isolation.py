"""conftest の安全網: テストのデータ書込み先が実データのフォルダに向いていないこと。"""

from __future__ import annotations

import src.io_csv as io_csv
from src import app_config

# conftest の autouse フィクスチャは %APPDATA% も一時フォルダへ向けるので、テスト本体の
# 中で load_data_dir() を呼ぶと既定フォルダしか見えない。隔離前（収集時）に実際の
# 設定を控えておき、それと比べる。
_REAL_DATA_DIR = app_config.load_data_dir()
_REAL_CONFIG_PATH = app_config.config_path()


def test_data_paths_never_point_at_the_real_data_dir():
    real = _REAL_DATA_DIR
    for name in ("DATA_DIR", *io_csv._DERIVED_PATHS):
        path = getattr(io_csv, name)
        assert path != real and real not in path.parents, (name, path)


def test_saving_in_a_test_does_not_touch_the_real_files():
    real = _REAL_DATA_DIR
    before = {p.name: p.stat().st_mtime_ns for p in real.glob("*.csv")} if real.exists() else {}
    io_csv.save_holdings([])
    io_csv.save_market_data([])
    after = {p.name: p.stat().st_mtime_ns for p in real.glob("*.csv")} if real.exists() else {}
    assert before == after
    assert io_csv.HOLDINGS_PATH.exists() and real not in io_csv.HOLDINGS_PATH.parents


def test_data_dir_setting_is_isolated_from_the_real_appdata():
    """set_data_dir(persist=True) を通る経路でも、実際の config.json を書き換えない。"""
    assert app_config.config_path() != _REAL_CONFIG_PATH
    before = _REAL_CONFIG_PATH.read_bytes() if _REAL_CONFIG_PATH.exists() else None
    io_csv.set_data_dir(io_csv.DATA_DIR / "switched")
    after = _REAL_CONFIG_PATH.read_bytes() if _REAL_CONFIG_PATH.exists() else None
    assert before == after
