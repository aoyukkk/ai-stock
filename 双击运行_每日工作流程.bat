@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title AI Trader Assistant - Daily Workflow

echo.
echo ==============================================
echo          AI Trader Assistant Daily Run
echo ==============================================
echo.
echo   [1] Midday workflow   - run around 11:30
echo   [2] Post-close flow   - run after 17:00
echo   [Q] Cancel
echo.
choice /C 12Q /N /M "Select 1, 2 or Q: "

if errorlevel 3 goto cancelled
if errorlevel 2 goto postclose
if errorlevel 1 goto midday

:midday
echo.
echo Starting midday workflow with Conda environment ai-stock-agent...
call "%~dp0run_midday_once.cmd"
set "WORKFLOW_EXIT=%ERRORLEVEL%"
goto finished

:postclose
echo.
echo Starting official post-close workflow with Conda environment ai-stock-agent...
call "%~dp0run_close_once.cmd"
set "WORKFLOW_EXIT=%ERRORLEVEL%"
goto finished

:cancelled
echo.
echo Cancelled. No workflow was started.
exit /b 0

:finished
echo.
if "%WORKFLOW_EXIT%"=="0" (
  echo Workflow completed successfully.
) else (
  echo Workflow stopped or failed. Exit code: %WORKFLOW_EXIT%
  echo Check the matching output audit and log before retrying.
)
echo.
pause
exit /b %WORKFLOW_EXIT%
