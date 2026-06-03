@echo off
REM Long-running daemon launcher for the lotto updater.
REM The Python script schedules itself (Tue/Thu/Sat at 23:55) — Task
REM Scheduler only needs to start this once "At log on" or "At startup".
REM If the Python process exits unexpectedly we wait 30 seconds and
REM restart automatically.

setlocal
cd /d "%~dp0"
set "LOG=update_local.log"

:loop
echo. >> "%LOG%"
echo ==================== Daemon start %date% %time% ==================== >> "%LOG%"
python update_local.py >> "%LOG%" 2>&1
echo ==================== Daemon exit %errorlevel% %date% %time% ==================== >> "%LOG%"
timeout /t 30 /nobreak >nul
goto loop
