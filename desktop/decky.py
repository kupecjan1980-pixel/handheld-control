"""Stand-in for Decky's `decky` module, so main.py runs on a desktop.

server.py fills in the paths and the event sink before importing main.
"""

import json
import logging
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DECKY_PLUGIN_DIR = ROOT
DECKY_USER_HOME = os.environ.get("HHC_USER_HOME") or os.path.expanduser("~")
DECKY_PLUGIN_SETTINGS_DIR = os.environ.get("HHC_SETTINGS_DIR") or os.path.join(
    DECKY_USER_HOME, ".config", "handheld-control-desktop"
)
with open(os.path.join(ROOT, "package.json")) as f:
    DECKY_PLUGIN_VERSION = json.load(f).get("version")

logger = logging.getLogger("hhctl")

# server.py replaces this with a function that pushes to the open windows.
sink = lambda event, args: None


async def emit(event: str, *args) -> None:
    sink(event, list(args))
