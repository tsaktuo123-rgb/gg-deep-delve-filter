@echo off
title GG DEEP DELVE - Watcher Monitor
cd /d "%~dp0.."
color 0E
echo ============================================================
echo   GG DEEP DELVE - live watcher feed
echo   what the watcher prices and puts on the market
echo   (log: watcher.log - close this window to stop watching)
echo ============================================================
echo.

:loop
if not exist watcher.log (
    echo [%time:~0,8%] watcher.log not found - waiting for the watcher to start...
    timeout /t 3 /nobreak >nul
    goto loop
)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-Content -LiteralPath 'watcher.log' -Wait -Tail 300"
echo.
echo [monitor stopped]
pause
