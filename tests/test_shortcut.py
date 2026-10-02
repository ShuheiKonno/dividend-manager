from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import src.shortcut as shortcut


class _Result:
    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_script_exists() -> None:
    """.lnk の作成本体（PowerShell スクリプト）が同梱されていること。"""
    assert shortcut.SCRIPT_PATH.exists(), shortcut.SCRIPT_PATH
    assert shortcut.SCRIPT_PATH.name == "make_shortcut.ps1"


def test_shortcut_path_is_under_desktop() -> None:
    path = shortcut.shortcut_path()
    assert path.name == shortcut.SHORTCUT_NAME
    assert path.parent == shortcut.desktop_dir()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows のみ")
def test_desktop_dir_exists_on_windows() -> None:
    """OneDrive リダイレクト環境でも実在するパスを返すこと（~/Desktop は無い）。"""
    assert shortcut.desktop_dir().is_dir()


def test_create_runs_powershell_and_returns_path(tmp_path, monkeypatch) -> None:
    """成功時は作成されたパスを返す（呼び出し先は PowerShell スクリプト1本）。"""
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        (tmp_path / shortcut.SHORTCUT_NAME).write_text("dummy", encoding="utf-8")
        return _Result()

    monkeypatch.setattr(shortcut, "is_supported", lambda: True)
    monkeypatch.setattr(shortcut, "desktop_dir", lambda: tmp_path)
    monkeypatch.setattr(subprocess, "run", fake_run)

    created = shortcut.create_desktop_shortcut()
    assert created == tmp_path / shortcut.SHORTCUT_NAME
    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[0] == "powershell"
    assert str(shortcut.SCRIPT_PATH) in cmd
    # 出力先はスクリプト任せにせず明示する（デスクトップの解決を1箇所に保つ）
    assert cmd[cmd.index("-Destination") + 1] == str(tmp_path)


def test_frozen_build_points_shortcut_at_the_exe(tmp_path, monkeypatch) -> None:
    """exe 版は実行中の exe 自身を指す（インタプリタ探索へ落とさない）。"""
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        (tmp_path / shortcut.SHORTCUT_NAME).write_text("dummy", encoding="utf-8")
        return _Result()

    monkeypatch.setattr(shortcut, "is_supported", lambda: True)
    monkeypatch.setattr(shortcut, "desktop_dir", lambda: tmp_path)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\app\DividendManager.exe")

    shortcut.create_desktop_shortcut()
    cmd = calls[0]
    assert cmd[cmd.index("-ExeTarget") + 1] == r"C:\app\DividendManager.exe"


def test_create_reports_powershell_error(tmp_path, monkeypatch) -> None:
    """失敗時は PowerShell のメッセージを載せた RuntimeError にする。"""
    monkeypatch.setattr(shortcut, "is_supported", lambda: True)
    monkeypatch.setattr(shortcut, "desktop_dir", lambda: tmp_path)
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **kw: _Result(returncode=1, stderr="アイコンが無い".encode("cp932")),
    )
    with pytest.raises(RuntimeError, match="アイコンが無い"):
        shortcut.create_desktop_shortcut()


def test_create_fails_when_file_missing(tmp_path, monkeypatch) -> None:
    """終了コード 0 でも .lnk が無ければ失敗として扱う（黙って成功を返さない）。"""
    monkeypatch.setattr(shortcut, "is_supported", lambda: True)
    monkeypatch.setattr(shortcut, "desktop_dir", lambda: tmp_path)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _Result())
    with pytest.raises(RuntimeError):
        shortcut.create_desktop_shortcut()


def test_create_rejects_non_windows(monkeypatch) -> None:
    monkeypatch.setattr(shortcut, "is_supported", lambda: False)
    with pytest.raises(RuntimeError, match="Windows"):
        shortcut.create_desktop_shortcut()


def test_create_requires_script(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(shortcut, "is_supported", lambda: True)
    monkeypatch.setattr(shortcut, "SCRIPT_PATH", tmp_path / "missing.ps1")
    with pytest.raises(RuntimeError, match="見つかりません"):
        shortcut.create_desktop_shortcut()


def test_decode_handles_cp932_and_utf8() -> None:
    """PowerShell 5.1 は日本語環境で cp932 を吐くので両方読めること。"""
    assert shortcut._decode("失敗".encode("cp932")) == "失敗"
    assert shortcut._decode("失敗".encode("utf-8")) == "失敗"
    assert shortcut._decode(b"") == ""
