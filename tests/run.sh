#!/usr/bin/env bash
# Run the backend test suites. test_hhctl.py compares packets with Handheld
# Daemon's source; point HHD_SRC at a folder of its device files to include it.
set -euo pipefail
cd "$(dirname "$0")/.."
for t in tests/test_*.py; do
    name=$(basename "$t" .py)
    if [ "$name" = test_hhctl ]; then
        [ -n "${HHD_SRC:-}" ] || { echo "$name: skipped (set HHD_SRC)"; continue; }
        python3 -I "$t" "$PWD" "$HHD_SRC" 2>/dev/null | tail -1
    else
        python3 -I "$t" "$PWD" 2>/dev/null | tail -1
    fi
done
