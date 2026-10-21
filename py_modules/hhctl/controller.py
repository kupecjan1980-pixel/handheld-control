"""Ties the backends together: settings, apply, periodic tick and state."""

import atexit
import copy
import glob
import json
import logging
import os
import shutil
import signal
import subprocess
import threading
import time

from . import background, backlight, battery, cpu, diag, ec, epp, fan, gamescope, gpu, kargs, proc, rgb, sysfs, tdp, watchdog
from .device import detect

log = logging.getLogger("hhctl")

DEFAULTS = {
    # Nothing is touched until the user enables a section.
    "tdp": {"enabled": False, "watts": None, "boost": True},
    "epp": {"enabled": False, "value": "balance_performance"},
    # None = leave as the system set it; True/False once the user toggles.
    "cpu": {"boost": None, "prefcore": None},
    # Game Mode HDR: "steam" leaves gamescope alone; others only after confirm.
    "gamescope": {"hdr": "steam"},
    # Game Mode brightness through gamescope's SDR-in-HDR nits. The first
    # change is a trial that must be confirmed; after that it applies directly.
    "sdr_brightness": {"enabled": False, "nits": 203, "confirmed": False},
    "ui": {"theme": "default", "bg": None, "bg_dim": 55},
    "fan": {"mode": "auto", "percent": 50, "curve": "balanced"},
    "rgb": {"enabled": False, "mode": "solid", "hue": 200, "sat": 100, "bright": 70, "effect": None},
    "battery": {"limit": 100, "bypass": "off"},
    # Raw EC writes are only used when the user opts in.
    "ec": {"enabled": False},
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
    def __init__(self, settings_path: str, user_home: str | None = None, version: str | None = None):
        self.settings_path = settings_path
        self.user_home = user_home
        self.version = version
        self.btest = {"running": False, "phase": None, "phases": 0, "verdict": None, "report": None, "answer": None}
        self.btest_sections: list[tuple[str, list[str]]] = []
        self.btest_mode = "desktop"
        self.dev = detect()
        self.settings = self._load()
        self.tdp = tdp.find_backend(self.dev)
        self.rgb = rgb.find_backend(self.dev)
        self.gpu = gpu.Gpu()
        self.epp = epp.Epp()  # snapshots the original EPP before anything is applied
        self.last_epp_check = 0.0
        self.boost = cpu.Boost()
        self.prefcore = cpu.Prefcore()
        self.gs: gamescope.Gamescope | None = None
        self.gs_seen = 0.0  # when the current gamescope process was first seen
        self.gs_applied_pid: int | None = None
        self.gs_pending: dict | None = None  # {"mode", "prev", "deadline"}
        self.gs_last_check = 0.0
        self.sb_pending: dict | None = None  # {"prev": nits|None, "deadline"}
        self.sb_applied_pid: int | None = None
        self.sb_last_check = 0.0
        if self.dev.gamescope_hdr_blocked and self.settings["gamescope"]["hdr"] != "steam":
            # A mode saved before this device was blocked must never be re-applied.
            self.settings["gamescope"]["hdr"] = "steam"
            self.save()
        self.backlight = backlight.Backlight.find()
        self.kargs = kargs.detect() if self.gpu.driver in ("xe", "i915") else None
        self.display_busy = False
        self.display_msg: str | None = None
        self.kargs_configured: set[str] | None = None  # cached; refreshed after edits
        self._scan()
        self.errors: dict[str, str] = {}
        self.fan_pct: int | None = None
        self.safety = False
        self.sleep_offset = _sleep_offset()
        self.ac = battery.ac_online()
        self.last_tdp_apply = 0.0
        self.watchdog_warned = False
        log.info(
            "Device %s (%s/%s): tdp=%s fan=%s rgb=%s battery limit=%s",
            self.dev.name, self.dev.vendor, self.dev.board,
            getattr(self.tdp, "name", None), getattr(self.fan, "name", None),
            getattr(self.rgb, "name", None), self.bat.limit_mode(),
        )

    def _scan(self) -> None:
        allow_ec = bool(self.settings["ec"]["enabled"])
        self.fan, self.fan_why = fan.find_backend(self.dev, allow_ec)
        self.bat = battery.Battery(self.dev, allow_ec)

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
        except ec.EcError as e:
            # The EC doesn't match its register map: stop touching it.
            log.error("%s: %s", section, e)
            self.errors[section] = str(e)
            if section == "fan" and self.fan:
                spec = self.fan.restore_spec()
                self.fan, self.fan_why = None, str(e)
                try:
                    watchdog.restore(spec)
                except OSError:
                    pass
                self._watchdog_stop()
            elif section == "battery":
                self.bat.disable_ec(str(e))
        except Exception as e:  # report every hardware error to the UI
            log.exception("%s apply failed", section)
            self.errors[section] = str(e)

    # --- fan watchdog -----------------------------------------------------

    def _heartbeat(self) -> None:
        with open(watchdog.HEARTBEAT, "w") as f:
            f.write(str(time.monotonic()))

    def _watchdog_alive(self) -> bool:
        try:
            with open(watchdog.PIDFILE) as f:
                pid = int(f.read())
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                return b"watchdog.py" in f.read()
        except (OSError, ValueError):
            return False

    def _watchdog_start(self) -> None:
        """Arm the watchdog while the fan is held in manual mode."""
        if not self.fan:
            return
        os.makedirs(watchdog.RUN_DIR, exist_ok=True)
        self._heartbeat()
        with open(watchdog.STATE, "w") as f:
            json.dump(self.fan.restore_spec(), f)
        if self._watchdog_alive():
            return
        # Decky's own interpreter is frozen, so use the system python.
        path = proc.env()["PATH"]
        py = shutil.which("python3", path=path)
        if not py:
            if not self.watchdog_warned:
                log.warning("python3 not found; fan watchdog unavailable")
                self.watchdog_warned = True
            return
        cmd = [py, "-I", watchdog.__file__]
        if shutil.which("systemd-run", path=path):
            proc.run(["systemctl", "reset-failed", "hhctl-fan-watchdog"], timeout=10)
            res = proc.run(
                ["systemd-run", "--unit=hhctl-fan-watchdog", "--collect", "--quiet", *cmd],
                text=True, timeout=10,
            )
            if res.returncode == 0:
                return
            log.warning("systemd-run failed (%s), starting watchdog directly", res.stderr.strip())
        proc.popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _watchdog_stop(self) -> None:
        # The watchdog exits by itself once the state file is gone.
        try:
            os.unlink(watchdog.STATE)
        except OSError:
            pass

    def install_exit_hooks(self) -> None:
        atexit.register(self.restore)
        try:
            prev = signal.getsignal(signal.SIGTERM)

            def on_term(signum, frame):
                self.restore()
                if callable(prev):
                    prev(signum, frame)
                else:
                    signal.signal(signal.SIGTERM, signal.SIG_DFL)
                    os.kill(os.getpid(), signal.SIGTERM)

            signal.signal(signal.SIGTERM, on_term)
        except ValueError:
            pass  # not on the main thread; atexit and the watchdog still cover us

    # --- apply ------------------------------------------------------------

    def apply_ui(self) -> None:
        pass  # purely cosmetic; the frontend reads it from settings

    def _ui_dir(self) -> str:
        return os.path.dirname(self.settings_path)

    def set_background(self, path: str) -> dict:
        try:
            self.settings["ui"]["bg"] = background.save(self._ui_dir(), path)
            self.save()
            self.errors.pop("ui", None)
        except OSError as e:
            self.errors["ui"] = str(e)
        return self.state()

    def clear_background(self) -> dict:
        background.clear(self._ui_dir())
        self.settings["ui"]["bg"] = None
        self.save()
        return self.state()

    def background_data(self) -> str | None:
        if not self.settings["ui"]["bg"]:
            return None
        try:
            return background.data_url(self._ui_dir())
        except OSError:
            return None

    def apply_all(self) -> None:
        self.apply_tdp()
        self.apply_epp()
        self.apply_cpu()
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

    def apply_epp(self) -> None:
        if not self.epp.available()[0]:
            return
        s = self.settings["epp"]
        if s["enabled"]:
            self._guard("epp", lambda: self.epp.apply(s["value"]))
        else:
            self._guard("epp", self.epp.restore)

    def apply_cpu(self) -> None:
        s = self.settings["cpu"]
        for key, ctl in (("boost", self.boost), ("prefcore", self.prefcore)):
            if s[key] is not None and ctl.available()[0]:
                self._guard("cpu", lambda: ctl.set(bool(s[key])))

    def apply_fan(self) -> None:
        if not self.fan:
            return
        self.fan_pct = None
        if self.settings["fan"]["mode"] == "auto":
            self._guard("fan", self.fan.set_auto)
            self._watchdog_stop()
        else:
            self.tick_fan()

    def apply_ec(self) -> None:
        # Hand everything back to firmware before swapping backends.
        self.restore()
        self._scan()
        self.apply_fan()
        self.apply_battery()

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

    # --- display ----------------------------------------------------------

    def set_brightness(self, pct: int) -> dict:
        if self.backlight:
            self._guard("display", lambda: self.backlight.set_percent(pct))
        return self.state()

    # --- Game Mode HDR -------------------------------------------------------

    GS_CONFIRM = 20.0  # seconds to confirm before a change is reverted
    GS_SETTLE = 15.0  # wait after gamescope starts so Steam sets its own HDR first

    def _gs_refresh(self) -> None:
        if self.gs and self.gs.alive():
            return
        self.gs = gamescope.Gamescope.find()
        self.gs_seen = time.monotonic()
        self.gs_pending = None  # a new session already runs Steam's defaults
        if self.sb_pending:
            # The trial's session is gone, and it was never confirmed.
            self.sb_pending = None
            self.settings["sdr_brightness"]["enabled"] = False
            self.save()

    def set_gamescope_hdr(self, mode: str) -> dict:
        self._gs_refresh()
        if self.dev.gamescope_hdr_blocked and mode != "steam":
            self.errors["gamescope"] = self.dev.gamescope_hdr_blocked
            return self.state()
        if not self.gs or mode not in self.gs.modes():
            self.errors["gamescope"] = "Game Mode (gamescope) is not running, or doesn't support this"
            return self.state()
        prev = self.gs_pending["prev"] if self.gs_pending else self.settings["gamescope"]["hdr"]
        if mode == prev:
            self.gs_pending = None
            self._guard("gamescope", lambda: self.gs.apply(mode))
            return self.state()
        self._guard("gamescope", lambda: self.gs.apply(mode))
        if "gamescope" not in self.errors:
            # Not saved until confirmed: a black screen means no confirm, so it reverts.
            self.gs_pending = {"mode": mode, "prev": prev, "deadline": time.monotonic() + self.GS_CONFIRM}
        return self.state()

    def confirm_gamescope_hdr(self, keep: bool) -> dict:
        p = self.gs_pending
        if not p:
            return self.state()
        self.gs_pending = None
        if keep:
            self.settings["gamescope"]["hdr"] = p["mode"]
            self.save()
            self.gs_applied_pid = self.gs.pid if self.gs else None
            self.gs_last_check = time.monotonic()
        elif self.gs:
            self._guard("gamescope", lambda: self.gs.apply(p["prev"]))
        return self.state()

    def _brightness_floor(self) -> None:
        """Keep the panel visible while we hold Game Mode in SDR.

        With HDR off, Steam's brightness slider reaches this panel for the
        first time and can set it to (near) zero, which looks like the screen
        turned off. Only acts while the plugin has changed gamescope's HDR.
        """
        if not self.backlight or not self.gs:
            return
        if not self.gs_pending and self.settings["gamescope"]["hdr"] == "steam":
            return
        pct = self.backlight.percent()
        if pct is not None and pct < backlight.MIN_PCT:
            log.info("Backlight at %s%% in Game Mode SDR, raising to %s%%", pct, backlight.MIN_PCT)
            self._guard("display", lambda: self.backlight.set_percent(backlight.MIN_PCT))

    def tick_gamescope(self) -> None:
        self._gs_refresh()
        self._brightness_floor()
        now = time.monotonic()
        self.tick_sdr_brightness(now)
        p = self.gs_pending
        if p and now > p["deadline"]:
            self.gs_pending = None
            if self.gs:
                self._guard("gamescope", lambda: self.gs.apply(p["prev"]))
            self.errors["gamescope"] = "Not confirmed within 20 s, so it was reverted."
            return
        mode = self.settings["gamescope"]["hdr"]
        if self.dev.gamescope_hdr_blocked or not self.gs or p or mode == "steam" or mode not in self.gs.modes():
            return
        if self.gs.pid != self.gs_applied_pid:
            if now - self.gs_seen >= self.GS_SETTLE:
                self._guard("gamescope", lambda: self.gs.apply(mode))
                self.gs_applied_pid = self.gs.pid
                self.gs_last_check = now  # give gamescope time to switch first
        elif mode == "off" and now - self.gs_last_check > 10:
            # Steam can switch HDR back on (e.g. after its display settings).
            self.gs_last_check = now
            if gamescope.hdr_output_active():
                log.info("Game Mode HDR came back on, turning it off again")
                self._guard("gamescope", lambda: self.gs.apply("off"))

    # --- Game Mode brightness (SDR-in-HDR nits) ----------------------------

    SB_MIN, SB_MAX = 40, 500  # keep well away from black, and within the panel

    def _sb_ready(self) -> bool:
        self._gs_refresh()
        return bool(self.gs and self.gs.sdr_brightness_supported())

    def _sb_nits(self) -> int:
        return max(self.SB_MIN, min(self.SB_MAX, int(self.settings["sdr_brightness"]["nits"])))

    def apply_sdr_brightness(self) -> None:
        s = self.settings["sdr_brightness"]
        if not self._sb_ready():
            if s["enabled"]:
                self.errors["sdr_brightness"] = "Only works in Game Mode (gamescope with HDR brightness support)"
            return
        if not s["enabled"]:
            self.sb_pending = None
            self._guard("sdr_brightness", lambda: self.gs.set_sdr_brightness(None))
            return
        nits = self._sb_nits()
        if s["confirmed"]:
            self._guard("sdr_brightness", lambda: self.gs.set_sdr_brightness(nits))
            self.sb_applied_pid = self.gs.pid
            return
        # Trial: remember what was there, apply, and wait for Keep.
        if self.sb_pending:
            prev = self.sb_pending["prev"]
        else:
            try:
                prev = self.gs.sdr_brightness()
            except OSError:
                prev = None
        self._guard("sdr_brightness", lambda: self.gs.set_sdr_brightness(nits))
        if "sdr_brightness" not in self.errors:
            self.sb_pending = {"prev": prev, "deadline": time.monotonic() + self.GS_CONFIRM}

    def _sb_revert(self, p: dict) -> None:
        if self.gs:
            self._guard("sdr_brightness", lambda: self.gs.set_sdr_brightness(p["prev"]))
        self.settings["sdr_brightness"]["enabled"] = False
        self.save()

    def confirm_sdr_brightness(self, keep: bool) -> dict:
        p = self.sb_pending
        if not p:
            return self.state()
        self.sb_pending = None
        if keep:
            self.settings["sdr_brightness"]["confirmed"] = True
            self.save()
            self.sb_applied_pid = self.gs.pid if self.gs else None
            self.sb_last_check = time.monotonic()
        else:
            self._sb_revert(p)
        return self.state()

    def tick_sdr_brightness(self, now: float) -> None:
        p = self.sb_pending
        if p and now > p["deadline"]:
            self.sb_pending = None
            self._sb_revert(p)
            self.errors["sdr_brightness"] = "Not confirmed within 20 s, so it was reverted."
            return
        s = self.settings["sdr_brightness"]
        if not (s["enabled"] and s["confirmed"]) or not self.gs or not self.gs.sdr_brightness_supported():
            return
        nits = self._sb_nits()
        if self.gs.pid != self.sb_applied_pid:
            if now - self.gs_seen >= self.GS_SETTLE:
                self._guard("sdr_brightness", lambda: self.gs.set_sdr_brightness(nits))
                self.sb_applied_pid = self.gs.pid
                self.sb_last_check = now
            return
        if now - self.sb_last_check > 10:
            self.sb_last_check = now
            try:
                cur = self.gs.sdr_brightness()
            except OSError:
                return
            if cur is None or abs(cur - nits) > 0.5:
                log.info("Game Mode brightness was reset (%s), re-applying %s nits", cur, nits)
                self._guard("sdr_brightness", lambda: self.gs.set_sdr_brightness(nits))

    def _sdr_brightness_state(self) -> dict:
        s = self.settings["sdr_brightness"]
        ok = bool(self.gs and self.gs.sdr_brightness_supported())
        current = None
        if ok:
            try:
                current = self.gs.sdr_brightness()
            except OSError:
                pass
        p = self.sb_pending
        return {
            "available": ok,
            "enabled": s["enabled"],
            "nits": self._sb_nits(),
            "confirmed": s["confirmed"],
            "min": self.SB_MIN,
            "max": self.SB_MAX,
            "current": round(current) if current is not None else None,
            "pending": {"seconds": max(0, round(p["deadline"] - time.monotonic()))} if p else None,
        }

    def _gamescope_state(self) -> dict:
        p = self.gs_pending
        return {
            "running": bool(self.gs),
            "modes": self.gs.modes() if self.gs and not self.dev.gamescope_hdr_blocked else [],
            "blocked": self.dev.gamescope_hdr_blocked,
            "mode": p["mode"] if p else self.settings["gamescope"]["hdr"],
            "saved": self.settings["gamescope"]["hdr"],
            "pending": {"mode": p["mode"], "seconds": max(0, round(p["deadline"] - time.monotonic()))} if p else None,
            "hdr_active": gamescope.hdr_output_active() if self.gs else None,
        }

    def run_brightness_test(self) -> dict:
        if not self.backlight or self.btest["running"]:
            return self.state()
        # Single pass only. A runtime PSR-off pass blanked the ONEXPLAYER 3's
        # panel until a second reboot, so the test never touches PSR.
        self.btest = {"running": True, "phase": 1, "phases": 1,
                      "verdict": None, "report": None, "answer": None}
        self.btest_mode = "game" if diag.game_mode() else "desktop"

        def run():
            try:
                verdict, lines = diag.brightness_test(self.backlight)
                self.btest_sections = [
                    ("verdict", [verdict]), ("test", lines), ("system", diag.context()),
                    ("drm state", diag.drm_state()),
                ]
                self.btest["verdict"] = verdict
                self.btest["report"] = diag.write_report(self.user_home, self.btest_sections, self.btest_mode)
            except Exception as e:
                log.exception("brightness test failed")
                self.btest["verdict"] = f"Test failed: {e}"
            finally:
                self.btest["phase"] = None
                self.btest["running"] = False

        # ~7 s of sleeps; don't block the fan loop.
        threading.Thread(target=run, daemon=True).start()
        return self.state()

    def answer_brightness_test(self, answer: str) -> dict:
        """answer: which passes visibly changed the screen: first/second/both/neither."""
        if self.btest_sections and self.btest["answer"] is None:
            self.btest["answer"] = answer
            self.btest_sections.insert(1, ("user", [f"screen visibly changed in: {answer}"]))
            self.btest["report"] = diag.write_report(self.user_home, self.btest_sections, self.btest_mode)
        return self.state()

    def _display_params(self) -> dict[str, list[str]]:
        """Kernel parameters behind each display toggle, for the bound driver."""
        drv = self.gpu.driver
        have = lambda p: os.path.exists(f"/sys/module/{drv}/parameters/{p}")
        groups = {
            "psr": [f"{drv}.{p}=0" for p in ("enable_psr", "enable_panel_replay") if have(p)],
            "dc": [f"{drv}.enable_dc=0"] if have("enable_dc") else [],
        }
        return {k: v for k, v in groups.items() if v}

    def set_display(self, group: str, enabled: bool) -> dict:
        params = self._display_params().get(group)
        if not self.kargs or not params or self.display_busy:
            return self.state()
        blocked = self.dev.display_blocked.get(group)
        if enabled and blocked:
            # Turning a blocked toggle off is always allowed, never on.
            self.errors["display"] = blocked
            return self.state()
        self.display_busy = True
        self.display_msg = f"Updating {self.kargs.name} boot entries…"

        def run():
            try:
                if enabled:
                    self.kargs.set(params, set())
                else:
                    self.kargs.set([], {t.split("=", 1)[0] for t in params})
                self.display_msg = None
                self.errors.pop("display", None)
            except Exception as e:
                log.exception("kernel parameter update failed")
                self.display_msg = None
                self.errors["display"] = str(e)
            finally:
                self.kargs_configured = None
                self.display_busy = False

        # Bootloader updates can take a minute; keep the fan loop running.
        threading.Thread(target=run, daemon=True).start()
        return self.state()

    def _display_state(self) -> dict:
        out = {
            "brightness": self.backlight.percent() if self.backlight else None,
            "backlight": self.backlight.name if self.backlight else None,
            "bootloader": self.kargs.name if self.kargs else None,
            "busy": self.display_busy,
            "test": self.btest,
            "message": self.display_msg,
            "toggles": {},
        }
        if not self.kargs:
            return out
        if self.kargs_configured is None and not self.display_busy:
            try:
                self.kargs_configured = set(self.kargs.configured())
            except Exception as e:
                self.errors["display"] = str(e)
        configured = self.kargs_configured or set()
        drv = self.gpu.driver
        for group, params in self._display_params().items():
            # "xe.enable_psr=0" -> /sys/module/xe/parameters/enable_psr
            active = all(
                sysfs.read(f"/sys/module/{drv}/parameters/{t.split('=', 1)[0].split('.', 1)[1]}") == "0"
                for t in params
            )
            on = all(t in configured for t in params)
            keys = {t.split("=", 1)[0] for t in params}
            try:
                # None when the bootloader's entries can't be read.
                entries = self.kargs.in_entries(params, set()) if on else self.kargs.in_entries([], keys)
            except Exception:
                entries = None
            out["toggles"][group] = {
                "params": params,
                "configured": on,
                "active": active,
                "in_entries": entries,
                "reboot": on != active and entries is not False,
                "blocked": self.dev.display_blocked.get(group),
            }
        return out

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
            self.fan_pct = target if self.fan else None
        if self.fan:
            self._guard("fan", self._watchdog_start)

    def tick(self) -> None:
        self.gpu.sample()
        self.tick_gamescope()
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
        # power-profiles-daemon / tuned rewrite EPP (and sometimes boost) on
        # profile or AC changes, so re-assert our CPU choices every 10 s.
        if time.monotonic() - self.last_epp_check > 10:
            self.last_epp_check = time.monotonic()
            e = self.settings["epp"]
            if e["enabled"] and self.epp.available()[0] and self.epp.drifted(e["value"]):
                log.info("EPP changed behind our back, re-applying %s", e["value"])
                self.apply_epp()
            self.apply_cpu()
        self.tick_fan()
        b = self.settings["battery"]
        self._guard("battery", lambda: self.bat.tick(int(b["limit"]), b["bypass"]))

    def restore(self) -> None:
        """Hand control back to firmware (unload, exit, EC switch)."""
        if self.fan and self.settings["fan"]["mode"] != "auto":
            try:
                self.fan.set_auto()
                self._watchdog_stop()
            except Exception:
                # Leave the watchdog armed: it restores auto once we stop beating.
                log.exception("could not return fan to auto")
        self.fan_pct = None
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
                "watchdog": self._watchdog_alive(),
            },
            "battery": {
                "limit_mode": self.bat.limit_mode(),
                "bypass_options": self.bat.bypass_options(),
                "unavailable": None if self.bat.behaviours() else (self.bat.ec_why or "no charge control driver"),
                **self.bat.status(),
            },
            "epp": {
                "available": self.epp.available()[0],
                "unavailable": self.epp.available()[1],
                "driver": self.epp.driver,
                "options": self.epp.options() if self.epp.available()[0] else [],
                "current": self.epp.current(),
            },
            "cpu": {
                name: {
                    "available": ok,
                    "unavailable": why,
                    "on": ctl.on() if ok else None,
                }
                for name, ctl in (("boost", self.boost), ("prefcore", self.prefcore))
                for ok, why in [ctl.available()]
            },
            "gpu": self.gpu.state(),
            "display": self._display_state(),
            "version": self.version,
            "home": self.user_home,
            "gamescope": self._gamescope_state(),
            "sdr_brightness": self._sdr_brightness_state(),
            "ec": {
                "supported": bool(self.dev.ec_fan or self.dev.ec_charge),
                "lockdown": ec.lockdown(),
            },
            "rgb": {
                "backend": getattr(self.rgb, "name", None),
                "modes": getattr(self.rgb, "modes", []),
                "effects": getattr(self.rgb, "effects", []),
            },
        }
