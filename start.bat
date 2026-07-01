@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

REM 无参数启动时显示 bz scan 规则简述（进 REPL 前快速参考）
if "%~1"=="" (
    echo.
    echo === bz scan 选股规则简述 ===
    echo   bz scan                  全市场技术初筛(healthy_pullback健康回调) ^> 笨总评分Top10
    echo   bz scan ^<主题^>           双通道选股(法C+全市场) ^> 笨总评分
    echo   bz scan --allrules       四规则全跑(各Top5合并) ^> 笨总评分 [推荐,不挑市况]
    echo   bz scan --rule ^<规则^>    指定规则:
    echo     healthy_pullback  健康回调  缩量小跌-3%%~-0.1%%(默认,牛市找回调买点)
    echo     steady_advance    温和上涨  放量上涨0.1%%~5%%(蓄势期找突破)
    echo     shrink_pullback   缩量回调  深跌缩量-5%%~-0.1%%(震荡市找洗盘企稳)
    echo     value_pick        低估值    PE^<20/PB^<2(价值投资,不挑市况)
    echo   平收股(涨跌幅0)已排除 - 主力控盘非回调/上涨
    echo.
)

if exist ".venv\Scripts\python.exe" (
	.venv\Scripts\python.exe start.py %*
) else (
	python start.py %*
)
