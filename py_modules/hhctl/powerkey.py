"""Power button in Game Mode, where nothing else acts on it.

On SteamOS a small service passes a short power press to Steam, which then
sleeps. Some installs lack it (CachyOS on the ONEXPLAYER 3: logind logs
"Power key pressed short." and nothing happens). The plugin reads the power
key read-only (no grab), and the controller puts it to sleep at once
(systemctl suspend, like Desktop Mode); Steam's slower way (it dims first)
is only the fallback.

The press that wakes the device is often delivered after the resume; it's
told apart by the jump between CLOCK_BOOTTIME and CLOCK_MONOTONIC (the time
spent asleep) and ignored.
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
EV_KEY, KEY_POWER = 0x01, 116
SHORT = 1.5  # seconds: longer is a hold (the firmware's power-off)
AFTER_WAKE = 5.0  # seconds: presses this soon after a resume woke it
RESCAN = 10.0


def _has(bitmap: str, code: int) -> bool:
    words = bitmap.split()
    idx = len(words) - 1 - code // 64
    return 0 <= idx < len(words) and bool(int(words[idx], 16) >> (code % 64) & 1)


def find_power_keys() -> list[str]:
    try:
        names = sorted(n for n in os.listdir(SYS_INPUT) if n.startswith("event"))
    except OSError:
        return []
    out = []
    for n in names:
        if _has(sysfs.read(f"{SYS_INPUT}/{n}/device/capabilities/key") or "0", KEY_POWER):
            out.append(f"{DEV}/{n}")
    return out


def _asleep_total() -> float:
    return time.clock_gettime(time.CLOCK_BOOTTIME) - time.monotonic()


class PowerKey:
    def __init__(self, on_short):
        self.on_short = on_short  # called in this thread for a short press
        self.nodes: list[str] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._asleep = _asleep_total()
        self._resumed = 0.0  # monotonic time of the last resume seen

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="hhctl-powerkey", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _check_resume(self) -> None:
        a = _asleep_total()
        if a - self._asleep > 1.0:
            self._resumed = time.monotonic()
        self._asleep = a

    def press(self, held: float) -> None:
        """A press and release, held for `held` seconds."""
        self._check_resume()
        if held > SHORT or time.monotonic() - self._resumed < AFTER_WAKE:
            return
        try:
            self.on_short()
        except Exception:
            log.exception("powerkey: handler failed")

    def _run(self) -> None:
        while not self._stop.is_set():
            nodes = find_power_keys()
            fds = {}
            for node in nodes:
                try:
                    fds[os.open(node, os.O_RDONLY | os.O_NONBLOCK)] = node
                except OSError as e:
                    log.warning("powerkey: cannot open %s: %s", node, e)
            self.nodes = list(fds.values())
            try:
                self._read(list(fds), nodes)
            except Exception:
                log.exception("powerkey: listener failed, starting over")
            finally:
                for fd in fds:
                    os.close(fd)
                self.nodes = []
            self._stop.wait(1.0)

    def _read(self, fds: list[int], nodes: list[str]) -> None:
        down: float | None = None
        next_scan = time.monotonic() + RESCAN
        while not self._stop.is_set():
            ready = select.select(fds, [], [], 1.0)[0] if fds else []
            if not fds:
                self._stop.wait(1.0)
            if not ready:
                self._check_resume()
            for fd in ready:
                try:
                    data = os.read(fd, EVENT.size * 64)
                except BlockingIOError:
                    continue
                except OSError:
                    return
                if not data:
                    return
                for off in range(0, len(data) - EVENT.size + 1, EVENT.size):
                    _, _, etype, code, value = EVENT.unpack_from(data, off)
                    if etype != EV_KEY or code != KEY_POWER or value == 2:
                        continue
                    if value == 1:
                        down = time.monotonic()
                    elif down is not None:
                        held, down = time.monotonic() - down, None
                        self.press(held)
            if time.monotonic() >= next_scan:
                if find_power_keys() != nodes:
                    return
                next_scan = time.monotonic() + RESCAN
