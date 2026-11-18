@echo off
setlocal
cd /d "%~dp0"
title DOG RACE - GBGB OLDER HISTORY
cls
echo ======================================================
echo DOG RACE - GBGB UK HISTORICAL RESULTS 2020 - 2023
echo ======================================================
echo Run only if your permission also covers these earlier dates.
echo Private/local use, no uploads, Ctrl+C stops and rerun resumes.
echo ======================================================
echo.
where py >nul 2>&1
if %errorlevel%==0 (
    py -3 -u gbgb.py collect --start 2020-11-18 --end 2023-11-17 --delay 3
    if errorlevel 1 goto :fail
    py -3 -u gbgb_normalize.py backfill
) else (
    python -u gbgb.py collect --start 2020-11-18 --end 2023-11-17 --delay 3
    if errorlevel 1 goto :fail
    python -u gbgb_normalize.py backfill
)
echo.
if errorlevel 1 goto :fail
echo GBGB older-history run completed; reports updated.
pause
exit /b 0
:fail
echo GBGB older-history run stopped. Existing results retained.
pause
exit /b 1
