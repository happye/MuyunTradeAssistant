@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set NO_PROXY=*
set no_proxy=*
if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe start_tui.py %*
) else (
    python start_tui.py %*
)
echo.
echo TUI exited. Press any key to close...
pause >nul
