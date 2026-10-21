"""Ties the backends together: settings, apply, periodic tick and state."""

import copy
import glob
import json
import logging
import os
import time

from . import battery, fan, rgb, tdp
from .device import detect

log = logging.getLogger("hhctl")

DEFAULTS = {
    # Nothing is touched until the user enables a section.
    "tdp": {"enabled": False, "watts": None, "boost": True},
    "fan": {"mode": "auto", "percent": 50, "curve": "balanced"},
    "rgb": {"enabled": False, "mode": "solid", "hue": 200, "sat": 100, "bright": 70, "effect": None},
    "battery": {"limit": 100, "bypass": "off"},
}

CONFLICTS = ("hhd", "powerstation", "inputplumber")


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def running_services() -> list[str]:
    found = set()
    for comm in glob.glob("/proc/[0-9]*/comm"):
        try:
            with open(comm) as f:
                name = f.read().strip()
        except OSError:
            continue
        for c in CONFLICTS:
            if name == c or name.startswith(c + "@"):
                found.add(c)
    cmdlines = glob.glob("/proc/[0-9]*/cmdline")
    if "hhd" not in found:
        for cl in cmdlines:
            try:
                with open(cl, "rb") as f:
                    argv = f.read().split(b"\0")
            except OSError:
                continue
            if any(os.path.basename(a) == b"hhd" for a in argv[:2]):
                found.add("hhd")
                break
    return sorted(found)


def _sleep_offset() -> float:
    return time.clock_gettime(time.CLOCK_BOOTTIME) - time.clock_gettime(time.CLOCK_MONOTONIC)


