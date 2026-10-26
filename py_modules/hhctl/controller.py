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

from . import ai, autotune, background, games, powerkey, rebar, systweak, backlight, buttons, fonts, gestures, lsfg, memtweak, padchord, updater, storage, inputcap, inputplumber, thermal, sleep, battery, cpu, diag, ec, epp, fan, gamescope, gpu, kargs, proc, rgb, scx, sysfs, tdp, upscale, watchdog
from .device import cpu_model, detect

log = logging.getLogger("hhctl")

DEFAULTS = {
    # Nothing is touched until the user enables a section.
    "tdp": {"enabled": False, "watts": None, "boost": True},
    "epp": {"enabled": False, "value": "balance_performance"},
    # None = leave as the system set it; True/False once the user toggles.
    # scx: None, "off" (kernel scheduler) or a scx_loader scheduler name.
    "cpu": {
        "boost": None, "prefcore": None, "ccx_off": None, "cap": False, "cap_mhz": None,
        "smt": None, "hwp_boost": None, "epp_boost": None, "scx": None, "scx_mode": "gaming",
        # Core types (Ryzen AI 300, hybrid Intel): None, "all", "fast", "small".
        "cores": None,
        # CPU temperature limit in °C (None: firmware's own), held by the CPU.
        "temp_limit": None,
        # SteamOS's gaming memory settings (memtweak.py), runtime only.
        "memtweak": False,
    },
    # GPU clock: "auto" leaves the driver alone; min/max/manual lock it.
    # profile: amdgpu power profile, only with mode "auto" (exclusive with a lock).
    "gpu": {"mode": "auto", "mhz": None, "profile": None},
    # Game Mode HDR: "steam" leaves gamescope alone; others only after confirm.
    "gamescope": {"hdr": "steam"},
    # Game Mode brightness through gamescope's SDR-in-HDR nits. The first
    # change is a trial that must be confirmed; after that it applies directly.
    "sdr_brightness": {"enabled": False, "nits": 203, "confirmed": False},
    # bg_pos: which part of the picture shows (CSS background-position %) and zoom (%).
    # Extra buttons open Steam things (AYN -> this plugin, Win+D -> keyboard).
    # ip_target: controller InputPlumber emulates (None leaves its choice).
    # ip_failed: our InputPlumber config made no controller; off until picked again.
    "controller": {"buttons": True, "ip_target": "xbox-elite", "ip_failed": None,
                   "swipe_keyboard": False, "swipe_edge": None, "chord": None},
    "ui": {"theme": "gold", "font": "quake", "bg": None, "bg_dim": 55, "bg_pos": {"x": 50, "y": 50, "zoom": 100}},
    # curve "custom": the speeds in "points", one per fan.CUSTOM_TEMPS.
    "fan": {"mode": "auto", "percent": 50, "curve": "balanced", "points": None},
    "rgb": {"enabled": False, "mode": "solid", "hue": 200, "sat": 100, "bright": 70, "effect": None, "match_theme": False},
    # rgb_off: lights off while it sleeps. pad_wake: the controller wakes it
    # (None = the device's default: on for the NEXT 2, which asked for it).
    # pad_wake_skip: "vendor:product" taken off because they woke it right away.
    # power_key: a short power press sleeps in Game Mode when nothing else
    # acts on it (None = the device's default, POWER_KEY_DEFAULT).
    "sleep": {"rgb_off": True, "pad_wake": None, "pad_wake_skip": [], "power_key": None},
    "battery": {"limit": 100, "bypass": "off"},
    # Raw EC writes are only used when the user opts in.
    "ec": {"enabled": False},
    # Per-game profiles (games.py): appid -> {"name", "settings"}; base: the
    # settings put aside while a game with a profile runs.
    "games": {"enabled": True, "profiles": {}, "base": None},
    # System toggles (systweak.py); None / False leaves the system's value.
    # Nothing touches drives or SD cards (see systweak.py).
    "system": {"thp": None, "swappiness": None, "usb_awake": False, "wifi_ps_off": False},
}

# Daemons that may also set TDP, RGB or the fan. Not InputPlumber: it only does the
# controller, and the plugin drives it itself.
CONFLICTS = ("hhd", "powerstation")

