@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
conda run -n ai-stock-agent python scripts\run_v2_postclose_official_once.py %*
exit /b %ERRORLEVEL%
