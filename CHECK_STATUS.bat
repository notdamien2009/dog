@echo off
setlocal
cd /d "%~dp0"
title DOG RACE - COVERAGE AND STATUS
cls
where py >nul 2>nul
if %errorlevel%==0 (py -3 collector.py status & py -3 audit.py) else (python collector.py status & python audit.py)
echo.
pause
