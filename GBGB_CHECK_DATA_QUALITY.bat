@echo off
setlocal
cd /d "%~dp0"
title DOG RACE - GBGB DATA QUALITY
cls
echo GBGB - RACE / RUNNER DATABASE QUALITY REPORT
where py >nul 2>&1
if %errorlevel%==0 (
    py -3 -u gbgb_normalize.py report
) else (
    python -u gbgb_normalize.py report
)
if errorlevel 1 (
    echo ERROR: Report could not be generated; read message above.
) else (
    echo Report: data\reports\gbgb_normalized_quality.txt
)
pause
