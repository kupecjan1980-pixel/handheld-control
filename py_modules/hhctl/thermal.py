"""CPU temperature limit, held by the CPU itself.

The CPU boosts as hard as the power limits allow until it reaches the limit,
then trims its clocks to stay there, so nothing has to poll and step the TDP.

- AMD APUs: the SMU's Tctl limit (ryzenadj --tctl-temp, MP1 message 0x19;
  0x1F on Raven / Picasso / Dali). Firmware may reset it after resume or an
  AC change, so it's re-sent every minute. The SMU can't report the stock
  value; turning the limit off sends the family's usual stock limit back
  (exact again after a reboot).
- Intel: the TCC offset, the same knob as Throttlestop's "TCC offset":
  throttling starts at TjMax - offset. Through intel_tcc / proc_thermal's
  sysfs file when there is one, else MSR 0x1A2 bits 29:24 on every CPU
  (MSR_PLATFORM_INFO bit 30 says if it's programmable). The original offset
  is put back when turned off and on unload.
"""

import glob
import logging
import os
import time

from . import proc, sysfs

log = logging.getLogger("hhctl")

MSR = "/dev/cpu/{}/msr"
TEMPERATURE_TARGET, PLATFORM_INFO = 0x1A2, 0xCE
REASSERT = 60.0  # seconds


class AmdTctl:
    name = "amd tctl"
    # stock Tctl limit by SMU family name prefix (the 7730U and Renoir/Cezanne
    # parts are rated 95 °C, Phoenix and later 100 °C)
    STOCK = {"Raven": 95, "Renoir": 95, "Cezanne": 95}

    def __init__(self, family: str, smu=None, ryzenadj: str | None = None):
        self.smu, self.ryzenadj = smu, ryzenadj
        fam = family
        self.msg = 0x1F if fam.startswith("Raven") else 0x19
        self.stock = next((v for k, v in self.STOCK.items() if fam.startswith(k)), 100)
        self.range = (60, self.stock)
        self.last: tuple[int, float] | None = None
        self.changed = False

    def _send(self, c: int) -> None:
        if self.smu:
            self.smu.send(self.msg, [c])
        else:
            r = proc.run([self.ryzenadj, f"--tctl-temp={c}"], text=True, timeout=10)
            if r.returncode != 0:
                raise OSError(r.stderr.strip() or r.stdout.strip() or "ryzenadj failed")

    def apply(self, c: int, force: bool = False) -> None:
        now = time.monotonic()
        if not force and self.last and self.last[0] == c and now - self.last[1] < REASSERT:
            return
        self._send(c)
        self.last, self.changed = (c, now), True

    def restore(self) -> None:
        if self.changed:
            self._send(self.stock)
            self.last, self.changed = None, False


class IntelTcc:
    name = "intel tcc"

    def __init__(self, tjmax: int, sysfs_path: str | None, cpus: list[str]):
        self.tjmax = tjmax
        self.path = sysfs_path  # tcc_offset_degree_celsius
        self.cpus = cpus  # /dev/cpu/N/msr, when there's no sysfs file
        self.range = (max(40, tjmax - 63), tjmax)
        self.orig: int | None = None
        self.last: int | None = None

    @staticmethod
    def _rd(node: str, reg: int) -> int:
        fd = os.open(node, os.O_RDONLY)
        try:
            return int.from_bytes(os.pread(fd, 8, reg), "little")
        finally:
            os.close(fd)

    @classmethod
    def find(cls):
        paths = glob.glob("/sys/bus/pci/devices/*/tcc_offset_degree_celsius")
        if not os.path.exists(MSR.format(0)):
            proc.run(["modprobe", "msr"], timeout=10)
        cpus = sorted(glob.glob(MSR.format("*")))
        try:
            v = cls._rd(cpus[0], TEMPERATURE_TARGET)
            tjmax = v >> 16 & 0xFF
            programmable = bool(cls._rd(cpus[0], PLATFORM_INFO) >> 30 & 1)
            if v >> 31 & 1:
                # Locked by the firmware: intel_tcc refuses every write (EPERM)
                # and the MSR ignores it (ONEXPLAYER 3, 2026-10-26).
                log.info("thermal: the TCC offset is locked by the firmware")
                return None
        except (OSError, IndexError):
            tjmax, programmable = 0, False
        if not tjmax:
            # coretemp's critical temperature is TjMax too
            for crit in glob.glob("/sys/devices/platform/coretemp.0/hwmon/hwmon*/temp1_crit"):
                tjmax = (sysfs.read_int(crit) or 0) // 1000
        if not tjmax or not (paths or programmable):
            return None
        return cls(tjmax, paths[0] if paths else None, [] if paths else cpus)

    def offset(self) -> int:
        if self.path:
            return sysfs.read_int(self.path) or 0
        return self._rd(self.cpus[0], TEMPERATURE_TARGET) >> 24 & 0x3F

    def _set(self, off: int) -> None:
        if self.path:
            sysfs.write(self.path, str(off))
            return
        for node in self.cpus:
            v = self._rd(node, TEMPERATURE_TARGET)
            v = v & ~(0x3F << 24) | off << 24
            fd = os.open(node, os.O_WRONLY)
            try:
                os.pwrite(fd, v.to_bytes(8, "little"), TEMPERATURE_TARGET)
            finally:
                os.close(fd)

    def apply(self, c: int, force: bool = False) -> None:
        off = max(0, min(63, self.tjmax - c))
        if self.orig is None:
            self.orig = self.offset()
        if force or self.offset() != off:
            self._set(off)
        self.last = c

    def restore(self) -> None:
        if self.orig is not None:
            self._set(self.orig)
            self.orig, self.last = None, None


def find(cpu: str, family: str = "", smu=None, ryzenadj: str | None = None):
    """family: AmdSmu family name ("" when unknown); smu or ryzenadj sends."""
    if cpu == "intel":
        return IntelTcc.find()
    if smu or ryzenadj:
        return AmdTctl(family, smu, ryzenadj)
    return None
