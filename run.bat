@echo off
cd /d %~dp0

if exist ".venv\Scripts\pythonw.exe" (
    start "" .venv\Scripts\pythonw.exe app.py
    exit /b 0
)

where pythonw >nul 2>&1
if %errorlevel%==0 (
    start "" pythonw app.py
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
