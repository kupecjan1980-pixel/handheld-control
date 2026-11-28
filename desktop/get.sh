#!/usr/bin/env bash
# Install (or update) HHC Desktop from the newest GitHub release, without a
# checkout of this repository or a build:
#   curl -fsSL https://raw.githubusercontent.com/kupecjan1980-pixel/handheld-control/master/desktop/get.sh | sudo bash
# Remove it with:
#   curl -fsSL https://raw.githubusercontent.com/kupecjan1980-pixel/handheld-control/master/desktop/get.sh | sudo bash -s -- --remove
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run with sudo (see the top of this script)."; exit 1; }
[ -n "${SUDO_USER:-}" ] || { echo "Run with sudo from your own account, not as root."; exit 1; }
REPO=kupecjan1980-pixel/handheld-control
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# Newest release's desktop zip: the tag from github.com/…/releases/latest (it redirects to it). Not the API:
# that allows 60 requests an hour for the whole home network, shared by every computer and app.
TAG=$(curl -fsSI "https://github.com/$REPO/releases/latest" | tr -d '\r' | sed -n 's|^[Ll]ocation: .*/releases/tag/\(v[0-9.]*\)$|\1|p')
[ -n "$TAG" ] || { echo "Could not find the newest release on GitHub."; exit 1; }
URL="https://github.com/$REPO/releases/download/$TAG/handheld-control-desktop-${TAG#v}.zip"
case "$URL" in
    "https://github.com/$REPO/releases/download/"*) ;;
    *) echo "Unexpected download URL: $URL"; exit 1 ;;
esac
echo "Downloading ${URL##*/}…"
curl -fsSL -o "$TMP/app.zip" "$URL"
python3 -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$TMP/app.zip" "$TMP"
[ -f "$TMP/Handheld Control/desktop/install.sh" ] || { echo "This release's zip has no installer (needs 0.10.36 or newer)."; exit 1; }
bash "$TMP/Handheld Control/desktop/install.sh" "$@"
