@echo off
title Valorant Scout
if not exist "%~dp0.scout\installed.json" goto :notinstalled
if not exist "%~dp0.venv\Scripts\python.exe" goto :notinstalled
rem One window for everything: start.ps1 shows a branded progress bar in THIS
rem console, then the scoreboard renders right here. Closing this window
rem closes the whole app.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1"
exit /b %errorlevel%

:notinstalled
echo.
echo   Valorant Scout isn't set up on this PC yet.
echo   Run install.bat first (one-time setup), then use start.bat.
echo.
pause
exit /b 1
