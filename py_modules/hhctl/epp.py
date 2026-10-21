"""Energy Performance Preference (EPP) through cpufreq.

Works with amd-pstate-epp (AMD) and intel_pstate with HWP (Intel), both in
"active" mode. Under the `performance` governor both drivers pin EPP to
`performance`, so any other value needs the `powersave` governor first.

"Off" restores what was there when the plugin started: the pstate mode,
then each policy's governor and EPP.
"""

import glob

from . import sysfs

CPUFREQ = "/sys/devices/system/cpu/cpufreq"
STATUS = {
    "amd-pstate-epp": "/sys/devices/system/cpu/amd_pstate/status",
    "amd-pstate": "/sys/devices/system/cpu/amd_pstate/status",
    "intel_pstate": "/sys/devices/system/cpu/intel_pstate/status",
    "intel_cpufreq": "/sys/devices/system/cpu/intel_pstate/status",
}
STANDARD = ["performance", "balance_performance", "balance_power", "power"]


def _policies() -> list[str]:
    return sorted(glob.glob(f"{CPUFREQ}/policy[0-9]*"), key=lambda p: int(p.rsplit("policy", 1)[1]))


class Epp:
    def __init__(self):
        self.policies = _policies()
        p0 = self.policies[0] if self.policies else ""
        self.driver = sysfs.read(f"{p0}/scaling_driver") if p0 else None
        self.status_path = STATUS.get(self.driver or "")
        self.orig_status = sysfs.read(self.status_path) if self.status_path else None
        self.orig = {
            p: (sysfs.read(f"{p}/scaling_governor"), sysfs.read(f"{p}/energy_performance_preference"))
            for p in self.policies
        }
        self.changed = False

    def available(self) -> tuple[bool, str | None]:
        if not self.policies:
            return False, "no cpufreq policies"
        if self.status_path is None:
            return False, f"cpufreq driver '{self.driver}' has no EPP"
        return True, None

    def options(self) -> list[str]:
        p0 = self.policies[0]
        avail = sysfs.choices(f"{p0}/energy_performance_available_preferences")
        # The performance governor hides everything but "performance".
        if len(avail) <= 1:
            return STANDARD
        return [v for v in STANDARD if v in avail] + [v for v in avail if v not in STANDARD and v != "default"]

    def current(self) -> str | None:
        return sysfs.read(f"{self.policies[0]}/energy_performance_preference") if self.policies else None

    def _ensure_active(self) -> None:
        if self.status_path and sysfs.read(self.status_path) not in ("active", None):
            sysfs.write(self.status_path, "active")
            self.policies = _policies()  # policies are recreated on a mode switch

    def apply(self, value: str) -> None:
        self._ensure_active()
        for p in self.policies:
            gov = sysfs.read(f"{p}/scaling_governor")
            if value != "performance" and gov == "performance":
                sysfs.write(f"{p}/scaling_governor", "powersave")
            sysfs.write(f"{p}/energy_performance_preference", value)
        self.changed = True

    def drifted(self, value: str) -> bool:
        return any(sysfs.read(f"{p}/energy_performance_preference") != value for p in self.policies)

    def restore(self) -> None:
        if not self.changed:
            return
        if self.status_path and self.orig_status and sysfs.read(self.status_path) != self.orig_status:
            sysfs.write(self.status_path, self.orig_status)
            self.policies = _policies()
        for p, (gov, pref) in self.orig.items():
            try:
                if gov and sysfs.read(f"{p}/scaling_governor") != gov:
                    sysfs.write(f"{p}/scaling_governor", gov)
                if pref and sysfs.read(f"{p}/energy_performance_preference") != pref:
                    sysfs.write(f"{p}/energy_performance_preference", pref)
            except OSError:
                pass  # policy vanished or is read-only in the restored mode
        self.changed = False
