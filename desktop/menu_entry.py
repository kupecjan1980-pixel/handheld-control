"""The app launcher entry and icon of HHC Desktop, in the user's home.

Run as the desktop user (install.sh with sudo -u, server.py drops to the
user's uid before calling it), never as root: these paths are the user's, so
a symlink there must not make root write somewhere else.

Chromium ignores --class on Wayland: an --app window's app id is
"chrome-<host>__<path>-<profile>". KDE Plasma ties a Wayland window to the
launcher whose file is named after its app id (StartupWMClass alone isn't
enough: the window showed up as a separate "W" item), so the entry's file is
named that way, and StartupWMClass stays for the bars that use it.
"""

import os
import shutil
import sys

APP_ID = "hhc-desktop"
WM_CLASS = "chrome-127.0.0.1__-Default"
ENTRY = WM_CLASS + ".desktop"
OLD_ENTRY = "handheld-control.desktop"  # before 0.10.47


def entry(launcher: str) -> str:
    return (
        "[Desktop Entry]\nType=Application\nName=HHC Desktop\n"
        "Comment=Handheld Control on this PC: TDP, CPU and GPU clocks, fan, RGB, themes\n"
        f"Exec={launcher}\nTerminal=false\nIcon={APP_ID}\nStartupWMClass={WM_CLASS}\n"
        "Categories=System;Settings;HardwareSettings;\n"
    )


def _write(path: str, data: bytes) -> bool:
    try:
        with open(path, "rb") as f:
            if f.read() == data:
                return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return True


def install(home: str, icon_svg: str, icon_png: str | None, launcher: str) -> bool:
    """Icon + launcher entry up to date; True if anything changed."""
    icons = os.path.join(home, ".local/share/icons/hicolor")
    changed = False
    with open(icon_svg, "rb") as f:
        changed |= _write(os.path.join(icons, "scalable/apps", APP_ID + ".svg"), f.read())
    if icon_png and os.path.exists(icon_png):
        with open(icon_png, "rb") as f:
            changed |= _write(os.path.join(icons, "256x256/apps", APP_ID + ".png"), f.read())
    apps = os.path.join(home, ".local/share/applications")
    changed |= _write(os.path.join(apps, ENTRY), entry(launcher).encode())
    old = os.path.join(apps, OLD_ENTRY)
    try:
        with open(old) as f:
            ours = "Name=HHC Desktop" in f.read()
        if ours:
            os.remove(old)
            changed = True
    except OSError:
        pass
    if changed and shutil.which("update-desktop-database"):
        os.system(f"update-desktop-database -q '{apps}' >/dev/null 2>&1")
    if changed and shutil.which("gtk-update-icon-cache"):
        os.system(f"gtk-update-icon-cache -q -t '{icons}' >/dev/null 2>&1")
    return changed


if __name__ == "__main__":
    # menu_entry.py <home> <icon.svg> <icon.png> <launcher>
    install(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
