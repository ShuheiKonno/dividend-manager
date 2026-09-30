"""conftest の安全網: テストのデータ書込み先が実データのフォルダに向いていないこと。"""

from __future__ import annotations

import src.io_csv as io_csv
from src import app_config


def test_data_paths_never_point_at_the_real_data_dir():
    real = app_config.load_data_dir()
    for name in ("DATA_DIR", *io_csv._DERIVED_PATHS):
        path = getattr(io_csv, name)
        assert path != real and real not in path.parents, (name, path)


def test_saving_in_a_test_does_not_touch_the_real_files():
    real = app_config.load_data_dir()
    before = {p.name: p.stat().st_mtime_ns for p in real.glob("*.csv")} if real.exists() else {}
    io_csv.save_holdings([])
    io_csv.save_market_data([])
    after = {p.name: p.stat().st_mtime_ns for p in real.glob("*.csv")} if real.exists() else {}
    assert before == after
    assert io_csv.HOLDINGS_PATH.exists() and real not in io_csv.HOLDINGS_PATH.parents
