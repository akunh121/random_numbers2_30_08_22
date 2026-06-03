@echo off
setlocal

set "TASK_NAME=LottoUpdaterDaemon"

echo ============================================
echo   Lotto Updater - Uninstall
echo ============================================
echo.

echo Stopping any running daemon...
REM Kill the hidden VBS-launched chain: wscript -> cmd (bat) -> python
REM We cannot match on window title because everything runs hidden, so
REM kill by parent: any python.exe whose command line points at this
REM folder's update_local.py is ours.
for /f "tokens=*" %%p in ('powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*update_local.py*' } | Select-Object -ExpandProperty ProcessId"') do (
    taskkill /f /pid %%p >nul 2>&1
)

echo Removing scheduled task "%TASK_NAME%"...
schtasks /delete /tn "%TASK_NAME%" /f
if errorlevel 1 (
    echo (No scheduled task found - already removed.)
)

echo.
echo The scheduled task is gone. The daemon will NOT start on next login.
echo.
echo Files in this folder are NOT deleted automatically.
echo You can delete the whole folder manually after closing this window.
echo.
echo Files you might want to keep first:
echo   - config.json       (your GitHub token)
echo   - update_local.log  (history of runs)
echo.
pause
