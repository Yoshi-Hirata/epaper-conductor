@echo off
rem E-paper Kunitachi Conductor - back up the Kunitachi show's data
rem (kunitachi-data\) to a dated zip next to this repository folder, e.g.
rem ..\kunitachi-data-20260929-1830.zip. The production show's data
rem (showdata\) is a different folder with its own Backup showdata.bat.
rem The zip holds everything the Kunitachi UI keeps: CSV files, the timeline
rem and unit assignments (show.json), the music file, the undo history and
rem this PC's fleet.json. Unzip it into a fresh clone on another PC (so that
rem kunitachi-data\files\... exists there) and Start Kunitachi Conductor.bat
rem shows the same show.
title Backup kunitachi-data
cd /d "%~dp0"
if not exist "kunitachi-data\" (
  echo No kunitachi-data folder here yet - nothing to back up.
  pause
  exit /b 1
)
for /f "tokens=1-5 delims=/:. " %%a in ("%date% %time%") do set STAMP=%%a%%b%%c-%%d%%e
set STAMP=%STAMP: =0%
set OUT=..\kunitachi-data-%STAMP%.zip
powershell -NoProfile -Command "Compress-Archive -Path 'kunitachi-data' -DestinationPath '%OUT%' -Force"
if errorlevel 1 (
  echo Backup failed.
  pause
  exit /b 1
)
for %%F in ("%OUT%") do echo Saved %%~fF (%%~zF bytes)
pause
