@echo off
title Valorant Scout
rem Launch fully hidden: the CLI scoreboard window is the app's face — closing
rem it stops everything (run.py watches it), so no server console needs to be
rem visible or in the taskbar. This window flashes once and closes.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','\"%~dp0scripts\start.ps1\"'"
exit /b
