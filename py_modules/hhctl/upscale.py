"""Newer upscalers in Proton games: FSR 4 in place of FSR 3.1, and the newest
XeSS. Proton-CachyOS, GE-Proton and Proton-EM download the DLLs themselves
when these variables are set (Valve's Proton ignores them); they go in
~/.config/environment.d, which gamescope-session and desktop sessions load,
so games started after the next login get them.

  FSR 4    PROTON_FSR4_UPGRADE=1 (Proton-CachyOS/EM), FSR4_UPGRADE=1 (GE-Proton).
           AMD RDNA 3 and newer only (GC 11+); on RDNA 3/3.5 it runs the INT8
           model, which costs some fps compared with FSR 3.1 (sharper picture).
  XeSS     PROTON_XESS_UPGRADE=1: the newest libxess.dll in games with XeSS.
           Best on Intel Arc; also runs on AMD.

OptiScaler (Proton-CachyOS: PROTON_USE_OPTISCALER) swaps the upscaler a game
asks for (DLSS, FSR, XeSS) for another one. It loads a DLL into the game,
which anti-cheat can take for a cheat, so it's set per game in the launch
options (the panel does that), never for every game here.
"""

import glob
import os
import re

from . import sysfs

FILE = ".config/environment.d/90-handheld-control-upscaling.conf"
VARS = {
    "fsr4": {"PROTON_FSR4_UPGRADE": "1", "FSR4_UPGRADE": "1"},
    "xess": {"PROTON_XESS_UPGRADE": "1"},
}
TOOLS = re.compile(r"cachyos|ge-proton|proton-ge|proton-em|^em-", re.I)


def gc_major(dev: str | None) -> int | None:
    v = sysfs.read(f"{dev}/ip_discovery/die/0/GC/0/major") if dev else None
    return int(v) if v and v.isdigit() else None


def proton_tools(home: str) -> list[str]:
    """Installed Proton builds that know these variables."""
    dirs = [f"{home}/.local/share/Steam/compatibilitytools.d", f"{home}/.steam/root/compatibilitytools.d",
            f"{home}/.var/app/com.valvesoftware.Steam/data/Steam/compatibilitytools.d",
            "/usr/share/steam/compatibilitytools.d"]
    found = set()
    for d in dirs:
        for p in glob.glob(f"{d}/*"):
            name = os.path.basename(p)
            if TOOLS.search(name):
                found.add(name)
    return sorted(found)


def optiscaler_vars(gpu_driver: str | None, gc: int | None) -> str:
    """Launch-option prefix: XeSS on Intel, else FSR 3.1 (FSR 4 on RDNA 3+)."""
    up = "xess" if gpu_driver in ("xe", "i915") else "fsr31"
    out = f'PROTON_USE_OPTISCALER=1 PROTON_OPTISCALER_CONFIG="Upscalers.Dx11Upscaler={up};Upscalers.Dx12Upscaler={up}"'
    if up == "fsr31" and gc is not None and gc >= 11:
        out += " PROTON_FSR4_UPGRADE=1"
    return out


class Upscale:
    def __init__(self, home: str, gpu_driver: str | None, gpu_dev: str | None):
        self.home = home
        self.path = os.path.join(home, FILE)
        gc = gc_major(gpu_dev) if gpu_driver == "amdgpu" else None
        self.options = (["fsr4"] if gc is not None and gc >= 11 else []) + ["xess"]
        self.rdna3 = gc == 11
        self.optiscaler = optiscaler_vars(gpu_driver, gc)
        self.opti_upscaler = "XeSS" if gpu_driver in ("xe", "i915") else ("FSR 4" if gc is not None and gc >= 11 else "FSR 3.1")

    def current(self) -> dict[str, bool]:
        try:
            with open(self.path) as f:
                text = f.read()
        except OSError:
            text = ""
        keys = {line.split("=", 1)[0] for line in text.splitlines() if "=" in line and not line.startswith("#")}
        return {o: all(k in keys for k in VARS[o]) for o in self.options}

    def set(self, option: str, on: bool) -> None:
        if option not in self.options:
            raise ValueError(f"{option} isn't available on this GPU")
        want = {**self.current(), option: bool(on)}
        lines = [f"{k}={v}" for o in self.options if want[o] for k, v in VARS[o].items()]
        if not lines:
            if os.path.exists(self.path):
                os.remove(self.path)
            return
        st = os.stat(self.home)
        d = os.path.dirname(self.path)
        for p in (os.path.dirname(d), d):
            if not os.path.isdir(p):
                os.mkdir(p)
                os.chown(p, st.st_uid, st.st_gid)
        with open(self.path, "w") as f:
            f.write("# Written by Handheld Control (Upscaling). Turn it off there to remove.\n")
            f.write("\n".join(lines) + "\n")
        os.chown(self.path, st.st_uid, st.st_gid)

    def state(self) -> dict:
        return {"options": self.options, "on": self.current(), "rdna3": self.rdna3,
                "tools": proton_tools(self.home), "optiscaler": self.optiscaler, "opti_upscaler": self.opti_upscaler}
