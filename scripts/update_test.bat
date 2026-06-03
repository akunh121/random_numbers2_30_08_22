@echo off
REM One-shot run for manual testing — does not loop, does not schedule.
REM Use this to verify the GitHub token / network before installing the
REM daemon.

setlocal
cd /d "%~dp0"
python update_local.py --once
pause
