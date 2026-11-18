@echo off
setlocal
cd /d "%~dp0"
title DOG RACE - PILOT DATA COLLECTION
cls
echo ===============================================
echo DOG RACE - PILOT / SOURCE CONNECTION TEST
 echo Checks a small number of files from each source.
echo ===============================================
where py >nul 2>nul
if %errorlevel%==0 (py -3 collector.py collect --start 2026-10-01 --end 2026-11-17 --pilot) else (python collector.py collect --start 2026-10-01 --end 2026-11-17 --pilot)
echo.
echo Pilot finished. Review FAIL messages and then run CHECK_STATUS.bat.
pause
