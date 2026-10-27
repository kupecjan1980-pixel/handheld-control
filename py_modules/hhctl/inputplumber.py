"""InputPlumber: the controller service Bazzite 44 ships instead of HHD.

InputPlumber grabs the built-in gamepad and the buttons' keyboard and makes a
virtual controller of its own, so the button listener can't see the buttons
directly. Instead it gets a device profile that turns the buttons into keys
on InputPlumber's virtual keyboard ("InputPlumber Keyboard"), which the
listener reads, and it is told which controller to emulate (Xbox Elite
instead of its Steam controller). busctl talks to it, like scx_loader.

The profile it had and the controllers it emulated are remembered and put
back when the feature is turned off or the plugin unloads. InputPlumber makes
the device anew after resume (back to its defaults), so tick() re-applies.

The NEXT 2 (no InputPlumber config) and the ONEXPLAYER 3 (whose stock config
grabs the keyboard the button listener reads) get device configs of our own
in /etc/inputplumber/devices.d, named to sort before the stock ones so they
claim the devices first: the gamepad as an Xbox Elite, the back buttons as
paddles, and the buttons' keyboard left alone (passthrough, never grabbed),
so AYN / Win+D / the OXP home and keyboard buttons work as before.
"""

import json
import logging
import os
import re
import subprocess
import time

from . import proc

log = logging.getLogger("hhctl")

BUS = ["busctl", "--system", "--json=short"]
SVC = "org.shadowblip.InputPlumber"
COMPOSITE = "org.shadowblip.Input.CompositeDevice"
TARGET = "org.shadowblip.Input.Target"
DEFAULT_PROFILE = "/usr/share/inputplumber/profiles/default.yaml"

# Gamepad button (from InputPlumber's capability map) -> key on its virtual
# keyboard; buttons.py maps the keys to actions.
KEYS = {"QuickAccess": "KeyF23", "Keyboard": "KeyF24"}
# InputPlumber device config names whose buttons are KEYS. OneXFly: Turbo is
# QuickAccess, the keyboard button Keyboard (capability map oxp7).
DEVICES = {"ONEXPLAYER ONEXFLY", "AYANEO NEXT 2 (HHC)", "ONEXPLAYER 3 (HHC)"}

GAMEPADS = {"deck", "deck-uhid", "ds5", "ds5-edge", "gamepad", "hori-steam", "xb360", "xbox-elite", "xbox-series"}
TARGETS = ["xbox-elite", "xbox-series", "xb360", "deck-uhid"]

# The rest of InputPlumber's default profile, kept as it is.
BASE_MAPPINGS = [
    ("LeftTop", {"gamepad": {"button": "LeftPaddle1"}}),
    ("RightTop", {"gamepad": {"button": "RightPaddle1"}}),
    ("QuickAccess2", {"gamepad": {"button": "Screenshot"}}),
]
DIALS = [
    ("LeftStickDial", "counter-clockwise", "KeyVolumeDown"),
    ("LeftStickDial", "clockwise", "KeyVolumeUp"),
    ("RightStickDial", "counter-clockwise", "KeyBrightnessDown"),
    ("RightStickDial", "clockwise", "KeyBrightnessUp"),
]


def profile() -> dict:
    mapping = [
        {"name": f"HHC {btn}", "source_event": {"gamepad": {"button": btn}}, "target_events": [{"keyboard": key}]}
        for btn, key in KEYS.items()
    ]
    mapping += [
        {"name": btn, "source_event": {"gamepad": {"button": btn}}, "target_events": [target]}
        for btn, target in BASE_MAPPINGS
    ]
    mapping += [
        {"name": f"{dial} {way}", "source_event": {"gamepad": {"dial": {"name": dial, "direction": way}}},
         "target_events": [{"keyboard": key}]}
        for dial, way, key in DIALS
    ]
    return {"version": 1, "kind": "DeviceProfile", "name": "Handheld Control", "mapping": mapping}


def _run(args: list[str], timeout: float = 10) -> subprocess.CompletedProcess:
    return proc.run(BUS + args, timeout=timeout)


def _get(path: str, iface: str, prop: str):
    try:
        r = _run(["get-property", SVC, path, iface, prop], timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout).get("data")
    except ValueError:
        return None


def _call(path: str, method: str, *args: str) -> None:
    try:
        r = _run(["call", SVC, path, COMPOSITE, method, *args], timeout=15)
    except (OSError, subprocess.SubprocessError) as e:
        raise OSError(f"InputPlumber {method}: {e}") from e
    if r.returncode != 0:
        raise OSError((r.stderr or b"").decode(errors="replace").strip() or f"InputPlumber {method} failed")


