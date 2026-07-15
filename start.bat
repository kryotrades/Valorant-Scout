@echo off
title Valorant Scout
if not exist "%~dp0.scout\installed.json" goto :notinstalled
if not exist "%~dp0.venv\Scripts\python.exe" goto :notinstalled
rem Run the launcher VISIBLY in this console: start.ps1 shows a clean branded
rem progress bar, then hands off to run.py detached+hidden and this window
rem closes on its own (the scoreboard window is the app's face).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1"
exit /b %errorlevel%

:notinstalled
echo.
echo   Valorant Scout isn't set up on this PC yet.
echo   Run install.bat first (one-time setup), then use start.bat.
echo.
pause
exit /b 1
