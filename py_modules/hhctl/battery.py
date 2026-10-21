"""Charge limit and charge bypass.

Native path: power_supply charge_control_end_threshold / charge_behaviour
(oxpec, ayaneo-ec). Fallback: raw EC registers. If a device can inhibit
charging but has no %-threshold (AYANEO), the limit is emulated by the
plugin's background loop.
"""

import glob

from . import ec, sysfs
from .device import Device

HYSTERESIS = 3


def find_battery() -> str | None:
    for bat in sorted(glob.glob("/sys/class/power_supply/BAT*")):
        if sysfs.read(f"{bat}/type") == "Battery":
            return bat
    return None


def ac_online() -> bool:
    for ps in glob.glob("/sys/class/power_supply/*"):
        if sysfs.read(f"{ps}/type") in ("Mains", "USB") and sysfs.read(f"{ps}/online") == "1":
            return True
    return False


class Battery:
    def __init__(self, dev: Device):
        self.dev = dev
        self.bat = find_battery()
        self.ec_ok = False
        self.ec_why = ""
        self.native_limit = bool(self.bat) and sysfs.writable(f"{self.bat}/charge_control_end_threshold")
        self.native_behaviour = bool(self.bat) and sysfs.writable(f"{self.bat}/charge_behaviour")
        if not (self.native_limit and self.native_behaviour) and dev.ec_charge:
            self.ec_ok, self.ec_why = ec.available()
        self.inhibited = False

    # --- capabilities -----------------------------------------------------

    def behaviours(self) -> list[str]:
        if self.native_behaviour:
            return sysfs.choices(f"{self.bat}/charge_behaviour")
        if self.ec_ok and self.dev.ec_charge:
            out = ["auto", "inhibit-charge"]
            if self.dev.ec_charge.inhibit_awake is not None:
                out.append("inhibit-charge-awake")
            return out
        return []

    def limit_mode(self) -> str | None:
        """'native', 'ec', 'emulated' or None."""
        if self.native_limit:
            return "native"
        if self.ec_ok and self.dev.ec_charge and self.dev.ec_charge.limit_reg is not None:
            return "ec"
        if "inhibit-charge" in self.behaviours():
            return "emulated"
        return None

    def bypass_options(self) -> list[str]:
        b = self.behaviours()
        out = ["off"] if b else []
        if "inhibit-charge-awake" in b:
            out.append("awake")
        if "inhibit-charge" in b:
            out.append("always")
        return out

    # --- writes -----------------------------------------------------------

    def set_behaviour(self, value: str) -> None:
        if self.native_behaviour:
            sysfs.write(f"{self.bat}/charge_behaviour", value)
        elif self.ec_ok and self.dev.ec_charge:
            c = self.dev.ec_charge
            raw = {"auto": c.auto, "inhibit-charge": c.inhibit, "inhibit-charge-awake": c.inhibit_awake}[value]
            ec.write(c.behaviour_reg, raw)
        else:
            raise OSError("charge behaviour not supported")
        self.inhibited = value != "auto"

    def set_bypass(self, bypass: str) -> None:
        self.set_behaviour({"off": "auto", "awake": "inhibit-charge-awake", "always": "inhibit-charge"}[bypass])

    def set_limit(self, pct: int) -> None:
        mode = self.limit_mode()
        if mode == "native":
            sysfs.write(f"{self.bat}/charge_control_end_threshold", pct)
        elif mode == "ec":
            ec.write(self.dev.ec_charge.limit_reg, pct)
        # emulated: enforced by tick()

    def tick(self, limit: int, bypass: str) -> None:
        """Emulated limit: inhibit at the limit, resume below limit - hysteresis."""
        if self.limit_mode() != "emulated" or bypass != "off" or not self.bat:
            return
        cap = self.capacity()
        if cap is None:
            return
        if limit >= 100:
            if self.inhibited:
                self.set_behaviour("auto")
        elif cap >= limit and not self.inhibited:
            self.set_behaviour("inhibit-charge")
        elif cap <= limit - HYSTERESIS and self.inhibited:
            self.set_behaviour("auto")

    # --- telemetry --------------------------------------------------------

    def capacity(self) -> int | None:
        return sysfs.read_int(f"{self.bat}/capacity") if self.bat else None

    def status(self) -> dict:
        if not self.bat:
            return {}
        power = sysfs.read_int(f"{self.bat}/power_now")
        if power is None:
            cur = sysfs.read_int(f"{self.bat}/current_now")
            volt = sysfs.read_int(f"{self.bat}/voltage_now")
            power = cur * volt // 1_000_000 if cur and volt else None
        return {
            "capacity": self.capacity(),
            "status": sysfs.read(f"{self.bat}/status"),
            "power_w": round(power / 1_000_000, 1) if power else None,
            "ac": ac_online(),
        }
