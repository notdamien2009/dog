@echo off
cd /d "%~dp0"
title DOG RACE - GBGB Status
where py >nul 2>&1
if %errorlevel%==0 (py -3 gbgb.py status) else (python gbgb.py status)
pause
