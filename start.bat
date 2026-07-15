@echo off
title Valorant Scout
if not exist "%~dp0.scout\installed.json" goto :notinstalled
if not exist "%~dp0.venv\Scripts\python.exe" goto :notinstalled
rem One window for everything: start.ps1 shows a branded progress bar in THIS
rem console, then the scoreboard renders right here. Closing this window
rem closes the whole app.
rem
rem The hand-off and `exit /b` MUST stay on ONE line. cmd.exe reads a batch file
rem by byte offset and REOPENS it for each line, so when an auto-update rewrites
rem start.bat mid-run, a separate exit line would resume at a stale offset in the
rem new file and print garbage ("'ho.' is not recognized..."). With `& exit /b`
rem on the same line, cmd has already buffered it and never reopens the file.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" & exit /b

:notinstalled
echo.
echo   Valorant Scout isn't set up on this PC yet.
echo   Run install.bat first (one-time setup), then use start.bat.
echo.
pause
exit /b 1
