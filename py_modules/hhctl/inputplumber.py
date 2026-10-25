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
"""

import json
import logging
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
DEVICES = {"ONEXPLAYER ONEXFLY"}

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

    def tick(self, buttons: bool, target: str | None) -> None:
        """Make InputPlumber match the settings; cheap when it already does."""
        # Most devices have no InputPlumber (or none of DEVICES): look rarely.
        if not self.devices and not self.prev_profile and not self.prev_targets:
            if time.monotonic() - self._looked < 60:
                return
            self._looked = time.monotonic()
        self.devices = self._composites()
        for path in self.devices:
            self._profile(path, buttons)
            self._target(path, target, buttons)

    def _profile(self, path: str, want: bool) -> None:
        cur = _get(path, COMPOSITE, "ProfilePath")
        if cur is None:
            return
        if want and cur != self.profile_path:
            with open(self.profile_path, "w") as f:
                json.dump(profile(), f, indent=1)  # JSON is YAML too
            self.prev_profile.setdefault(path, cur)
            log.info("inputplumber: %s profile %s -> %s", path, cur or "(none)", self.profile_path)
            _call(path, "LoadProfilePath", "s", self.profile_path)
        elif not want and cur == self.profile_path:
            self._restore_profile(path)

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
