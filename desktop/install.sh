#!/usr/bin/env bash
# One-time setup of the desktop app (run with sudo; again to update it):
# - copies the plugin and the desktop server to /opt/handheld-control-desktop,
#   owned by root, so nothing running as you can change what runs as root;
# - installs hhc-desktop.socket / .service: systemd starts the backend as root
#   when the app opens (no password, no terminal) and it exits when it closes;
# - adds "HHC Desktop" to your app launcher (/usr/local/bin/hhc-desktop).
# Runs from a checkout of this repository (builds the panel first) or from an
# unpacked desktop release zip (already built; see desktop/get.sh).
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
LAUNCHER=/usr/local/bin/hhc-desktop
APPS="$USER_HOME/.local/share/applications"

if [ "${1:-}" = "--remove" ]; then
    systemctl disable --now hhc-desktop.socket hhc-desktop.service 2>/dev/null || true
    rm -f "$UNITS/hhc-desktop.socket" "$UNITS/hhc-desktop.service" "$APPS/handheld-control.desktop" "$LAUNCHER"
    rm -f "$USER_HOME/.local/share/icons/hicolor/scalable/apps/hhc-desktop.svg" "$USER_HOME/.local/share/icons/hicolor/256x256/apps/hhc-desktop.png"
    rm -rf "$DEST"
    systemctl daemon-reload
    echo "Removed."
    exit 0
fi

# Build the panel as the user, unless it's already up to date (a release zip
# has it built and no sources). sudo's PATH lacks the user's node (e.g. mise
# shims), so add the usual places.
if [ ! -d "$REPO/src" ]; then
    [ -f "$DIR/dist/bundle.js" ] || { echo "No built panel and no sources to build it from."; exit 1; }
elif [ ! -f "$DIR/dist/bundle.js" ] || [ ! -f "$DIR/dist/hhc-desktop.svg" ] || [ -n "$(find "$REPO/src" "$DIR/shims" "$DIR/entry.tsx" "$DIR/index.html" "$DIR/hhc-desktop.svg" -newer "$DIR/dist/bundle.js" 2>/dev/null)" ]; then
    echo "Building the panel (as $USER_NAME)…"
    USER_PATH="$USER_HOME/.local/share/mise/shims:$USER_HOME/.local/bin:$USER_HOME/.volta/bin:/usr/local/bin:/usr/bin:/bin"
    sudo -u "$USER_NAME" env PATH="$USER_PATH" HOME="$USER_HOME" bash -c \
        "cd '$REPO' && npm install --no-audit --no-fund >/dev/null && npx rollup -c desktop/rollup.config.mjs" >/dev/null \
        || { echo "Couldn't build the panel. Run this as yourself, then this script again:"
             echo "  cd $REPO && npm install && npx rollup -c desktop/rollup.config.mjs"; exit 1; }
fi

echo "Copying to $DEST…"
systemctl stop hhc-desktop.service 2>/dev/null || true
rm -rf "$DEST.new"
mkdir -p "$DEST.new/desktop"
cp -r "$REPO/plugin.json" "$REPO/main.py" "$REPO/package.json" "$REPO/py_modules" "$REPO/fonts" "$DEST.new/"
cp -r "$DIR/server.py" "$DIR/decky.py" "$DIR/menu_entry.py" "$DIR/dist" "$DEST.new/desktop/"
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
# Keep the token across restarts (after an update the window reloads with it).
RuntimeDirectoryPreserve=yes
Restart=no
UNIT
systemctl daemon-reload
systemctl enable --now hhc-desktop.socket

# The launcher lives outside $DEST so updating (or going back) can't remove it.
install -m 755 "$DIR/hhc-desktop" "$LAUNCHER"

# Launcher entry + icon, written as the user (their home, their files).
sudo -u "$USER_NAME" python3 -I "$DEST/desktop/menu_entry.py" "$USER_HOME" \
    "$DEST/desktop/dist/hhc-desktop.svg" "$DEST/desktop/dist/hhc-desktop.png" "$LAUNCHER"
echo "Done. Open HHC Desktop from the app launcher."
