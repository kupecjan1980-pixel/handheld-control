"""The app launcher entry and icon of HHC Desktop, in the user's home.

Run as the desktop user (install.sh with sudo -u, server.py drops to the
user's uid before calling it), never as root: these paths are the user's, so
a symlink there must not make root write somewhere else.

Chromium ignores --class on Wayland: an --app window's app id is
"<browser>-<host>__<path>-<profile>", e.g. "chrome-127.0.0.1__-Default" for
Chromium and Chrome, "brave-127.0.0.1__-Default" for Brave (DeskMini, Bazzite).
KDE Plasma ties a Wayland window to the launcher whose file is named after its
app id (StartupWMClass alone isn't enough: the window showed up as a separate
"W" item), so the entry's file is named after the app id of the browser the
launcher (hhc-desktop) will pick, and StartupWMClass stays for other bars.
"""

import os
import shutil
import subprocess
import sys

APP_ID = "hhc-desktop"
OLD_ENTRY = "handheld-control.desktop"  # before 0.10.47

# The launcher's browser order (desktop/hhc-desktop) and each one's app id prefix.
BROWSERS = [("chromium", "chrome"), ("chromium-browser", "chrome"), ("google-chrome-stable", "chrome"),
            ("google-chrome", "chrome"), ("brave-browser", "brave"), ("brave", "brave")]
FLATPAKS = [("org.chromium.Chromium", "chrome"), ("com.google.Chrome", "chrome"), ("com.brave.Browser", "brave"),
            ("io.github.ungoogled_software.ungoogled_chromium", "chrome")]
PREFIXES = sorted({p for _, p in BROWSERS + FLATPAKS})


def browser_prefix() -> str:
    for cmd, prefix in BROWSERS:
        if shutil.which(cmd):
            return prefix
    if shutil.which("flatpak"):
        for app, prefix in FLATPAKS:
            if subprocess.run(["flatpak", "info", app], capture_output=True).returncode == 0:
                return prefix
    return "chrome"


def wm_class(prefix: str) -> str:
    return f"{prefix}-127.0.0.1__-Default"


def entry(launcher: str, wm: str) -> str:
    return (
        "[Desktop Entry]\nType=Application\nName=HHC Desktop\n"
        "Comment=Handheld Control on this PC: TDP, CPU and GPU clocks, fan, RGB, themes\n"
        f"Exec={launcher}\nTerminal=false\nIcon={APP_ID}\nStartupWMClass={wm}\n"
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
    wm = wm_class(browser_prefix())
    changed |= _write(os.path.join(apps, wm + ".desktop"), entry(launcher, wm).encode())
    # Ours under another name: older versions, or another browser picked before.
    for name in [OLD_ENTRY] + [wm_class(p) + ".desktop" for p in PREFIXES if wm_class(p) != wm]:
        old = os.path.join(apps, name)
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
