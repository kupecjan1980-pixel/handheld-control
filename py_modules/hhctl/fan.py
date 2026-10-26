"""Fan control through hwmon (ayaneo-ec / oxpec) or raw EC fallback."""

import glob
import os
import subprocess

from . import ec, proc, sysfs
from .device import Device

CURVES = {
    "quiet": [(40, 0), (50, 20), (60, 35), (70, 55), (80, 75), (88, 100)],
    "balanced": [(40, 20), (50, 30), (60, 45), (70, 65), (80, 85), (86, 100)],
    "performance": [(40, 30), (50, 45), (60, 60), (70, 80), (78, 100)],
}

# The editable curve: a speed (%) at each of these temperatures (°C).
CUSTOM_TEMPS = (40, 50, 60, 70, 80, 88)
CUSTOM_DEFAULT = [p for _, p in CURVES["balanced"]]


def custom_curve(points) -> list[tuple[int, int]]:
    """The user's speeds, made non-decreasing so it never slows as it heats up."""
    pts = list(points or CUSTOM_DEFAULT)[: len(CUSTOM_TEMPS)]
    pts += CUSTOM_DEFAULT[len(pts):]
    out, low = [], 0
    for t, p in zip(CUSTOM_TEMPS, pts):
        low = max(low, min(100, max(0, int(p))))
        out.append((t, low))
    return out


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
    """hwmon pwmN channels (1 = manual). auto: the pwmN_enable value that
    hands the fan back to the firmware/chip (2 for the handheld EC drivers;
    for Super I/O chips, whatever automatic mode the BIOS had set)."""

    def __init__(self, path: str, channels: tuple[int, ...] = (1,), auto: int = 2):
        self.path = path
        self.channels = channels
        self.auto = auto
        self.name = f"hwmon ({sysfs.read(f'{path}/name')})"
        if channels != (1,):
            self.name += " fan " + ", ".join(map(str, channels))

    def set_auto(self) -> None:
        for n in self.channels:
            sysfs.write(f"{self.path}/pwm{n}_enable", self.auto)

    def set_percent(self, pct: int) -> None:
        for n in self.channels:
            if sysfs.read(f"{self.path}/pwm{n}_enable") != "1":
                sysfs.write(f"{self.path}/pwm{n}_enable", 1)
            sysfs.write(f"{self.path}/pwm{n}", round(pct * 255 / 100))

    def rpm(self) -> int | None:
        vals = [v for n in self.channels if (v := sysfs.read_int(f"{self.path}/fan{n}_input")) is not None]
        return max(vals) if vals else None

    def restore_spec(self) -> dict:
        return {"kind": "hwmon", "path": self.path, "channels": list(self.channels), "auto": self.auto}


# Super I/O hwmon drivers of desktop and mini-PC boards (ASRock DeskMini
# X300 / X600 have Nuvoton chips). Each only binds if it finds its chip.
SUPERIO_MODULES = ("nct6775", "nct6683", "it87")
SUPERIO_NAMES = ("nct", "it8")
HWMON = "/sys/class/hwmon"


