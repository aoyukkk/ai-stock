@echo off
setlocal
cd /d "%~dp0"
set /p WEEK_ENDING=Week ending (YYYY-MM-DD):
set /p QUANT_VERSION=Quant factor version:
set /p SCREENING_VERSION=Screening version:
conda run -n ai-stock-agent python scripts\run_model_stage_effectiveness_once.py --mode weekly --week-ending %WEEK_ENDING% --quant-factor-version %QUANT_VERSION% --screening-version %SCREENING_VERSION%
pause
