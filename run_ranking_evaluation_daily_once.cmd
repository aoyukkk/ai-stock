@echo off
setlocal
cd /d "%~dp0"
call conda run -n ai-stock-agent python scripts\run_ranking_evaluation_once.py --mode daily %*
if errorlevel 1 (
  echo.
  echo Ranking evaluation daily run failed.
  pause
  exit /b 1
)
echo.
echo Ranking evaluation daily run completed.
pause
