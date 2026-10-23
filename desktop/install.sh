#!/usr/bin/env bash
# One-time setup of the desktop app (run with sudo; again to update it):
# - copies the plugin and the desktop server to /opt/handheld-control-desktop,
#   owned by root, so nothing running as you can change what runs as root;
# - installs hhc-desktop.socket / .service: systemd starts the backend as root
#   when the app opens (no password, no terminal) and it exits when it closes;
# - adds "Handheld Control" to your app launcher.
# Remove everything with: sudo desktop/install.sh --remove
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run with sudo: sudo $0"; exit 1; }
USER_NAME=${SUDO_USER:?run with sudo from your own account}
USER_HOME=$(getent passwd "$USER_NAME" | cut -d: -f6)
USER_UID=$(id -u "$USER_NAME")
DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
REPO=$(dirname "$DIR")
DEST=/opt/handheld-control-desktop
UNITS=/etc/systemd/system
APPS="$USER_HOME/.local/share/applications"

if [ "${1:-}" = "--remove" ]; then
    systemctl disable --now hhc-desktop.socket hhc-desktop.service 2>/dev/null || true
    rm -f "$UNITS/hhc-desktop.socket" "$UNITS/hhc-desktop.service" "$APPS/handheld-control.desktop"
    rm -rf "$DEST"
    systemctl daemon-reload
    echo "Removed."
    exit 0
fi

echo "Building the panel (as $USER_NAME)…"
sudo -u "$USER_NAME" bash -c "cd '$REPO' && npm install --no-audit --no-fund >/dev/null && npx rollup -c desktop/rollup.config.mjs" >/dev/null

echo "Copying to $DEST…"
systemctl stop hhc-desktop.service 2>/dev/null || true
rm -rf "$DEST.new"
mkdir -p "$DEST.new/desktop"
cp -r "$REPO/main.py" "$REPO/package.json" "$REPO/py_modules" "$REPO/fonts" "$DEST.new/"
cp -r "$DIR/server.py" "$DIR/decky.py" "$DIR/dist" "$DEST.new/desktop/"
find "$DEST.new" -name __pycache__ -prune -exec rm -rf {} +
chown -R root:root "$DEST.new"
chmod -R go-w "$DEST.new"
rm -rf "$DEST" && mv "$DEST.new" "$DEST"

cat > "$UNITS/hhc-desktop.socket" <<UNIT
[Unit]
Description=Handheld Control desktop app (starts the backend on demand)

[Socket]
ListenStream=127.0.0.1:8765

[Install]
WantedBy=sockets.target
UNIT
cat > "$UNITS/hhc-desktop.service" <<UNIT
[Unit]
Description=Handheld Control desktop backend
Requires=hhc-desktop.socket

[Service]
ExecStart=/usr/bin/python3 $DEST/desktop/server.py --port 8765 --home $USER_HOME --uid $USER_UID --token-file /run/hhc-desktop/token
RuntimeDirectory=hhc-desktop
Restart=no
UNIT
systemctl daemon-reload
systemctl enable --now hhc-desktop.socket

mkdir -p "$APPS"
cat > "$APPS/handheld-control.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Handheld Control
Comment=The Handheld Control plugin on this PC: TDP, CPU and GPU clocks, fan, RGB, battery
Exec=$DIR/hhc-desktop
Terminal=false
Icon=utilities-system-monitor
Categories=System;Settings;HardwareSettings;
DESKTOP
chown "$USER_NAME:" "$APPS/handheld-control.desktop"
echo "Done. Open Handheld Control from the app launcher."
