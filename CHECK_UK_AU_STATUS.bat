@echo off
setlocal
cd /d "%~dp0"
title DOG RACE - UK + AU DATA STATUS
where py >nul 2>&1
if %errorlevel%==0 (
    py -3 -u uk_au_jobs.py status
) else (
    python -u uk_au_jobs.py status
)
echo.
pause
