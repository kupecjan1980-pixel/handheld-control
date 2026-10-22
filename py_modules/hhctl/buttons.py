"""Extra buttons that open Steam things (NEXT 2 so far).

The NEXT 2's extra buttons are keys on the 'AYANEO COMPOSITE DEVICE'
keyboard (input report 2026-10-22): AYN sends F23, the button next to it
sends LEFTMETA+D. This listener only reads that keyboard (no EVIOCGRAB), so
nothing is taken away from Steam or games, and there's nothing to restore.
Actions go to the panel, which opens the Quick Access Menu or the keyboard.

The back buttons (F17-F20) and ☰ (the gamepad's own Start button) need a
virtual controller to become paddles / QAM; that comes later.
"""

import logging
import os
import select
import struct
import threading

from . import sysfs
from .device import Device

log = logging.getLogger("hhctl")

SYS_INPUT = "/sys/class/input"
DEV = "/dev/input"
EVENT = struct.Struct("llHHi")
EV_KEY = 0x01
KEY_D, KEY_LEFTMETA, KEY_RIGHTMETA = 32, 125, 126
KEY_F23 = 193

# board -> (vendor, product, {key: action}, {key with Meta held: action})
MAPS = {
    "AB09": ("1c4f", "0002", {KEY_F23: "open_plugin"}, {KEY_D: "keyboard"}),
}


def has_key(caps: str, code: int) -> bool:
    """`caps` is a capabilities/key bitmap: hex longs, most significant first."""
    words = caps.split()
    bits = 64
    idx = len(words) - 1 - code // bits
    return 0 <= idx < len(words) and bool(int(words[idx], 16) >> (code % bits) & 1)


def find_keyboard(vendor: str, product: str, code: int) -> str | None:
    try:
        names = sorted(n for n in os.listdir(SYS_INPUT) if n.startswith("event"))
    except OSError:
        return None
    for n in names:
        d = f"{SYS_INPUT}/{n}/device"
        if sysfs.read(f"{d}/id/vendor") != vendor or sysfs.read(f"{d}/id/product") != product:
            continue
        if has_key(sysfs.read(f"{d}/capabilities/key") or "0", code):
            return f"{DEV}/{n}"
    return None


class Buttons:
    RETRY = 3.0  # seconds between looks for the keyboard (it reappears after resume)

    def __init__(self, dev: Device, emit):
        self.map = MAPS.get(dev.board)
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
        probe = next(iter(keys))
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
            finally:
                os.close(fd)
                self.node = None
            self._stop.wait(1.0)  # gone (resume, unplug): don't spin while it comes back

    def _read(self, fd: int, keys: dict, meta_keys: dict) -> None:
        meta = set()
        while not self._stop.is_set():
            ready, _, _ = select.select([fd], [], [], 0.5)
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
                if value != 1:  # presses only, not release or repeat
                    continue
                action = meta_keys.get(code) if meta else keys.get(code)
                if action:
                    log.info("buttons: key %d -> %s", code, action)
                    try:
                        self.emit(action)
                    except Exception:
                        log.exception("buttons: emit failed")
