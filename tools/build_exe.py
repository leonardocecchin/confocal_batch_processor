#!/usr/bin/env python3
"""Build the standalone ConfocalProcessor folder with PyInstaller.

Shared by build_windows_exe.bat and CI. Does not cross-compile. Modules loaded
at run time are named explicitly; add any that raise ModuleNotFoundError.
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
