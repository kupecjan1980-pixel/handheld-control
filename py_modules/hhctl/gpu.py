"""GPU and package telemetry, read as root.

Steam's overlay runs unprivileged, so on Intel it sees no GPU power (RAPL
energy counters are root-only) and no "VRAM" (integrated Arc has none). The
plugin reads the same data itself:

- AMD: amdgpu hwmon power/clock and mem_info_vram_used / mem_info_gtt_used.
- Intel: GPU power from the card's hwmon energy counter, or the RAPL
  "uncore" domain; GPU memory summed from per-client DRM fdinfo.
- Package power (CPU + GPU) from the RAPL package domain when present.
"""

import glob
import os
import time

from . import sysfs


def find_card() -> tuple[str, str] | None:
    """Return (device dir, driver) of the first GPU bound to a known driver."""
    for card in sorted(glob.glob("/sys/class/drm/card[0-9]*")):
        if "-" in os.path.basename(card):
            continue  # connector, not a card
        dev = os.path.realpath(f"{card}/device")
        try:
            driver = os.path.basename(os.readlink(f"{dev}/driver"))
        except OSError:
            continue
        if driver in ("amdgpu", "xe", "i915"):
            return dev, driver
    return None


class EnergyMeter:
    """Turn a cumulative energy counter (µJ) into watts between samples."""

    def __init__(self, path: str, wrap: int | None = None):
        self.path = path
        self.wrap = wrap
        self.last: tuple[float, int] | None = None
        self.watts: float | None = None

    def sample(self) -> None:
        val = sysfs.read_int(self.path)
        now = time.monotonic()
        if val is None:
            return
        if self.last:
            t0, e0 = self.last
            delta = val - e0
            if delta < 0 and self.wrap:
                delta += self.wrap
            if now > t0 and delta >= 0:
                self.watts = delta / (now - t0) / 1_000_000
        self.last = (now, val)


def _rapl_zone(name: str) -> str | None:
    for zone in sorted(glob.glob("/sys/class/powercap/intel-rapl*")):
        if sysfs.read(f"{zone}/name") == name and os.path.exists(f"{zone}/energy_uj"):
            return zone
    return None


def _meter(zone: str | None) -> EnergyMeter | None:
    if not zone:
        return None
    return EnergyMeter(f"{zone}/energy_uj", sysfs.read_int(f"{zone}/max_energy_range_uj"))


def _hwmon(dev: str) -> str | None:
    found = sorted(glob.glob(f"{dev}/hwmon/hwmon*"))
    return found[0] if found else None


def drm_client_memory(driver: str) -> int | None:
    """Sum resident GPU memory over all DRM clients of `driver`, in bytes."""
    seen = set()
    total = 0
    found = False
    for fdinfo in glob.glob("/proc/[0-9]*/fdinfo/*"):
        try:
            with open(fdinfo) as f:
                text = f.read()
        except OSError:
            continue
        if f"drm-driver:\t{driver}" not in text:
            continue
        fields = dict(
            line.split(":", 1) for line in text.splitlines() if ":" in line
        )
        key = (fields.get("drm-pdev", "").strip(), fields.get("drm-client-id", "").strip())
        if key in seen:
            continue  # same client open through several fds
        seen.add(key)
        found = True
        for k, v in fields.items():
            if k.startswith("drm-resident-"):
                num, _, unit = v.strip().partition(" ")
                try:
                    total += int(num) * {"": 1, "KiB": 1024, "MiB": 1 << 20, "GiB": 1 << 30}[unit]
                except (ValueError, KeyError):
                    pass
    return total if found else None


class Gpu:
    def __init__(self):
        card = find_card()
        self.dev, self.driver = card if card else (None, None)
        self.hwmon = _hwmon(self.dev) if self.dev else None
        self.pkg = _meter(_rapl_zone("package-0"))
        self.gpu_meter = None
        if self.driver in ("xe", "i915"):
            energy = f"{self.hwmon}/energy1_input" if self.hwmon else None
            if energy and os.path.exists(energy):
                self.gpu_meter = EnergyMeter(energy)
            else:
                self.gpu_meter = _meter(_rapl_zone("uncore"))

    def sample(self) -> None:
        """Called every tick so energy deltas stay fresh."""
        for m in (self.pkg, self.gpu_meter):
            if m:
                m.sample()

    def _gpu_watts(self) -> float | None:
        if self.driver == "amdgpu" and self.hwmon:
            for f in ("power1_average", "power1_input"):
                uw = sysfs.read_int(f"{self.hwmon}/{f}")
                if uw:
                    return uw / 1_000_000
            return None
        return self.gpu_meter.watts if self.gpu_meter else None

    def _freq_mhz(self) -> int | None:
        if not self.dev:
            return None
        if self.driver == "amdgpu" and self.hwmon:
            hz = sysfs.read_int(f"{self.hwmon}/freq1_input")
            return hz // 1_000_000 if hz else None
        for path in sorted(glob.glob(f"{self.dev}/tile0/gt0/freq0/act_freq")) + [
            f"{self.dev}/drm/card0/gt_act_freq_mhz",
            *glob.glob(f"{self.dev}/drm/card*/gt_act_freq_mhz"),
        ]:
            val = sysfs.read_int(path)
            if val is not None:
                return val
        return None

    def state(self) -> dict:
        out = {
            "driver": self.driver,
            "gpu_w": self._gpu_watts(),
            "pkg_w": self.pkg.watts if self.pkg else None,
            "freq_mhz": self._freq_mhz(),
            "vram_mb": None,
            "gtt_mb": None,
            "mem_mb": None,
        }
        if self.driver == "amdgpu":
            vram = sysfs.read_int(f"{self.dev}/mem_info_vram_used")
            gtt = sysfs.read_int(f"{self.dev}/mem_info_gtt_used")
            out["vram_mb"] = vram >> 20 if vram is not None else None
            out["gtt_mb"] = gtt >> 20 if gtt is not None else None
        elif self.driver:
            mem = drm_client_memory(self.driver)
            out["mem_mb"] = mem >> 20 if mem is not None else None
        for k in ("gpu_w", "pkg_w"):
            if out[k] is not None:
                out[k] = round(out[k], 1)
        return out
