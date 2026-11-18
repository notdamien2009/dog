@echo off
setlocal
cd /d "%~dp0"
title DOG RACE - UK BETFAIR + AU PERFORMANCE - 3 YEAR COLLECTOR
cls
echo ============================================================
echo DOG RACE - TWO JOBS, ONE RUNNER
echo 1. UK BETFAIR historical odds, results and BSP
echo 2. Australian official performance: QLD + optional Topaz
echo Date range: 18 Nov 2023 to 17 Nov 2026
echo Existing GBGB and AU odds downloads are NOT modified.
echo ============================================================
echo.
if exist "access.local.bat" call "access.local.bat"
where py >nul 2>&1
if %errorlevel%==0 (
   py -3 -u uk_au_jobs.py collect --start 2023-11-18 --end 2026-11-17
) else (
   python -u uk_au_jobs.py collect --start 2023-11-18 --end 2026-11-17
)
set "RESULT=%errorlevel%"
echo.
if not "%RESULT%"=="0" echo One or more jobs could not complete. Check the status report.
echo Status: data\reports\uk_au_collection_status.txt
echo Safe to rerun: existing completed files will be reused.
echo.
pause
exit /b %RESULT%
