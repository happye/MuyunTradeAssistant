@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
rem ── 避免中文乱码 + 代理干扰（start_tui.py 内也会设，这里在 python 启动前设更稳）──
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
echo TUI 已退出，按任意键关闭...
pause >nul
