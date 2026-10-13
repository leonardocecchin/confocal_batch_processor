#!/usr/bin/env python3
"""Launch the confocal batch processing GUI."""

import faulthandler
import multiprocessing

from confocal.gui import main

if __name__ == "__main__":
    # Windows (and a frozen build on any OS) starts worker processes by
    # re-running this file; without this the pool would spawn GUIs instead.
    multiprocessing.freeze_support()

    # Import check for CI, no window.
    import sys

    if "--selftest" in sys.argv:
        import confocal.cli, confocal.pipeline  # noqa: F401
        import nd2, skimage.io, scipy.ndimage, tifffile, pandas, matplotlib  # noqa: F401
        sys.exit(0)

    # If the window ever does freeze, `kill -USR1 <pid>` prints the stack of
    # every thread to the terminal, which says where it is stuck.
    faulthandler.enable()
    try:
        import signal

        faulthandler.register(signal.SIGUSR1, all_threads=True)
    except (AttributeError, ValueError):
        pass  # SIGUSR1 does not exist on Windows

    main()
