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
import re
import subprocess
import time

from . import proc, sysfs


def memory_mb() -> tuple[int | None, int | None]:
    """(used, total) system RAM in MB, from /proc/meminfo."""
    info = {}
    for line in (sysfs.read("/proc/meminfo") or "").splitlines():
        k, _, v = line.partition(":")
        if v.strip().split()[:1]:
            info[k] = int(v.split()[0])  # kB
    total, avail = info.get("MemTotal"), info.get("MemAvailable")
    if total is None or avail is None:
        return None, None
    return (total - avail) >> 10, total >> 10


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


DEBUG_DRI = "/sys/kernel/debug/dri"


def gfx_pipes(dev: str | None) -> dict:
    """Is amdgpu's second graphics pipe running? (GFX11 APUs, Linux 7.3+)

    The kernel only offers it on RDNA 3 / 3.5 APUs (GC 11.x) with F32
    firmware, and nothing can switch it on from userspace: this only reports.
    The second pipe shows up as an extra gfx ring (gfx_0.1.0) in debugfs.
    """
    gc = [sysfs.read(f"{dev}/ip_discovery/die/0/GC/0/{k}") for k in ("major", "minor", "revision")] if dev else []
    version = ".".join(gc) if gc and all(gc) else None
    out = {"gc": version, "state": "unknown"}
    if version and not version.startswith("11."):
        out["state"] = "unsupported"
        return out
    if not os.path.ismount("/sys/kernel/debug"):
        try:
            proc.run(["mount", "-t", "debugfs", "none", "/sys/kernel/debug"], timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass
    pci = os.path.basename(dev or "")
    for name in glob.glob(f"{DEBUG_DRI}/*/name"):
        if pci and pci in (sysfs.read(name) or ""):
            d = os.path.dirname(name)
            if os.path.exists(f"{d}/amdgpu_ring_gfx_0.1.0"):
                out["state"] = "on"
            elif os.path.exists(f"{d}/amdgpu_ring_gfx_0.0.0"):
                out["state"] = "off"
            break
    return out


class Gpu:
    def __init__(self):
        card = find_card()
        self.dev, self.driver = card if card else (None, None)
        self.hwmon = _hwmon(self.dev) if self.dev else None
        self.pkg = _meter(_rapl_zone("package-0"))
        self.core = _meter(_rapl_zone("core"))
        self.gpu_meter = None
        if self.driver in ("xe", "i915"):
            energy = f"{self.hwmon}/energy1_input" if self.hwmon else None
            if energy and os.path.exists(energy):
                self.gpu_meter = EnergyMeter(energy)
            else:
                self.gpu_meter = _meter(_rapl_zone("uncore"))
        # Utilization: amdgpu reports it; on Intel it's 100% minus the share of
        # time the GT spent idle (xe gtidle, i915 RC6) since the last tick.
        self.idle_file = self._idle_file()
        self.idle_prev: tuple[float, int] | None = None
        self.util: float | None = None

    def _idle_file(self) -> str | None:
        if self.driver == "xe":
            files = sorted(glob.glob(f"{self.dev}/tile0/gt0/gtidle/idle_residency_ms"))
        elif self.driver == "i915":
            files = sorted(glob.glob(f"{self.dev}/drm/card*/power/rc6_residency_ms"))
        else:
            return None
        return files[0] if files else None

    def sample(self) -> None:
        """Called every tick so energy deltas stay fresh."""
        for m in (self.pkg, self.core, self.gpu_meter):
            if m:
                m.sample()
        if self.idle_file:
            idle = sysfs.read_int(self.idle_file)
            now = time.monotonic()
            if idle is not None:
                if self.idle_prev:
                    t0, i0 = self.idle_prev
                    ms = (now - t0) * 1000
                    if ms > 0:
                        self.util = max(0.0, min(100.0, 100 * (1 - (idle - i0) / ms)))
                self.idle_prev = (now, idle)

    def _util(self) -> int | None:
        if self.driver == "amdgpu" and self.dev:
            return sysfs.read_int(f"{self.dev}/gpu_busy_percent")
        return round(self.util) if self.util is not None else None

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

    def _temp_c(self) -> float | None:
        """GPU temperature from the card's hwmon (amdgpu "edge", xe). Integrated
        Intel GPUs usually have none; the GPU then shares the CPU package."""
        if not self.hwmon:
            return None
        for path in sorted(glob.glob(f"{self.hwmon}/temp*_input")):
            val = sysfs.read_int(path)
            if val:
                return round(val / 1000)
        return None

    def state(self) -> dict:
        out = {
            "driver": self.driver,
            "temp_c": self._temp_c(),
            "gpu_w": self._gpu_watts(),
            "pkg_w": self.pkg.watts if self.pkg else None,
            "cpu_w": self.core.watts if self.core else None,
            "cpu_w_est": False,
            "freq_mhz": self._freq_mhz(),
            "util": self._util(),
            "vram_mb": None,
            "vram_total_mb": None,
            "gtt_mb": None,
            "mem_mb": None,
        }
        if self.driver == "amdgpu":
            vram = sysfs.read_int(f"{self.dev}/mem_info_vram_used")
            gtt = sysfs.read_int(f"{self.dev}/mem_info_gtt_used")
            out["vram_mb"] = vram >> 20 if vram is not None else None
            total = sysfs.read_int(f"{self.dev}/mem_info_vram_total")
            out["vram_total_mb"] = total >> 20 if total else None
            out["gtt_mb"] = gtt >> 20 if gtt is not None else None
        elif self.driver:
            mem = drm_client_memory(self.driver)
            out["mem_mb"] = mem >> 20 if mem is not None else None
        if out["cpu_w"] is None and out["pkg_w"] is not None and out["gpu_w"] is not None:
            # No RAPL core domain (AMD): package minus GPU is close enough.
            out["cpu_w"] = max(0.0, out["pkg_w"] - out["gpu_w"])
            out["cpu_w_est"] = True
        for k in ("gpu_w", "pkg_w", "cpu_w"):
            if out[k] is not None:
                out[k] = round(out[k], 1)
        return out


def _intel_freq_dir(dev: str, driver: str) -> dict[str, str] | None:
    """Map min/max/rpn/rp0 to the clock files of the render GT."""
    if driver == "xe":
        d = f"{dev}/tile0/gt0/freq0"
        if os.path.exists(f"{d}/min_freq"):
            return {k: f"{d}/{k}_freq" for k in ("min", "max", "rpn", "rp0", "rpe")} | {"throttle": f"{d}/throttle"}
    elif driver == "i915":
        for card in sorted(glob.glob(f"{dev}/drm/card[0-9]*")):
            if os.path.exists(f"{card}/gt_min_freq_mhz"):
                return {
                    "min": f"{card}/gt_min_freq_mhz", "max": f"{card}/gt_max_freq_mhz",
                    "rpn": f"{card}/gt_RPn_freq_mhz", "rp0": f"{card}/gt_RP0_freq_mhz",
                    "rpe": f"{card}/gt_RP1_freq_mhz", "throttle": f"{card}/gt/gt0/throttle_reason",
                }
    return None


def parse_dpm_levels(text: str) -> list[int]:
    """'0: 600Mhz\\n1: 2900Mhz *' -> [600, 2900]"""
    return [int(m) for m in re.findall(r"^\s*\d+:\s*(\d+)\s*mhz", text or "", re.I | re.M)]


class PowerProfile:
    """amdgpu's workload profile (pp_power_profile_mode): how eagerly the
    power firmware ramps clocks up and down. 3D_FULL_SCREEN ramps fast for
    steady frame times, POWER_SAVING slowly for battery.

    Only used with the GPU clock on auto: a locked clock leaves the profile
    nothing to decide, so the controller makes the two exclusive.
    """

    # CUSTOM needs a parameter list; BOOTUP_DEFAULT is what "off" restores.
    SKIP = ("CUSTOM",)

    def __init__(self, dev: str | None, driver: str | None):
        self.path = f"{dev}/pp_power_profile_mode" if dev and driver == "amdgpu" else None
        self.orig = self.current()
        self.changed = False

    def _parse(self) -> list[tuple[int, str, bool]]:
        # SMU:        " 1 3D_FULL_SCREEN*"   powerplay: "  1 3D_FULL_SCREEN*:  70 ..."
        # Per-clock detail rows ("  0(  GFXCLK)") don't match.
        out = []
        for line in (sysfs.read(self.path) or "").splitlines() if self.path else []:
            m = re.match(r"\s*(\d+)\s+([A-Z0-9_]+)\s*(\*)?", line)
            if m:
                out.append((int(m.group(1)), m.group(2), bool(m.group(3))))
        return out

    def available(self) -> tuple[bool, str | None]:
        if not self.path:
            return False, "AMD GPUs only"
        if not self._parse():
            return False, "this driver has no power profiles"
        return True, None

    def options(self) -> list[str]:
        return [name for _, name, _ in self._parse() if name not in self.SKIP]

    def current(self) -> str | None:
        return next((name for _, name, on in self._parse() if on), None)

    def apply(self, name: str) -> None:
        idx = next((i for i, n, _ in self._parse() if n == name), None)
        if idx is None:
            raise OSError(f"no GPU power profile {name}")
        if self.current() != name:
            sysfs.write(self.path, idx)
        self.changed = True

    def restore(self) -> None:
        if not self.changed:
            return
        want = self.orig or "BOOTUP_DEFAULT"
        idx = next((i for i, n, _ in self._parse() if n == want), None)
        if idx is not None and self.current() != want:
            sysfs.write(self.path, idx)
        self.changed = False


class GpuClock:
    """Lock the GPU clock: auto / min / max / manual (MHz).

    - Intel (xe, i915): the render GT's min/max frequency files, bounded by
      RPn (lowest) and RP0 (highest). A lock writes min = max.
    - AMD: power_dpm_force_performance_level low / high, and for manual the
      soft clock range in pp_od_clk_voltage ("s 0 X", "s 1 X", "c").

    Auto restores what the driver had when the plugin started, so nothing is
    written until the user picks another mode.
    """

    MODES = ("auto", "min", "max", "manual")

    def __init__(self, dev: str | None, driver: str | None):
        self.dev, self.driver = dev, driver
        self.files = _intel_freq_dir(dev, driver) if dev and driver in ("xe", "i915") else None
        self.level = f"{dev}/power_dpm_force_performance_level" if driver == "amdgpu" else None
        self.od = f"{dev}/pp_od_clk_voltage" if driver == "amdgpu" else None
        self.changed = False
        self.orig: tuple[int, int] | str | None = None
        if self.files:
            lo, hi = sysfs.read_int(self.files["min"]), sysfs.read_int(self.files["max"])
            rpn, rp0 = self.hw_range()
            # min == max means a lock left over from a crash: use the hardware range.
            self.orig = (lo, hi) if lo is not None and hi is not None and lo < hi else (rpn, rp0)
        elif self.level and os.path.exists(self.level):
            cur = sysfs.read(self.level)
            self.orig = cur if cur and cur != "manual" else "auto"

    def available(self) -> tuple[bool, str | None]:
        if not self.driver:
            return False, "no supported GPU found"
        if self.files:
            return True, None
        if self.level and os.path.exists(self.level):
            return True, None
        return False, f"{self.driver} exposes no clock controls"

    def manual_ok(self) -> bool:
        if self.files:
            return True
        return bool(self.od and os.path.exists(self.od) and self.hw_range()[1])

    def hw_range(self) -> tuple[int, int]:
        if self.files:
            return sysfs.read_int(self.files["rpn"], 0), sysfs.read_int(self.files["rp0"], 0)
        if self.dev:
            od = sysfs.read(self.od) if self.od else None
            for line in (od or "").splitlines():
                # OD_RANGE "SCLK:     600Mhz       2900Mhz"
                if line.strip().upper().startswith("SCLK:"):
                    nums = [int(n) for n in re.findall(r"(\d+)\s*mhz", line, re.I)]
                    if len(nums) >= 2:
                        return min(nums), max(nums)
            levels = parse_dpm_levels(sysfs.read(f"{self.dev}/pp_dpm_sclk") or "")
            if levels:
                return min(levels), max(levels)
        return 0, 0

    def current(self) -> tuple[int | None, int | None]:
        """The min/max clock the GPU is currently allowed (Intel), or None."""
        if self.files:
            return sysfs.read_int(self.files["min"]), sysfs.read_int(self.files["max"])
        return None, None

    def throttle(self) -> list[str]:
        """Intel: why the GPU is held below the requested clock right now."""
        if not self.files:
            return []
        t = self.files["throttle"]
        if self.driver == "xe":
            # Newer kernels have one "reasons" file ("pl4", "none", ...).
            listed = sysfs.read(f"{t}/reasons")
            if listed is not None:
                return [] if listed in ("", "none") else sorted(set(listed.replace(",", " ").split()))
            if sysfs.read(f"{t}/status") != "1":
                return []
            return sorted(
                os.path.basename(p)[len("reason_"):]
                for p in glob.glob(f"{t}/reason_*")
                if sysfs.read(p) == "1"
            ) or ["unknown"]
        val = sysfs.read_int(t)
        return ["active"] if val else []

    def _target(self, mode: str, mhz: int | None) -> tuple[int, int]:
        lo, hi = self.hw_range()
        if mode == "min":
            return lo, lo
        if mode == "max":
            return hi, hi
        v = max(lo, min(hi, int(mhz or hi)))
        return v, v

    def _intel_set(self, lo: int, hi: int) -> None:
        f = self.files
        cur_lo, cur_hi = self.current()
        if cur_lo == lo and cur_hi == hi:
            return
        # Keep min <= max at every step, or the driver rejects the write.
        if cur_hi is not None and lo > cur_hi:
            sysfs.write(f["max"], hi)
            sysfs.write(f["min"], lo)
        else:
            sysfs.write(f["min"], lo)
            sysfs.write(f["max"], hi)

    def _amd_reset_od(self) -> None:
        if sysfs.read(self.level) == "manual" and self.od and os.path.exists(self.od):
            try:
                sysfs.write(self.od, "r")
                sysfs.write(self.od, "c")
            except OSError:
                pass

    def apply(self, mode: str, mhz: int | None) -> None:
        if mode == "auto":
            self.restore()
            return
        if mode == "manual" and not self.manual_ok():
            raise OSError("manual GPU clocks are not supported by this driver")
        if self.files:
            self._intel_set(*self._target(mode, mhz))
        else:
            want = {"min": "low", "max": "high", "manual": "manual"}[mode]
            if want != "manual":
                if sysfs.read(self.level) != want:
                    self._amd_reset_od()
                    sysfs.write(self.level, want)
            else:
                v, _ = self._target(mode, mhz)
                if sysfs.read(self.level) != "manual":
                    sysfs.write(self.level, "manual")
                sysfs.write(self.od, f"s 0 {v}")
                sysfs.write(self.od, f"s 1 {v}")
                sysfs.write(self.od, "c")
        self.changed = True

    def drifted(self, mode: str, mhz: int | None) -> bool:
        """True if something else changed the clocks we set."""
        if mode == "auto" or not self.changed:
            return False
        if self.files:
            return self.current() != self._target(mode, mhz)
        want = {"min": "low", "max": "high", "manual": "manual"}[mode]
        return sysfs.read(self.level) != want

    def restore(self) -> None:
        if not self.changed:
            return
        if self.files and isinstance(self.orig, tuple):
            self._intel_set(*self.orig)
        elif self.level and isinstance(self.orig, str):
            self._amd_reset_od()
            if sysfs.read(self.level) != self.orig:
                sysfs.write(self.level, self.orig)
        self.changed = False
