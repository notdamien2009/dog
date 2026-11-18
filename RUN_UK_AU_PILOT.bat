@echo off
setlocal
cd /d "%~dp0"
title DOG RACE - UK BETFAIR + AU PERFORMANCE - SMALL PILOT
cls
echo ============================================================
echo UK + AU PILOT: at most 2 Betfair files, 2 QLD CSV files,
echo and one Australian Topaz monthly file (if key available).
echo ============================================================
echo.
if exist "access.local.bat" call "access.local.bat"
where py >nul 2>&1
if %errorlevel%==0 (
   py -3 -u uk_au_jobs.py pilot --start 2023-11-18 --end 2026-11-17
) else (
   python -u uk_au_jobs.py pilot --start 2023-11-18 --end 2026-11-17
)
set "RESULT=%errorlevel%"
echo.
echo CHECK: data\reports\uk_au_collection_status.txt
if not "%RESULT%"=="0" echo Pilot did not fully pass; resolve the above before full run.
pause
exit /b %RESULT%
