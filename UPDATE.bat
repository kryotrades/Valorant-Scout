@echo off
title Valorant Scout - Update
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update.ps1"
set "VS_EXIT=%ERRORLEVEL%"
echo.
pause
exit /b %VS_EXIT%
