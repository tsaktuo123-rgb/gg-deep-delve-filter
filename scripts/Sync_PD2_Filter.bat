@echo off
title Sync PD2 Filter Prices (GG DEEP DELVE - Season 14)
cd /d "%~dp0.."
where python >nul 2>nul
if %errorlevel%==0 (
    python "%~dp0sync_pd2_prices.py" %*
) else (
    py "%~dp0sync_pd2_prices.py" %*
)
echo.
pause
