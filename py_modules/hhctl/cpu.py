"""CPU boost (turbo) and preferred-core scheduling.

Boost:
- amd-pstate / acpi-cpufreq: cpufreq `boost` (global, or per policy).
- intel_pstate: `intel_pstate/no_turbo` (inverted).

Preferred cores: the scheduler's ITMT switch,
/sys/kernel/debug/x86/sched_itmt_enabled. With it on, tasks go to the cores
the firmware ranks highest through CPPC (amd-pstate "prefcore", Intel Turbo
Boost Max 3.0). Hybrid Intel CPUs don't use ITMT: intel_pstate switches the
scheduler to core capacities instead, so there is nothing to toggle.
"""

import glob
import json
import os

from . import proc, sysfs

CPU_ROOT = "/sys/devices/system/cpu"
CPUFREQ = "/sys/devices/system/cpu/cpufreq"
NO_TURBO = "/sys/devices/system/cpu/intel_pstate/no_turbo"
ITMT = "/sys/kernel/debug/x86/sched_itmt_enabled"
AMD_PREFCORE = "/sys/devices/system/cpu/amd_pstate/prefcore"
SMT = "/sys/devices/system/cpu/smt/control"
HWP_BOOST = "/sys/devices/system/cpu/intel_pstate/hwp_dynamic_boost"
EPP_BOOST = "/sys/module/amd_pstate/parameters/epp_boost"


class Boost:
    def __init__(self):
        self.paths: list[str] = []
        self.inverted = False
        if os.path.exists(NO_TURBO):
            self.paths, self.inverted = [NO_TURBO], True
        elif os.path.exists(f"{CPUFREQ}/boost"):
            self.paths = [f"{CPUFREQ}/boost"]
        else:
            self.paths = sorted(glob.glob(f"{CPUFREQ}/policy[0-9]*/boost"))

    def available(self) -> tuple[bool, str | None]:
        if not self.paths:
            return False, "the cpufreq driver has no boost control"
        # As root, a read-only boost file means firmware has turbo locked off.
        if os.geteuid() == 0 and not all(sysfs.writable(p) for p in self.paths):
            return False, "boost is locked (disabled in firmware?)"
        return True, None

    def on(self) -> bool | None:
        val = sysfs.read(self.paths[0]) if self.paths else None
        if val not in ("0", "1"):
            return None
        return (val == "0") if self.inverted else (val == "1")

    def set(self, on: bool) -> None:
        raw = int(not on) if self.inverted else int(on)
        for p in self.paths:
            if sysfs.read(p) != str(raw):
                sysfs.write(p, raw)


class Prefcore:
    def _ensure_debugfs(self) -> None:
        if not os.path.ismount("/sys/kernel/debug"):
            proc.run(["mount", "-t", "debugfs", "none", "/sys/kernel/debug"], timeout=10)

    def available(self) -> tuple[bool, str | None]:
        self._ensure_debugfs()
        if os.path.exists(ITMT):
            mode = sysfs.selected("/sys/kernel/security/lockdown")
            if mode and mode != "none":
                return False, f"kernel lockdown is '{mode}' (Secure Boot); debugfs writes are blocked"
            return True, None
        if sysfs.read(AMD_PREFCORE) == "disabled":
            return False, "amd-pstate preferred cores are off (firmware setting or amd_prefcore=disable)"
        driver = sysfs.read(f"{CPUFREQ}/policy0/scaling_driver") or ""
        if driver.startswith("intel"):
            return False, "hybrid Intel CPU: the scheduler uses core capacities instead, nothing to toggle"
        if not os.path.isdir("/sys/kernel/debug/x86"):
            return False, "debugfs is not available"
        return False, "this CPU reports no core ranking"

    def on(self) -> bool | None:
        val = sysfs.read(ITMT)
        return {"1": True, "Y": True, "0": False, "N": False}.get(val or "")

    def set(self, on: bool) -> None:
        if self.on() != on:
            sysfs.write(ITMT, int(on))


class Smt:
    """SMT (Hyper-Threading) on/off through the kernel's smt/control switch."""

    def available(self) -> tuple[bool, str | None]:
        val = sysfs.read(SMT)
        if val is None:
            return False, "the kernel has no SMT control"
        if val not in ("on", "off"):
            return False, f"SMT is '{val}'"
        return True, None

    def on(self) -> bool | None:
        val = sysfs.read(SMT)
        return {"on": True, "off": False}.get(val or "")

    def set(self, on: bool) -> bool:
        """Returns True if it changed."""
        if self.on() == on:
            return False
        sysfs.write(SMT, "on" if on else "off")
        return True


