@echo off
setlocal
cd /d "%~dp0"
if "%~1"=="" (
  echo Usage: %~nx0 FACTOR_VERSION [additional arguments]
  echo Example: %~nx0 TUSHARE_BASELINE_V1
  pause
  exit /b 2
)
set "RANKING_FACTOR_VERSION=%~1"
shift
call conda run -n ai-stock-agent python scripts\run_ranking_evaluation_once.py --mode weekly --factor-version "%RANKING_FACTOR_VERSION%" %*
if errorlevel 1 (
  echo.
  echo Ranking evaluation weekly run failed.
  pause
  exit /b 1
)
echo.
echo Ranking evaluation weekly run completed.
pause