class InputPlumber:
    def __init__(self, profile_path: str):
        self.profile_path = profile_path
        self.devices: list[str] = []  # composite device paths we manage
        # path -> what InputPlumber had before we changed it
        self.prev_profile: dict[str, str] = {}
        self.prev_targets: dict[str, list[str]] = {}
        self._looked = -600.0
        self._next = 0.0  # next check while in sync
        self.sets: list[float] = []  # checks in which we changed InputPlumber
        self._changed = False  # changed something in this check
        self.gave_up: str | None = None  # why we stopped (something else keeps changing it)

    def _composites(self) -> list[str]:
        try:
            r = _run(["--list", "tree", SVC], timeout=5)
        except (OSError, subprocess.SubprocessError):
            return []
        if r.returncode != 0:
            return []
        paths = re.findall(r"/org/shadowblip/InputPlumber/CompositeDevice\d+", r.stdout.decode(errors="replace"))
        return [p for p in dict.fromkeys(paths) if _get(p, COMPOSITE, "Name") in DEVICES]

    def _targets(self, path: str) -> list[str]:
        out = []
        for t in _get(path, COMPOSITE, "TargetDevices") or []:
            kind = _get(t, TARGET, "DeviceType")
            if kind:
                out.append(kind)
        return out

    RECHECK = 30.0  # s between checks while it matches
    # More checks than this that had to change something, within this long:
    # someone else keeps changing it back. Counted per check, not per call: a
    # check sets the profile and the outputs, and one switch by something else
    # (OpenGamepadUI as Game Mode starts, OneXFly 2026-10-27) needs one check.
    FIGHT = (3, 300.0)

    def tick(self, buttons: bool, target: str | None) -> bool:
        """Make InputPlumber match the settings; cheap when it already does.
        True when it changed something (the controller was made anew).

        Every change makes InputPlumber recreate the controller (games see it
        disconnect), so if something else keeps changing it back, we stop
        instead of taking turns with it."""
        if self.gave_up:
            return False
        now = time.monotonic()
        if now < self._next:
            return False
        self._next = now + self.RECHECK
        # Most devices have no InputPlumber (or none of DEVICES): look rarely.
        if not self.devices and not self.prev_profile and not self.prev_targets:
            if time.monotonic() - self._looked < 60:
                return False
            self._looked = time.monotonic()
        self.devices = self._composites()
        self._changed = False
        for path in self.devices:
            self._profile(path, buttons)
            self._target(path, target, buttons)
        return self._changed

    def _profile(self, path: str, want: bool) -> None:
        cur = _get(path, COMPOSITE, "ProfilePath")
        if cur is None:
            return
        if want and cur != self.profile_path:
            with open(self.profile_path, "w") as f:
                json.dump(profile(), f, indent=1)  # JSON is YAML too
            self.prev_profile.setdefault(path, cur)
            log.info("inputplumber: %s profile %s -> %s", path, cur or "(none)", self.profile_path)
            self._changing("profile")
            _call(path, "LoadProfilePath", "s", self.profile_path)
        elif not want and cur == self.profile_path:
            self._restore_profile(path)

    def _changing(self, what: str) -> None:
        if self._changed:
            return
        self._changed = True
        now = time.monotonic()
        n, window = self.FIGHT
        self.sets = [t for t in self.sets if now - t < window] + [now]
        if len(self.sets) > n:
            self.gave_up = (f"something else keeps changing InputPlumber's {what} back, so HHC stopped "
                            "changing it (each change reconnects the controller)")
            log.warning("inputplumber: %s", self.gave_up)
            raise OSError(self.gave_up)

    def _restore_profile(self, path: str) -> None:
        prev = self.prev_profile.pop(path, None) or DEFAULT_PROFILE
        log.info("inputplumber: %s profile back to %s", path, prev)
        _call(path, "LoadProfilePath", "s", prev)

    def _target(self, path: str, want: str | None, keyboard: bool) -> None:
        cur = self._targets(path)
        if not cur:
            return
        if want:
            new = [want] + [t for t in cur if t not in GAMEPADS]
            if keyboard and "keyboard" not in new:
                new.append("keyboard")
            if sorted(new) == sorted(cur):
                return
            self.prev_targets.setdefault(path, cur)
            log.info("inputplumber: %s targets %s -> %s", path, cur, new)
            self._changing("controller outputs")
            _call(path, "SetTargetDevices", "as", str(len(new)), *new)
        elif path in self.prev_targets:
            self._restore_targets(path)

    def _restore_targets(self, path: str) -> None:
        prev = self.prev_targets.pop(path)
        log.info("inputplumber: %s targets back to %s", path, prev)
        _call(path, "SetTargetDevices", "as", str(len(prev)), *prev)

    def restore(self) -> None:
        for path in list(self.prev_profile):
            self._restore_profile(path)
        for path in list(self.prev_targets):
            self._restore_targets(path)


