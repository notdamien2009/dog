@echo off
setlocal
cd /d "%~dp0"
title DOG RACE - GBGB TWO-DATE PILOT
cls
echo ======================================================
echo GBGB PILOT - one older and one recent racing day
 echo Checks index, pagination, meeting downloads, storage.
echo Does not attempt a full archive run.
echo ======================================================
echo.
where py >nul 2>&1
if %errorlevel%==0 (
    set "PY=py -3 -u"
) else (
    set "PY=python -u"
)
%PY% gbgb.py collect --start 2024-06-01 --end 2024-06-01 --delay 3
if errorlevel 1 goto :fail
%PY% gbgb.py collect --start 2026-11-16 --end 2026-11-16 --delay 3
if errorlevel 1 goto :fail
%PY% gbgb_normalize.py backfill
if errorlevel 1 goto :fail
%PY% gbgb.py status
if errorlevel 1 goto :fail
echo.
echo Pilot requests finished. Check counts above; share them if empty.
echo Review data\reports\gbgb_normalized_quality.txt before full history.
pause
exit /b 0
:fail
echo.
echo PILOT FAILED. Do not run three years before resolving this error.
pause
exit /b 1
