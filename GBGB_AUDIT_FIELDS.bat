@echo off
setlocal
cd /d "%~dp0"
title DOG RACE - GBGB CACHED DATA FIELD AUDIT
cls
echo =====================================================
echo GBGB AUDIT - ALL LOCAL DATA, NO DOWNLOADS
 echo Reads the finished pilot JSON from data\gbgb.sqlite3.
echo Reports stored in data\reports\
echo It does NOT change your database or files.
echo =====================================================
echo.
where py >nul 2>&1
if %errorlevel%==0 (
    py -3 -u gbgb_field_audit.py
) else (
    python -u gbgb_field_audit.py
)
if errorlevel 1 goto :fail
echo.
echo FIELD AUDIT FINISHED. Please share the three GBGB report files.
pause
exit /b 0
:fail
echo.
echo GBGB AUDIT FAILED - copy the error above.
pause
exit /b 1
