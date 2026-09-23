#!/usr/bin/env bash
# Create an isolated virtual environment for the confocal batch processor.
#
# Deliberately avoids the system `virtualenv` on Debian/Ubuntu: it lays the
# interpreter out under .venv/local/bin, after which pip resolves against
# ~/.local and installs there instead of into the environment -- silently
# upgrading packages other projects depend on. See the README.
set -euo pipefail

cd "$(dirname "$0")"
VENV="${1:-.venv}"

if [ -d "$VENV" ]; then
    echo "'$VENV' already exists; remove it first to rebuild."
    exit 1
fi

echo "==> creating $VENV"
if python3 -m venv "$VENV" >/dev/null 2>&1; then
    :  # python3-venv is installed, nothing more to do
else
    # Debian without python3-venv: ensurepip is missing, so make the
    # environment without pip and bootstrap pip into it.
    echo "    python3-venv unavailable, bootstrapping pip by hand"
    rm -rf "$VENV"
    python3 -m venv --without-pip "$VENV"
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    curl -fsSL -o "$tmp/get-pip.py" https://bootstrap.pypa.io/get-pip.py
    "$VENV/bin/python" "$tmp/get-pip.py" --quiet 2>/dev/null
fi

echo "==> installing dependencies"
"$VENV/bin/pip" install --quiet --upgrade pip 2>/dev/null
"$VENV/bin/pip" install --quiet -r requirements.txt

echo "==> checking the install is isolated"
"$VENV/bin/python" tools/check_install.py || exit 1

echo
echo "Done. Run the GUI with:"
echo "    $VENV/bin/python run_gui.py"
