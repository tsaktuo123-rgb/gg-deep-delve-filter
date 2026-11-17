@echo off
title GG DEEP DELVE - Auto Stash Watcher Installer
cd /d "%~dp0.."

echo Installing GG DEEP DELVE auto-watcher (runs at logon, polls every 90s while PD2 is open)
where python >nul 2>nul
if not %errorlevel%==0 (
    echo Python was not found on PATH. Install Python 3.10+ first.
    pause
    exit /b 1
)
python -c "import keyboard" 2>nul
if not %errorlevel%==0 (
    echo Installing requirements...
    python -m pip install -r requirements.txt
)
schtasks /Create /TN "GG Deep Delve Auto Watcher" /TR "\"%CD%\scripts\Price_My_Stash.bat\" --watch" /SC ONLOGON /RL LIMITED /F
if %errorlevel%==0 (
    echo.
    echo Registered scheduled task "GG Deep Delve Auto Watcher" at logon.
    echo Configure your token first: copy .env.example to .env and set PD2_AUTH_TOKEN.
    echo Remove anytime with:  schtasks /Delete /TN "GG Deep Delve Auto Watcher"
) else (
    echo schtasks failed - you can instead start the watcher manually:
    echo   scripts\Price_My_Stash.bat --watch
)
pause
