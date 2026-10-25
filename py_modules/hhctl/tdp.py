"""TDP backends, tried in order:

1. firmware-attributes ppt_pl1_spl/ppt_pl2_sppt/ppt_pl3_fppt (amd-dptc,
   asus-armoury, ...). This is what Bazzite's kernel exposes for the NEXT 2.
2. Intel RAPL powercap (package MSR zone and the MMIO zone, both enforced).
3. ryzenadj, if the binary is installed.
4. AMD SMU mailbox, directly (what ryzenadj does), for the APUs listed in
   SMU_FAMILIES (Raven to Strix Point), so those PCs need nothing installed.

On Intel the ACPI platform profile matters too: in "low-power" the firmware
holds the package near 15 W whatever RAPL says (seen on the ONEXPLAYER 3,
where CachyOS's power-profiles-daemon had picked it). PlatformProfile holds
"performance" while TDP control is on, so the TDP slider is the only limit.
"""

import glob
import logging
import os
import shutil
import struct
import subprocess
import time
from dataclasses import dataclass

from . import proc, sysfs
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
    PL4_FACTOR = 2  # PL4 is a peak-current guard, not a TDP knob: keep it well above PL2

    def __init__(self, zones: list[str]):
        self.zones = zones
        # Firmware's PL4, read before we write anything; never go below it.
        self.pl4_orig = {z: sysfs.read_int(p) or 0 for z in zones if (p := self._constraints(z).get("peak_power"))}

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

    def pl4_uw(self, dev: Device, zone: str) -> int:
        # A PL4 just above PL2 (what this plugin used to write) capped the
        # OXP3's GPU at ~700 MHz with the package at 15 W ("peak current").
        return max(self.pl4_orig.get(zone, 0), self.PL4_FACTOR * dev.tdp_boost_max * UW)

    def apply(self, dev: Device, watts: int, boost: bool) -> None:
        pl2, _ = boost_limits(dev, watts, boost)
        errors = []
        for zone in self.zones:
            cons = self._constraints(zone)
            for kind, uw in (("long_term", watts * UW), ("short_term", pl2 * UW), ("peak_power", self.pl4_uw(dev, zone))):
                path = cons.get(kind)
                if not path or not sysfs.writable(path):
                    continue
                if kind == "peak_power" and (sysfs.read_int(path) or 0) <= 0:
                    continue
                try:
                    sysfs.write(path, uw)
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


class PlatformProfile:
    """ACPI platform profile, set through power-profiles-daemon when it runs.

    Writing sysfs under a running daemon works until the daemon re-applies its
    own choice, so powerprofilesctl (also served by tuned-ppd) comes first.
    """

    PATH = "/sys/firmware/acpi/platform_profile"
    WANT = "performance"

    def __init__(self, path: str | None = None):
        self.path = path or self.PATH
        self.orig = self.current()
        self.changed = False

    def current(self) -> str | None:
        return sysfs.read(self.path)

    def available(self) -> bool:
        return self.WANT in sysfs.choices(f"{self.path}_choices")

    def _daemon(self) -> str | None:
        binary = shutil.which("powerprofilesctl", path=proc.env()["PATH"])
        if not binary:
            return None
        try:
            ok = proc.run([binary, "get"], text=True, timeout=5).returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
        return binary if ok else None

    def set(self, name: str) -> None:
        binary = self._daemon()
        if binary:
            res = proc.run([binary, "set", name], text=True, timeout=10)
            if res.returncode == 0 and self.current() == name:
                return
            log.warning("powerprofilesctl set %s: %s", name, (res.stderr or res.stdout).strip())
        sysfs.write(self.path, name)

    def hold(self) -> bool:
        """Select WANT if it isn't; True if something was changed."""
        if not self.available() or self.current() == self.WANT:
            return False
        log.info("Platform profile is %s, selecting %s", self.current(), self.WANT)
        self.set(self.WANT)
        self.changed = True
        return True

    def restore(self) -> None:
        if self.changed and self.orig and self.current() != self.orig:
            self.set(self.orig)
        self.changed = False


