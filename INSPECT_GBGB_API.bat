@echo off
cd /d "%~dp0"
title DOG RACE - GBGB ONE REQUEST INSPECTION
where py >nul 2>&1
if %errorlevel%==0 (py -3 gbgb.py inspect --date 2024-06-01) else (python gbgb.py inspect --date 2024-06-01)
echo.
echo CHECK THE ABOVE RESULT - no GBGB bulk collection is performed.
pause
