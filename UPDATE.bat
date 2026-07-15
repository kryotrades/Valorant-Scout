@echo off
title Valorant Scout - Update
rem update.ps1 REPLACES this file mid-run. Keep the hand-off + pause + exit on
rem ONE line: cmd reads a batch file by byte offset and reopens it per line, so a
rem separate line after the hand-off would resume at a stale offset in the new
rem file and print garbage. One buffered line means cmd never reopens it.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update.ps1" & echo. & pause & exit /b