class HwpBoost:
    """intel_pstate's HWP dynamic boost: raises the minimum performance for a
    while after a task wakes from I/O, then lets it drop back."""

    def available(self) -> tuple[bool, str | None]:
        if os.path.exists(HWP_BOOST):
            return True, None
        if os.path.exists(NO_TURBO):
            return False, "needs intel_pstate in active mode with HWP"
        return False, "Intel only (intel_pstate)"

    def on(self) -> bool | None:
        val = sysfs.read(HWP_BOOST)
        return {"1": True, "0": False}.get(val or "")

    def set(self, on: bool) -> None:
        if self.on() != on:
            sysfs.write(HWP_BOOST, int(on))


class EppBoost:
    """amd-pstate's per-core EPP boost: busy cores get EPP performance for a
    moment. Only in kernels carrying that patch (not upstream yet)."""

    def available(self) -> tuple[bool, str | None]:
        if not os.path.exists(EPP_BOOST):
            return False, "not in this kernel"
        if os.geteuid() == 0 and not sysfs.writable(EPP_BOOST):
            return False, "only settable at boot (amd_pstate.epp_boost=1)"
        return True, None

    def on(self) -> bool | None:
        val = sysfs.read(EPP_BOOST)
        return {"1": True, "Y": True, "0": False, "N": False}.get(val or "")

    def set(self, on: bool) -> None:
        if self.on() != on:
            sysfs.write(EPP_BOOST, int(on))


def parse_cpulist(text: str) -> list[int]:
    """'0-7,16-23' -> [0..7, 16..23]"""
    out = []
    for part in (text or "").strip().split(","):
        if not part:
            continue
        lo, _, hi = part.partition("-")
        out += range(int(lo), int(hi or lo) + 1)
    return out


