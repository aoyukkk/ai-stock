@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
conda run -n ai-stock-agent python scripts\run_midday_once.py %*
exit /b %ERRORLEVEL%
