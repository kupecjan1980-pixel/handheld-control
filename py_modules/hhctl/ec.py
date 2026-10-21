"""Raw embedded controller access through the ec_sys debugfs interface.

Only used when no kernel driver (ayaneo-ec / oxpec) is bound for the device.
Blocked when the kernel is locked down (Secure Boot on Fedora/Bazzite).
"""

import os
import subprocess

from . import sysfs

EC_IO = "/sys/kernel/debug/ec/ec0/io"
WRITE_PARAM = "/sys/module/ec_sys/parameters/write_support"


def lockdown() -> str | None:
    return sysfs.selected("/sys/kernel/security/lockdown")


def available() -> tuple[bool, str]:
    mode = lockdown()
    if mode and mode != "none":
        return False, f"kernel lockdown is '{mode}' (Secure Boot); raw EC access is blocked"
    if not os.path.exists(EC_IO):
        subprocess.run(
            ["modprobe", "ec_sys", "write_support=1"],
            capture_output=True, timeout=10,
        )
    if not os.path.exists(EC_IO):
        return False, "ec_sys module or debugfs is not available"
    if sysfs.read(WRITE_PARAM) not in ("Y", "1"):
        try:
            sysfs.write(WRITE_PARAM, "Y")
        except OSError:
            pass
    if sysfs.read(WRITE_PARAM) not in ("Y", "1"):
        return False, "ec_sys loaded without write_support=1 (reload the module)"
    return True, "ec_sys"


def read(reg: int, size: int = 1) -> int:
    """Read `size` bytes big-endian, as oxpec/ayaneo-ec do for tach values."""
    with open(EC_IO, "rb") as f:
        f.seek(reg)
        data = f.read(size)
    return int.from_bytes(data, "big")


def write(reg: int, value: int) -> None:
    with open(EC_IO, "r+b", buffering=0) as f:
        f.seek(reg)
        f.write(bytes([value & 0xFF]))
