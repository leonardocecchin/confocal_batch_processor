#!/usr/bin/env bash
# Start the confocal batch processor. Double-click, or run ./run_gui.sh
cd "$(dirname "$(readlink -f "$0")")" || exit 1

if [ ! -x .venv/bin/python ]; then
    msg="The environment is not set up yet. Run ./setup.sh first."
    echo "$msg"
    command -v zenity >/dev/null && zenity --error --text="$msg"
    exit 1
fi

exec .venv/bin/python run_gui.py "$@"
