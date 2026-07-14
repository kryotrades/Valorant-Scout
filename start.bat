@echo off
title Valorant Scout
if not exist "%~dp0.scout\installed.json" goto :notinstalled
if not exist "%~dp0.venv\Scripts\python.exe" goto :notinstalled
rem Launch fully hidden: the CLI scoreboard window is the app's face - closing
rem it stops everything (run.py watches it), so no server console needs to be
rem visible or in the taskbar. This window flashes once and closes.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','\"%~dp0scripts\start.ps1\"'"
exit /b

:notinstalled
echo.
echo   Valorant Scout isn't set up on this PC yet.
echo   Run install.bat first (one-time setup), then use start.bat.
echo.
pause
exit /b 1
