#!/usr/bin/env bash
# Create a double-clickable "Confocal Processor" launcher (Ubuntu/GNOME).
# Adds it to the application menu and to the project folder.
dir="$(dirname "$(readlink -f "$0")")"
chmod +x "$dir/run_gui.sh"

entry="[Desktop Entry]
Type=Application
Name=Confocal Processor
Comment=Confocal batch processor
Exec=\"$dir/run_gui.sh\"
Path=$dir
Terminal=false
Categories=Graphics;"

mkdir -p "$HOME/.local/share/applications"
printf '%s\n' "$entry" > "$HOME/.local/share/applications/confocal-processor.desktop"
printf '%s\n' "$entry" > "$dir/Confocal Processor.desktop"
chmod +x "$dir/Confocal Processor.desktop"
gio set "$dir/Confocal Processor.desktop" metadata::trusted true 2>/dev/null

echo "Done. Search 'Confocal Processor' in the app menu, or double-click"
echo "'Confocal Processor.desktop' in this folder (right-click -> Allow Launching if asked)."
