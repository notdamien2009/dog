@echo off
setlocal
cd /d "%~dp0"
title DOG RACE - THREE YEAR HISTORICAL COLLECTION
cls
echo ==========================================================
echo DOG RACE - HISTORY 18 NOV 2023 THROUGH 17 NOV 2026
 echo Source archives are incomplete in some regions/years.
echo Results are saved as work progresses. CTRL+C safely stops.
echo ==========================================================
where py >nul 2>nul
if %errorlevel%==0 (py -3 collector.py collect --start 2023-11-18 --end 2026-11-17) else (python collector.py collect --start 2023-11-18 --end 2026-11-17)
echo.
echo Collection finished. Run CHECK_STATUS.bat for coverage.
pause
