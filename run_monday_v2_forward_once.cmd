@echo off
setlocal
cd /d "%~dp0"

conda run -n ai-stock-agent python scripts\freeze_monday_v2_decision.py
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
  echo.
  echo Monday V2 forward tracking failed with exit code %EXIT_CODE%.
) else (
  echo.
  echo Monday V2 forward tracking completed.
)

pause
exit /b %EXIT_CODE%
