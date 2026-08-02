@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
echo V3.1 Event Overlay Shadow - formal daily flow
set /p TRADE_DATE=Trade date (YYYY-MM-DD):
if "%TRADE_DATE%"=="" exit /b 2
conda run --no-capture-output -n ai-stock-agent python scripts\run_v3_event_overlay_shadow_once.py --trade-date "%TRADE_DATE%" --daily-real-search
if errorlevel 1 (
  echo.
  echo Run failed closed. Review outputs\event_overlay\%TRADE_DATE% before retrying.
  pause
  exit /b 1
)
echo.
echo V3.1 Shadow completed and eligible results were synchronized to Web.
echo No real or virtual orders were created. Scheduler remains disabled.
pause
endlocal
