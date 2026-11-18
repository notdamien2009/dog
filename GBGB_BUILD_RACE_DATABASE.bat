@echo off
setlocal
cd /d "%~dp0"
title DOG RACE - GBGB BUILD RACE AND RUNNER DATABASE
cls
echo =====================================================
echo GBGB - BUILD NORMALISED RACE / RUNNER DATABASE
echo No downloads. Reads 27 existing meetings and any newer cache.
echo Does NOT delete source JSON or Australian data.
echo =====================================================
echo.
where py >nul 2>&1
if %errorlevel%==0 (
    py -3 -u gbgb_normalize.py backfill
) else (
    python -u gbgb_normalize.py backfill
)
if errorlevel 1 goto :fail
echo.
echo SUCCESS: View data\reports\gbgb_normalized_quality.txt
echo Also view gbgb_track_coverage.csv and gbgb_normalized_dates.csv
pause
exit /b 0
:fail
echo.
echo MAPPING STOPPED - source data kept. Read error above.
pause
exit /b 1
