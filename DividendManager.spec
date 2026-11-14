# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 設定（onedir / ウィンドウアプリ）。ビルドは tools\build_exe.ps1 から。
#
# onefile にしない理由: 起動のたびに一時フォルダへ展開するため PySide6 + pandas で
# 起動が数秒遅くなり、`__file__` 基準のパスも毎回変わる。
# yfinance / ToushinReader は market_data が取得時に遅延 import するので、
# 静的解析だけでは同梱漏れしやすい。collect_all で依存データごと取り込む。
from PyInstaller.utils.hooks import collect_all

datas = [
    ("assets", "assets"),  # gui.APP_ICON_PATH（__file__ の親/assets）
    ("tools", "tools"),    # shortcut.SCRIPT_PATH（ショートカット作成メニュー）
]
binaries = []
hiddenimports = []

for package in ("yfinance", "ToushinReader", "curl_cffi"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # GUI に不要な依存（旧 TUI の名残・テスト専用）を同梱しない
    excludes=["textual", "rich", "pytest", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DividendManager",  # ASCII 名（ウィンドウ名・ショートカット名は日本語のまま）
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX 圧縮は Defender の誤検知を増やすので使わない
    console=False,
    icon="assets/app.ico",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="DividendManager",
)
