"""TDP backends, tried in order:

1. firmware-attributes ppt_pl1_spl/ppt_pl2_sppt/ppt_pl3_fppt (amd-dptc,
   asus-armoury, ...). This is what Bazzite's kernel exposes for the NEXT 2.
2. Intel RAPL powercap (package MSR zone and the MMIO zone, both enforced).
3. ryzenadj, if the binary is installed.
"""

import glob
import logging
import os
import shutil
import subprocess

from . import sysfs
from .device import Device

log = logging.getLogger("hhctl")

UW = 1_000_000


def boost_limits(dev: Device, watts: int, boost: bool) -> tuple[int, int]:
    """Return (short boost, peak boost) for a sustained limit."""
    if not boost:
        return watts, watts
    return min(watts + 2, dev.tdp_boost_max), min(watts + 5, dev.tdp_boost_max)


class FirmwareAttrs:
    name = "firmware-attributes"

    def __init__(self, base: str):
        self.base = base
        self.driver = base.split("/")[-2]

    @classmethod
    def find(cls):
        for spl in sorted(glob.glob("/sys/class/firmware-attributes/*/attributes/ppt_pl1_spl")):
            return cls(os.path.dirname(os.path.dirname(spl)))
        return None

    def _attr(self, name: str) -> str:
        return f"{self.base}/{name}"

    def _set(self, name: str, value: int) -> None:
        path = self._attr(name)
        if not os.path.isdir(path):
            return
        lo = sysfs.read_int(f"{path}/min_value")
        hi = sysfs.read_int(f"{path}/max_value")
        if lo is not None:
            value = max(lo, value)
        if hi is not None:
            value = min(hi, value)
        sysfs.write(f"{path}/current_value", value)

    def _ensure_custom_profile(self) -> None:
        # amd-dptc rejects tunable writes (-EBUSY) unless the profile is custom.
        for pp in glob.glob("/sys/class/platform-profile/platform-profile-*"):
            if "custom" in sysfs.choices(f"{pp}/choices") and sysfs.read(f"{pp}/profile") != "custom":
                try:
                    sysfs.write(f"{pp}/profile", "custom")
                except OSError as e:
                    log.warning("Could not select custom platform profile on %s: %s", pp, e)
        legacy = "/sys/firmware/acpi/platform_profile"
        if "custom" in sysfs.choices(f"{legacy}_choices") and sysfs.read(legacy) != "custom":
            try:
                sysfs.write(legacy, "custom")
            except OSError:
                pass
        save = self._attr("save_settings/current_value")
        if os.path.exists(save) and sysfs.read(save) != "single":
            try:
                sysfs.write(save, "single")
            except OSError:
                pass

    def limits(self) -> tuple[int | None, int | None]:
        path = self._attr("ppt_pl1_spl")
        return sysfs.read_int(f"{path}/min_value"), sysfs.read_int(f"{path}/max_value")

    def apply(self, dev: Device, watts: int, boost: bool) -> None:
        self._ensure_custom_profile()
        sppt, fppt = boost_limits(dev, watts, boost)
        # Raise the ceilings first so SPL never exceeds SPPT/FPPT mid-update.
        self._set("ppt_pl3_fppt", fppt)
        self._set("ppt_pl2_sppt", sppt)
        self._set("ppt_pl1_spl", watts)

    def current(self) -> int | None:
        return sysfs.read_int(self._attr("ppt_pl1_spl/current_value"))


class Rapl:
    name = "intel-rapl"

    def __init__(self, zones: list[str]):
        self.zones = zones

    @classmethod
    def find(cls):
        zones = []
        for zone in sorted(glob.glob("/sys/class/powercap/intel-rapl*:*")):
            # Only package zones: never core/uncore/dram/psys.
            if not (sysfs.read(f"{zone}/name") or "").startswith("package-"):
                continue
            if sysfs.read(f"{zone}/enabled") != "1":
                continue
            zones.append(zone)
        return cls(zones) if zones else None

    def _constraints(self, zone: str) -> dict[str, str]:
        out = {}
        for c in glob.glob(f"{zone}/constraint_*_name"):
            kind = sysfs.read(c)
            out[kind] = c.removesuffix("name") + "power_limit_uw"
        return out

    def apply(self, dev: Device, watts: int, boost: bool) -> None:
        pl2, pl4 = boost_limits(dev, watts, boost)
        errors = []
        for zone in self.zones:
            cons = self._constraints(zone)
            for kind, value in (("long_term", watts), ("short_term", pl2), ("peak_power", pl4)):
                path = cons.get(kind)
                if not path or not sysfs.writable(path):
                    continue
                if kind == "peak_power" and (sysfs.read_int(path) or 0) <= 0:
                    continue
                try:
                    sysfs.write(path, value * UW)
                except OSError as e:
                    errors.append(f"{path}: {e}")
        if errors:
            raise OSError("; ".join(errors))

    def current(self) -> int | None:
        for zone in self.zones:
            path = self._constraints(zone).get("long_term")
            if path:
                uw = sysfs.read_int(path)
                if uw:
                    return uw // UW
        return None


class RyzenAdj:
    name = "ryzenadj"

    def __init__(self, binary: str):
        self.binary = binary
        self.last: int | None = None

    @classmethod
    def find(cls):
        binary = shutil.which("ryzenadj")
        return cls(binary) if binary else None

    def apply(self, dev: Device, watts: int, boost: bool) -> None:
        fast, _ = boost_limits(dev, watts, boost)
        mw = watts * 1000
        res = subprocess.run(
            [self.binary, f"--stapm-limit={mw}", f"--slow-limit={mw}", f"--fast-limit={fast * 1000}"],
            capture_output=True, text=True, timeout=10,
        )
        if res.returncode != 0:
            raise OSError(res.stderr.strip() or res.stdout.strip() or "ryzenadj failed")
        self.last = watts

    def current(self) -> int | None:
        return self.last


def find_backend(dev: Device):
    fw = FirmwareAttrs.find()
    if fw:
        return fw
    if dev.cpu == "intel":
        return Rapl.find()
    return RyzenAdj.find()
