@echo off
setlocal
cd /d "%~dp0"
conda run -n ai-stock-agent python scripts\run_model_stage_effectiveness_once.py --mode daily
pause
