from __future__ import annotations

import json
import os
from pathlib import Path

import src.app_config as app_config


def _use_tmp_config(monkeypatch, tmp_path) -> Path:
    """設定ファイルの保存先を一時ディレクトリへ逃がす。

    実ユーザーの %APPDATA% を書き換えないよう、テストは必ずこれを通すこと。
    """
    target = tmp_path / "config" / "config.json"
    monkeypatch.setattr(app_config, "config_path", lambda: target)
    return target


def test_load_data_dir_defaults_when_unset(monkeypatch, tmp_path):
    _use_tmp_config(monkeypatch, tmp_path)
    assert app_config.load_data_dir() == app_config.DEFAULT_DATA_DIR


def test_save_then_load_roundtrip(monkeypatch, tmp_path):
    target = _use_tmp_config(monkeypatch, tmp_path)
    data_dir = tmp_path / "my data"
    data_dir.mkdir()
    app_config.save_data_dir(data_dir)
    assert target.exists(), "設定ファイルが作られていない"
    assert app_config.load_data_dir() == data_dir


def test_save_data_dir_keeps_unknown_keys(monkeypatch, tmp_path):
    target = _use_tmp_config(monkeypatch, tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"future_key": 1}), encoding="utf-8")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    app_config.save_data_dir(data_dir)
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["future_key"] == 1
    assert saved["data_dir"] == str(data_dir)


def test_broken_config_falls_back_to_default(monkeypatch, tmp_path):
    target = _use_tmp_config(monkeypatch, tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text("{ not json", encoding="utf-8")
    assert app_config.load_data_dir() == app_config.DEFAULT_DATA_DIR


def test_invalid_values_fall_back_to_default(monkeypatch, tmp_path):
    target = _use_tmp_config(monkeypatch, tmp_path)
    target.parent.mkdir(parents=True)
    for value in (None, 123, "", "   ", ["a"]):
        target.write_text(json.dumps({"data_dir": value}), encoding="utf-8")
        assert app_config.load_data_dir() == app_config.DEFAULT_DATA_DIR, value


def test_unreachable_dir_falls_back_without_rewriting_config(monkeypatch, tmp_path):
    """到達できないフォルダ設定は既定へ落とすが、設定ファイルは書き換えない。

    外付け/ネットワークドライブを外したまま起動しても窓が出て、繋ぎ直せば
    次回起動で元のフォルダへ戻る（設定が残っているため）。
    """
    target = _use_tmp_config(monkeypatch, tmp_path)
    target.parent.mkdir(parents=True)
    as_file = tmp_path / "not-a-dir.txt"  # 同名ファイルがある = フォルダにできない
    as_file.write_text("x", encoding="utf-8")
    target.write_text(json.dumps({"data_dir": str(as_file)}), encoding="utf-8")
    assert app_config.load_data_dir() == app_config.DEFAULT_DATA_DIR
    assert json.loads(target.read_text(encoding="utf-8"))["data_dir"] == str(as_file)


def test_is_usable_data_dir_rejects_missing_drive():
    """存在しないドライブ配下（外付けを外した状態）は使用不可と判定する。"""
    if os.name == "nt":
        missing = Path("Q:/dividend-manager-does-not-exist")
        if Path("Q:/").exists():  # 実在する環境ではこの判定を試せない
            return
    else:
        missing = Path("/nonexistent-anchor-test/データ")
        if Path("/nonexistent-anchor-test").exists():
            return
        # POSIX の anchor は "/" で必ず存在するため、作成可能＝使用可でよい
        assert app_config.is_usable_data_dir(missing) is True
        return
    assert app_config.is_usable_data_dir(missing) is False


def test_is_usable_data_dir_accepts_new_subdir(tmp_path):
    """まだ無いフォルダでも、親が辿れるなら作成できるので使用可とする。"""
    assert app_config.is_usable_data_dir(tmp_path / "a" / "b") is True
