@echo off
cd /d %~dp0

if exist ".venv\Scripts\pythonw.exe" (
    start "" .venv\Scripts\pythonw.exe app.py
    exit /b 0
)

rem bare pythonw は PATH 先頭の別ツール venv (PySide6 なし) を拾うため使わない。
rem py ランチャー (pyw) はデフォルト Python を PATH 順に依存せず選ぶ。
where pyw >nul 2>&1
if %errorlevel%==0 (
    start "" pyw -3 app.py
    exit /b 0
)

for /d %%D in ("%LocalAppData%\Programs\Python\Python*") do (
    if exist "%%~fD\pythonw.exe" (
        start "" "%%~fD\pythonw.exe" app.py
        exit /b 0
    )
)

echo Python runtime not found.
echo Run the setup commands in README.md to create .venv first.
pause
exit /b 1
