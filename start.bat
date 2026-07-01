@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

if "%~1"=="" call :show_rules

if exist ".venv\Scripts\python.exe" (
	.venv\Scripts\python.exe start.py %*
) else (
	python start.py %*
)
goto :eof

:show_rules
echo.
echo === bz scan 选股规则简述 ===
echo   bz scan                  全市场技术初筛(健康回调) - 笨总评分Top10
echo   bz scan 主题词            双通道选股(法C+全市场) - 笨总评分
echo   bz scan --allrules       四规则全跑(各Top5合并) - 笨总评分 [推荐,不挑市况]
echo   bz scan --rule 规则名     指定单规则:
echo     healthy_pullback  健康回调  缩量小跌-3%~-0.1%(默认,牛市找回调买点)
echo     steady_advance    温和上涨  放量上涨0.1%~5%(蓄势期找突破)
echo     shrink_pullback   缩量回调  深跌缩量-5%~-0.1%(震荡市找洗盘企稳)
echo     value_pick        低估值    PE 1-20 / PB 0.1-2(价值投资,不挑市况)
echo   注:平收股(涨跌幅0,主力控盘)已排除 - 非回调/上涨
echo.
goto :eof
