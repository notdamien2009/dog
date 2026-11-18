@echo off
setlocal
cd /d "%~dp0"
title DOG RACE - ALL OFFLINE TESTS
cls
echo DOG RACE - Testing the UK and Australian data collectors
where py >nul 2>&1
if %errorlevel%==0 (
    py -3 -m unittest discover -s tests -v
) else (
    python -m unittest discover -s tests -v
)
echo.
if errorlevel 1 (
    echo TEST FAILURE - check messages above.
) else (
    echo ALL OFFLINE TESTS PASSED.
)
pause
