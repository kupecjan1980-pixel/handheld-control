#!/usr/bin/env bash
# Adds "Handheld Control" to the app launcher (runs desktop/hhc-desktop in a terminal).
set -euo pipefail
DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APPS"
cat > "$APPS/handheld-control.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Handheld Control
Comment=The Handheld Control plugin on this PC: TDP, CPU and GPU clocks, fan, RGB, battery
Exec=$DIR/hhc-desktop
Terminal=true
Icon=utilities-system-monitor
Categories=System;Settings;HardwareSettings;
DESKTOP
echo "Added $APPS/handheld-control.desktop"
