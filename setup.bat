@echo off
REM ---------------------------------------------------------------------------
REM  Confocal batch processor - one-time setup on Windows.
REM  Creates an isolated .venv next to this file and installs everything into
REM  it. Nothing is installed system-wide.
REM
REM  Run it by double-clicking, or from a terminal:  setup.bat
REM ---------------------------------------------------------------------------
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo(
echo ==^> Looking for Python

REM The py launcher is what the python.org installer puts on PATH; prefer the
REM versions we know have wheels for every dependency.
set "PY="
for %%V in (3.12 3.11 3.13 3.10) do (
    if not defined PY (
        py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
    )
)
if not defined PY (
    python -c "import sys; sys.exit(0 if sys.version_info[:2] in ((3,10),(3,11),(3,12),(3,13),(3,14)) else 1)" >nul 2>&1 && set "PY=python"
)

if not defined PY (
    echo(
    echo     Python 3.10 or newer was not found.
    echo(
    echo     Install it from https://www.python.org/downloads/windows/
    echo     and TICK "Add python.exe to PATH" on the first screen.
    echo     Then close this window and run setup.bat again.
    echo(
    pause
    exit /b 1
)

for /f "delims=" %%V in ('%PY% -c "import sys;print(sys.version.split()[0])"') do set "PYVER=%%V"
echo     using Python !PYVER!

if exist ".venv" (
    echo(
    echo ==^> .venv already exists
    echo     Delete the .venv folder first if you want to rebuild it.
    echo(
    pause
    exit /b 1
)

echo(
echo ==^> Creating the virtual environment
%PY% -m venv .venv
if errorlevel 1 (
    echo     Could not create .venv - is Python installed correctly?
    pause
    exit /b 1
)

echo(
echo ==^> Installing dependencies ^(a few minutes the first time^)
echo     a full log is written to setup_log.txt
".venv\Scripts\python.exe" -m pip install --upgrade pip  > setup_log.txt 2>&1
".venv\Scripts\python.exe" -m pip install -r requirements.txt >> setup_log.txt 2>&1
if errorlevel 1 (
    echo(
    echo     Installation FAILED. The last lines of setup_log.txt were:
    echo(
    powershell -NoProfile -Command "Get-Content setup_log.txt -Tail 15" 2>nul
    echo(
    echo     Common causes on Windows:
    echo       * no internet, or a university/corporate proxy blocking pypi.org
    echo       * the project folder is inside OneDrive - move it to C:\Users\%USERNAME%\confocal
    echo       * antivirus locking files while pip unpacks them
    echo(
    echo     The full log is in setup_log.txt next to this script.
    pause
    exit /b 1
)

echo(
echo ==^> Checking the install
".venv\Scripts\python.exe" tools\check_install.py
if errorlevel 1 (
    echo(
    echo ==^> Some packages did not install correctly. Repairing them...
    echo(
    set "BROKEN="
    for /f "usebackq delims=" %%P in (`".venv\Scripts\python.exe" tools\check_install.py --names`) do (
        echo     reinstalling %%P
        ".venv\Scripts\python.exe" -m pip install --force-reinstall --no-cache-dir %%P >> setup_log.txt 2>&1
    )
    echo(
    echo ==^> Checking again
    ".venv\Scripts\python.exe" tools\check_install.py
    if errorlevel 1 (
        echo(
        echo     Still not healthy. Please send setup_log.txt and the message above.
        echo(
        echo     Two things fix this most of the time:
        echo       1^) move the project out of OneDrive, e.g. to C:\Users\%USERNAME%\confocal
        echo       2^) delete the .venv folder and run setup.bat again
        pause
        exit /b 1
    )
)

echo(
echo ===========================================================
echo   Done. Start the program by double-clicking  run_gui.bat
echo ===========================================================
echo(
pause
