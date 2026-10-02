"""データフォルダの保存先設定（Qt 非依存 / io_csv より下位のブートストラップ）。

データフォルダ自体をユーザーが変更できるため、その設定値だけはデータフォルダの
**外**（OS のユーザー設定領域）へ置く。アプリの設定 `settings.json` はデータフォルダ
の中にあるので、そこへ書くと「どのフォルダを見るか」を決めるのに、その決定結果が
必要になる（鶏と卵）。ここは io_csv を import しない——io_csv がこのモジュールを
import する側なので、循環参照になる。JSON の原子的書き込みは短いので自前で持つ。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _default_data_dir() -> Path:
    """未設定・設定が使えないときの落とし先（リポジトリ直下、exe 版は exe の隣）。

    PyInstaller 版は `__file__` が展開先（`_internal/`）を指すため、そのまま
    `parents[1] / "data"` にするとデータが `_internal` の奥へ埋もれる（onefile なら
    終了時に消える一時フォルダ）。凍結時は実行ファイルの隣の `data/` を使う。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "data"
    return Path(__file__).resolve().parents[1] / "data"


DEFAULT_DATA_DIR = _default_data_dir()

_CONFIG_DIR_NAME = "dividend-manager-gui"
_CONFIG_FILE_NAME = "config.json"
_DATA_DIR_KEY = "data_dir"


def config_path() -> Path:
    """データフォルダ設定の保存先を返す（Windows は %APPDATA% 配下）。

    %APPDATA% が無い環境（他 OS・サービス実行）では `~/.config` へ落とす。
    """
    appdata = os.environ.get("APPDATA")
    root = Path(appdata) if appdata else Path.home() / ".config"
    return root / _CONFIG_DIR_NAME / _CONFIG_FILE_NAME


def _read_config() -> dict:
    """設定 JSON を読む（無い・壊れている場合は空 dict）。"""
    path = config_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — 壊れた設定で起動不能にしない
        return {}


def is_usable_data_dir(path: Path) -> bool:
    """そのパスをデータフォルダとして使えるか判定する。

    存在すればフォルダであること、存在しなければ**ドライブ（アンカー）が
    在ること**を見る。外付けドライブを外した・ネットワークドライブが切れた
    状態を起動前に検出するのが目的で、`E:\\portfolio` のような設定のまま
    起動して `mkdir` が OSError を投げ、ウィンドウが出ない＝設定を直す
    メニューにも到達できない、という詰みを防ぐ。
    """
    if path.is_dir():
        return True
    if path.exists():  # 同名のファイルがある
        return False
    anchor = path.anchor
    return Path(anchor).exists() if anchor else True


def load_data_dir() -> Path:
    """現在のデータフォルダを返す（未設定・到達不能なら既定フォルダ）。

    設定ファイルは書き換えずに既定へ落とすだけなので、ドライブを繋ぎ直せば
    次回起動で元のフォルダへ戻る。
    """
    raw = _read_config().get(_DATA_DIR_KEY)
    if isinstance(raw, str) and raw.strip():
        path = Path(raw).expanduser()
        if is_usable_data_dir(path):
            return path
    return DEFAULT_DATA_DIR


def save_data_dir(path: str | Path) -> None:
    """データフォルダの設定を保存する（tmp 書き→replace の原子的置換）。"""
    config = _read_config()
    config[_DATA_DIR_KEY] = str(Path(path))
    target = config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False)
    tmp_path.replace(target)
