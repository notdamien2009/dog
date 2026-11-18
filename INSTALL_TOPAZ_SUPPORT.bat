@echo off
setlocal
cd /d "%~dp0"
title DOG RACE - OPTIONAL TOPAZ DEPENDENCIES
echo Topaz is optional. You need a legitimately issued Topaz key.
where py >nul 2>&1
if %errorlevel%==0 (
    py -3 -m pip install --upgrade topaz_api pandas
) else (
    python -m pip install --upgrade topaz_api pandas
)
echo.
if errorlevel 1 echo Installation failed. Read README_UK_AU_JOBS.txt.
pause