class RyzenAdj:
    name = "ryzenadj"

    def __init__(self, binary: str):
        self.binary = binary
        self.last: int | None = None

    @classmethod
    def find(cls):
        binary = shutil.which("ryzenadj", path=proc.env()["PATH"])
        return cls(binary) if binary else None

    def apply(self, dev: Device, watts: int, boost: bool) -> None:
        fast, _ = boost_limits(dev, watts, boost)
        mw = watts * 1000
        res = proc.run(
            [self.binary, f"--stapm-limit={mw}", f"--slow-limit={mw}", f"--fast-limit={fast * 1000}"],
            text=True, timeout=10,
        )
        if res.returncode != 0:
            raise OSError(res.stderr.strip() or res.stdout.strip() or "ryzenadj failed")
        self.last = watts

    def current(self) -> int | None:
        return self.last


@dataclass(frozen=True)
class SmuFamily:
    name: str
    cpu_family: int
    models: tuple[int, ...]
    rep: int  # MP1 response register
    mp1: tuple[int, int, int]  # stapm, fast, slow message ids
    rsmu: tuple[int, int, int]
    msg: int = 0x3B10528  # MP1 message register


_ZEN2_3 = ((0x14, 0x15, 0x16), (0x31, 0x32, 0x33))
SMU_FAMILIES = [
    # Message ids from ryzenadj (lib/api.c, nb_smu_ops.c) and ZenStates-Core
    # (APUSettings0 / APUSettings1 / APUSettings1_Phoenix); both agree.
    SmuFamily("Raven / Picasso / Dali", 0x17, (0x11, 0x18, 0x20), 0x3B10564, (0x1A, 0x1B, 0x1C), (0x2E, 0x2F, 0x30)),
    SmuFamily("Renoir / Lucienne", 0x17, tuple(range(0x60, 0x70)), 0x3B10564, *_ZEN2_3),
    SmuFamily("Cezanne / Barcelo", 0x19, tuple(range(0x50, 0x60)), 0x3B10564, *_ZEN2_3),
    # Rembrandt and later answer at 0x3B10578.
    SmuFamily("Phoenix / Hawk Point", 0x19, (0x74, 0x75, 0x78), 0x3B10578, *_ZEN2_3),
    # Strix Point (Ryzen AI 9 365/370/HX 375) and Krackan Point moved MP1's
    # message and response registers; the ids stay (APUSettings1_Strix).
    SmuFamily("Strix Point / Krackan Point", 0x1A, (0x20, 0x24, 0x60), 0x3B10978, *_ZEN2_3, msg=0x3B10928),
    # Strix Halo (NEXT 2): same mailbox as Strix Point in ryzenadj (FAM_STRIXHALO).
    SmuFamily("Strix Halo", 0x1A, (0x70,), 0x3B10978, *_ZEN2_3, msg=0x3B10928),
]


