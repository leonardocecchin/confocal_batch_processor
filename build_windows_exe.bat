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

REM scikit-image, scipy and nd2 resolve plugins at run time, so PyInstaller's
REM import scanner cannot see them; they are named explicitly here. If the exe
REM starts and then dies with ModuleNotFoundError, add the module the same way.
".venv\Scripts\python.exe" -m PyInstaller ^
    --noconfirm ^
    --windowed ^
    --name ConfocalProcessor ^
    --collect-submodules skimage ^
    --collect-submodules scipy ^
    --collect-submodules nd2 ^
    --collect-data skimage ^
    --collect-data matplotlib ^
    --hidden-import edt ^
    --hidden-import tifffile ^
    --hidden-import imagecodecs ^
    --hidden-import PIL._tkinter_finder ^
    --hidden-import pandas ^
    run_gui.py

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
