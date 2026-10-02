"""デスクトップの起動ショートカット作成（Windows 専用。UI 非依存）。

Windows のタスクバーはウィンドウのアイコン（`setWindowIcon`）ではなく**起動元の
ショートカット**からアイコンを引く。そのため `.lnk` を消してしまうとタスクバーの
アイコンが Python のものに戻る——ここは GUI のメニューから作り直せるようにする入口。

`.lnk` の作り方そのものは `tools/make_shortcut.ps1` に一本化する（COM を Python から
叩く実装をもう一つ持つと、インタプリタの解決順やアイコン指定が二重管理になる）。
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
from pathlib import Path

SHORTCUT_NAME = "配当優待管理.lnk"
SCRIPT_PATH = Path(__file__).resolve().parents[1] / "tools" / "make_shortcut.ps1"

_CSIDL_DESKTOPDIRECTORY = 0x0010
_CREATE_NO_WINDOW = 0x08000000  # PowerShell の黒窓を一瞬たりとも出さない


def is_supported() -> bool:
    """ショートカット作成が使えるプラットフォームか。"""
    return sys.platform == "win32"


def desktop_dir() -> Path:
    """デスクトップの実パスを返す。

    OneDrive がデスクトップをリダイレクトしている環境では `~/Desktop` は存在せず、
    実体は `~/OneDrive/デスクトップ` にある。シェルへ問い合わせて解決すること
    （実際にこの環境がそう）。問い合わせに失敗したときだけ `~/Desktop` へ落とす。
    """
    if is_supported():
        try:
            buf = ctypes.create_unicode_buffer(260)
            # SHGetFolderPathW は非推奨だが全 Windows に在り、リダイレクト先を返す。
            if ctypes.windll.shell32.SHGetFolderPathW(
                None, _CSIDL_DESKTOPDIRECTORY, None, 0, buf
            ) == 0 and buf.value:
                return Path(buf.value)
        except Exception:  # noqa: BLE001 — 解決できなければ既定の場所を使う
            pass
    return Path.home() / "Desktop"


def shortcut_path() -> Path:
    """作成されるショートカットのフルパス。"""
    return desktop_dir() / SHORTCUT_NAME


def _decode(raw: bytes) -> str:
    """PowerShell の出力をデコードする（5.1 は日本語環境で cp932 を吐く）。"""
    for encoding in ("utf-8", "cp932"):
        try:
            return raw.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace").strip()


def create_desktop_shortcut() -> Path:
    """デスクトップへ起動ショートカットを作成し、そのパスを返す。

    既存のファイルは上書きする（作り直しが目的のため）。失敗時は RuntimeError を
    送出する——メッセージはそのままダイアログへ出せる日本語にしてある。
    """
    if not is_supported():
        raise RuntimeError("ショートカットの作成は Windows でのみ利用できます")
    if not SCRIPT_PATH.exists():
        raise RuntimeError(f"作成スクリプトが見つかりません: {SCRIPT_PATH}")

    destination = desktop_dir()
    if not destination.is_dir():
        raise RuntimeError(f"デスクトップが見つかりません: {destination}")

    command = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(SCRIPT_PATH), "-Destination", str(destination),
    ]
    if getattr(sys, "frozen", False):
        # exe 版: 実行中の exe 自身を指すショートカットにする（インタプリタ探索は不要）
        command += ["-ExeTarget", sys.executable]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            creationflags=_CREATE_NO_WINDOW,
        )
    except OSError as exc:
        raise RuntimeError(f"PowerShell を実行できません: {exc}") from exc

    path = destination / SHORTCUT_NAME
    if result.returncode != 0 or not path.exists():
        detail = _decode(result.stderr) or _decode(result.stdout) or "原因不明"
        raise RuntimeError(detail)
    return path
