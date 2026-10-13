#!/usr/bin/env python3
"""Build the standalone ConfocalProcessor folder with PyInstaller.

Used by build_windows_exe.bat and by the GitHub Actions workflow, so the flags
live in one place. PyInstaller does not cross-compile: run it on the OS you
are targeting.

scikit-image, scipy and nd2 resolve plugins at run time, so PyInstaller's
import scanner cannot see them; they are named explicitly. If the exe starts
and then dies with ModuleNotFoundError, add the module the same way.
"""

import pathlib
import sys

import PyInstaller.__main__

ROOT = pathlib.Path(__file__).resolve().parent.parent

PyInstaller.__main__.run([
    "--noconfirm",
    "--windowed",
    "--name", "ConfocalProcessor",
    "--collect-submodules", "skimage",
    "--collect-submodules", "scipy",
    "--collect-submodules", "nd2",
    "--collect-data", "skimage",
    "--collect-data", "matplotlib",
    "--hidden-import", "edt",
    "--hidden-import", "tifffile",
    "--hidden-import", "imagecodecs",
    "--hidden-import", "PIL._tkinter_finder",
    "--hidden-import", "pandas",
    "--distpath", str(ROOT / "dist"),
    "--workpath", str(ROOT / "build"),
    "--specpath", str(ROOT / "build"),
    str(ROOT / "run_gui.py"),
])
sys.exit(0)
