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