class AmdSmu:
    """TDP through the SMU mailboxes of AMD APUs, as ryzenadj does it.

    The SMU's registers are reached through the host bridge's (00:00.0) SMN
    index/data pair at 0xB8 / 0xBC of its PCI config space (the kernel uses
    0x60 / 0x64, so the two don't race). A message: clear the response
    register, write six arguments, write the message id, wait for the
    response (1 = OK). Limits are in mW. Needs root and no kernel lockdown.

    Each limit goes to the MP1 mailbox and then to the RSMU one. Laptop
    firmware takes it on MP1; desktop APUs (5700G, 8700G, ...) may refuse it
    there or ignore it, and take it on RSMU. It fails only if both refuse.
    """

    name = "amd smu"
    PCI = "/sys/bus/pci/devices/0000:00:00.0/config"
    INDEX, DATA = 0xB8, 0xBC
    # (message, response, first argument). MP1's response register depends
    # on the family: with the wrong one the 7730U "didn't answer" (2026-10-23).
    MP1_ARGS = 0x3B10998
    RSMU = (0x3B10A20, 0x3B10A80, 0x3B10A88)
    TEST, GET_VERSION = 0x01, 0x02  # the same on every mailbox
    TIMEOUT = 0.5

    def __init__(self, family: SmuFamily, pci: str | None = None):
        self.family = family
        self.pci = pci or self.PCI
        self.mp1 = (family.msg, family.rep, self.MP1_ARGS)
        self.silent: set[tuple[int, int, int]] = set()  # mailboxes that never answered
        self.last: int | None = None

    @staticmethod
    def detect(cpuinfo: str) -> SmuFamily | None:
        info = {}
        for line in cpuinfo.splitlines():
            k, _, v = line.partition(":")
            info.setdefault(k.strip(), v.strip())
        if info.get("vendor_id") != "AuthenticAMD":
            return None
        try:
            fam, model = int(info["cpu family"]), int(info["model"])
        except (KeyError, ValueError):
            return None
        return next((f for f in SMU_FAMILIES if f.cpu_family == fam and model in f.models), None)

    @classmethod
    def supported(cls, cpuinfo: str) -> bool:
        return cls.detect(cpuinfo) is not None

    @classmethod
    def find(cls, cpuinfo: str | None = None):
        family = cls.detect(cpuinfo if cpuinfo is not None else (sysfs.read("/proc/cpuinfo") or ""))
        if not family:
            return None
        smu = cls(family)
        try:
            smu.send(cls.TEST)
        except OSError as e:
            log.info("amd smu (%s): not usable (%s)", family.name, e)
            return None
        try:
            log.info("amd smu (%s): firmware %s", family.name, smu.version())
        except OSError as e:
            log.info("amd smu (%s): no version (%s)", family.name, e)
        return smu

    def _rw(self, fd: int, addr: int, value: int | None = None) -> int:
        os.pwrite(fd, struct.pack("<I", addr), self.INDEX)
        if value is not None:
            os.pwrite(fd, struct.pack("<I", value & 0xFFFFFFFF), self.DATA)
            return value
        return struct.unpack("<I", os.pread(fd, 4, self.DATA))[0]

    def send(self, msg: int, args: list[int] | None = None, box: tuple[int, int, int] | None = None) -> list[int]:
        msg_reg, rep_reg, arg_reg = box or self.mp1
        args = (args or []) + [0] * (6 - len(args or []))
        fd = self._open()
        try:
            self._rw(fd, rep_reg, 0)
            for i, a in enumerate(args):
                self._rw(fd, arg_reg + 4 * i, a)
            self._rw(fd, msg_reg, msg)
            deadline = time.monotonic() + self.TIMEOUT
            while (rep := self._rw(fd, rep_reg)) == 0:
                if time.monotonic() > deadline:
                    raise OSError(f"SMU didn't answer message {msg:#x}")
                time.sleep(0.001)
            if rep != 1:
                raise OSError(f"SMU refused message {msg:#x} (response {rep:#x})")
            return [self._rw(fd, arg_reg + 4 * i) for i in range(6)]
        finally:
            self._close(fd)

    def _open(self) -> int:
        return os.open(self.pci, os.O_RDWR)

    def _close(self, fd: int) -> None:
        os.close(fd)

    def version(self) -> str:
        v = self.send(self.GET_VERSION)[0]
        return f"{v >> 16 & 0xFF}.{v >> 8 & 0xFF}.{v & 0xFF}"

    def _limit(self, which: int, mw: int) -> None:
        """which: 0 stapm, 1 fast, 2 slow."""
        errors, ok = [], False
        for box, ids in ((self.mp1, self.family.mp1), (self.RSMU, self.family.rsmu)):
            if box in self.silent:
                continue
            try:
                self.send(ids[which], [mw], box)
                ok = True
            except OSError as e:
                if "didn't answer" in str(e):
                    self.silent.add(box)  # don't wait for it on every limit
                errors.append(str(e))
        if not ok:
            raise OSError("; ".join(errors) or "no SMU mailbox answers")

    def apply(self, dev: Device, watts: int, boost: bool) -> None:
        slow, fast = boost_limits(dev, watts, boost)  # short boost, peak boost
        # Ceilings first, as with firmware-attributes: sustained never above boost.
        self._limit(1, fast * 1000)
        self._limit(2, slow * 1000)
        self._limit(0, watts * 1000)
        self.last = watts

    def current(self) -> int | None:
        return self.last


def find_backend(dev: Device):
    fw = FirmwareAttrs.find()
    if fw:
        return fw
    if dev.cpu == "intel":
        return Rapl.find()
    return RyzenAdj.find() or AmdSmu.find()
