@echo off
setlocal EnableDelayedExpansion

set "INSTALL_DIR=%~dp0"
set "DISPLAY_DIR=%INSTALL_DIR:~0,-1%"
set "TASK_NAME=LottoUpdaterDaemon"

echo ============================================
echo   Lotto Updater - Setup
echo ============================================
echo.
echo Install folder: %DISPLAY_DIR%
echo Schedule:       Tuesday / Thursday / Saturday at 23:55
echo.

REM ----- 1. Check Python -----
python --version >nul 2>&1
if errorlevel 1 (
    echo [X] Python is not installed or not on PATH.
    echo.
    echo Download Python 3 from https://python.org
    echo During install, tick "Add Python to PATH".
    echo Then run setup.bat again.
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('python --version') do echo [OK] Found: %%v
echo.

REM ----- 2. GitHub token -----
if exist "%INSTALL_DIR%config.json" (
    echo config.json already exists in this folder.
    set /p "REUSE=Reuse the existing token? [Y/n]: "
    if /i "!REUSE!"=="N" del /q "%INSTALL_DIR%config.json"
)

if not exist "%INSTALL_DIR%config.json" (
    echo.
    echo === GitHub Personal Access Token ===
    echo.
    echo A Windows dialog will open. Paste your token there.
    echo.
    echo If you don't have one yet, create it at:
    echo   https://github.com/settings/tokens?type=beta
    echo Required:
    echo   Repository access: Only akunh121/random_numbers2_30_08_22
    echo   Permissions:       Contents = Read and write
    echo.
    pause

    REM Pop up a real Windows InputBox via PowerShell.
    set "TOKEN="
    for /f "usebackq delims=" %%t in (`powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "Add-Type -AssemblyName Microsoft.VisualBasic; ^
         $msg = 'Paste your GitHub Personal Access Token below.' + [Environment]::NewLine + [Environment]::NewLine + 'Required permissions on this repo:' + [Environment]::NewLine + '  Contents -> Read and write' + [Environment]::NewLine + [Environment]::NewLine + 'Create one at https://github.com/settings/tokens?type=beta'; ^
         [Microsoft.VisualBasic.Interaction]::InputBox($msg, 'Lotto Updater - GitHub Token', '')"`) do set "TOKEN=%%t"

    REM Fallback to console prompt if PowerShell failed or the user cancelled.
    if "!TOKEN!"=="" (
        echo.
        echo Dialog cancelled or unavailable. Falling back to console input.
        set /p "TOKEN=Paste your token (github_pat_...): "
    )

    if "!TOKEN!"=="" (
        echo [X] No token provided.
        pause
        exit /b 1
    )

    (
        echo {
        echo   "github_token": "!TOKEN!"
        echo }
    ) > "%INSTALL_DIR%config.json"
    echo [OK] Saved to config.json
)
echo.

REM ----- 3. One-shot test -----
echo === Testing connection (one-shot run) ===
echo.
pushd "%INSTALL_DIR%"
python update_local.py --once
set "TESTRC=%errorlevel%"
popd
echo.

if not "%TESTRC%"=="0" (
    echo [X] Test run failed (exit code %TESTRC%).
    echo     Fix the error above (most often a wrong token^) and run
    echo     setup.bat again.
    pause
    exit /b 1
)

echo [OK] Test successful.
echo.

REM ----- 4. Schedule the daemon at logon -----
echo === Creating scheduled task "%TASK_NAME%" ===

schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1
schtasks /create /tn "%TASK_NAME%" /tr "\"%INSTALL_DIR%update_local.bat\"" /sc ONLOGON /rl HIGHEST /f
if errorlevel 1 (
    echo [X] Failed to create the scheduled task.
    pause
    exit /b 1
)
echo [OK] Task created. It will start every time you log in.
echo.

REM ----- 5. Start it now -----
echo === Starting the daemon now ===
schtasks /run /tn "%TASK_NAME%"
echo [OK] Daemon kicked off.
echo.

echo ============================================
echo   DONE
echo ============================================
echo.
echo Schedule:   Tue / Thu / Sat at 23:55 (local time)
echo Log file:   %INSTALL_DIR%update_local.log
echo Test again: double-click update_test.bat
echo Uninstall:  double-click uninstall.bat
echo.
echo Tip: watch the log live with:
echo   powershell -command "Get-Content '%INSTALL_DIR%update_local.log' -Wait -Tail 20"
echo.
pause
