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


# OptiScaler's own frame generation (OptiFG, DX12 games): the game's upscaler
# feeds FSR frame generation, which runs on AMD and Intel. The FidelityFX
# DLLs it needs come from PROTON_FFX4_UPGRADE.
FRAMEGEN = "FrameGen.Enabled=true;FrameGen.FGInput=upscaler;FrameGen.FGOutput=fsrfg"


def optiscaler_vars(gpu_driver: str | None, gc: int | None, framegen: bool = False) -> str:
    """Launch-option prefix: XeSS on Intel, else FSR 3.1 (FSR 4 on RDNA 3+)."""
    up = "xess" if gpu_driver in ("xe", "i915") else "fsr31"
    conf = f"Upscalers.Dx11Upscaler={up};Upscalers.Dx12Upscaler={up}" + (f";{FRAMEGEN}" if framegen else "")
    out = f'PROTON_USE_OPTISCALER=1 PROTON_OPTISCALER_CONFIG="{conf}"'
    if up == "fsr31" and gc is not None and gc >= 11:
        out += " PROTON_FSR4_UPGRADE=1"
    if framegen:
        out += " PROTON_FFX4_UPGRADE=1"
    return out


class Upscale:
    def __init__(self, home: str, gpu_driver: str | None, gpu_dev: str | None):
        self.home = home
        self.path = os.path.join(home, FILE)
        gc = gc_major(gpu_dev) if gpu_driver == "amdgpu" else None
        self.options = (["fsr4"] if gc is not None and gc >= 11 else []) + ["xess"]
        self.fsr4_why = (None if "fsr4" in self.options
                         else f"needs AMD RDNA 3 or newer (this GPU is GFX {gc})" if gc is not None
                         else "needs AMD RDNA 3 or newer graphics")
        self.rdna3 = gc == 11
        self.optiscaler = optiscaler_vars(gpu_driver, gc)
        self.optiscaler_fg = optiscaler_vars(gpu_driver, gc, framegen=True)
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
        return {"options": self.options, "on": self.current(), "rdna3": self.rdna3, "fsr4_why": self.fsr4_why,
                "tools": proton_tools(self.home), "optiscaler": self.optiscaler, "optiscaler_fg": self.optiscaler_fg,
                "opti_upscaler": self.opti_upscaler}


# What the running game actually got, from its processes: which Proton runs
# it, the variables it was started with, and the DLLs / layers loaded into
# it (Wine maps a game's DLLs from their files, so they show in
# /proc/<pid>/maps).
ENV_KEYS = ("PROTON_FSR4_UPGRADE", "FSR4_UPGRADE", "PROTON_XESS_UPGRADE", "PROTON_USE_OPTISCALER",
            "PROTON_FFX4_UPGRADE", "LSFGVK_MULTIPLIER")
DLLS = {
    "fsr4": re.compile(r"/amdxcffx64\.dll$", re.I),  # FSR 4's DLL, there only with the upgrade
    "fidelityfx": re.compile(r"/amd_fidelityfx_(dx12|vk)\.dll$", re.I),
    "xess": re.compile(r"/libxess(_dx11)?\.dll$", re.I),
    "optiscaler": re.compile(r"optiscaler", re.I),
    "lsfg": re.compile(r"lsfg", re.I),
}
PROTON = re.compile(r"/(?:compatibilitytools\.d|steamapps/common)/([^/]+)/(?:proton|files/|dist/)")


def _environ(pid: str) -> dict[str, str]:
    try:
        with open(f"/proc/{pid}/environ", "rb") as f:
            raw = f.read().split(b"\0")
    except OSError:
        return {}
    out = {}
    for item in raw:
        k, sep, v = item.partition(b"=")
        if sep:
            out[k.decode(errors="replace")] = v.decode(errors="replace")
    return out


def game_status(appid: int) -> dict | None:
    """None when no process of the game is running."""
    want = str(appid)
    pids, env, proton, loaded, optiscaler_dir = [], {}, None, {k: False for k in DLLS}, False
    for d in glob.glob("/proc/[0-9]*"):
        pid = d.rsplit("/", 1)[1]
        e = _environ(pid)
        if want not in (e.get("SteamAppId"), e.get("STEAM_COMPAT_APP_ID"), e.get("SteamGameId")):
            continue
        pids.append(pid)
        for k in ENV_KEYS:
            if k in e:
                env[k] = e[k]
        try:
            with open(f"{d}/maps") as f:
                maps = f.read()
        except OSError:
            maps = ""
        for line in maps.splitlines():
            parts = line.split(None, 5)
            if len(parts) < 6:
                continue
            path = parts[5]
            if proton is None and (m := PROTON.search(path)):
                proton = m.group(1)
            for k, rx in DLLS.items():
                if not loaded[k] and rx.search(path):
                    loaded[k] = True
        if not optiscaler_dir:
            try:
                exe = os.readlink(f"{d}/cwd")
                optiscaler_dir = os.path.exists(os.path.join(exe, "OptiScaler.ini"))
            except OSError:
                pass
    if not pids:
        return None
    if proton is None:
        for pid in pids:
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as f:
                    m = PROTON.search(f.read().replace(b"\0", b" ").decode(errors="replace"))
            except OSError:
                continue
            if m:
                proton = m.group(1)
                break
    loaded["optiscaler"] = loaded["optiscaler"] or optiscaler_dir
    return {
        "proton": proton,
        "knows_vars": bool(proton and TOOLS.search(proton)),
        "env": env,
        "loaded": loaded,
    }
