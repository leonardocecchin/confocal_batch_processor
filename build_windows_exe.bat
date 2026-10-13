@echo off
REM ---------------------------------------------------------------------------
REM  Build a standalone Windows folder that runs without Python installed.
REM  MUST be run on Windows, after setup.bat has worked: PyInstaller does not
REM  cross-compile. See WINDOWS.md for the trade-offs before using this.
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Run setup.bat first.
    pause
    exit /b 1
)

echo ==^> Installing PyInstaller into the environment
".venv\Scripts\python.exe" -m pip install --quiet pyinstaller
if errorlevel 1 ( echo Could not install PyInstaller. & pause & exit /b 1 )

echo(
echo ==^> Building ^(several minutes, and the result is large^)

".venv\Scripts\python.exe" tools\build_exe.py

if errorlevel 1 (
    echo(
    echo Build failed - the error is above.
    pause
    exit /b 1
)

echo(
echo ===========================================================
echo   Built dist\ConfocalProcessor\
echo   Launch it with dist\ConfocalProcessor\ConfocalProcessor.exe
echo   Zip the whole folder to hand it to someone else.
echo ===========================================================
echo(
pause
