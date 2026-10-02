<#
.SYNOPSIS
  デスクトップへ「配当優待管理」のショートカットを作る（タスクバーのアイコン対策）。

.DESCRIPTION
  Windows のタスクバーはウィンドウのアイコン（QApplication.setWindowIcon）ではなく
  「起動元のショートカット」からアイコンを引く。そのため run.bat や
  `pyw -3 app.py` で直接起動すると、タイトルバーだけ自前のアイコンで、タスクバーは
  Python の .py アイコンのままになる。IconLocation に assets/app.ico を指定した
  .lnk を作り、そこから起動すればタスクバーにも自前のアイコンが出る（実測。
  詳細と反証済みの代替案は CLAUDE.md を参照）。

  インタプリタは run.bat と同じ優先順（.venv → py ランチャーの既定 → Programs\Python）
  で解決する。**環境を変えたら作り直すこと**（.lnk は解決結果を焼き込むので、
  後から .venv を作っても古いインタプリタを指したままになる）。

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\make_shortcut.ps1
#>

[CmdletBinding()]
param(
    # 出力先。既定はデスクトップ。
    [string]$Destination = [Environment]::GetFolderPath("Desktop"),
    # exe 版（PyInstaller）から呼ぶときの実行ファイル。指定時はインタプリタ探索を
    # 飛ばし、この exe 自身を起動するショートカットを作る（アイコンも exe の埋め込み）。
    [string]$ExeTarget = ""
)

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$icon = Join-Path $repo "assets\app.ico"
$entry = Join-Path $repo "app.py"

if ($ExeTarget) {
    if (-not (Test-Path $ExeTarget)) {
        throw "実行ファイルが無い: $ExeTarget"
    }
    $path = Join-Path $Destination "配当優待管理.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $ExeTarget
    $lnk.Arguments = ""
    $lnk.WorkingDirectory = Split-Path -Parent $ExeTarget
    $lnk.IconLocation = "$ExeTarget,0"
    $lnk.Description = "配当優待管理"
    $lnk.Save()
    Write-Host "作成: $path"
    Write-Host "  target : $ExeTarget"
    return
}

if (-not (Test-Path $icon)) {
    throw "アイコンが無い: $icon（py -3 tools\make_app_icon.py で生成する）"
}

# インタプリタの解決（run.bat と同じ順序。GUI なのでコンソールの出ない pythonw を使う）
$venv = Join-Path $repo ".venv\Scripts\pythonw.exe"
$target = $null
if (Test-Path $venv) {
    $target = $venv
} else {
    # py ランチャーが選ぶ既定の Python を、PATH 順に依存せず問い合わせる
    # （PATH 先頭に別ツールの venv が居ると bare python は当てにならない）。
    try {
        $exe = (& py -3 -c "import sys; print(sys.executable)" 2>$null)
        if ($LASTEXITCODE -eq 0 -and $exe) {
            $candidate = Join-Path (Split-Path -Parent $exe) "pythonw.exe"
            if (Test-Path $candidate) { $target = $candidate }
        }
    } catch {
        # py ランチャーが無い環境。次のフォールバックへ落とす。
    }
}
if (-not $target) {
    $found = Get-ChildItem "$env:LocalAppData\Programs\Python\Python*\pythonw.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | Select-Object -First 1
    if ($found) { $target = $found.FullName }
}
if (-not $target) {
    throw "pythonw.exe が見つからない。README.md の手順で .venv を作るか Python を入れること。"
}

$path = Join-Path $Destination "配当優待管理.lnk"
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($path)
$lnk.TargetPath = $target
$lnk.Arguments = "`"$entry`""
$lnk.WorkingDirectory = $repo
$lnk.IconLocation = "$icon,0"   # ここがタスクバーのアイコンの出どころ
$lnk.Description = "配当優待管理"
$lnk.Save()

Write-Host "作成: $path"
Write-Host "  target : $target"
Write-Host "  icon   : $icon"
Write-Host "タスクバーへ自前のアイコンを出すには、このショートカットから起動すること。"
