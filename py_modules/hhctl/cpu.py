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
import os
import subprocess

from . import sysfs

CPUFREQ = "/sys/devices/system/cpu/cpufreq"
NO_TURBO = "/sys/devices/system/cpu/intel_pstate/no_turbo"
ITMT = "/sys/kernel/debug/x86/sched_itmt_enabled"
AMD_PREFCORE = "/sys/devices/system/cpu/amd_pstate/prefcore"


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
            subprocess.run(["mount", "-t", "debugfs", "none", "/sys/kernel/debug"],
                           capture_output=True, timeout=10)

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
