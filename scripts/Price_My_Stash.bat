@echo off
title GG DEEP DELVE - Stash Pricer
cd /d "%~dp0.."
where python >nul 2>nul
if %errorlevel%==0 (
    python "%~dp0stash_pricer.py" %*
) else (
    py "%~dp0stash_pricer.py" %*
)
echo.
pause
