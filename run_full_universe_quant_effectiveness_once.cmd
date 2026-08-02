@echo off
setlocal
cd /d "%~dp0"
if "%~1"=="" (
  echo Usage: run_full_universe_quant_effectiveness_once.cmd YYYY-MM-DD QUANT_RUN_ID FACTOR_VERSION
  exit /b 2
)
conda run -n ai-stock-agent python scripts\capture_full_universe_quant_effectiveness.py --trade-date %1 --quant-run-id %2 --factor-version %3 --no-network
if errorlevel 1 exit /b %errorlevel%
echo Full-universe Quant effectiveness snapshot completed.
endlocal