def superio_fan():
    """(HwmonFan or None, why). Picks the chip's spinning fan headers; with
    none spinning (fans stopped at idle), every header with a tach."""
    for mod in SUPERIO_MODULES:
        try:
            proc.run(["modprobe", mod], timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass
    read_only = None
    for hw in sorted(glob.glob(f"{HWMON}/hwmon*")):
        name = sysfs.read(f"{hw}/name") or ""
        if not name.startswith(SUPERIO_NAMES):
            continue
        tachs = {}
        for f in glob.glob(f"{hw}/fan[0-9]*_input"):
            n = int(os.path.basename(f)[3:].split("_")[0])
            tachs[n] = sysfs.read_int(f) or 0
        ctl = [n for n in sorted(tachs) if sysfs.writable(f"{hw}/pwm{n}_enable") and sysfs.writable(f"{hw}/pwm{n}")]
        if not ctl:
            if tachs:
                read_only = name
            continue
        spinning = tuple(n for n in ctl if tachs[n] > 0)
        channels = spinning or tuple(ctl)
        # Back to the mode the BIOS chose; 1 (manual) means we were cut off
        # mid-run last time, so fall back to the chip's automatic mode.
        orig = sysfs.read_int(f"{hw}/pwm{channels[0]}_enable")
        auto = orig if orig not in (None, 1) else (5 if name.startswith("nct") else 2)
        return HwmonFan(hw, channels, auto), None
    if read_only:
        return None, f"{read_only} reads the fans but its driver can't control them"
    return None, None


class EcFanCtl:
    def __init__(self, cfg, transport: str):
        self.cfg = cfg
        self.name = f"EC via {transport}"

    def probe(self) -> None:
        """Refuse to drive the fan if the registers don't look as mapped.
        The duty register is only ours in manual mode: in auto the firmware
        keeps its own value there (0x66 on a NEXT 2, 2026-10-25)."""
        if ec.check(self.cfg.mode_reg, self.cfg.mode_values()) == self.cfg.manual:
            ec.check(self.cfg.pwm_reg, self.cfg.pwm_values())

    def set_auto(self) -> None:
        ec.write(self.cfg.mode_reg, self.cfg.auto, self.cfg.mode_values())

    def set_percent(self, pct: int) -> None:
        manual = ec.check(self.cfg.mode_reg, self.cfg.mode_values()) == self.cfg.manual
        ec.write(self.cfg.mode_reg, self.cfg.manual, self.cfg.mode_values())
        # Coming from auto, whatever the firmware left there is fine to overwrite.
        ec.write(self.cfg.pwm_reg, round(pct * self.cfg.pwm_max / 100),
                 self.cfg.pwm_values() if manual else range(256))

    def to_firmware(self) -> None:
        """Hand the fan back after a failed check, if the mode register is still
        one of ours (else nothing is written)."""
        try:
            if ec.read(self.cfg.mode_reg) == self.cfg.manual:
                ec.write(self.cfg.mode_reg, self.cfg.auto, self.cfg.mode_values())
        except OSError:
            pass

    def rpm(self) -> int | None:
        try:
            return ec.read(self.cfg.tach_reg, 2)
        except OSError:
            return None

    def restore_spec(self) -> dict:
        return {
            "kind": "ec",
            **ec.transport_spec(),
            "mode_reg": self.cfg.mode_reg,
            "manual": self.cfg.manual,
            "auto": self.cfg.auto,
        }


def find_backend(dev: Device, allow_ec: bool):
    hw = sysfs.find_hwmon(dev.hwmon_names) if dev.hwmon_names else None
    if hw and sysfs.writable(f"{hw}/pwm1_enable"):
        return HwmonFan(hw), None
    if not dev.ec_fan:
        if dev.key == "generic":
            ctl, why = superio_fan()
            if ctl or why:
                return ctl, why
            # e.g. the GenMachine Ren (7730U): no ACPI EC, no Super I/O, no fan
            # in its ACPI tables (checked with sensors-detect and the DSDT/SSDTs).
            return None, ("Linux sees no fan controller on this PC (no embedded controller, "
                          "Super I/O chip or fan driver); the firmware runs the fan. "
                          "Its curve can only be changed in the BIOS setup.")
        return None, "no fan driver for this device"
    if not allow_ec:
        return None, "no kernel fan driver; enable raw EC writes under Advanced to use the EC fallback"
    ok, why = ec.available(dev.ec_mmio, dev.ec_check_regs())
    if not ok:
        return None, why
    ctl = EcFanCtl(dev.ec_fan, why)
    try:
        ctl.probe()
    except OSError as e:
        ctl.to_firmware()
        return None, str(e) + "; the firmware runs the fan"
    return ctl, None
