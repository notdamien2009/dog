@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title DOG RACE - Collection Command Centre
if exist "access.local.bat" call "access.local.bat"
where py >nul 2>&1
if not errorlevel 1 (
    py -3 -u dog_race_manager.py
) else (
    where python >nul 2>&1
    if errorlevel 1 (
        echo ERROR: Python 3 is not installed or not available on PATH.
        echo Install Python 3.10 or newer, then run this file again.
        pause
        exit /b 1
    )
    python -u dog_race_manager.py
)
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
    echo.
    echo DOG RACE command centre finished with code %EXITCODE%.
    pause
)
exit /b %EXITCODE%
