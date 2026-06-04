@echo off
setlocal EnableDelayedExpansion

set "INSTALL_DIR=%~dp0"
set "DISPLAY_DIR=%INSTALL_DIR:~0,-1%"
set "TASK_NAME=LottoUpdaterDaemon"
set "EXITCODE=0"

cd /d "%INSTALL_DIR%"

call :main

echo.
echo ============================================
echo   Press any key to close this window.
echo ============================================
pause >nul
exit /b %EXITCODE%


:main
echo ============================================
echo   Lotto Updater - Setup
echo ============================================
echo.
echo Install folder: %DISPLAY_DIR%
echo Schedule:       Tuesday / Thursday / Saturday at 23:55
echo.

REM ----- 0. Detect and remove a previous installation -----
set "OLD_DIR="
for /f "usebackq delims=" %%i in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%INSTALL_DIR%find-existing.ps1"`) do set "OLD_DIR=%%i"

if defined OLD_DIR (
    echo Existing installation detected at:
    echo   !OLD_DIR!
    echo.

    REM Reuse the existing GitHub token unless we already have one in
    REM the new folder.
    if exist "!OLD_DIR!config.json" if not exist "%INSTALL_DIR%config.json" (
        if /i not "!OLD_DIR!"=="%INSTALL_DIR%" (
            echo Importing GitHub token from the previous install ...
            copy /Y "!OLD_DIR!config.json" "%INSTALL_DIR%config.json" >nul
            if errorlevel 1 (
                echo [!] Could not copy config.json. You may need to re-enter the token.
            ) else (
                echo [OK] Token imported.
            )
        )
    )

    echo Stopping the running daemon ...
    schtasks /end /tn "%TASK_NAME%" >nul 2>&1
    powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*update_local.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

    echo Removing the old scheduled task ...
    schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1
    echo [OK] Old installation cleaned up.
    echo.
)

REM ----- 1. Check Python -----
python --version >nul 2>&1
if errorlevel 1 (
    echo [X] Python is not installed or not on PATH.
    echo.
    echo Download Python 3 from https://python.org
    echo During install, TICK "Add Python to PATH".
    echo Then run setup.bat again.
    set "EXITCODE=1"
    exit /b
)
for /f "tokens=*" %%v in ('python --version') do echo [OK] Found: %%v
echo.

REM ----- 2. GitHub token -----
set "NEED_TOKEN=1"
if exist "%INSTALL_DIR%config.json" (
    echo config.json already exists in this folder.
    set "REUSE="
    set /p "REUSE=Reuse the existing token? [Y/n]: "
    if /i not "!REUSE!"=="N" set "NEED_TOKEN=0"
    if /i "!REUSE!"=="N" del /q "%INSTALL_DIR%config.json"
)

if "!NEED_TOKEN!"=="1" (
    echo.
    echo === GitHub Personal Access Token ===
    echo.
    echo A Windows dialog will open now. Paste your token there.
    echo If you do not have one, create it first at:
    echo   https://github.com/settings/tokens?type=beta
    echo Required: Contents = Read and write on the lotto repo.
    echo.
    pause

    set "TOKEN="
    if exist "%INSTALL_DIR%get-token.ps1" (
        for /f "usebackq delims=" %%t in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%INSTALL_DIR%get-token.ps1"`) do set "TOKEN=%%t"
    )

    if "!TOKEN!"=="" (
        echo.
        echo Dialog cancelled or PowerShell unavailable.
        echo Enter the token here instead:
        set /p "TOKEN=Token: "
    )

    if "!TOKEN!"=="" (
        echo.
        echo [X] No token provided. Aborting.
        set "EXITCODE=1"
        exit /b
    )

    (
        echo {
        echo   "github_token": "!TOKEN!"
        echo }
    ) > "%INSTALL_DIR%config.json"
    echo [OK] Token saved to config.json
)
echo.

REM ----- 3. One-shot test -----
echo === Test run ===
echo.
python "%INSTALL_DIR%update_local.py" --once
set "TESTRC=%errorlevel%"
echo.
if not "%TESTRC%"=="0" (
    echo [X] Test failed (exit code %TESTRC%).
    echo     Fix the error above - usually a wrong or expired token.
    echo     Delete config.json and run setup.bat again to re-enter.
    set "EXITCODE=1"
    exit /b
)
echo [OK] Test passed.
echo.

REM ----- 4. Schedule the daemon at logon (hidden via VBS) -----
echo === Creating scheduled task "%TASK_NAME%" ===
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1
schtasks /create /tn "%TASK_NAME%" /tr "wscript.exe \"%INSTALL_DIR%run_hidden.vbs\"" /sc ONLOGON /rl HIGHEST /f
if errorlevel 1 (
    echo [X] Failed to create scheduled task.
    set "EXITCODE=1"
    exit /b
)
echo [OK] Task created. It will start hidden on every login.
echo.

REM ----- 5. Start the daemon now (hidden) -----
echo === Starting the daemon now ===
schtasks /run /tn "%TASK_NAME%"
echo [OK] Daemon started silently in the background.
echo.

echo ============================================
echo   DONE
echo ============================================
echo.
echo Schedule:   Tue / Thu / Sat at 23:55 local time
echo Log file:   %INSTALL_DIR%update_local.log
echo Test again: double-click update_test.bat
echo Uninstall:  double-click uninstall.bat
echo.
echo Tip - watch the log live:
echo   powershell -command "Get-Content '%INSTALL_DIR%update_local.log' -Wait -Tail 20"

exit /b
