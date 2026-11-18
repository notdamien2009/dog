@echo off
setlocal
cd /d "%~dp0"
title DOG RACE - GBGB 3 YEAR LOCAL COLLECTOR
cls
echo ======================================================
echo DOG RACE - GBGB UK HISTORICAL RESULTS 2023 - 2026
echo ======================================================
echo Private/local research collection under your stated permission.
echo Results are saved only in this DOG RACE folder.
echo Ctrl+C stops the run; double-click again to resume.
echo GBGB server 401/403/429 errors stop collection.
echo ======================================================
echo.
where py >nul 2>&1
if %errorlevel%==0 (
    py -3 -u gbgb.py collect --start 2023-11-18 --end 2026-11-17 --delay 3
    if errorlevel 1 goto :fail
    py -3 -u gbgb_normalize.py backfill
) else (
    python -u gbgb.py collect --start 2023-11-18 --end 2026-11-17 --delay 3
    if errorlevel 1 goto :fail
    python -u gbgb_normalize.py backfill
)
echo.
if errorlevel 1 goto :fail
echo.
echo GBGB three-year run completed. Quality report in data\reports.
pause
exit /b 0
:fail
echo.
echo GBGB historical collector stopped. Existing raw and parsed results retained.
pause
exit /b 1
