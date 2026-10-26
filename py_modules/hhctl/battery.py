"""Charge limit and charge bypass.

Native path: power_supply charge_control_end_threshold / charge_behaviour
(oxpec, ayaneo-ec). Fallback: raw EC registers. If a device can inhibit
charging but has no %-threshold (AYANEO), the limit is emulated by the
plugin's background loop.
"""

import glob
import math
import time

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
    def __init__(self, dev: Device, allow_ec: bool):
        self.dev = dev
        self.bat = find_battery()
        self.draw: tuple[float, float] | None = None  # (smoothed draw, when)
        self.ec_ok = False
        self.ec_why = ""
        self.native_limit = bool(self.bat) and sysfs.writable(f"{self.bat}/charge_control_end_threshold")
        self.native_behaviour = bool(self.bat) and sysfs.writable(f"{self.bat}/charge_behaviour")
        if not (self.native_limit and self.native_behaviour) and dev.ec_charge:
            if not allow_ec:
                self.ec_why = "no kernel charge driver; enable raw EC writes under Advanced to use the EC fallback"
            else:
                self.ec_ok, self.ec_why = ec.available(dev.ec_mmio, dev.ec_check_regs())
                if self.ec_ok:
                    try:
                        self._probe()
                    except OSError as e:
                        self.disable_ec(str(e))
        self.inhibited = False

    def _probe(self) -> None:
        c = self.dev.ec_charge
        if not self.native_behaviour:
            ec.check(c.behaviour_reg, c.behaviour_values())
        if not self.native_limit and c.limit_reg is not None:
            ec.check(c.limit_reg, c.limit_values())

    def disable_ec(self, why: str) -> None:
        self.ec_ok = False
        self.ec_why = why

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
            ec.write(c.behaviour_reg, raw, c.behaviour_values())
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
            c = self.dev.ec_charge
            ec.write(c.limit_reg, pct, c.limit_values())
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

    def hold_for_sleep(self, limit: int, bypass: str) -> bool:
        """Emulated limit, just before a suspend: nothing runs tick() while it
        sleeps, so a charger would fill it to 100 %. At (or just under) the
        limit charging is paused now, and the EC keeps that while asleep.
        Further below, it charges on (it can't stop at the limit asleep).
        True if paused."""
        if self.limit_mode() != "emulated" or bypass != "off" or limit >= 100:
            return False
        cap = self.capacity()
        if cap is None or cap < limit - HYSTERESIS:
            return False
        self.set_behaviour("inhibit-charge")  # written again even if paused: firmware may have reset it
        return True

    def raw_behaviour(self) -> int | None:
        """The EC's charge register, for the wake report."""
        if self.native_behaviour or not (self.ec_ok and self.dev.ec_charge):
            return None
        try:
            return ec.read(self.dev.ec_charge.behaviour_reg)
        except Exception:
            return None

    # --- telemetry --------------------------------------------------------

    def capacity(self) -> int | None:
        return sysfs.read_int(f"{self.bat}/capacity") if self.bat else None

    def remaining_min(self) -> int | None:
        """Minutes left on battery, like MangoHud's: what's left divided by
        the draw. The draw is smoothed over ~30 s so the time doesn't jump
        with every frame-rate spike."""
        if sysfs.read(f"{self.bat}/status") != "Discharging":
            self.draw = None
            return None
        # Energy (µWh / µW) where the battery reports it, else charge (µAh / µA).
        left, draw = sysfs.read_int(f"{self.bat}/energy_now"), sysfs.read_int(f"{self.bat}/power_now")
        if left is None or not draw:
            left, draw = sysfs.read_int(f"{self.bat}/charge_now"), sysfs.read_int(f"{self.bat}/current_now")
        if left is None or not draw:
            return None
        now, draw = time.monotonic(), abs(draw)
        if self.draw:
            k = 1 - math.exp(-(now - self.draw[1]) / 30)
            draw = self.draw[0] + k * (draw - self.draw[0])
        self.draw = (draw, now)
        return int(60 * left / draw)

    def status(self) -> dict:
        if not self.bat:
            return {}
        power = sysfs.read_int(f"{self.bat}/power_now")
        if power is None:
            cur = sysfs.read_int(f"{self.bat}/current_now")
            volt = sysfs.read_int(f"{self.bat}/voltage_now")
            power = cur * volt // 1_000_000 if cur is not None and volt is not None else None
        return {
            "capacity": self.capacity(),
            "status": sysfs.read(f"{self.bat}/status"),
            "remaining_min": self.remaining_min(),
            # 0 W is real information (bypass working), so only None hides it.
            "power_w": round(abs(power) / 1_000_000, 1) if power is not None else None,
            "ac": ac_online(),
            # Charging held off by us. Some firmware (AYANEO NEXT 2) keeps
            # reporting "Charging" while bypassed; the 0 W reading is the truth.
            "inhibited": self.inhibited,
        }
