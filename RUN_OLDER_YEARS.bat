@echo off
setlocal
cd /d "%~dp0"
title DOG RACE - OLDER HISTORICAL COLLECTION
cls
echo ==========================================================
echo DOG RACE - OLDER HISTORY 18 NOV 2020 THROUGH 17 NOV 2023
 echo Only published source files will be downloaded.
echo ==========================================================
where py >nul 2>nul
if %errorlevel%==0 (py -3 collector.py collect --start 2020-11-18 --end 2023-11-17) else (python collector.py collect --start 2020-11-18 --end 2023-11-17)
echo.
pause
