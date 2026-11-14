"""The NPU (AMD Ryzen AI "XDNA", Intel AI Boost) and upscaling games through it, the way Windows'
Auto SR does (the user's "ASR": the whole picture upscaled by a neural net on the NPU, for any game).

Looked into 2026-11-14: nothing on Linux does that. gamescope upscales on the GPU (FSR 1, NIS), Proton's
upgrades (FSR 4, XeSS) run on the GPU inside the game, and the NPU drivers (amdxdna, intel_vpu) only run
models handed to them by programs such as ONNX Runtime or an LLM server; no compositor or driver feeds
game frames to one. So the panel's row is there on every computer (the user: a toggle where it applies,
greyed out with a note elsewhere), says what NPU this one has, and stays greyed out with the reason
until there is something for it to switch on (`usable` below is the one place to change then).

Found by PCI id (there with or without a driver) and by the kernel's accelerator class (/sys/class/accel:
whatever NPU a loaded driver serves, ids not listed here included).
"""

import glob
import os

from . import sysfs

# vendor:device -> what it is
KNOWN = {
    "1022:1502": "AMD XDNA 1 (Ryzen AI: Ryzen 7040 / 8040 series, 8000G)",
    "1022:17f0": "AMD XDNA 2 (Ryzen AI 300 series and newer)",
    "8086:7d1d": "Intel AI Boost (Core Ultra, Meteor Lake)",
    "8086:ad1d": "Intel AI Boost (Core Ultra 200, Arrow Lake)",
    "8086:643e": "Intel AI Boost (Core Ultra 200V, Lunar Lake)",
    "8086:b03e": "Intel AI Boost (Core Ultra 300, Panther Lake)",
}
DRIVERS = {"amdxdna": "AMD XDNA", "intel_vpu": "Intel AI Boost"}
NO_SOFTWARE = ("nothing on Linux upscales games through an NPU yet: Windows' Auto SR has no Linux counterpart, "
               "gamescope and Proton upscale on the GPU, and the NPU's driver only runs AI programs' models")


def _id(dev: str) -> str:
    v, d = sysfs.read(f"{dev}/vendor") or "", sysfs.read(f"{dev}/device") or ""
    return f"{v.strip().removeprefix('0x')}:{d.strip().removeprefix('0x')}".lower()


def _driver(dev: str) -> str | None:
    try:
        return os.path.basename(os.readlink(f"{dev}/driver"))
    except OSError:
        return None


def find(root: str = "/sys") -> dict | None:
    """The NPU: {"name", "driver" (None: no driver loaded for it)}; None when the computer has none."""
    for dev in sorted(glob.glob(f"{root}/bus/pci/devices/*")):
        name = KNOWN.get(_id(dev))
        if name:
            return {"name": name, "driver": _driver(dev)}
    for acc in sorted(glob.glob(f"{root}/class/accel/accel*")):  # one served by a driver, its id not listed above
        drv = _driver(f"{acc}/device")
        if drv in DRIVERS:
            return {"name": DRIVERS[drv], "driver": drv}
    return None


def state(root: str = "/sys") -> dict:
    """For the panel: the NPU found, whether games can be upscaled through it, and why not."""
    npu = find(root)
    usable = False  # (no software for it on Linux: see the top)
    if npu is None:
        why = ("this computer has no NPU (AMD has one from the Ryzen 7040 series on, Intel from Core Ultra; "
               "older chips, the Ryzen 5000 / 6000 / 7030 series among them, don't)")
    else:
        why = NO_SOFTWARE
    return {"present": npu is not None, "name": npu["name"] if npu else None,
            "driver": npu["driver"] if npu else None, "usable": usable, "enabled": False, "reason": why}