class Ccx:
    """Take every CCX except CPU 0's offline (e.g. the second CCD on Strix Halo).

    CCXs are told apart by their shared L3 cache. Offline CPUs lose their
    cache/topology files, so the grouping is cached while all are online.
    """

    def __init__(self, cache_path: str):
        self.cache_path = cache_path
        self.groups = self._detect()

    @staticmethod
    def _cpus() -> list[int]:
        return sorted(int(os.path.basename(p)[3:]) for p in glob.glob(f"{CPU_ROOT}/cpu[0-9]*"))

    @staticmethod
    def _l3(cpu: int) -> list[int] | None:
        for idx in glob.glob(f"{CPU_ROOT}/cpu{cpu}/cache/index*"):
            if sysfs.read(f"{idx}/level") == "3":
                return parse_cpulist(sysfs.read(f"{idx}/shared_cpu_list") or "")
        return None

    def _detect(self) -> list[list[int]]:
        cpus = self._cpus()
        offline = [c for c in cpus if sysfs.read(f"{CPU_ROOT}/cpu{c}/online") == "0"]
        if not offline:
            groups = []
            for c in cpus:
                l3 = self._l3(c)
                if l3 and l3 not in groups:
                    groups.append(l3)
            try:
                os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
                with open(self.cache_path, "w") as f:
                    json.dump(groups, f)
            except OSError:
                pass
            return groups
        try:
            with open(self.cache_path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return []  # can't tell the CCXs apart with CPUs already offline

    def _first(self) -> list[int]:
        return next((g for g in self.groups if 0 in g), [])

    def others(self) -> list[int]:
        keep = set(self._first())
        return sorted(c for g in self.groups for c in g if c not in keep)

    def available(self) -> tuple[bool, str | None]:
        if len(self.groups) < 2:
            if not self.groups:
                return False, "CPU layout unknown (some CPUs were already offline)"
            return False, "this CPU has a single CCX (one shared L3 cache)"
        if not all(os.path.exists(f"{CPU_ROOT}/cpu{c}/online") for c in self.others()):
            return False, "CPU hotplug isn't available for these cores"
        return True, None

    def on(self) -> bool | None:
        others = self.others()
        if not others:
            return None
        return all(sysfs.read(f"{CPU_ROOT}/cpu{c}/online") == "0" for c in others)

    def set(self, off: bool) -> bool:
        """Returns True if any CPU changed state."""
        changed = False
        smt_off = sysfs.read(SMT) in ("off", "forceoff")
        for c in self.others():
            path = f"{CPU_ROOT}/cpu{c}/online"
            want = "0" if off else "1"
            if sysfs.read(path) != want:
                try:
                    sysfs.write(path, want)
                except OSError:
                    if off or not smt_off:
                        raise
                    continue  # a sibling thread: stays off while SMT is off
                changed = True
        return changed

    def keep_count(self) -> int:
        return len(self._first())

    def total(self) -> int:
        return sum(len(g) for g in self.groups)


class Util:
    """Whole-CPU utilization (%) since the previous sample, from /proc/stat."""

    STAT = "/proc/stat"

    def __init__(self):
        self.prev: tuple[int, int] | None = None
        self.value: float | None = None

    def sample(self) -> float | None:
        line = (sysfs.read(self.STAT) or "").split("\n", 1)[0].split()
        if not line or line[0] != "cpu":
            return self.value
        v = [int(x) for x in line[1:9]]
        busy, total = sum(v) - v[3] - v[4], sum(v)  # minus idle and iowait
        if self.prev and total > self.prev[1]:
            self.value = round(100 * (busy - self.prev[0]) / (total - self.prev[1]), 1)
        self.prev = (busy, total)
        return self.value


class FreqCap:
    """Cap the CPU clock through each cpufreq policy's scaling_max_freq.

    The cap is one MHz value; each policy gets it clamped to its own hardware
    range, so on hybrid Intel CPUs the P-cores and E-cores each stay within
    their limits. It's a ceiling: the CPU still drops lower when idle.
    "Off" puts every policy back to its hardware maximum.
    """

    def __init__(self):
        self.changed = False

    @staticmethod
    def _policies() -> list[str]:
        # Policies of offline CPUs stay listed but take no writes.
        return [
            p for p in sorted(glob.glob(f"{CPUFREQ}/policy[0-9]*"))
            if (sysfs.read(f"{p}/affected_cpus") or "").strip()
        ]

    def available(self) -> tuple[bool, str | None]:
        pols = self._policies()
        if not pols:
            return False, "no cpufreq policies"
        if not all(os.path.exists(f"{p}/scaling_max_freq") for p in pols):
            return False, "the cpufreq driver has no frequency limits"
        return True, None

    def range_mhz(self) -> tuple[int, int]:
        """(lowest cap valid on every core, highest clock of any core)."""
        lows, highs = [], []
        for p in self._policies():
            lo = sysfs.read_int(f"{p}/cpuinfo_min_freq")
            hi = sysfs.read_int(f"{p}/cpuinfo_max_freq")
            if lo and hi:
                lows.append(lo)
                highs.append(hi)
        if not highs:
            return 0, 0
        return max(lows) // 1000, max(highs) // 1000

    def limits_mhz(self) -> list[int]:
        """Current scaling_max_freq of each distinct core type, high first."""
        vals = {sysfs.read_int(f"{p}/scaling_max_freq") for p in self._policies()}
        return sorted((v // 1000 for v in vals if v), reverse=True)

    def peak_mhz(self) -> int | None:
        vals = [sysfs.read_int(f"{p}/scaling_cur_freq") for p in self._policies()]
        vals = [v for v in vals if v]
        return max(vals) // 1000 if vals else None

    def avg_mhz(self) -> int | None:
        vals = [sysfs.read_int(f"{p}/scaling_cur_freq") for p in self._policies()]
        vals = [v for v in vals if v]
        return sum(vals) // len(vals) // 1000 if vals else None

    def _write_all(self, khz_for) -> None:
        for p in self._policies():
            want = khz_for(p)
            if want and sysfs.read_int(f"{p}/scaling_max_freq") != want:
                sysfs.write(f"{p}/scaling_max_freq", want)

    def apply(self, mhz: int) -> None:
        def khz(p):
            lo = sysfs.read_int(f"{p}/cpuinfo_min_freq") or 0
            hi = sysfs.read_int(f"{p}/cpuinfo_max_freq") or 0
            return max(lo, min(hi, int(mhz) * 1000)) if hi else None

        self._write_all(khz)
        self.changed = True

    def restore(self) -> None:
        if not self.changed:
            return
        self._write_all(lambda p: sysfs.read_int(f"{p}/cpuinfo_max_freq"))
        self.changed = False


MSR_DEV = "/dev/cpu/0/msr"
PERF_LIMIT_REASONS = 0x64F  # MSR_CORE_PERF_LIMIT_REASONS, Intel client CPUs
LIMIT_BITS = {0: "prochot", 1: "thermal", 5: "ratl", 6: "vr_thermalert", 7: "vr_tdc", 8: "other", 10: "pl1", 11: "pl2"}


class LimitReasons:
    """Why an Intel CPU runs below its requested clock, from MSR 0x64F.

    Returns None where it can't be read (AMD, no msr module); the caller
    falls back to inferring the reason from power and temperature.
    """

    def __init__(self):
        cpuinfo = sysfs.read("/proc/cpuinfo") or ""
        self.intel = "GenuineIntel" in cpuinfo
        self.tried_modprobe = False

    def read(self) -> list[str] | None:
        if not self.intel:
            return None
        if not os.path.exists(MSR_DEV) and not self.tried_modprobe:
            self.tried_modprobe = True
            proc.run(["modprobe", "msr"], timeout=10)
        try:
            fd = os.open(MSR_DEV, os.O_RDONLY)
            try:
                val = int.from_bytes(os.pread(fd, 8, PERF_LIMIT_REASONS), "little")
            finally:
                os.close(fd)
        except OSError:
            return None
        return [name for bit, name in LIMIT_BITS.items() if val >> bit & 1]
