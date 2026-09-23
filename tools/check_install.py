#!/usr/bin/env python3
"""Verify the environment is complete and genuinely isolated.

Used by setup.bat and setup.sh, and worth running on its own if something
stops working:

    .venv/bin/python tools/check_install.py        (Linux / macOS)
    .venv\\Scripts\\python.exe tools\\check_install.py  (Windows)
"""

from __future__ import annotations

import pathlib
import sys

REQUIRED = ["numpy", "scipy", "skimage", "nd2", "tifffile", "pandas", "matplotlib", "PIL"]
OPTIONAL = {
    "edt": "fast, low-memory distance transform (falls back to SciPy, ~5x more memory)",
    "imagecodecs": "compressed TIFF support",
}


def main() -> int:
    prefix = pathlib.Path(sys.prefix).resolve()
    print(f"    python  {sys.version.split()[0]}")
    print(f"    venv    {prefix}")

    if sys.prefix == sys.base_prefix:
        print("\n    NOT running inside a virtual environment.")
        print("    Use the interpreter inside .venv, not the system one.")
        return 1

    missing, stray = [], []
    for name in REQUIRED:
        try:
            module = __import__(name)
        except ImportError:
            missing.append(name)
            continue
        location = pathlib.Path(getattr(module, "__file__", "") or "").resolve()
        # A package resolving outside the venv means the environment is not
        # isolated, which silently mixes in whatever the machine already had.
        if prefix not in location.parents:
            stray.append(f"{name} -> {location}")

    if missing:
        print("\n    missing packages: " + ", ".join(missing))
        print("    re-run the setup script.")
        return 1
    if stray:
        print("\n    these came from outside the environment:")
        for item in stray:
            print(f"      {item}")
        return 1

    try:
        import tkinter  # noqa: F401

        print("    tkinter present (the GUI will start)")
    except ImportError:
        print("\n    tkinter is missing, so the GUI cannot start.")
        print("    Windows: re-run the Python installer and enable 'tcl/tk and IDLE'.")
        print("    Debian/Ubuntu: sudo apt install python3-tk")
        return 1

    for name, why in OPTIONAL.items():
        try:
            __import__(name)
            print(f"    {name} present ({why})")
        except ImportError:
            print(f"    {name} not installed - {why}")

    print("\n    environment OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
