"""Swipe in from the screen's bottom edge: opens the Steam keyboard.

Reads the touchscreen read-only (no grab), so touches still reach Steam and
games. Handheld panels are often portrait underneath, so the touchscreen's
own "bottom" may be a side of the landscape picture: the edge is learned
from one swipe (calibrate()) and kept in the settings as x0/x1/y0/y1, the
touch axis and its low or high end.
"""

import fcntl
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
EV_SYN, EV_ABS = 0x00, 0x03
SYN_REPORT = 0
ABS_MT_SLOT, ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID = 0x2F, 0x35, 0x36, 0x39
INPUT_PROP_DIRECT = 1
ABSINFO = struct.Struct("6i")  # value, minimum, maximum, fuzz, flat, resolution

EDGE = 0.05  # a swipe starts within this share of the screen from an edge
DISTANCE = 0.12  # and goes this far in
TIME = 0.8  # seconds


def _bit(bitmap: str, n: int) -> bool:
    words = bitmap.split()
    idx = len(words) - 1 - n // 64
    return 0 <= idx < len(words) and bool(int(words[idx], 16) >> (n % 64) & 1)


def find_touchscreens() -> list[str]:
    """Direct-touch devices (screens, not touchpads) with multitouch positions.
    All of them: InputPlumber or gamescope may grab the real one and pass the
    touches on through a virtual one."""
    try:
        names = sorted(n for n in os.listdir(SYS_INPUT) if n.startswith("event"))
    except OSError:
        return []
    out = []
    for n in names:
        d = f"{SYS_INPUT}/{n}/device"
        abs_caps = sysfs.read(f"{d}/capabilities/abs") or "0"
        if (_bit(sysfs.read(f"{d}/properties") or "0", INPUT_PROP_DIRECT)
                and _bit(abs_caps, ABS_MT_POSITION_X) and _bit(abs_caps, ABS_MT_POSITION_Y)):
            out.append(f"{DEV}/{n}")
    return out


def _range(fd: int, code: int) -> tuple[int, int]:
    buf = bytearray(ABSINFO.size)
    fcntl.ioctl(fd, 0x80184540 + code, buf)  # EVIOCGABS(code)
    _, lo, hi, _, _, _ = ABSINFO.unpack(buf)
    return lo, max(hi, lo + 1)


class Swipes:
    """Tracks one finger at a time; calls found(edge) for an edge swipe."""

    def __init__(self, xr: tuple[int, int], yr: tuple[int, int], found):
        self.xr, self.yr, self.found = xr, yr, found
        self.slot = 0
        self.touches: dict[int, dict] = {}

    def _norm(self, t: dict) -> tuple[float, float]:
        return ((t["x"] - self.xr[0]) / (self.xr[1] - self.xr[0]),
                (t["y"] - self.yr[0]) / (self.yr[1] - self.yr[0]))

    def event(self, etype: int, code: int, value: int, now: float) -> None:
        if etype == EV_ABS:
            if code == ABS_MT_SLOT:
                self.slot = value
            elif code == ABS_MT_TRACKING_ID:
                if value < 0:
                    self.touches.pop(self.slot, None)
                else:
                    self.touches[self.slot] = {"x": None, "y": None, "start": None, "t0": now, "done": False}
            elif code in (ABS_MT_POSITION_X, ABS_MT_POSITION_Y) and self.slot in self.touches:
                self.touches[self.slot]["x" if code == ABS_MT_POSITION_X else "y"] = value
        elif etype == EV_SYN and code == SYN_REPORT:
            self._check(now)

    def _check(self, now: float) -> None:
        if len(self.touches) != 1:
            for t in self.touches.values():
                t["done"] = True  # two fingers: not a swipe
            return
        t = next(iter(self.touches.values()))
        if t["done"] or t["x"] is None or t["y"] is None:
            return
        nx, ny = self._norm(t)
        if t["start"] is None:
            near = {"x0": nx, "x1": 1 - nx, "y0": ny, "y1": 1 - ny}
            edge = min(near, key=near.get)
            if near[edge] > EDGE:
                t["done"] = True  # began away from the edges
                return
            t["start"] = (edge, nx, ny)
            return
        edge, sx, sy = t["start"]
        if now - t["t0"] > TIME:
            t["done"] = True
            return
        inward = {"x0": nx - sx, "x1": sx - nx, "y0": ny - sy, "y1": sy - ny}[edge]
        across = abs(ny - sy) if edge[0] == "x" else abs(nx - sx)
        if inward >= DISTANCE and inward > 2 * across:
            t["done"] = True
            self.found(edge)


class EdgeSwipe:
    RETRY = 3.0

    def __init__(self, emit, get_edge, set_edge):
        self.emit = emit  # emit("keyboard")
        self.get_edge = get_edge  # the learned edge or None
        self.set_edge = set_edge  # called with the edge of the first swipe while learning
        self.calibrating = False
        self._last = 0.0  # when the last swipe was taken
        self.node: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def available(self) -> bool:
        return bool(find_touchscreens())

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="hhctl-swipe", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def found(self, edge: str) -> None:
        now = time.monotonic()
        if now - self._last < 1.0:
            return  # the same swipe from a second (virtual) touchscreen
        self._last = now
        if self.calibrating:
            self.calibrating = False
            log.info("swipe: bottom edge is %s", edge)
            self.set_edge(edge)
        elif edge == self.get_edge():
            log.info("swipe: keyboard")
            try:
                self.emit("keyboard")
            except Exception:
                log.exception("swipe: emit failed")

    def _run(self) -> None:
        while not self._stop.is_set():
            fds: dict[int, Swipes] = {}
            for node in find_touchscreens():
                try:
                    fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
                except OSError as e:
                    log.warning("swipe: cannot open %s: %s", node, e)
                    continue
                try:
                    fds[fd] = Swipes(_range(fd, ABS_MT_POSITION_X), _range(fd, ABS_MT_POSITION_Y), self.found)
                except OSError as e:
                    log.warning("swipe: cannot use %s: %s", node, e)
                    os.close(fd)
            if not fds:
                self._stop.wait(self.RETRY)
                continue
            self.node = ", ".join(sorted(find_touchscreens()))
            log.info("swipe: listening on %s", self.node)
            try:
                self._read(fds)
            except Exception:
                log.exception("swipe: listener failed, starting over")
            finally:
                for fd in fds:
                    os.close(fd)
                self.node = None
            self._stop.wait(1.0)

    def _read(self, fds: dict[int, "Swipes"]) -> None:
        while not self._stop.is_set():
            ready, _, _ = select.select(list(fds), [], [], 0.5)
            for fd in ready:
                try:
                    data = os.read(fd, EVENT.size * 64)
                except BlockingIOError:
                    continue
                except OSError:
                    return  # one went away (resume): open them all again
                if not data:
                    return
                now = time.monotonic()
                sw = fds[fd]
                for off in range(0, len(data) - EVENT.size + 1, EVENT.size):
                    _, _, etype, code, value = EVENT.unpack_from(data, off)
                    sw.event(etype, code, value, now)
