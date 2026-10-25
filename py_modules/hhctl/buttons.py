"""Extra buttons that open Steam things.

The NEXT 2's extra buttons are keys on the 'AYANEO COMPOSITE DEVICE'
keyboard (input report 2026-10-22): AYN sends F23, the button next to it
sends LEFTMETA+D. A key can have a tap and a hold action: tapping AYN opens
this plugin, holding it opens the regular Quick Access Menu (☰ stays Start,
so games keep it). This listener only reads that keyboard (no EVIOCGRAB), so
nothing is taken away from Steam or games, and there's nothing to restore.
Actions go to the panel, which opens the Quick Access Menu or the keyboard.

The ONEXPLAYER 3's keyboard (1a86:fe00, input report 2026-10-22) sends
Ctrl+Win+O (the keyboard button) and Ctrl+Win+F17 (the home button, held as
long as the button is), handled the same way.

On the OneXFly, InputPlumber (Bazzite 44) grabs the buttons; inputplumber.py
has it send Turbo and the keyboard button as F23 and F24 on its virtual
keyboard, which is read here instead (matched by name, it has no USB ids).

The back buttons (NEXT 2: F17-F20; OXP3: vendor HID only) need a virtual
controller to become paddles; that comes later.
"""

import logging
import os
import select
import struct
import threading
import time

from . import sysfs
from .device import Device

log = logging.getLogger("hhctl")

SYS_INPUT = "/sys/class/input"
DEV = "/dev/input"
EVENT = struct.Struct("llHHi")
EV_KEY = 0x01
KEY_O, KEY_D, KEY_LEFTMETA, KEY_RIGHTMETA = 24, 32, 125, 126
KEY_F17, KEY_F23, KEY_F24 = 187, 193, 194

HOLD = 0.5  # seconds held before a key's hold action fires (while still held)

# device key -> (vendor, product, {key: action or (tap, hold)}, {key with Meta held: same});
# vendor None: product is the input device's name.
MAPS = {
    "ayaneo_next2": ("1c4f", "0002", {KEY_F23: "open_plugin"}, {KEY_D: "qam"}),
    "oxp3": ("1a86", "fe00", {}, {KEY_F17: "open_plugin", KEY_O: "qam"}),
    "onexfly": (None, "InputPlumber Keyboard", {KEY_F23: "open_plugin", KEY_F24: "qam"}, {}),
}
HELP = {
    "ayaneo_next2": "AYN opens Handheld Control · the button next to it opens Quick Access",
    "oxp3": "Home button opens Handheld Control · keyboard button opens Quick Access",
    "onexfly": "Turbo opens Handheld Control · keyboard button opens Quick Access (through InputPlumber)",
}


def map_key(dev: Device) -> str:
    # The OneXFly family runs as a generic device; only its buttons are known.
    if dev.key == "generic" and dev.name.upper().startswith("ONEXPLAYER F1"):
        return "onexfly"
    return dev.key


def has_key(caps: str, code: int) -> bool:
    """`caps` is a capabilities/key bitmap: hex longs, most significant first."""
    words = caps.split()
    bits = 64
    idx = len(words) - 1 - code // bits
    return 0 <= idx < len(words) and bool(int(words[idx], 16) >> (code % bits) & 1)


def find_keyboard(vendor: str | None, product: str, code: int) -> str | None:
    try:
        names = sorted(n for n in os.listdir(SYS_INPUT) if n.startswith("event"))
    except OSError:
        return None
    for n in names:
        d = f"{SYS_INPUT}/{n}/device"
        if vendor is None:
            if sysfs.read(f"{d}/name") != product:
                continue
        elif sysfs.read(f"{d}/id/vendor") != vendor or sysfs.read(f"{d}/id/product") != product:
            continue
        if has_key(sysfs.read(f"{d}/capabilities/key") or "0", code):
            return f"{DEV}/{n}"
    return None


class Buttons:
    RETRY = 3.0  # seconds between looks for the keyboard (it reappears after resume)

    def __init__(self, dev: Device, emit):
        self.key = map_key(dev)
        self.map = MAPS.get(self.key)
        self.help = HELP.get(self.key)
        self.emit = emit
        self.enabled = False
        self.node: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def available(self) -> tuple[bool, str | None]:
        if not self.map:
            return False, "no button map for this device yet"
        return True, None

    def start(self) -> None:
        if not self.map or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="hhctl-buttons", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        vendor, product, keys, meta_keys = self.map
        probe = next(iter(keys or meta_keys))
        while not self._stop.is_set():
            node = find_keyboard(vendor, product, probe)
            if not node:
                self._stop.wait(self.RETRY)
                continue
            try:
                fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
            except OSError as e:
                log.warning("buttons: cannot open %s: %s", node, e)
                self._stop.wait(self.RETRY)
                continue
            self.node = node
            log.info("buttons: listening on %s", node)
            try:
                self._read(fd, keys, meta_keys)
            except Exception:
                log.exception("buttons: listener failed, starting over")
            finally:
                os.close(fd)
                self.node = None
            self._stop.wait(1.0)  # gone (resume, unplug): don't spin while it comes back

    def _fire(self, action: str) -> None:
        log.info("buttons: %s", action)
        try:
            self.emit(action)
        except Exception:
            log.exception("buttons: emit failed")

    def _read(self, fd: int, keys: dict, meta_keys: dict) -> None:
        meta = set()
        # tap/hold key -> (pressed at, or None once the hold fired; its (tap, hold))
        held: dict[int, tuple[float | None, tuple[str, str]]] = {}
        while not self._stop.is_set():
            now = time.monotonic()
            for code, (t, act) in list(held.items()):
                if t is not None and now - t >= HOLD:
                    held[code] = (None, act)
                    self._fire(act[1])
            waits = [t + HOLD - now for t, _ in held.values() if t is not None]
            ready, _, _ = select.select([fd], [], [], max(0.01, min(waits)) if waits else 0.5)
            if not ready:
                continue
            try:
                data = os.read(fd, EVENT.size * 64)
            except BlockingIOError:
                continue
            except OSError:
                return  # unplugged (resume): look for it again
            if not data:
                return
            for off in range(0, len(data) - EVENT.size + 1, EVENT.size):
                _, _, etype, code, value = EVENT.unpack_from(data, off)
                if etype != EV_KEY:
                    continue
                if code in (KEY_LEFTMETA, KEY_RIGHTMETA):
                    (meta.add if value else meta.discard)(code)
                    continue
                if value == 0 and code in held:
                    t, act = held.pop(code)
                    if t is not None:  # released before the hold fired
                        self._fire(act[0])
                    continue
                action = meta_keys.get(code) if meta else keys.get(code)
                if isinstance(action, tuple):
                    if value == 1:
                        held[code] = (time.monotonic(), action)
                    continue
                if action and value == 1:  # presses only, not release or repeat
                    self._fire(action)
