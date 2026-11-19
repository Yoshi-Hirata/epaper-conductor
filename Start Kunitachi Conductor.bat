@echo off
rem E-paper Kunitachi Conductor - double-click to start the Conductor for the
rem show at JR Kunitachi station (2026-11-22). The same program as Start
rem Conductor.bat, but a separate application: its own window title, its own
rem port (8767; the PC show's is 8765, the exhibition's 8766), its own data
rem folder (kunitachi-data\: the seven garments of the PC show, the 35-minute
rem music kunitachi100_music_mix_1119.mp3) and a KUNITACHI badge on the page,
rem so it can never be mixed up with the other shows.
rem
rem Never run this and another Conductor against the fleet at the same time:
rem two Conductors "correct" each other's T0 and fight.
rem
rem It serves http://127.0.0.1:8767 (this PC only) and opens it in the
rem default browser. Keep this window open while you work; close it (or
rem press Ctrl+C) to stop the UI. Starting it twice just opens the page again.
title E-paper Kunitachi Conductor
cd /d "%~dp0"

set PY=python
where python >nul 2>nul
if errorlevel 1 (
  where py >nul 2>nul
  if errorlevel 1 (
    echo Python 3.9 or newer was not found. Install it from https://www.python.org/ and try again.
    pause
    exit /b 1
  )
  set PY=py -3
)

%PY% -m conductor serve --workspace kunitachi-data --port 8767 --label KUNITACHI --open
if errorlevel 1 pause
