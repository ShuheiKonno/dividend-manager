<#
.SYNOPSIS
  配当優待管理を exe 化する（PyInstaller / onedir）。

.DESCRIPTION
  成果物は dist\DividendManager\DividendManager.exe（フォルダごと配布・移動する。
  exe 単体では動かない）。ビルド用の依存は .venv に入れる:

    py -3 -m venv .venv
    .venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\build_exe.ps1
#>

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$python = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw ".venv が無い。先頭の手順で作ること。"
}

& $python -m PyInstaller --noconfirm --clean DividendManager.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller が失敗した (exit $LASTEXITCODE)" }

$exe = Join-Path $repo "dist\DividendManager\DividendManager.exe"
if (-not (Test-Path $exe)) { throw "exe が生成されていない: $exe" }
Write-Host "生成: $exe"
