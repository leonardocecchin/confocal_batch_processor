@echo off
REM Start the confocal batch processor. Double-click this file.
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo(
    echo   The environment is not set up yet.
    echo   Run setup.bat first.
    echo(
    pause
    exit /b 1
)

REM pythonw.exe runs without leaving a console window behind the GUI.
start "" ".venv\Scripts\pythonw.exe" run_gui.py
