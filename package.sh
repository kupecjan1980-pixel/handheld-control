#!/usr/bin/env bash
# Build the frontend and produce a Decky-installable zip in out/.
set -euo pipefail
cd "$(dirname "$0")"
NAME="Handheld Control"
VERSION=$(python3 -c 'import json;print(json.load(open("package.json"))["version"])')

npm install --no-audit --no-fund
npm run build

rm -rf out && mkdir -p "out/$NAME/dist"
cp plugin.json package.json main.py README.md LICENSE "out/$NAME/"
cp dist/index.js "out/$NAME/dist/"
cp -r py_modules fonts "out/$NAME/"
find "out/$NAME" -name __pycache__ -prune -exec rm -rf {} +
(cd out && zip -qr "handheld-control-$VERSION.zip" "$NAME")
echo "out/handheld-control-$VERSION.zip"

# Desktop app zip (installed in /opt by desktop/install.sh, which updates from it):
# the plugin's backend plus the desktop server and its browser panel.
npx rollup -c desktop/rollup.config.mjs
D="out/desktop/$NAME"
mkdir -p "$D/desktop"
cp plugin.json package.json main.py LICENSE "$D/"
cp -r py_modules fonts "$D/"
cp -r desktop/server.py desktop/decky.py desktop/menu_entry.py desktop/dist desktop/install.sh desktop/hhc-desktop "$D/desktop/"
find "$D" -name __pycache__ -prune -exec rm -rf {} +
(cd out/desktop && zip -qr "../handheld-control-desktop-$VERSION.zip" "$NAME")
rm -rf out/desktop
echo "out/handheld-control-desktop-$VERSION.zip"