# USB ids of each handheld's controller (gamepad + the buttons' keyboard), for waking with it.
# NEXT 2: the buttons only. Its gamepad's right trigger jitters all the time and
# woke it right after every suspend (2026-10-25).
PAD_IDS = {
    "ayaneo_next2": [("1c4f", "0002")],
    "oxp3": [("045e", "028e"), ("1a86", "fe00")],
    "onexfly": [("045e", "028e")],
}
PAD_WAKE_DEFAULT = {"ayaneo_next2": True}
# CachyOS on the ONEXPLAYER 3: logind sees the press, nothing passes it to Steam (2026-10-26).
POWER_KEY_DEFAULT = {"oxp3": True}
PAD_WAKE_TEXT = {"ayaneo_next2": "AYN, the back buttons and the button next to AYN wake it from sleep "
                                 "(not the gamepad: its trigger never stops sending and woke it at once)"}


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
        self.icap = {"running": False, "left": 0, "summary": None, "report": None}
        self.upd = {"busy": False, "releases": [], "error": None, "message": None}
        self.zone_test: str | None = None  # legend of the last OXP light zone test
        self.plugin_dir: str | None = None  # set by main.py; where updates are installed
        # The desktop app (desktop/server.py) installs its own zips and restarts itself.
        self.update_kind = "plugin"
        self.topuse: dict | None = None  # last "what's using it" look
        self.desktop = False  # HHC Desktop (desktop/server.py): games are found from processes
        self.next_game_scan = 0.0
        self.after_update = updater.restart_loader
        self.update_note = "Decky restarts in a moment and loads it."
        self.dev = detect()
        self.cpu_model = cpu_model()
        self.settings = self._load()
        self.game: tuple[str, str] | None = None  # running Steam game: (appid, name)
        self.pre_game: dict | None = None  # settings when a game without a profile started
        for prof in self.settings["games"]["profiles"].values():
            # 0.10.85's auto-tune could leave a game on the small cores only (see autotune.py).
            cpu_s = prof.get("settings", {}).get("cpu", {})
            if prof.get("auto") and cpu_s.get("cores") == "small":
                cpu_s["cores"] = None
        if self.settings["games"]["base"]:
            # Stopped while a game's profile was in use: back to the usual settings.
            games.put(self.settings, self.settings["games"]["base"])
            self.settings["games"]["base"] = None
            self.save()
        self.tdp = tdp.find_backend(self.dev)
        self.thermal = self._thermal_backend()
        # Intel only: firmware-attributes already selects "custom", ryzenadj needs nothing.
        self.profile = tdp.PlatformProfile() if isinstance(self.tdp, tdp.Rapl) else None
        self.rgb = rgb.find_backend(self.dev)
        self.gpu = gpu.Gpu()
        # Fixed while the driver is loaded, so read once.
        self.gfx_pipes = gpu.gfx_pipes(self.gpu.dev) if self.gpu.driver == "amdgpu" else None
        self.epp = epp.Epp()  # snapshots the original EPP before anything is applied
        self.last_epp_check = 0.0
        self.boost = cpu.Boost()
        self.prefcore = cpu.Prefcore()
        self.ccx = cpu.Ccx(os.path.join(os.path.dirname(settings_path), "ccx-groups.json"))
        self.freqcap = cpu.FreqCap()
        self.cores = cpu.CoreTypes(self.ccx.groups)
        # Ryzen AI 300: its two CCXs are the Zen 5 and the Zen 5c cores, so
        # "First CCX only" would just be "Zen 5 only"; the core dropdown does that.
        self.ccx_hybrid = bool(self.cores.small) and len(self.ccx.groups) > 1
        self.core_counts = (self.cores.cores(self.cores.fast), self.cores.cores(self.cores.small))
        self.smt = cpu.Smt()
        self.hwp_boost = cpu.HwpBoost()
        self.epp_boost = cpu.EppBoost()
        self.scx = scx.Scx()
        self.cpu_util = cpu.Util()
        self.limit_reasons = cpu.LimitReasons()
        self.ai = ai.AiEnv(self._home(), self.gpu.driver)
        self.upscale = upscale.Upscale(self._home(), self.gpu.driver, self.gpu.dev)
        self.lsfg = lsfg.Lsfg(self._home())
        self.memtweak = memtweak.MemTweak()
        self.systweak = systweak.SysTweaks()
        self.autotune = autotune.AutoTune()
        self.lsfg_busy = False
        self.gpuclk = gpu.GpuClock(self.gpu.dev, self.gpu.driver)  # snapshots the driver's clocks
        self.gpu_profile = gpu.PowerProfile(self.gpu.dev, self.gpu.driver)
        self.uma = gpu.UmaCarveout(self.gpu.dev, self.gpu.driver)
        self.emit = lambda action: None  # set by main.py: sends a button action to the panel
        self.buttons = buttons.Buttons(self.dev, lambda action: self.emit(action))
        self.swipe = gestures.EdgeSwipe(lambda action: self.emit(action),
                                        lambda: self.settings["controller"].get("swipe_edge"), self._swipe_learned)
        self.swipe_avail = self.swipe.available()
        self.chord = padchord.PadChord(lambda action: self.emit(action), self.chord_setting)
        self.powerkey = powerkey.PowerKey(self._power_short)
        self.ip = inputplumber.InputPlumber(os.path.join(os.path.dirname(settings_path), "inputplumber-profile.yaml"))
        self.ipconf = inputplumber.Configs(self.buttons.key)
        self.sleeper = sleep.SleepWatch(self._before_sleep, self._after_wake)
        self.slept_at: float | None = None  # boot time when it went to sleep
        self.sleep_bat: tuple[int | None, bool, bool] = (None, False, False)  # capacity, on AC, charging paused
        self.woke: float | None = None  # set by the wake signal; tick() acts on it
        self.sleep_note: str | None = None
        self.wake_before: dict = {}
        self.last_wake: str | None = None  # "slept 3 s, woken by …", shown in the panel
        self.padwake = sleep.PadWake([i for i in PAD_IDS.get(self.buttons.key, [])
                                      if f"{i[0]}:{i[1]}" not in self.settings["sleep"].get("pad_wake_skip", [])])
        self.ip_check_at: float | None = None  # when to check our InputPlumber controller came up
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
        self.gpumem = gpu.GpuMemLimit(self.kargs or kargs.detect())
        self.gpumem_busy = False
        self.gpumem_conf: int | None = None  # % in the boot config; cached
        self._scan()
        self.errors: dict[str, str] = {}
        self.fan_pct: int | None = None
        # After an EC error the fan goes back to firmware; it's taken up again
        # after FAN_RETRY s, at most FAN_RESTARTS times an hour.
        self.fan_retry_at: float | None = None
        self.fan_restarts: list[float] = []
        self.fan_note: str | None = None  # "restarted at …", shown under the fan settings
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
        log.info(
            "CPU: driver=%s clock range=%s MHz cap=%s; GPU: driver=%s clock range=%s MHz manual=%s (%s)",
            self.epp.driver, self.freqcap.range_mhz(), self.freqcap.available(),
            self.gpu.driver, self.gpuclk.hw_range(), self.gpuclk.manual_ok(), self.gpuclk.available(),
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
        if section == "fan" and not self.fan and self.fan_why:
            self.fan_restarts, self.fan_retry_at = [], time.monotonic()  # picked again: try now
        if section == "controller" and "ip_target" in values:
            values = {**values, "ip_failed": None}  # picked again: try again
            self.errors.pop("controller", None)
        if section == "sleep" and "pad_wake" in values:
            self.sleep_note = None  # switched by hand: the old note no longer applies
        if section == "gpu":
            # A power profile and a clock lock exclude each other: the newer wins.
            if values.get("profile"):
                values = {**values, "mode": "auto"}
            elif values.get("mode", "auto") != "auto":
                values = {**values, "profile": None}
        self.settings[section].update(values)
        prof = self.game_profile()
        if prof and prof.get("auto") and section == "cpu" and {"cap", "cap_mhz"} & set(values):
            prof["auto"] = False  # set by hand: the user tunes it now
            self.autotune.reset()
        if prof and self.settings["games"]["base"] is not None:
            games.record(prof, section, values)
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
                if self.settings["fan"]["mode"] != "auto":
                    self.fan_retry_at = time.monotonic() + self.FAN_RETRY
            elif section == "battery":
                self.bat.disable_ec(str(e))
        except Exception as e:  # report every hardware error to the UI
            if self.errors.get(section) != str(e):  # not again every 10 s
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
        atexit.register(lambda: self.restore(unload=True))
        try:
            prev = signal.getsignal(signal.SIGTERM)

            def on_term(signum, frame):
                self.restore(unload=True)
                if callable(prev):
                    prev(signum, frame)
                else:
                    signal.signal(signal.SIGTERM, signal.SIG_DFL)
                    os.kill(os.getpid(), signal.SIGTERM)

            signal.signal(signal.SIGTERM, on_term)
        except ValueError:
            pass  # not on the main thread; atexit and the watchdog still cover us

    # --- apply ------------------------------------------------------------

    # (hue, saturation) the lights take with "Match theme color"; the default
    # theme has no color, so the RGB section's own hue stays.
    THEME_RGB = {"red": (0, 100), "redgold": (0, 100), "redyellow": (0, 100), "blue": (215, 100), "green": (130, 100), "gold": (42, 100)}

    def apply_ui(self) -> None:
        # The panel itself reads ui from settings; only the lights follow it.
        if self.settings["rgb"].get("match_theme"):
            self.apply_rgb()

    def rgb_color(self) -> tuple[int, int]:
        s = self.settings["rgb"]
        if s.get("match_theme"):
            theme = self.THEME_RGB.get(self.settings["ui"].get("theme"))
            if theme:
                return theme
        return s["hue"], s["sat"]

    def _ui_dir(self) -> str:
        return os.path.dirname(self.settings_path)

    def set_background(self, path: str) -> dict:
        try:
            self.settings["ui"]["bg"] = background.save(self._ui_dir(), path)
            self.settings["ui"]["bg_pos"] = {"x": 50, "y": 50, "zoom": 100}  # new picture: start centered
            self.save()
            self.errors.pop("ui", None)
        except OSError as e:
            self.errors["ui"] = str(e)
        return self.state()

    def _home(self) -> str:
        return self.user_home or os.path.expanduser("~")

    def list_pictures(self, folder: str | None) -> dict:
        home = self._home()
        if not folder:
            pics = os.path.join(home, "Pictures")
            folder = pics if os.path.isdir(pics) else home
        try:
            return background.list_dir(folder, home)
        except OSError as e:
            return {"folder": folder, "parent": os.path.dirname(folder), "home": home,
                    "dirs": [], "files": [], "more": 0, "error": str(e)}

    def picture_data(self, path: str) -> str | None:
        try:
            return background.read_data_url(path)
        except OSError as e:
            self.errors["ui"] = str(e)
            return None

    def set_background_data(self, data_url: str) -> dict:
        try:
            self.settings["ui"]["bg"] = background.save_data_url(self._ui_dir(), data_url)
            self.settings["ui"]["bg_pos"] = {"x": 50, "y": 50, "zoom": 100}
            self.save()
            self.errors.pop("ui", None)
        except (OSError, ValueError) as e:
            self.errors["ui"] = str(e)
        return self.state()

    def picture_thumb(self, path: str) -> str | None:
        try:
            return background.thumb(path, self._home())
        except OSError:
            return None

    def clear_background(self) -> dict:
        background.clear(self._ui_dir())
        self.settings["ui"]["bg"] = None
        self.save()
        return self.state()

    def font_css(self, name: str) -> str | None:
        try:
            return fonts.css(name)
        except OSError:
            log.exception("font %s missing", name)
            return None

    def background_data(self) -> str | None:
        if not self.settings["ui"]["bg"]:
            return None
        try:
            return background.data_url(self._ui_dir())
        except OSError:
            return None

    def chord_setting(self) -> str | None:
        """The controller shortcut; by default on for PCs (no extra button of
        their own), off on the handhelds, which have AYN / Home / Turbo."""
        v = self.settings["controller"].get("chord")
        if v is None:
            return "guide_b" if self.buttons.key == "generic" else None
        return None if v == "off" else v

    def _swipe_learned(self, edge: str) -> None:
        self.settings["controller"]["swipe_edge"] = edge
        self.save()

    def apply_controller(self) -> None:
        c = self.settings["controller"]
        if c["buttons"]:
            self.buttons.start()
        else:
            self.buttons.stop()
        if self.chord_setting():
            self.chord.start()
        else:
            self.chord.stop()
        if c.get("swipe_keyboard"):
            self.swipe.calibrating = not c.get("swipe_edge")  # first swipe teaches the bottom edge
            self.swipe.start()
        else:
            self.swipe.calibrating = False
            self.swipe.stop()
        self.apply_ip_config()
        self.sync_inputplumber()

    def apply_ip_config(self) -> None:
        """NEXT 2 / OXP3: our InputPlumber config while a controller type is picked."""
        if not self.ipconf.supported():
            return
        c = self.settings["controller"]
        if c.get("ip_target") and not c.get("ip_failed"):
            started = []
            self._guard("controller", lambda: started.append(self.ipconf.ensure()))
            if self.ipconf.installed():
                # Also after boot: InputPlumber may come up after us.
                self.ip_check_at = time.monotonic() + (20 if any(started) else 45)
        else:
            self.ip_check_at = None
            self._guard("controller", self.ipconf.remove)
        self.ip.devices = []  # look for its composite device again now
        self.ip._looked = -600.0

    def check_ip_controller(self) -> None:
        """Fail-safe: no controller through our config -> take it away again."""
        if self.ip_check_at is None or time.monotonic() < self.ip_check_at:
            return
        self.ip_check_at = None
        problem = self.ipconf.health()
        if not problem:
            log.info("inputplumber: controller up with %s", self.ipconf.dev_path)
            return
        log.error("inputplumber: %s; removing our config", problem)
        path = diag.write_report(self.user_home, self.ipconf.report(), "game", kind="inputplumber")
        self._guard("controller", self.ipconf.remove)
        self.settings["controller"]["ip_failed"] = problem
        self.save()
        self.errors["controller"] = (
            f"{problem}, so the plain controller is back."
            + (f" Report: {path}" if path else "")
        )

    def save_ip_report(self) -> dict:
        path = diag.write_report(self.user_home, self.ipconf.report(), "game", kind="inputplumber")
        self.errors["controller"] = f"InputPlumber report saved: {path}" if path else "Couldn't write the report"
        return self.state()

    def sync_inputplumber(self) -> None:
        c = self.settings["controller"]
        ip_buttons = c["buttons"] and self.buttons.key == "onexfly"
        self._guard("controller", lambda: self.ip.tick(ip_buttons, c.get("ip_target")))

    QUICK_WAKE = 15.0  # s: woken this soon by a controller device = that device is noisy

    def _before_sleep(self) -> None:
        """From the sleep watcher's thread, while logind waits: the lights and
        the charge limit."""
        self.slept_at = sleep.boottime()
        self.sleep_bat = (self.bat.capacity(), battery.ac_online(), False)
        b = self.settings["battery"]
        try:
            held = self.bat.hold_for_sleep(int(b["limit"]), b["bypass"])
            self.sleep_bat = self.sleep_bat[:2] + (held,)
        except Exception:
            log.exception("sleep: couldn't pause charging")
        if self.pad_wake_on():
            self.padwake.snapshot()
        if self.settings["sleep"].get("rgb_off") and self.rgb and self.settings["rgb"]["enabled"]:
            self.rgb.apply("off", 0, 0, 0, None)
            # The controller answers the command; let that land before the
            # suspend, so a late answer can't count as a wake-up.
            time.sleep(0.5)
        self.wake_before = sleep.wake_counts()

    def _after_wake(self) -> None:
        self.woke = sleep.boottime()

    def tick_wake(self) -> None:
        """After every wake (even one too short for the resume check): lights
        back on, and a controller device that woke it at once loses its wakeup."""
        if self.woke is None:
            return
        woke, self.woke = self.woke, None
        slept = woke - self.slept_at if self.slept_at else None
        self.apply_rgb()
        sources = sleep.woken_by(self.wake_before, sleep.wake_counts())
        irq = sleep.wake_irq()
        parts = [f"slept {slept:.0f} s" if slept is not None else "woke"]
        if sources or irq:
            parts.append("woken by " + ", ".join(sources + ([irq] if irq else [])))
        cap0, ac0, held = self.sleep_bat
        cap1 = self.bat.capacity()
        if ac0 and cap0 is not None and cap1 is not None and self.bat.limit_mode() == "emulated":
            raw = self.bat.raw_behaviour()
            parts.append(f"battery {cap0} → {cap1} %, charging {'paused' if held else 'on'}"
                         + (f" (EC 0x{raw:02X})" if raw is not None else ""))
        self.last_wake = f"Last sleep ({time.strftime('%H:%M')}): " + ", ".join(parts)
        log.info("sleep: %s", self.last_wake)
        if not self.pad_wake_on():
            return
        by = self.padwake.woke_by()
        if by:
            log.info("sleep: woken after %.0f s by %s", slept or -1, ", ".join(by))
        # Woken at once by a hub or the USB controller we allowed to wake it,
        # not by the device: with wakeup on, a root hub also wakes on a port
        # (dis)connecting, e.g. a controller that drops off USB while asleep
        # (NEXT 2: usb3 + its xHCI, 4 s after sleeping, 2026-10-25). Off for good.
        chain = {os.path.basename(os.path.dirname(os.path.dirname(w))) for w in self.padwake.files()}
        hubs = [s for s in sources if s.split()[0] in chain]
        if not by and hubs and slept is not None and slept < self.QUICK_WAKE:
            self.padwake.restore()
            self.settings["sleep"]["pad_wake"] = False
            self.save()
            self.sleep_note = (f"The USB controller ({hubs[0].split()[0]}) woke it right away ({slept:.0f} s after "
                               "sleeping), so waking with the controller is off. The power button still wakes it.")
            log.info("sleep: pad wake off, %s", self.sleep_note)
            return
        if by and slept is not None and slept < self.QUICK_WAKE:
            noisy = [tuple(x.split(":")) for x in by]
            self.padwake.restore()
            self.padwake.ids = [i for i in self.padwake.ids if i not in noisy]
            self.settings["sleep"]["pad_wake_skip"] = sorted(set(self.settings["sleep"].get("pad_wake_skip", [])) | set(by))
            self.save()
            if self.padwake.ids:
                self.padwake.enable()
            self.sleep_note = (f"{', '.join(by)} woke it right away ({slept:.0f} s after sleeping), "
                               "so it no longer wakes it")

    # --- per-game profiles ------------------------------------------------

    def game_profile(self) -> dict | None:
        g = self.settings["games"]
        return g["profiles"].get(self.game[0]) if self.game and g["enabled"] else None

    def set_game(self, appid: str | None, name: str | None = None) -> dict:
        """From the panel whenever Steam's running game changes."""
        self.game_changed(appid, name)
        return self.state()

    def game_changed(self, appid: str | None, name: str | None = None) -> None:
        game = (str(appid), name or f"App {appid}") if appid else None
        if (game and game[0]) != (self.game and self.game[0]):
            self.game = game
            # Tuning for a game before saving its profile: the usual settings
            # are what was in use when it started.
            self.pre_game = games.snapshot(self.settings) if game and not self.game_profile() else None
            self.autotune.reset()
            log.info("games: running %s", game[1] if game else "nothing")
            self.switch_profile()

    def switch_profile(self) -> None:
        g = self.settings["games"]
        prof = self.game_profile()
        if prof:
            if g["base"] is None:
                g["base"] = games.snapshot(self.settings)
            games.put(self.settings, prof["settings"])
            log.info("games: profile for %s", prof.get("name"))
        elif g["base"] is not None:
            tdp_on = self.settings["tdp"]["enabled"]
            games.put(self.settings, g["base"])
            g["base"] = None
            if tdp_on and not self.settings["tdp"]["enabled"] and self.tdp:
                # Custom TDP was only on for the game: the device's default again.
                self._guard("tdp", lambda: self.tdp.apply(self.dev, self.dev.tdp_default, True))
            log.info("games: usual settings")
        else:
            return
        self.save()
        self.apply_profiled()

    def apply_profiled(self) -> None:
        self.apply_tdp()
        self.apply_epp()
        self.apply_cpu()
        self.apply_temp_limit(force=True)
        self.apply_gpu()
        self.fan_pct = None
        self.apply_fan()

    def save_game_profile(self, on: bool) -> dict:
        """Panel switch: keep the current settings for the running game, or drop its profile."""
        if not self.game:
            return self.state()
        g = self.settings["games"]
        appid, name = self.game
        if on:
            if g["base"] is None:
                g["base"] = self.pre_game or games.snapshot(self.settings)
            g["profiles"][appid] = {"name": name, "settings": games.snapshot(self.settings)}
            self.save()
        elif appid in g["profiles"]:
            del g["profiles"][appid]
            self.switch_profile()  # back to the usual settings
            self.save()
        return self.state()

    def set_autotune(self, on: bool) -> dict:
        """Panel switch: auto-tune the running game (saves its profile first)."""
        if not self.game:
            return self.state()
        if not self.game_profile():
            self.save_game_profile(True)
        prof = self.game_profile()
        if prof:
            prof["auto"] = bool(on)
            self.autotune.reset()
            self.save()
        return self.state()

    def set_topuse(self, res: dict) -> dict:
        """Panel button: the busiest processes over 2 s (topuse.sample)."""
        self.topuse = res
        log.info("topuse: %s", res)
        return self.state()

    def tick_desktop_game(self) -> None:
        """HHC Desktop: no Steam UI reports the game, so look for it every 5 s."""
        if not self.desktop or time.monotonic() < self.next_game_scan:
            return
        self.next_game_scan = time.monotonic() + 5
        g = games.running_steam_game(self.user_home)
        self.game_changed(*g) if g else self.game_changed(None)

    def tick_autotune(self) -> None:
        prof = self.game_profile()
        if not (prof and prof.get("auto") and self.settings["games"]["base"] is not None
                and self.freqcap.available()[0]):
            return
        now = time.monotonic()
        self.autotune.feed(now, self.gpu._util(), self.cpu_util.busiest)
        lo, hi = self.freqcap.range_mhz()
        s = self.settings["cpu"]
        vals = self.autotune.decide(now, s, lo, hi, self.smt.available()[0])
        if not vals:
            return
        s.update(vals)
        games.record(prof, "cpu", vals)
        self.save()
        log.info("autotune: %s", self.autotune.status)
        self.apply_cpu()

    def apply_games(self) -> None:
        self.switch_profile()  # profiles switched on or off

    def power_key_on(self) -> bool:
        v = self.settings["sleep"].get("power_key")
        return POWER_KEY_DEFAULT.get(self.dev.key, False) if v is None else bool(v)

    POWER_WAIT = 3.0  # s: time for Steam or logind to start a sleep by themselves

    def _power_short(self) -> None:
        """From the power key thread: a short press. In Game Mode, if no sleep
        starts by itself, ask Steam to sleep."""
        if not self.power_key_on() or not gamescope._gamescope_pid():
            return
        pressed = sleep.boottime()
        time.sleep(self.POWER_WAIT)
        if self.slept_at and self.slept_at >= pressed - 0.5:
            return  # something else put it to sleep
        log.info("powerkey: nothing put it to sleep, asking Steam")
        self.emit("sleep")

    def pad_wake_on(self) -> bool:
        v = self.settings["sleep"].get("pad_wake")
        return PAD_WAKE_DEFAULT.get(self.buttons.key, False) if v is None else bool(v)

    def limit_while_asleep(self) -> bool:
        b = self.settings["battery"]
        return self.bat.limit_mode() == "emulated" and int(b["limit"]) < 100

    def apply_sleep(self) -> None:
        s = self.settings["sleep"]
        if self.power_key_on():
            self.powerkey.start()
        else:
            self.powerkey.stop()
        if (s.get("rgb_off") and self.rgb) or self.pad_wake_on() or self.limit_while_asleep() or self.power_key_on():
            self.sleeper.start()
        else:
            self.sleeper.stop()
        if self.pad_wake_on():
            self._guard("sleep", self.padwake.enable)  # also after resume: devices come back new
        else:
            self._guard("sleep", self.padwake.restore)

    def apply_system(self) -> None:
        self._guard("system", lambda: self.systweak.apply(self.settings["system"]))

    def apply_all(self) -> None:
        self.apply_system()
        self.apply_sleep()
        self.apply_controller()
        self.apply_tdp()
        self.apply_epp()
        self.apply_cpu()
        self.apply_temp_limit(force=True)  # firmware resets it on resume
        self.apply_gpu()
        self.apply_fan()
        self.apply_battery()
        self.apply_rgb()

    def apply_tdp(self) -> None:
        s = self.settings["tdp"]
        if not self.tdp:
            return
        if not s["enabled"]:
            if self.profile:
                self._guard("tdp", self.profile.restore)
            return
        watts = s["watts"] or self.dev.tdp_default
        watts = max(self.dev.tdp_min, min(self.dev.tdp_max, int(watts)))
        self._guard("tdp", lambda: self.tdp.apply(self.dev, watts, s["boost"]))
        self.last_tdp_apply = time.monotonic()
        self.hold_profile()

    def hold_profile(self) -> None:
        """Keep "performance" while TDP control is on (low-power caps ~15 W)."""
        if not self.profile or not self.settings["tdp"]["enabled"]:
            return
        changed = []
        self._guard("tdp", lambda: changed.append(self.profile.hold()))
        if any(changed):
            # power-profiles-daemon sets EPP along with the profile.
            self.apply_epp()

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
        self._guard("memtweak", self.memtweak.apply if s.get("memtweak") else self.memtweak.restore)
        # Before the CCX: SMT on would bring back the other CCX's threads.
        if s.get("smt") is not None and self.smt.available()[0]:
            changed = []
            self._guard("cpu", lambda: changed.append(self.smt.set(bool(s["smt"]))))
            if any(changed) and s["smt"]:
                self.apply_epp()  # threads coming back online start with default EPP
        if self.ccx_hybrid and s.get("ccx_off"):
            s["ccx_off"] = None  # set before the toggle was hidden: bring the cores back
            self.save()
            self._guard("cpu", lambda: self.ccx.set(False))
            self.apply_epp()
        if s.get("ccx_off") is not None and self.ccx.available()[0] and not self.ccx_hybrid:
            changed = []
            self._guard("cpu", lambda: changed.append(self.ccx.set(bool(s["ccx_off"]))))
            if any(changed) and not s["ccx_off"]:
                self.apply_epp()  # CPUs coming back online start with default EPP
        for key, ctl in self._toggles():
            if s.get(key) is not None and ctl.available()[0]:
                self._guard("cpu", lambda: ctl.set(bool(s[key])))
        if s.get("cores") is not None and self.cores.available()[0]:
            self._guard("cpu", lambda: self.cores.apply(s["cores"]))
        if s.get("scx") is not None and self.scx.available()[0]:
            self._guard("scx", lambda: self.scx.apply(s["scx"], s.get("scx_mode") or "gaming"))
        # After boost and CCX: toggling either can reset the policies' limits.
        if self.freqcap.available()[0]:
            if s.get("cap") and s.get("cap_mhz"):
                self._guard("cpu", lambda: self.freqcap.apply(int(s["cap_mhz"])))
            else:
                self._guard("cpu", self.freqcap.restore)
        self.apply_temp_limit()

    def _thermal_backend(self):
        if self.dev.cpu == "intel":
            return thermal.find("intel")
        cpuinfo = sysfs.read("/proc/cpuinfo") or ""
        family = tdp.AmdSmu.detect(cpuinfo)
        smu = self.tdp if isinstance(self.tdp, tdp.AmdSmu) else None
        ra = self.tdp.binary if isinstance(self.tdp, tdp.RyzenAdj) else None
        if not smu and not ra:
            ra = getattr(tdp.RyzenAdj.find(), "binary", None)
        if not smu and not ra and family:
            smu = tdp.AmdSmu.find(cpuinfo)
        return thermal.find("amd", family.name if family else "", smu, ra)

    def apply_temp_limit(self, force: bool = False) -> None:
        if not self.thermal:
            return
        c = self.settings["cpu"].get("temp_limit")
        if c is None:
            self._guard("thermal", self.thermal.restore)
            return
        lo, hi = self.thermal.range
        c = max(lo, min(hi, int(c)))
        self._guard("thermal", lambda: self.thermal.apply(c, force))

    def _toggles(self) -> tuple:
        return (("boost", self.boost), ("prefcore", self.prefcore), ("hwp_boost", self.hwp_boost), ("epp_boost", self.epp_boost))

    def apply_gpu(self) -> None:
        if not self.gpuclk.available()[0]:
            return
        s = self.settings["gpu"]
        if s.get("profile") and s["mode"] != "auto":
            s["profile"] = None  # settings from before the two were exclusive
        # Profile off first, so a lock never runs with a profile set.
        if self.gpu_profile.available()[0] and not s.get("profile"):
            self._guard("gpu", self.gpu_profile.restore)
        self._guard("gpu", lambda: self.gpuclk.apply(s["mode"], s.get("mhz")))
        if self.gpu_profile.available()[0] and s.get("profile"):
            self._guard("gpu", lambda: self.gpu_profile.apply(s["profile"]))

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
        if self.limit_while_asleep() and not self.sleeper.enabled:
            self.apply_sleep()  # the limit has to be held before it sleeps too

    def apply_rgb(self) -> None:
        s = self.settings["rgb"]
        if not self.rgb or not s["enabled"]:
            return
        hue, sat = self.rgb_color()
        self._guard("rgb", lambda: self.rgb.apply(s["mode"], hue, sat, s["bright"], s["effect"]))

    # --- display ----------------------------------------------------------

    def set_lsfg(self, install: bool) -> dict:
        """Install (or remove) the lsfg-vk layer in ~/.local; a download, so in a thread."""
        if self.lsfg_busy:
            return self.state()

        def go():
            try:
                self._guard("lsfg", self.lsfg.install if install else self.lsfg.remove)
            finally:
                self.lsfg_busy = False

        self.lsfg_busy = True
        threading.Thread(target=go, name="hhctl-lsfg", daemon=True).start()
        return self.state()

    def set_upscale(self, option: str, on: bool) -> dict:
        self._guard("upscale", lambda: self.upscale.set(option, bool(on)))
        return self.state()

    def set_ai(self, on: bool) -> dict:
        self._guard("ai", lambda: self.ai.set(bool(on)))
        return self.state()

    def set_gpu_mem(self, gb: int) -> dict:
        if not self.gpumem.available() or self.gpumem_busy:
            return self.state()
        self.gpumem_busy = True

        def run():
            try:
                self.gpumem.set(int(gb))
                self.errors.pop("gpumem", None)
            except Exception as e:
                log.exception("GPU memory limit update failed")
                self.errors["gpumem"] = str(e)
            finally:
                self.gpumem_conf = None
                self.kargs_configured = None
                self.gpumem_busy = False

        # Bootloader updates can take a minute (rpm-ostree longer).
        threading.Thread(target=run, daemon=True).start()
        return self.state()

    def _gpumem_state(self) -> dict:
        if not self.gpumem.available():
            return {"available": False}
        if self.gpumem_conf is None and not self.gpumem_busy:
            try:
                self.gpumem_conf = self.gpumem.configured_gb(self.gpumem.boot.configured())
            except Exception as e:
                self.errors["gpumem"] = str(e)
        return {
            "available": True,
            "bootloader": self.gpumem.boot.name,
            "choices": self.gpumem.choices(),
            "default": self.gpumem.default_gb(),
            "current": self.gpumem.current_gb(),
            "configured": self.gpumem_conf,
            "busy": self.gpumem_busy,
        }

    def set_vram(self, index: int) -> dict:
        if self.uma.available()[0]:
            self._guard("vram", lambda: self.uma.set(int(index)))
        return self.state()

    # --- extra drives (own calls: lsblk isn't run every tick) ----------------

    def drives(self, action: str | None = None, path: str | None = None) -> dict:
        out: dict = {"error": None, "done": None}
        try:
            if action == "mount":
                out["done"] = storage.mount(path, self._home())
            elif action == "unmount":
                storage.unmount(path)
            elif action == "format":
                out["done"] = storage.format_ext4(path, self._home())
        except (OSError, ValueError) as e:
            log.error("storage: %s %s: %s", action, path, e)
            out["error"] = str(e)
        try:
            out["drives"] = storage.drives()
        except (OSError, ValueError) as e:
            out["drives"], out["error"] = [], out["error"] or str(e)
        return out

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

    def run_input_capture(self, seconds: int = 30) -> dict:
        if self.icap["running"]:
            return self.state()
        self.icap = {"running": True, "left": seconds, "summary": None, "report": None}
        mode = "game" if diag.game_mode() else "desktop"

        def run():
            try:
                devs = inputcap.devices()
                lines, buttons = inputcap.record(seconds, lambda left: self.icap.update(left=left))
                summary = (
                    "Button presses seen on " + ", ".join(f"{d} ({n})" for d, n in sorted(buttons.items()))
                    if buttons else "No button presses seen"
                )
                self.icap["summary"] = summary
                self.icap["report"] = diag.write_report(
                    self.user_home, [("summary", [summary]), ("events", lines), ("devices", devs)], mode, "input"
                )
            except Exception as e:
                log.exception("input capture failed")
                self.icap["summary"] = f"Recording failed: {e}"
            finally:
                self.icap.update(running=False, left=0)

        threading.Thread(target=run, daemon=True).start()
        return self.state()

    # --- updates (install any GitHub release) --------------------------------

    def check_updates(self) -> dict:
        if self.upd["busy"]:
            return self.state()
        self.upd.update(busy=True, error=None, message="Checking GitHub…")

        def run():
            try:
                self.upd["releases"] = updater.releases(self.update_kind)
                self.upd["message"] = None if self.upd["releases"] else "No releases found"
            except Exception as e:
                log.warning("update check failed: %s", e)
                self.upd.update(error=f"Couldn't reach GitHub: {e}", message=None)
            finally:
                self.upd["busy"] = False

        threading.Thread(target=run, daemon=True).start()
        return self.state()

    def install_update(self, tag: str) -> dict:
        rel = next((r for r in self.upd["releases"] if r["tag"] == tag), None)
        if self.upd["busy"] or not rel or not self.plugin_dir:
            return self.state()
        self.upd.update(busy=True, error=None, message=f"Downloading {tag}…")

        def run():
            try:
                updater.install(rel["url"], self.plugin_dir, self.update_kind)
                self.upd["message"] = f"Installed {tag}. {self.update_note}"
                # Give the panel a moment to show that before the backend goes away.
                threading.Timer(2.0, self.after_update).start()
            except Exception as e:
                log.exception("update to %s failed", tag)
                self.upd.update(error=f"Install failed, nothing was changed: {e}", message=None, busy=False)

        threading.Thread(target=run, daemon=True).start()
        return self.state()

    def rgb_zone_test(self) -> dict:
        self.errors.pop("rgb", None)
        try:
            self.zone_test = rgb.oxp_zone_test()
        except OSError as e:
            self.errors["rgb"] = f"Zone test: {e}"
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
            curve = (fan.custom_curve(s.get("points")) if s["curve"] == "custom"
                     else fan.CURVES.get(s["curve"], fan.CURVES["balanced"]))
            target = fan.interpolate(curve, temp or 100)
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

    FAN_RETRY = 30.0
    FAN_RESTARTS = 6

    def tick_fan_restart(self) -> None:
        """Take the fan up again after an EC error (they're rare and pass)."""
        if self.fan or self.fan_retry_at is None or time.monotonic() < self.fan_retry_at:
            return
        now = time.monotonic()
        self.fan_restarts = [t for t in self.fan_restarts if now - t < 3600]
        if len(self.fan_restarts) >= self.FAN_RESTARTS:
            self.fan_retry_at = None
            self.errors["fan"] = (f"Fan control stopped {self.FAN_RESTARTS} times this hour, so the firmware "
                                  f"runs the fan now. Pick a fan mode to take it up again. Last error: {self.fan_why}")
            return
        allow_ec = bool(self.settings["ec"]["enabled"])
        found, why = fan.find_backend(self.dev, allow_ec)
        if not found:
            log.warning("fan: not back yet (%s)", why)
            self.fan_retry_at = now + self.FAN_RETRY
            return
        last = self.fan_why
        self.fan, self.fan_why, self.fan_retry_at = found, None, None
        self.fan_restarts.append(now)
        log.info("fan: control taken up again after: %s", last)
        self.apply_fan()
        self.fan_note = f"Fan control restarted by itself at {time.strftime('%H:%M')} after: {last}"

    def tick(self) -> None:
        self.tick_fan_restart()
        self.tick_wake()
        self.gpu.sample()
        self.cpu_util.sample()
        self.tick_desktop_game()
        self.tick_autotune()
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
        if ac_changed or (isinstance(self.tdp, (tdp.RyzenAdj, tdp.AmdSmu)) and time.monotonic() - self.last_tdp_apply > 60):
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
            self.check_ip_controller()
            self.sync_inputplumber()
            self.hold_profile()
            g = self.settings["gpu"]
            if self.gpuclk.drifted(g["mode"], g.get("mhz")):
                log.info("GPU clocks changed behind our back, re-applying %s", g["mode"])
                self.apply_gpu()
        self.tick_fan()
        b = self.settings["battery"]
        self._guard("battery", lambda: self.bat.tick(int(b["limit"]), b["bypass"]))

    def uninstall(self) -> None:
        """Plugin removed: take our InputPlumber config away too."""
        self._guard("controller", self.ipconf.remove)

    def restore(self, unload: bool = False) -> None:
        """Hand control back to firmware (unload, exit, EC switch)."""
        if unload and self.settings["cpu"].get("cores") not in (None, "all") and self.cores.available()[0]:
            self._guard("cpu", lambda: self.cores.apply("all"))
        if unload and self.settings["cpu"].get("smt") is False and self.smt.on() is False:
            self._guard("cpu", lambda: self.smt.set(True))
        if unload and self.settings["cpu"].get("ccx_off") and self.ccx.on():
            self._guard("cpu", lambda: self.ccx.set(False))
        if unload:
            self.buttons.stop()
            self.swipe.stop()
            self.chord.stop()
            self.powerkey.stop()
            self._guard("memtweak", self.memtweak.restore)
            self._guard("system", self.systweak.restore)
            self._guard("controller", self.ip.restore)
            self.sleeper.stop()
            self._guard("sleep", self.padwake.restore)
            if self.thermal:
                self._guard("thermal", self.thermal.restore)
            # Clock limits go back to the driver; they're re-applied on load.
            self._guard("cpu", self.freqcap.restore)
            self._guard("gpu", self.gpuclk.restore)
            self._guard("gpu", self.gpu_profile.restore)
            if self.profile:
                self._guard("tdp", self.profile.restore)
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

    def _limits(self, g: dict, tdp_now: int | None) -> dict[str, list[str]]:
        """What holds the CPU and GPU back right now.

        Intel reports exact reasons (MSR 0x64F for the CPU, xe throttle
        files for the GPU). Elsewhere it's inferred: package power at the
        TDP limit, temperature, or a limit set in this plugin.
        """
        temp = fan.cpu_temp()
        pkg = g.get("pkg_w")
        inferred = []
        if pkg is not None and tdp_now and pkg >= 0.92 * tdp_now:
            inferred.append("tdp")
        if temp is not None and temp >= 95:
            inferred.append("thermal")
        msr = self.limit_reasons.read()
        cpu_why = list(msr) if msr is not None else list(inferred)
        s = self.settings["cpu"]
        if s.get("cap") and s.get("cap_mhz") and self.freqcap.available()[0]:
            peak = self.freqcap.peak_mhz()
            if peak and peak >= 0.97 * min(int(s["cap_mhz"]), self.freqcap.range_mhz()[1]):
                cpu_why.append("clock_limit")
        if self.boost.available()[0] and self.boost.on() is False:
            cpu_why.append("boost_off")
        gpu_why = []
        if self.gpuclk.available()[0]:
            gpu_why = self.gpuclk.throttle() if self.gpuclk.files else list(inferred)
            if self.settings["gpu"]["mode"] in ("min", "manual"):
                gpu_why.append("mode")
        if self.profile and self.profile.current() == "low-power":
            cpu_why.append("profile")
            gpu_why.append("profile")
        dedupe = lambda xs: list(dict.fromkeys(xs))
        return {"cpu": dedupe(cpu_why), "gpu": dedupe(gpu_why)}

    def _perf_state(self, g: dict, tdp_now: int | None) -> dict:
        used, total = gpu.memory_mb()
        ok = self.freqcap.available()[0]
        return {
            "cpu_mhz": self.freqcap.avg_mhz() if ok else None,
            "cpu_peak_mhz": self.freqcap.peak_mhz() if ok else None,
            "cpu_util": round(self.cpu_util.value) if self.cpu_util.value is not None else None,
            "cpu_temp_c": round(t) if (t := fan.cpu_temp()) is not None else None,
            "ram_used_mb": used, "ram_total_mb": total,
            "tdp_w": tdp_now,
            "limits": self._limits(g, tdp_now),
        }

    def _cap_state(self) -> dict:
        ok, why = self.freqcap.available()
        lo, hi = self.freqcap.range_mhz() if ok else (0, 0)
        return {
            "available": ok, "unavailable": why, "min": lo, "max": hi,
            "limits": self.freqcap.limits_mhz() if ok else [], "avg": self.freqcap.avg_mhz() if ok else None,
        }

    def _cores_state(self) -> dict:
        ok, why = self.cores.available()
        intel = self.dev.cpu == "intel"
        return {
            "available": ok, "unavailable": why,
            "fast": len(self.cores.fast), "small": len(self.cores.small),
            "fast_cores": self.core_counts[0], "small_cores": self.core_counts[1],
            "fast_name": "P-cores" if intel else "Zen 5", "small_name": "E-cores" if intel else "Zen 5c",
            "current": self.cores.current() if ok else None,
        }

    def _scx_state(self) -> dict:
        ok, why = self.scx.available()
        return {
            "available": ok, "unavailable": why,
            "schedulers": [{"name": n, "label": scx.short(n)} for n in self.scx.supported()] if ok else [],
            "modes": list(scx.MODES),
            "running": self.scx.running(),
        }

    def _gpu_clock_state(self) -> dict:
        ok, why = self.gpuclk.available()
        lo, hi = self.gpuclk.hw_range() if ok else (0, 0)
        manual = ok and self.gpuclk.manual_ok() and hi > lo
        cur_lo, cur_hi = self.gpuclk.current()
        return {
            "available": ok, "unavailable": why,
            "modes": [m for m in gpu.GpuClock.MODES if m != "manual" or manual],
            "min": lo, "max": hi, "cur_min": cur_lo, "cur_max": cur_hi,
            "throttle": self.gpuclk.throttle() if ok else [],
        }

    def state(self) -> dict:
        d = self.dev
        cur = None
        if self.tdp:
            try:
                cur = self.tdp.current()
            except Exception:
                cur = None
        gpu_state = self.gpu.state()
        rpm = None
        if self.fan:
            try:
                rpm = self.fan.rpm()
            except Exception:
                rpm = None
        return {
            "device": {"name": d.name, "board": d.board, "vendor": d.vendor, "notes": d.notes, "cpu": self.cpu_model,
                       "generic": d.key == "generic"},
            "conflicts": running_services(),
            "settings": self.settings,
            "errors": self.errors,
            "tdp": {
                "backend": getattr(self.tdp, "name", None),
                "min": d.tdp_min, "max": d.tdp_max, "default": d.tdp_default,
                "current": cur,
                "profile": self.profile.current() if self.profile else None,
            },
            "fan": {
                "backend": getattr(self.fan, "name", None),
                "unavailable": self.fan_why,
                "curves": [*fan.CURVES, "custom"],
                "custom_temps": list(fan.CUSTOM_TEMPS),
                "custom_default": fan.CUSTOM_DEFAULT,
                "rpm": rpm,
                "percent": self.fan_pct,
                "temp": fan.cpu_temp(),
                "safety": self.safety,
                "watchdog": self._watchdog_alive(),
                "restarting": self.fan_retry_at is not None,
                "note": self.fan_note,
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
                for name, ctl in (*self._toggles(), ("ccx", self.ccx), ("smt", self.smt))
                for ok, why in [
                    (False, "the CCXs are the two core types; use Cores for apps and games")
                    if name == "ccx" and self.ccx_hybrid else ctl.available()
                ]
            }
            | {"scx": self._scx_state(), "cores": self._cores_state()}
            | {"ccx_keep": self.ccx.keep_count(), "ccx_total": self.ccx.total()}
            | {"cap": self._cap_state()},
            "gpu": gpu_state | {
                "clock": self._gpu_clock_state(), "pipes": self.gfx_pipes,
                "profile": {
                    "available": self.gpu_profile.available()[0],
                    "options": self.gpu_profile.options(),
                    "current": self.gpu_profile.current(),
                },
            },
            "perf": self._perf_state(gpu_state, cur),
            "display": self._display_state(),
            "version": self.version,
            "home": self.user_home,
            "gamescope": self._gamescope_state(),
            "sdr_brightness": self._sdr_brightness_state(),
            "input_capture": dict(self.icap),
            "update": {**self.upd, "releases": [{**r, "notes": r["notes"][:600]} for r in self.upd["releases"]]},
            "chord": {"value": self.chord_setting() or "off", "pads": len(self.chord.nodes)},
            "swipe": {
                "available": self.swipe_avail or self.swipe.node is not None,
                "listening": self.swipe.node,
                "learning": self.swipe.calibrating,
                "last_touch": self.swipe.last_touch,
                "edge": self.settings["controller"].get("swipe_edge"),
                "last_swipe": self.swipe.last_swipe,
            },
            "buttons": {
                "available": self.buttons.available()[0],
                "unavailable": self.buttons.available()[1],
                "listening": self.buttons.node,
                "help": self.buttons.help,
            },
            "inputplumber": {
                "devices": len(self.ip.devices),
                "targets": inputplumber.TARGETS,
                "configurable": self.ipconf.supported(),
                "status": self.ipconf.status() if self.ipconf.supported() else None,
                "failed": self.settings["controller"].get("ip_failed"),
                "installed": self.ipconf.installed(),
                "install": "sudo pacman -S inputplumber" if os.path.exists("/usr/bin/pacman") and not os.path.exists("/run/ostree-booted") else None,
            },
            "ai": {"enabled": self.ai.enabled(), "vars": self.ai.vars},
            "memtweak": {"available": self.memtweak.available(), "pending": self.memtweak.pending()},
            "games": {
                "enabled": self.settings["games"]["enabled"],
                "game": {"appid": self.game[0], "name": self.game[1]} if self.game else None,
                "profile": bool(self.game and self.game[0] in self.settings["games"]["profiles"]),
                "count": len(self.settings["games"]["profiles"]),
                "auto": bool((self.game_profile() or {}).get("auto")),
                "auto_available": self.freqcap.available()[0],
                "auto_status": self.autotune.status,
                "steam": not self.desktop or games.steam_installed(self.user_home),
                "desktop": self.desktop,
            },
            "system": self.systweak.state(),
            "topuse": self.topuse,
            "rebar": rebar.status(self.gpu.dev, self.gpu.driver),
            "upscale": self.upscale.state(),
            "lsfg": {**self.lsfg.state(), "busy": self.lsfg_busy},
            "thermal": {
                "available": bool(self.thermal),
                "range": list(self.thermal.range) if self.thermal else None,
                "backend": getattr(self.thermal, "name", None),
            },
            "vram": {
                "available": self.uma.available()[0],
                "unavailable": self.uma.available()[1],
                "options": self.uma.options() if self.uma.available()[0] else [],
                "current": self.uma.current(),
                "boot": self.uma.boot,
                "now_mb": self.uma.vram_mb(),
                "ram_gb": gpu.installed_ram_gb(self.uma.vram_mb()),
                "suggested_gb": gpu.suggested_vram_gb(gpu.installed_ram_gb(self.uma.vram_mb())),
                "bios_path": gpu.bios_path(self.dev.vendor or ""),
            },
            "gpu_mem": self._gpumem_state(),
            "sleep": {
                "rgb_off_available": bool(self.rgb),
                "pad_wake_available": bool(self.padwake.ids),
                "pad_wake": self.pad_wake_on(),
                "power_key": self.power_key_on(),
                "power_key_listening": self.powerkey.nodes,
                "note": self.sleep_note,
                "last_wake": self.last_wake,
                "pad_wake_text": PAD_WAKE_TEXT.get(self.buttons.key),
            },
            "ec": {
                "supported": bool(self.dev.ec_fan or self.dev.ec_charge),
                "lockdown": ec.lockdown(),
            },
            "rgb": {
                "backend": getattr(self.rgb, "name", None),
                "modes": getattr(self.rgb, "modes", []),
                "effects": getattr(self.rgb, "effects", []),
                "zone_test": self.zone_test,
                "zone_test_available": self.dev.rgb == "oxp",
            },
        }
