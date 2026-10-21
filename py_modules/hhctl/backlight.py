"""Panel brightness written straight to sysfs.

Game Mode (gamescope) sometimes drives the wrong backlight device, or none,
on new hardware. Writing the backlight as root works regardless.
"""

import glob
import os

from . import sysfs

MIN_PCT = 5  # never let the slider black the panel out completely
TYPE_ORDER = {"raw": 0, "platform": 1, "firmware": 2}


class Backlight:
    def __init__(self, path: str):
        self.path = path
        self.name = os.path.basename(path)
        self.max = sysfs.read_int(f"{path}/max_brightness") or 1

    @classmethod
    def find(cls):
        devs = sorted(
            glob.glob("/sys/class/backlight/*"),
            key=lambda d: (TYPE_ORDER.get(sysfs.read(f"{d}/type") or "", 9), d),
        )
        for d in devs:
            if (sysfs.read_int(f"{d}/max_brightness") or 0) > 0:
                return cls(d)
        return None

    def percent(self) -> int | None:
        val = sysfs.read_int(f"{self.path}/brightness")
        return round(val * 100 / self.max) if val is not None else None

    def set_percent(self, pct: int) -> None:
        pct = max(MIN_PCT, min(100, int(pct)))
        sysfs.write(f"{self.path}/brightness", max(1, round(pct * self.max / 100)))