# --- device configs of our own (NEXT 2, ONEXPLAYER 3) ------------------------

ETC_DEVICES = "/etc/inputplumber/devices.d"
ETC_MAPS = "/etc/inputplumber/capability_maps.d"


def _paddle(key: str, button: str) -> dict:
    return {"name": button, "source_events": [{"keyboard": key}], "target_event": {"gamepad": {"button": button}}}


# buttons.py device key -> (device config, capability map or None)
CONFIGS = {
    # NEXT 2 (report 2026-10-22): xpad gamepad; the back buttons are F17-F20 on
    # the AYANEO keyboard (user-confirmed: F19 L4, F17 L5, F20 R4, F18 R5).
    "ayaneo_next2": (
        {
            "version": 1, "kind": "CompositeDevice", "name": "AYANEO NEXT 2 (HHC)",
            "matches": [{"dmi_data": {"board_name": "AB09"}}],
            "source_devices": [
                {"group": "gamepad", "evdev": {"name": "Microsoft X-Box 360 pad", "vendor_id": "045e",
                                               "product_id": "028e", "handler": "event*"}},
                # Both interfaces (keyboard + silent mouse) match: unique false keeps
                # them in this one device (else the second made a second Elite pad).
                {"group": "keyboard", "passthrough": True, "unique": False,
                 "evdev": {"vendor_id": "1c4f", "product_id": "0002", "handler": "event*"},
                 "events": {"exclude": ["*"], "include": ["Keyboard:KeyF17", "Keyboard:KeyF18",
                                                          "Keyboard:KeyF19", "Keyboard:KeyF20"]}},
            ],
            "options": {"auto_manage": True},
            "target_devices": ["xbox-elite", "mouse", "keyboard"],
            "capability_map_id": "hhc_next2",
        },
        {
            "version": 1, "kind": "CapabilityMap", "name": "AYANEO NEXT 2 (HHC)", "id": "hhc_next2",
            "mapping": [_paddle("KeyF19", "LeftPaddle1"), _paddle("KeyF17", "LeftPaddle2"),
                        _paddle("KeyF20", "RightPaddle1"), _paddle("KeyF18", "RightPaddle2")],
            "filtered_events": [],
        },
    ),
    # ONEXPLAYER 3: InputPlumber's own config without its keyboard source; the
    # vendor HID only reads (M1/M2 -> paddles), its Keyboard/Guide are left out
    # because the button listener already handles those buttons.
    "oxp3": (
        {
            "version": 1, "kind": "CompositeDevice", "name": "ONEXPLAYER 3 (HHC)",
            "matches": [{"dmi_data": {"product_name": "ONEXPLAYER 3", "sys_vendor": "ONE-NETBOOK"}}],
            "source_devices": [
                {"group": "gamepad", "evdev": {"name": "Microsoft X-Box 360 pad", "phys_path": "usb-0000:00:14.0-7/input0",
                                               "handler": "event*"}},
                # Claimed so the stock config can't take (and grab) it; never grabbed, nothing read.
                {"group": "keyboard", "passthrough": True,
                 "evdev": {"name": "HID 1a86:fe00", "phys_path": "usb-0000:00:14.0-5/input0", "handler": "event*"},
                 "events": {"exclude": ["*"]}},
                {"group": "gamepad", "hidraw": {"vendor_id": 0x1A86, "product_id": 0xFE00, "interface_num": 2},
                 "events": {"exclude": ["Gamepad:Button:Keyboard", "Gamepad:Button:Guide"]}},
                {"group": "imu", "iio": {"name": "bmi260", "mount_matrix": {"x": [0, 1, 0], "y": [1, 0, 0], "z": [0, 0, 1]}}},
            ],
            "options": {"auto_manage": True},
            "target_devices": ["xbox-elite", "mouse", "keyboard"],
        },
        None,
    ),
}