class Controller:
    def __init__(self, settings_path: str):
        self.settings_path = settings_path
        self.dev = detect()
        self.tdp = tdp.find_backend(self.dev)
        self.fan, self.fan_why = fan.find_backend(self.dev)
        self.bat = battery.Battery(self.dev)
        self.rgb = rgb.find_backend(self.dev)
        self.settings = self._load()
        self.errors: dict[str, str] = {}
        self.fan_pct: int | None = None
        self.safety = False
        self.sleep_offset = _sleep_offset()
        self.ac = battery.ac_online()
        self.last_tdp_apply = 0.0
        log.info(
            "Device %s (%s/%s): tdp=%s fan=%s rgb=%s battery limit=%s",
            self.dev.name, self.dev.vendor, self.dev.board,
            getattr(self.tdp, "name", None), getattr(self.fan, "name", None),
            getattr(self.rgb, "name", None), self.bat.limit_mode(),
        )

    # --- settings ---------------------------------------------------------

    def _load(self) -> dict:
        try:
            with open(self.settings_path) as f:
                return _merge(DEFAULTS, json.load(f))
        except (OSError, ValueError):
            return copy.deepcopy(DEFAULTS)

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.settings_path), exist_ok=True)
        tmp = self.settings_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.settings, f, indent=2)
        os.replace(tmp, self.settings_path)

    def update(self, section: str, values: dict) -> dict:
        self.settings[section].update(values)
        self.save()
        getattr(self, f"apply_{section}")()
        return self.state()

    def _guard(self, section: str, fn) -> None:
        try:
            fn()
            self.errors.pop(section, None)
        except Exception as e:  # report every hardware error to the UI
            log.exception("%s apply failed", section)
            self.errors[section] = str(e)

    # --- apply ------------------------------------------------------------

    def apply_all(self) -> None:
        self.apply_tdp()
        self.apply_fan()
        self.apply_battery()
        self.apply_rgb()

    def apply_tdp(self) -> None:
        s = self.settings["tdp"]
        if not self.tdp or not s["enabled"]:
            return
        watts = s["watts"] or self.dev.tdp_default
        watts = max(self.dev.tdp_min, min(self.dev.tdp_max, int(watts)))
        self._guard("tdp", lambda: self.tdp.apply(self.dev, watts, s["boost"]))
        self.last_tdp_apply = time.monotonic()

    def apply_fan(self) -> None:
        if not self.fan:
            return
        if self.settings["fan"]["mode"] == "auto":
            self.fan_pct = None
            self._guard("fan", self.fan.set_auto)
        else:
            self.fan_pct = None
            self.tick_fan()

    def apply_battery(self) -> None:
        s = self.settings["battery"]
        bypass = s["bypass"] if s["bypass"] in self.bat.bypass_options() else "off"

        def go():
            if self.bat.limit_mode():
                self.bat.set_limit(int(s["limit"]))
            if self.bat.behaviours():
                if bypass != "off":
                    self.bat.set_bypass(bypass)
                elif self.bat.limit_mode() == "emulated":
                    # Start from a known state, then let tick() decide.
                    self.bat.set_behaviour("auto")
                    self.bat.tick(int(s["limit"]), "off")
                else:
                    self.bat.set_bypass("off")

        self._guard("battery", go)

    def apply_rgb(self) -> None:
        s = self.settings["rgb"]
        if not self.rgb or not s["enabled"]:
            return
        self._guard("rgb", lambda: self.rgb.apply(s["mode"], s["hue"], s["sat"], s["bright"], s["effect"]))

    # --- periodic ---------------------------------------------------------

    def tick_fan(self) -> None:
        s = self.settings["fan"]
        if not self.fan or s["mode"] == "auto":
            return
        temp = fan.cpu_temp()
        if s["mode"] == "curve":
            target = fan.interpolate(fan.CURVES.get(s["curve"], fan.CURVES["balanced"]), temp or 100)
        else:
            target = int(s["percent"])
        if temp is None or temp >= fan.SAFETY_TEMP:
            self.safety = True
        elif temp <= fan.SAFETY_RELEASE:
            self.safety = False
        if self.safety:
            target = 100
        if self.fan_pct is not None and target < self.fan_pct:
            target = max(target, self.fan_pct - 5)  # ramp down gently
        if target != self.fan_pct:
            self._guard("fan", lambda: self.fan.set_percent(target))
            self.fan_pct = target

    def tick(self) -> None:
        offset = _sleep_offset()
        resumed = offset - self.sleep_offset > 2
        self.sleep_offset = offset
        ac = battery.ac_online()
        ac_changed = ac != self.ac
        self.ac = ac
        if resumed:
            log.info("Resume detected, re-applying settings")
            self.fan_pct = None
            self.apply_all()
            return
        # Firmware tends to reset power limits on AC changes; ryzenadj
        # limits also drift, so re-assert them every minute.
        if ac_changed or (isinstance(self.tdp, tdp.RyzenAdj) and time.monotonic() - self.last_tdp_apply > 60):
            self.apply_tdp()
        self.tick_fan()
        b = self.settings["battery"]
        self._guard("battery", lambda: self.bat.tick(int(b["limit"]), b["bypass"]))

    def restore(self) -> None:
        """Hand control back to firmware when the plugin unloads."""
        if self.fan and self.settings["fan"]["mode"] != "auto":
            self._guard("fan", self.fan.set_auto)
        if self.bat.limit_mode() == "emulated" and self.bat.inhibited and self.settings["battery"]["bypass"] == "off":
            self._guard("battery", lambda: self.bat.set_behaviour("auto"))

    # --- state for the UI -------------------------------------------------

    def state(self) -> dict:
        d = self.dev
        cur = None
        if self.tdp:
            try:
                cur = self.tdp.current()
            except Exception:
                cur = None
        rpm = None
        if self.fan:
            try:
                rpm = self.fan.rpm()
            except Exception:
                rpm = None
        return {
            "device": {"name": d.name, "board": d.board, "vendor": d.vendor, "notes": d.notes},
            "conflicts": running_services(),
            "settings": self.settings,
            "errors": self.errors,
            "tdp": {
                "backend": getattr(self.tdp, "name", None),
                "min": d.tdp_min, "max": d.tdp_max, "default": d.tdp_default,
                "current": cur,
            },
            "fan": {
                "backend": getattr(self.fan, "name", None),
                "unavailable": self.fan_why,
                "curves": list(fan.CURVES),
                "rpm": rpm,
                "percent": self.fan_pct,
                "temp": fan.cpu_temp(),
                "safety": self.safety,
            },
            "battery": {
                "limit_mode": self.bat.limit_mode(),
                "bypass_options": self.bat.bypass_options(),
                "unavailable": None if self.bat.behaviours() else (self.bat.ec_why or "no charge control driver"),
                **self.bat.status(),
            },
            "rgb": {
                "backend": getattr(self.rgb, "name", None),
                "modes": getattr(self.rgb, "modes", []),
                "effects": getattr(self.rgb, "effects", []),
            },
        }
