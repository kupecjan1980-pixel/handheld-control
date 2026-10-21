"""Fan control through hwmon (ayaneo-ec / oxpec) or raw EC fallback."""

import glob

from . import ec, sysfs
from .device import Device

CURVES = {
    "quiet": [(40, 0), (50, 20), (60, 35), (70, 55), (80, 75), (88, 100)],
    "balanced": [(40, 20), (50, 30), (60, 45), (70, 65), (80, 85), (86, 100)],
    "performance": [(40, 30), (50, 45), (60, 60), (70, 80), (78, 100)],
}

# Above this the fan is forced to 100% in every manual mode.
SAFETY_TEMP = 90
SAFETY_RELEASE = 80


def interpolate(curve: list[tuple[int, int]], temp: float) -> int:
    if temp <= curve[0][0]:
        return curve[0][1]
    for (t0, p0), (t1, p1) in zip(curve, curve[1:]):
        if temp <= t1:
            return round(p0 + (p1 - p0) * (temp - t0) / (t1 - t0))
    return curve[-1][1]


def cpu_temp() -> float | None:
    hw = sysfs.find_hwmon(("k10temp", "coretemp"))
    if hw:
        val = sysfs.read_int(f"{hw}/temp1_input")
        if val:
            return val / 1000
    for zone in glob.glob("/sys/class/thermal/thermal_zone*"):
        if sysfs.read(f"{zone}/type") in ("x86_pkg_temp", "acpitz"):
            val = sysfs.read_int(f"{zone}/temp")
            if val:
                return val / 1000
    return None


class HwmonFan:
    def __init__(self, path: str):
        self.path = path
        self.name = f"hwmon ({sysfs.read(f'{path}/name')})"

    def set_auto(self) -> None:
        sysfs.write(f"{self.path}/pwm1_enable", 2)

    def set_percent(self, pct: int) -> None:
        if sysfs.read(f"{self.path}/pwm1_enable") != "1":
            sysfs.write(f"{self.path}/pwm1_enable", 1)
        sysfs.write(f"{self.path}/pwm1", round(pct * 255 / 100))

    def rpm(self) -> int | None:
        return sysfs.read_int(f"{self.path}/fan1_input")

    def restore_spec(self) -> dict:
        return {"kind": "hwmon", "path": self.path}


class EcFanCtl:
    name = "ec_sys"

    def __init__(self, cfg):
        self.cfg = cfg

    def probe(self) -> None:
        """Refuse to drive the fan if the registers don't look as mapped."""
        ec.check(self.cfg.mode_reg, self.cfg.mode_values())
        ec.check(self.cfg.pwm_reg, self.cfg.pwm_values())

    def set_auto(self) -> None:
        ec.write(self.cfg.mode_reg, self.cfg.auto, self.cfg.mode_values())

    def set_percent(self, pct: int) -> None:
        ec.write(self.cfg.mode_reg, self.cfg.manual, self.cfg.mode_values())
        ec.write(self.cfg.pwm_reg, round(pct * self.cfg.pwm_max / 100), self.cfg.pwm_values())

    def rpm(self) -> int | None:
        try:
            return ec.read(self.cfg.tach_reg, 2)
        except OSError:
            return None

    def restore_spec(self) -> dict:
        return {
            "kind": "ec",
            "mode_reg": self.cfg.mode_reg,
            "manual": self.cfg.manual,
            "auto": self.cfg.auto,
        }


def find_backend(dev: Device, allow_ec: bool):
    hw = sysfs.find_hwmon(dev.hwmon_names) if dev.hwmon_names else None
    if hw and sysfs.writable(f"{hw}/pwm1_enable"):
        return HwmonFan(hw), None
    if not dev.ec_fan:
        return None, "no fan driver for this device"
    if not allow_ec:
        return None, "no kernel fan driver; enable raw EC writes under Advanced to use the EC fallback"
    ok, why = ec.available()
    if not ok:
        return None, why
    ctl = EcFanCtl(dev.ec_fan)
    try:
        ctl.probe()
    except OSError as e:
        return None, str(e)
    return ctl, None
