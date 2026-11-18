@echo off
cd /d "%~dp0"
title DOG RACE - GBGB ONE MEETING SCHEMA INSPECTION
where py >nul 2>&1
if %errorlevel%==0 (py -3 gbgb.py inspect-meeting --meeting 415753) else (python gbgb.py inspect-meeting --meeting 415753)
echo.
echo CHECK THE ABOVE FIELDS - no GBGB bulk collection performed.
pause
