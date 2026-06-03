@echo off
setlocal

set "TASK_NAME=LottoUpdaterDaemon"

echo ============================================
echo   Lotto Updater - Uninstall
echo ============================================
echo.

echo Stopping any running daemon...
taskkill /f /im python.exe /fi "WINDOWTITLE eq update_local*" >nul 2>&1
taskkill /f /im cmd.exe /fi "WINDOWTITLE eq update_local.bat*" >nul 2>&1

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
