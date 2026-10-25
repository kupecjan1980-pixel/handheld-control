"""A controller shortcut that opens the plugin: Xbox + B, or View + Menu.

For PCs in Game Mode and any Xbox-style controller (Elite, 8BitDo...), where
there's no extra button like the handhelds' AYN or Turbo. Gamepads are read
read-only (no grab), so Steam and games still get every press. B, the Xbox
button, View and Menu have the same codes on every Linux gamepad driver
(xpad, xpadneo, hid-generic); X and Y don't, so no shortcut uses them.
Steam's own Xbox + B shortcut needs a long hold (closes the game), so a
quick press is free and keeps Steam's menu from opening.
"""

import logging
import os
import select
import struct
import threading
import time

from . import sysfs

log = logging.getLogger("hhctl")

SYS_INPUT = "/sys/class/input"
DEV = "/dev/input"
EVENT = struct.Struct("llHHi")
EV_KEY = 0x01
BTN_SOUTH, BTN_EAST, BTN_SELECT, BTN_START, BTN_MODE = 0x130, 0x131, 0x13A, 0x13B, 0x13C
CHORDS = {"guide_b": (BTN_MODE, BTN_EAST), "select_start": (BTN_SELECT, BTN_START)}
RESCAN = 5.0  # seconds between looks for controllers plugged in or paired since


def _has(bitmap: str, code: int) -> bool:
    words = bitmap.split()
    idx = len(words) - 1 - code // 64
    return 0 <= idx < len(words) and bool(int(words[idx], 16) >> (code % 64) & 1)


def find_gamepads() -> list[str]:
    try:
        names = sorted(n for n in os.listdir(SYS_INPUT) if n.startswith("event"))
    except OSError:
        return []
    out = []
    for n in names:
        keys = sysfs.read(f"{SYS_INPUT}/{n}/device/capabilities/key") or "0"
        if all(_has(keys, c) for c in (BTN_SOUTH, BTN_EAST, BTN_MODE, BTN_START)):
            out.append(f"{DEV}/{n}")
    return out


class Chord:
    """Fires once when the second button of the chord goes down while the
    first is held (either order)."""

    def __init__(self, buttons: tuple[int, int], fire):
        self.buttons, self.fire = set(buttons), fire
        self.down: set[int] = set()

    def key(self, code: int, value: int) -> None:
        if code not in self.buttons or value == 2:
            return
        if value:
            self.down.add(code)
            if self.down == self.buttons:
                self.fire()
        else:
            self.down.discard(code)


class PadChord:
    def __init__(self, emit, get_chord):
        self.emit = emit  # emit("open_plugin")
        self.get_chord = get_chord  # "guide_b", "select_start" or None
        self.nodes: list[str] = []
        self._last = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="hhctl-chord", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _fire(self) -> None:
        now = time.monotonic()
        if now - self._last < 1.0:
            return  # the same press from a second (virtual) device
        self._last = now
        log.info("chord: open_plugin")
        try:
            self.emit("open_plugin")
        except Exception:
            log.exception("chord: emit failed")

    def _run(self) -> None:
        while not self._stop.is_set():
            chord = CHORDS.get(self.get_chord() or "")
            nodes = find_gamepads() if chord else []
            fds: dict[int, Chord] = {}
            for node in nodes:
                try:
                    fds[os.open(node, os.O_RDONLY | os.O_NONBLOCK)] = Chord(chord, self._fire)
                except OSError as e:
                    log.warning("chord: cannot open %s: %s", node, e)
            self.nodes = nodes if fds else []
            try:
                self._read(fds, nodes)
            except Exception:
                log.exception("chord: listener failed, starting over")
            finally:
                for fd in fds:
                    os.close(fd)
                self.nodes = []

    def _read(self, fds: dict[int, Chord], nodes: list[str]) -> None:
        """Until a controller comes or goes, or the shortcut changes."""
        chord = self.get_chord()
        next_scan = time.monotonic() + RESCAN
        while not self._stop.is_set():
            if fds:
                ready, _, _ = select.select(list(fds), [], [], 1.0)
            else:
                ready = []
                self._stop.wait(1.0)  # no controller (yet): just look again later
            for fd in ready:
                try:
                    data = os.read(fd, EVENT.size * 64)
                except BlockingIOError:
                    continue
                except OSError:
                    return  # unplugged
                if not data:
                    return
                for off in range(0, len(data) - EVENT.size + 1, EVENT.size):
                    _, _, etype, code, value = EVENT.unpack_from(data, off)
                    if etype == EV_KEY:
                        fds[fd].key(code, value)
            if time.monotonic() >= next_scan:
                if find_gamepads() != nodes or self.get_chord() != chord:
                    return
                next_scan = time.monotonic() + RESCAN
