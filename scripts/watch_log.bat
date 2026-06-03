@echo off
REM Watch update_local.log live (refreshes as new lines are appended).
REM Press Ctrl+C to stop watching - the daemon itself keeps running.

setlocal
cd /d "%~dp0"

echo Watching update_local.log
echo Press Ctrl+C to stop watching (the daemon itself keeps running).
echo.

if not exist "update_local.log" (
    echo No log yet - has the daemon ever run?
    echo Try: update_test.bat for a one-shot test,
    echo or:   setup.bat to install the scheduled daemon.
    pause
    exit /b 1
)

powershell -NoProfile -Command "Get-Content 'update_local.log' -Wait -Tail 30"
