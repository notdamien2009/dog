@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title DOG RACE - SAFE PROJECT CLEANUP
cls
echo ============================================================
echo DOG RACE - VERIFIED CLEANUP (LOCAL FILES ONLY)
echo ============================================================
echo Old files are MOVED to a Desktop recovery backup, not erased.
echo Never touches data, private keys, tests, or Git history.
echo.
where py >nul 2>&1
if not errorlevel 1 (
    py -3 -u dog_race_cleanup.py
) else (
    where python >nul 2>&1
    if errorlevel 1 (
        echo ERROR: Python 3.10+ is required to run this safety-checked cleanup.
        pause
        exit /b 1
    )
    python -u dog_race_cleanup.py
)
set "RESULT=%errorlevel%"
echo.
if not "%RESULT%"=="0" echo CLEANUP NOT COMPLETE. No databases were intentionally changed.
pause
exit /b %RESULT%
