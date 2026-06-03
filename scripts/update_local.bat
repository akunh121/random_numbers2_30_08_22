@echo off
REM Wrapper for Task Scheduler — runs the lotto updater and logs the output.
REM Place this next to update_local.py and point Task Scheduler at this file.

setlocal
cd /d "%~dp0"
set "LOG=update_local.log"

echo. >> "%LOG%"
echo ==================== %date% %time% ==================== >> "%LOG%"
python update_local.py >> "%LOG%" 2>&1
set "RC=%errorlevel%"
echo Exit code: %RC% >> "%LOG%"
exit /b %RC%
