@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set NO_PROXY=*
set no_proxy=*
if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe start_web.py %*
) else (
    python start_web.py %*
)
echo.
echo Web server exited. Press any key to close...
pause >nul
