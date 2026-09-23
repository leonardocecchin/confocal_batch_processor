# Installing on Windows 11

*Confocal batch processor — by Klaudia Saladauskas and Leonardo Cecchin*

Two one-time installs (Python and Git), then a script does the rest. Budget
fifteen minutes, most of it downloads.

Everything lands in one folder. Nothing is installed system-wide except Python
and Git themselves, and nothing touches your other Python projects.

---

## Step 1 — Install Python

1. Go to <https://www.python.org/downloads/windows/>.
2. Download **Python 3.12** (the "Windows installer (64-bit)").

   > Take 3.12 rather than the newest release. Every package this tool needs
   > ships a ready-built Windows package for 3.11 and 3.12, so nothing has to
   > be compiled. The very newest Python is often ahead of those packages.

3. Run the installer and, **on the first screen**, tick:
   - ☑ **Add python.exe to PATH** (bottom of the window — easy to miss)
   - ☑ **Install launcher for all users** if offered
4. Click *Install Now*.
5. If you get a *Customize installation* screen, leave **tcl/tk and IDLE**
   ticked — that is the graphical toolkit the program's windows are built on.

**Check it worked.** Press `Win`, type `cmd`, press Enter, then type:

```bat
py --version
```

You should see `Python 3.12.x`. If instead you get "not recognized", Python was
installed without *Add python.exe to PATH* — re-run the installer, choose
*Modify*, and tick it.

## Step 2 — Install Git

1. Download from <https://git-scm.com/download/win> and install it. All the
   default options are fine; just keep clicking *Next*.

*(Prefer not to install Git? Instead, open the repository page on GitHub, click
the green **Code** button, choose **Download ZIP**, and right-click the
downloaded file → **Extract All**. Then skip to step 4.)*

## Step 3 — Get the code

> **Put it outside OneDrive.** A OneDrive-synced folder is the single most
> common cause of a broken install here: OneDrive syncs and locks files while
> pip is still unpacking them, and packages end up half-written. Deep OneDrive
> paths can also exceed Windows' path length limit. Use something short and
> local, such as `C:\Users\<you>\confocal`.

Open a terminal in the folder where you keep your projects — open the folder in
File Explorer, then type `cmd` in the address bar and press Enter — and run:

```bat
git clone <the repository URL> confocal-processor
cd confocal-processor
```

## Step 4 — Run the setup script

In that folder, **double-click `setup.bat`** (or type `setup.bat` in the
terminal).

It creates an isolated environment in a `.venv` folder, installs everything into
it, and checks the result. A few minutes the first time, and it needs internet
access.

When it finishes you should see:

```
===========================================================
  Done. Start the program by double-clicking  run_gui.bat
===========================================================
```

## Step 5 — Start it

**Double-click `run_gui.bat`.**

That is the only step you repeat. Make a shortcut to it on the Desktop if you
like: right-click `run_gui.bat` → *Show more options* → *Send to* → *Desktop
(create shortcut)*.

---

## If something goes wrong

| What you see | What to do |
|---|---|
| `Missing packages: pandas` (or any other) | The install did not complete. `setup.bat` now retries the failing packages by itself; if it still fails, the reason is in `setup_log.txt` next to the script. Usually OneDrive, a proxy, or antivirus — see below. |
| `These are installed but will not import` | The package is there but damaged, typically a half-written file. The script prints the exact error and the command to repair just that package. |
| `Python 3.10 or newer was not found` | Python is not on PATH. Re-run the Python installer, choose *Modify*, tick **Add python.exe to PATH**, and restart the terminal. |
| `tkinter is missing` | Re-run the Python installer → *Modify* → tick **tcl/tk and IDLE**. |
| Setup fails while downloading | Usually no internet or a workplace proxy. On a university network, try again off the VPN. |
| `.venv already exists` | Delete the `.venv` folder and run `setup.bat` again. |
| Windows SmartScreen warns about the `.bat` | Click *More info* → *Run anyway*. The files are plain text — open them in Notepad to see exactly what they do. |
| It runs but is very slow | Check RAM. The tool sizes its parallelism to fit memory and says so in the log: `processing N image(s) with M worker(s) (limited by memory, not cores)`. |

To check the environment at any time:

```bat
.venv\Scripts\python.exe tools\check_install.py
```

Add `--report` to list every installed version — that plus `setup_log.txt` is
what to send if you need help:

```bat
.venv\Scripts\python.exe tools\check_install.py --report
```

### If a package will not install

1. **Move the project out of OneDrive** and run `setup.bat` again. This fixes
   it most of the time.
2. Delete the `.venv` folder and re-run `setup.bat` — a partial environment
   never repairs itself fully.
3. On a university network, try off the VPN: some proxies block `pypi.org`.
4. If antivirus is the problem, the log shows a permission or "file in use"
   error. Add the project folder to its exclusions, or install from a local
   account folder.

To update after new code is pulled:

```bat
git pull
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

---

## What about a single .exe, with no Python at all?

It is possible, with real trade-offs. Three options, most to least practical:

### 1. Keep the script install (what this guide does) — recommended

Python plus one script. Easy to update (`git pull`), easy to debug, and it is
the same code path that has been tested. The only cost is the one-time Python
install.

### 2. Build a standalone `.exe` with PyInstaller

Produces a folder (or a single big file) that runs on a Windows machine with no
Python installed. Useful if you need to hand the tool to someone who cannot
install software, or for a shared lab PC.

**This must be built on Windows** — PyInstaller does not cross-compile, so it
cannot be produced from Linux or macOS. On a Windows machine that already has
the tool working:

```bat
.venv\Scripts\python.exe -m pip install pyinstaller
build_windows_exe.bat
```

The result appears in `dist\ConfocalProcessor\`. Zip that folder and it runs
anywhere; the launcher is `ConfocalProcessor.exe` inside it.

Honest trade-offs:

- **Large.** SciPy, scikit-image and matplotlib together make a 400 MB–1 GB
  folder. One-file mode is smaller to hand over but unpacks to a temp folder on
  every launch, so it starts slowly.
- **Fragile to build.** Scientific packages hide imports from PyInstaller's
  scanner. `build_windows_exe.bat` lists the ones known to need help, but a
  dependency update can break the build and the error only shows at run time.
- **Antivirus.** Corporate antivirus regularly quarantines freshly built
  PyInstaller executables. Expect to whitelist it.
- **Updates mean rebuilding** and redistributing the whole folder.

I would build this only once the settings have stopped changing.

### 3. A conda / mamba environment

If your lab already standardises on Anaconda, `conda create -n confocal python=3.12`
then `pip install -r requirements.txt` works and some people find the Anaconda
Navigator GUI easier than a terminal. It is a heavier install than plain Python
and brings its own channel/licensing questions, so it is only worth it if you
are on conda already.

**Not worth considering here:** a Microsoft Store install (sandboxing breaks
file access to network drives), or a web/server version (these are ~800 MB
files per acquisition — uploading them would dominate the runtime).
