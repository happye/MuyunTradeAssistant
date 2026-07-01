@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
	.venv\Scripts\python.exe start.py %*
) else (
	python start.py %*
)