def _systemctl(*args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    return proc.run(["systemctl", *args], timeout=timeout)


class Configs:
    """Writes / removes our device configs and (re)starts InputPlumber."""

    def __init__(self, key: str, etc_devices: str = ETC_DEVICES, etc_maps: str = ETC_MAPS):
        self.key = key
        self.conf = CONFIGS.get(key)
        self.dev_path = f"{etc_devices}/40-hhc-{key}.yaml"
        self.map_path = f"{etc_maps}/hhc-{key}.yaml"
        self._status: tuple[str, float] | None = None

    def supported(self) -> bool:
        return self.conf is not None

    def status(self) -> str:
        """'missing' (not installed), 'stopped' or 'running'; cached 10 s."""
        if self._status and time.monotonic() - self._status[1] < 10:
            return self._status[0]
        try:
            if _systemctl("cat", "inputplumber.service", timeout=10).returncode != 0:
                st = "missing"
            else:
                st = "running" if _systemctl("is-active", "--quiet", "inputplumber.service", timeout=10).returncode == 0 else "stopped"
        except (OSError, subprocess.SubprocessError):
            st = "missing"
        self._status = (st, time.monotonic())
        return st

    def _files(self) -> dict[str, str | None]:
        dev, cmap = self.conf
        return {self.dev_path: json.dumps(dev, indent=1) + "\n",
                self.map_path: json.dumps(cmap, indent=1) + "\n" if cmap else None}

    def installed(self) -> bool:
        return os.path.exists(self.dev_path)

    def name(self) -> str | None:
        return self.conf[0]["name"] if self.conf else None

    def health(self) -> str | None:
        """None when InputPlumber made our controller, else what's wrong."""
        try:
            r = _run(["--list", "tree", SVC], timeout=5)
        except (OSError, subprocess.SubprocessError) as e:
            return f"InputPlumber didn't answer: {e}"
        paths = dict.fromkeys(re.findall(r"/org/shadowblip/InputPlumber/CompositeDevice\d+", r.stdout.decode(errors="replace")))
        mine = [p for p in paths if _get(p, COMPOSITE, "Name") == self.name()]
        if not mine:
            return "InputPlumber didn't take the controller with our config"
        if not _get(mine[0], COMPOSITE, "SourceDevicePaths"):
            return "InputPlumber found none of the controller's devices"
        targets = _get(mine[0], COMPOSITE, "TargetDevices") or []
        if not any((_get(t, TARGET, "DeviceType") or "") in GAMEPADS for t in targets):
            return "InputPlumber made no virtual controller"
        return None

    def report(self) -> list[tuple[str, list[str]]]:
        """What to look at when it didn't work (for the user to send)."""
        def out(cmd: list[str]) -> list[str]:
            try:
                r = proc.run(cmd, timeout=15)
                return (r.stdout + r.stderr).decode(errors="replace").splitlines()
            except (OSError, subprocess.SubprocessError) as e:
                return [f"{cmd[0]}: {e}"]
        sections = [
            ("inputplumber version", out(["inputplumber", "--version"])),
            ("service", out(["systemctl", "status", "--no-pager", "-n", "0", "inputplumber.service"])),
            ("journal (this boot)", out(["journalctl", "-u", "inputplumber.service", "-b", "--no-pager", "-n", "250"])),
            ("D-Bus objects", out(["busctl", "--system", "--list", "tree", SVC])),
            ("input devices", out(["cat", "/proc/bus/input/devices"])),
        ]
        for path in (self.dev_path, self.map_path):
            try:
                with open(path) as f:
                    sections.append((path, f.read().splitlines()))
            except OSError:
                pass
        return sections

    def ensure(self) -> bool:
        """Our config in place and InputPlumber running with it; True if it
        was (re)started, so the caller checks the controller came up."""
        if not self.conf or self.status() == "missing":
            return False
        changed = False
        for path, text in self._files().items():
            if text is None:
                continue
            try:
                with open(path) as f:
                    if f.read() == text:
                        continue
            except OSError:
                pass
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(text)
            changed = True
        if self.status() != "running":
            log.info("inputplumber: starting the service")
            r = _systemctl("enable", "--now", "inputplumber.service")
            if r.returncode != 0:
                raise OSError((r.stderr or b"").decode(errors="replace").strip() or "couldn't start InputPlumber")
            self._status = None
            return True
        if changed:
            log.info("inputplumber: config %s written, restarting the service", self.dev_path)
            _systemctl("restart", "inputplumber.service")
        return changed

    def remove(self) -> None:
        if not self.conf:
            return
        gone = False
        for path in (self.dev_path, self.map_path):
            try:
                os.remove(path)
                gone = True
            except FileNotFoundError:
                pass
        if gone and self.status() == "running":
            log.info("inputplumber: our config removed, restarting the service")
            _systemctl("restart", "inputplumber.service")
