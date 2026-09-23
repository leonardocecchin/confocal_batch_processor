#!/usr/bin/env python3
"""Verify the environment is complete, isolated and importable.

Run it on its own whenever something stops working:

    .venv/bin/python tools/check_install.py            (Linux / macOS)
    .venv\\Scripts\\python.exe tools\\check_install.py     (Windows)

Add --report to print everything that is installed, which is the useful thing
to paste into a bug report.
"""

from __future__ import annotations

import importlib
import importlib.util
import pathlib
import platform
import sys
import traceback

# import name -> pip name, where they differ
REQUIRED = {
    "numpy": "numpy",
    "scipy": "scipy",
    "skimage": "scikit-image",
    "nd2": "nd2",
    "tifffile": "tifffile",
    "pandas": "pandas",
    "matplotlib": "matplotlib",
    "PIL": "pillow",
}
OPTIONAL = {
    "edt": "fast, low-memory distance transform (falls back to SciPy, ~5x more memory)",
    "imagecodecs": "compressed TIFF support",
}


def _describe(name: str):
    """Return (status, detail) for one import.

    Distinguishes 'not installed at all' from 'installed but will not import',
    because the fix is completely different and the two are easy to confuse.
    """
    try:
        spec = importlib.util.find_spec(name)
    except Exception as exc:  # a broken package can even break the lookup
        return "broken", f"{type(exc).__name__}: {exc}"
    if spec is None:
        return "absent", ""
    try:
        module = importlib.import_module(name)
    except BaseException as exc:  # noqa: BLE001 - report anything, never crash
        return "broken", f"{type(exc).__name__}: {exc}"
    return "ok", module


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    prefix = pathlib.Path(sys.prefix).resolve()

    # --names prints only the pip names that need reinstalling, one per line,
    # so the setup script can repair exactly those and nothing else.
    if "--names" in argv:
        problems = [
            pip_name
            for name, pip_name in REQUIRED.items()
            if _describe(name)[0] != "ok"
        ]
        for pip_name in problems:
            print(pip_name)
        return 1 if problems else 0

    print(f"    python  {sys.version.split()[0]}  ({platform.platform()})")
    print(f"    venv    {prefix}")

    if sys.prefix == sys.base_prefix:
        print("\n    NOT running inside a virtual environment.")
        print("    Use the interpreter inside .venv, not the system one.")
        return 1

    absent, broken, stray = [], [], []
    for name, pip_name in REQUIRED.items():
        status, detail = _describe(name)
        if status == "absent":
            absent.append(pip_name)
        elif status == "broken":
            broken.append((name, pip_name, detail))
        else:
            location = pathlib.Path(getattr(detail, "__file__", "") or "").resolve()
            if prefix not in location.parents:
                stray.append(f"{name} -> {location}")

    if broken:
        print("\n    These are installed but will not import:\n")
        for name, pip_name, detail in broken:
            print(f"      {name}: {detail}")
        print("\n    This is an installation problem, not a missing package.")
        print("    Most often on Windows it means the download was corrupted or")
        print("    a file was locked during install (antivirus, or a OneDrive-")
        print("    synced folder). Reinstall just that package:\n")
        for _name, pip_name, _detail in broken:
            print(f"      {_pip_cmd()} install --force-reinstall --no-cache-dir {pip_name}")
        print("\n    Full traceback for the first one:\n")
        sys.stdout.flush()
        try:
            importlib.import_module(broken[0][0])
        except BaseException:  # noqa: BLE001
            traceback.print_exc(file=sys.stdout)
        sys.stdout.flush()
        return 1

    if absent:
        print("\n    Missing packages: " + ", ".join(absent))
        print(f"\n    Install them with:\n      {_pip_cmd()} install -r requirements.txt")
        return 1

    if stray:
        print("\n    These came from outside the environment:")
        for item in stray:
            print(f"      {item}")
        print("\n    The environment is not isolated; rebuild it with the setup script.")
        return 1

    try:
        import tkinter  # noqa: F401

        print("    tkinter present (the GUI will start)")
    except ImportError:
        print("\n    tkinter is missing, so the GUI cannot start.")
        print("    Windows: re-run the Python installer, choose Modify, and tick")
        print("             'tcl/tk and IDLE'.")
        print("    Debian/Ubuntu: sudo apt install python3-tk")
        return 1

    for name, why in OPTIONAL.items():
        status, _detail = _describe(name)
        print(f"    {name} {'present' if status == 'ok' else 'not installed'} - {why}")

    if "--report" in argv:
        print("\n    Installed versions:")
        for name in list(REQUIRED) + list(OPTIONAL):
            status, detail = _describe(name)
            version = getattr(detail, "__version__", "?") if status == "ok" else status
            print(f"      {name:14s} {version}")

    print("\n    environment OK")
    return 0


def _pip_cmd() -> str:
    """The pip command for this environment, spelled for this platform."""
    if sys.platform == "win32":
        return r".venv\Scripts\python.exe -m pip"
    return ".venv/bin/python -m pip"


if __name__ == "__main__":
    sys.exit(main())
