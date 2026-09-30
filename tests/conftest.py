"""全テスト共通の安全網: データの書込み先を必ず一時フォルダへ向ける。

`io_csv.HOLDINGS_PATH` などはモジュール読込時に「現在のデータフォルダ」から確定する。
テストが `save_holdings()` / `save_market_data()` / `_atomic_write(io_csv.HOLDINGS_PATH, ...)`
を直接呼ぶと、**利用者の実データを上書きする**（実際に `holdings.csv` が1行のテスト
データに置き換わった）。個々のテストが `DATA_DIR` だけを差し替えても、`*_PATH` は
読込時の値のまま残るので防げない。

ここで全パスをまとめて一時フォルダへ張り替える。新しいテストがパスの差し替えを
忘れても、実データには届かない。個別に差し替えるテスト（`_configure_tmp_dirs` など）
は、この上から monkeypatch で重ねて上書きするだけなので影響を受けない。
"""

from __future__ import annotations

import pytest

import src.io_csv as io_csv


@pytest.fixture(autouse=True)
def _never_touch_real_data(monkeypatch, tmp_path_factory):
    isolated = tmp_path_factory.mktemp("isolated_data")
    monkeypatch.setattr(io_csv, "DATA_DIR", isolated)
    # BACKUP_DIR / HOLDINGS_PATH / ... を _DERIVED_PATHS から全件張り替える
    # （足し忘れると、そのファイルだけ実データを指したままになる）。
    for name, child in io_csv._DERIVED_PATHS.items():
        monkeypatch.setattr(io_csv, name, isolated / child)
