@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "app\main.py"
) else (
    python "app\main.py"
)
if errorlevel 1 pause
endlocal
